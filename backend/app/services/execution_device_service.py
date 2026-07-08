import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.worker import Worker, WorkerAccountTaskLease, WorkerLeaseStatus, WorkerStatus
from app.schemas.execution_devices import DeletedExecutionDeviceRead, ExecutionDeviceDeleteResponse, ExecutionDeviceRead
from app.schemas.worker import WorkerApproveRequest
from app.services.task_rules import utc_now
from app.services.worker_dispatch_service import disable_worker_and_reclaim
from app.services.worker_service import approve_worker, ensure_worker


PAUSED_HEALTH_STATUS = "receiving_paused"
RECYCLED_HEALTH_STATUS = "recycled"


def list_execution_device_reads(
    db: Session,
    *,
    q: str = "",
    status: str = "",
    approval_status: str = "",
    update_status: str = "",
    current_state: str = "",
    usable_for_production: Optional[bool] = None,
) -> list[ExecutionDeviceRead]:
    workers = list(db.scalars(select(Worker).order_by(Worker.is_platform_worker.desc(), Worker.worker_id.asc())))
    reads = [_device_read(db, worker) for worker in workers if worker.health_status != RECYCLED_HEALTH_STATUS]
    if q:
        needle = q.lower()
        reads = [item for item in reads if needle in item.worker_id.lower() or needle in item.device_name.lower() or needle in item.needs_attention.lower()]
    if status:
        reads = [item for item in reads if item.status == status]
    if approval_status:
        reads = [item for item in reads if item.approval_status == approval_status]
    if update_status:
        reads = [item for item in reads if item.update_status == update_status]
    if current_state:
        reads = [item for item in reads if item.current_state == current_state]
    if usable_for_production is not None:
        reads = [item for item in reads if item.usable_for_production is usable_for_production]
    return reads


def get_execution_device(db: Session, worker_id: str) -> ExecutionDeviceRead:
    return _device_read(db, ensure_worker(db, worker_id))


def approve_execution_device(db: Session, worker_id: str, manual_slots: int = 1) -> ExecutionDeviceRead:
    worker = approve_worker(db, worker_id, WorkerApproveRequest(configured_http_account_slots=max(1, int(manual_slots or 1))))
    worker.effective_http_account_slots = worker.configured_http_account_slots
    return _device_read(db, worker)


def reject_execution_device(db: Session, worker_id: str) -> ExecutionDeviceRead:
    worker = ensure_worker(db, worker_id)
    worker.status = WorkerStatus.REJECTED
    worker.health_status = "rejected"
    worker.disabled_reason = "人工拒绝"
    db.flush()
    return _device_read(db, worker)


def disable_execution_device(db: Session, worker_id: str) -> ExecutionDeviceRead:
    disable_worker_and_reclaim(db, worker_id, reason="执行设备管理禁用")
    worker = ensure_worker(db, worker_id)
    return _device_read(db, worker)


def delete_execution_device(db: Session, worker_id: str) -> ExecutionDeviceDeleteResponse:
    worker = ensure_worker(db, worker_id)
    if worker.is_platform_worker or worker.worker_id == "platform-worker":
        raise ValueError("platform-worker 是平台内置执行器，不能删除。")
    metadata = {
        "deletedAt": utc_now().isoformat(),
        "deleteReason": "用户在执行设备管理执行删除",
        "previousStatus": worker.status.value if hasattr(worker.status, "value") else str(worker.status),
        "previousHealthStatus": worker.health_status,
    }
    worker.status = WorkerStatus.DISABLED
    worker.health_status = RECYCLED_HEALTH_STATUS
    worker.disabled_reason = json.dumps(metadata, ensure_ascii=False)
    db.flush()
    return ExecutionDeviceDeleteResponse(worker_id=worker_id, deleted=True, message="执行设备已移入回收站，历史租约和日志已保留。")


def list_deleted_execution_devices(db: Session) -> list[DeletedExecutionDeviceRead]:
    workers = list(db.scalars(select(Worker).where(Worker.health_status == RECYCLED_HEALTH_STATUS).order_by(Worker.updated_at.desc(), Worker.worker_id.asc())))
    return [_deleted_device_read(worker) for worker in workers]


def restore_execution_device(db: Session, worker_id: str) -> ExecutionDeviceRead:
    worker = ensure_worker(db, worker_id)
    if worker.health_status != RECYCLED_HEALTH_STATUS:
        raise ValueError("回收站中未找到该执行设备。")
    worker.status = WorkerStatus.OFFLINE
    worker.health_status = "restored"
    worker.disabled_reason = ""
    db.flush()
    return _device_read(db, worker)


def rename_execution_device(db: Session, worker_id: str, device_name: str) -> ExecutionDeviceRead:
    worker = ensure_worker(db, worker_id)
    worker.display_name = device_name.strip() or worker.worker_id
    db.flush()
    return _device_read(db, worker)


def update_execution_device_capacity(db: Session, worker_id: str, manual_slots: int) -> ExecutionDeviceRead:
    worker = ensure_worker(db, worker_id)
    slots = max(1, int(manual_slots))
    worker.configured_http_account_slots = slots
    worker.effective_http_account_slots = slots
    db.flush()
    return _device_read(db, worker)


def pause_execution_device_receiving(db: Session, worker_id: str) -> ExecutionDeviceRead:
    worker = ensure_worker(db, worker_id)
    worker.health_status = PAUSED_HEALTH_STATUS
    worker.disabled_reason = "暂停接收任务"
    db.flush()
    return _device_read(db, worker)


def resume_execution_device_receiving(db: Session, worker_id: str) -> ExecutionDeviceRead:
    worker = ensure_worker(db, worker_id)
    if worker.status != WorkerStatus.DISABLED:
        worker.health_status = "passed"
        worker.disabled_reason = ""
    db.flush()
    return _device_read(db, worker)


def check_execution_device_updates(db: Session, worker_id: str) -> ExecutionDeviceRead:
    worker = ensure_worker(db, worker_id)
    worker.health_checked_at = utc_now()
    if not worker.health_status or worker.health_status == "unknown":
        worker.health_status = "passed"
    db.flush()
    return _device_read(db, worker)


def selected_worker_ids_for_production(db: Session, *, execution_mode: str, device_mode: str, worker_ids: list[str]) -> list[str]:
    devices = list_execution_device_reads(db, usable_for_production=True)
    platform = [item.worker_id for item in devices if item.worker_id == "platform-worker"]
    external = [item.worker_id for item in devices if item.worker_id != "platform-worker"]
    if execution_mode == "platform":
        if not platform:
            raise ValueError("platform-worker 不可用，不能启动平台执行。")
        return platform
    if device_mode == "specified":
        selected = [item for item in devices if item.worker_id in set(worker_ids)]
        missing = [worker_id for worker_id in worker_ids if worker_id not in {item.worker_id for item in selected}]
        if missing:
            raise ValueError("指定设备不可用或可用并发为 0：" + "、".join(missing))
        external = [item.worker_id for item in selected if item.worker_id != "platform-worker"]
    if execution_mode == "devices":
        if not external:
            raise ValueError("没有可用并发的外部执行设备，不能启动设备执行。")
        return external
    result = platform + external
    if not result:
        raise ValueError("没有可用执行设备。")
    return result


def _device_read(db: Session, worker: Worker) -> ExecutionDeviceRead:
    running_slots = _running_slots(db, worker.worker_id)
    manual_slots = max(0, int(worker.configured_http_account_slots or worker.effective_http_account_slots or 0))
    if worker.status == WorkerStatus.ONLINE and not worker.is_platform_worker and manual_slots <= 0:
        manual_slots = 1
    effective_slots = manual_slots
    available_slots = max(effective_slots - running_slots, 0)
    can_receive = worker.status in {WorkerStatus.ONLINE, WorkerStatus.DEGRADED} and worker.health_status != PAUSED_HEALTH_STATUS
    usable = can_receive and available_slots > 0
    current_state = "running" if running_slots > 0 else "idle"
    if worker.health_status == PAUSED_HEALTH_STATUS:
        current_state = "paused_receiving"
    return ExecutionDeviceRead(
        worker_id=worker.worker_id,
        agent_id=worker.worker_id,
        device_name=worker.display_name or worker.worker_id,
        status=worker.status.value if hasattr(worker.status, "value") else str(worker.status),
        approval_status=_approval_status(worker),
        current_state=current_state,
        manual_slots=manual_slots,
        running_slots=running_slots,
        effective_slots=effective_slots,
        available_slots=available_slots,
        local_agent_version=worker.version,
        worker_runtime_version=worker.version,
        update_status="unknown" if worker.health_status in {"unknown", ""} else "latest",
        last_seen_at=worker.last_heartbeat_at,
        current_run={"task_id": worker.current_task_id, "account_user_id": worker.current_account_user_id} if worker.current_task_id or worker.current_account_user_id else {},
        needs_attention=worker.last_error or worker.disabled_reason or ("" if usable else _needs_attention(worker, available_slots)),
        can_receive_tasks=can_receive,
        usable_for_production=usable,
    )


def _running_slots(db: Session, worker_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(WorkerAccountTaskLease)
            .where(WorkerAccountTaskLease.worker_id == worker_id, WorkerAccountTaskLease.status == WorkerLeaseStatus.ACTIVE)
        )
        or 0
    )


def _approval_status(worker: Worker) -> str:
    if worker.status == WorkerStatus.PENDING_APPROVAL:
        return "pending"
    if worker.status == WorkerStatus.REJECTED:
        return "rejected"
    if worker.status == WorkerStatus.DISABLED:
        return "disabled"
    return "approved"


def _needs_attention(worker: Worker, available_slots: int) -> str:
    if worker.status == WorkerStatus.PENDING_APPROVAL:
        return "待批准"
    if worker.status == WorkerStatus.DISABLED:
        return "已禁用"
    if worker.health_status == PAUSED_HEALTH_STATUS:
        return "已暂停接收任务"
    if available_slots <= 0:
        return "可用并发为 0"
    return ""


def _deleted_device_read(worker: Worker) -> DeletedExecutionDeviceRead:
    metadata = _recycle_metadata(worker.disabled_reason)
    return DeletedExecutionDeviceRead(
        worker_id=worker.worker_id,
        device_name=worker.display_name or worker.worker_id,
        deleted_at=_parse_datetime(str(metadata.get("deletedAt") or "")),
        delete_reason=str(metadata.get("deleteReason") or ""),
        last_seen_at=worker.last_heartbeat_at,
    )


def _recycle_metadata(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
