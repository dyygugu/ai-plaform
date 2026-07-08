import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.worker import Worker, WorkerEvent, WorkerEventType, WorkerStatus
from app.schemas.worker import WorkerApproveRequest, WorkerEventReportRequest, WorkerHeartbeatRequest, WorkerRegisterRequest
from app.services.notification_service import send_error_notification
from app.services.task_rules import utc_now

BLOCKED_WORKER_STATUSES = {WorkerStatus.PENDING_APPROVAL, WorkerStatus.REJECTED, WorkerStatus.DISABLED}


def ensure_worker(db: Session, worker_id: str) -> Worker:
    worker = db.scalar(select(Worker).where(Worker.worker_id == worker_id))
    if worker is None:
        worker = Worker(worker_id=worker_id, display_name=worker_id, status=WorkerStatus.OFFLINE)
        db.add(worker)
        db.flush()
    return worker


def add_worker_event(
    db: Session,
    worker_id: str,
    event_type: WorkerEventType,
    account_user_id: str = "",
    task_id: str = "",
    target_version: str = "",
    severity: str = "info",
    message: str = "",
) -> WorkerEvent:
    event = WorkerEvent(
        worker_id=worker_id,
        event_type=event_type,
        account_user_id=account_user_id,
        task_id=task_id,
        target_version=target_version,
        severity=severity,
        message=message,
        trace_id=uuid4().hex,
    )
    db.add(event)
    db.flush()
    if severity in {"error", "critical"}:
        send_error_notification(
            event="worker.error",
            level=severity,
            message=message or f"Worker {worker_id} 上报错误",
            data={"worker_id": worker_id, "event_type": event_type.value, "account_user_id": account_user_id, "task_id": task_id, "target_version": target_version},
            trace_id=event.trace_id,
        )
    return event


def upsert_worker_heartbeat(db: Session, payload: WorkerHeartbeatRequest) -> Worker:
    worker = ensure_worker(db, payload.worker_id)
    worker.display_name = payload.display_name or payload.worker_id
    worker.version = payload.version
    worker.current_account_user_id = payload.current_account_user_id
    worker.current_task_id = payload.current_task_id
    worker.last_error = payload.last_error
    if worker.status not in BLOCKED_WORKER_STATUSES:
        worker.status = WorkerStatus.DEGRADED if payload.last_error else WorkerStatus.ONLINE
    worker.last_heartbeat_at = utc_now()
    add_worker_event(
        db,
        worker.worker_id,
        WorkerEventType.HEARTBEAT,
        account_user_id=worker.current_account_user_id,
        task_id=worker.current_task_id,
        target_version=worker.version,
        severity="warning" if payload.last_error else "info",
        message=payload.last_error or "heartbeat ok",
    )
    db.flush()
    return worker


def register_worker(db: Session, payload: WorkerRegisterRequest) -> Worker:
    worker = ensure_worker(db, payload.worker_id)
    previous_status = worker.status
    was_approved = previous_status in {WorkerStatus.ONLINE, WorkerStatus.DEGRADED} or (
        previous_status == WorkerStatus.OFFLINE and int(worker.configured_http_account_slots or 0) > 0
    )
    is_blocked = previous_status in {WorkerStatus.REJECTED, WorkerStatus.DISABLED}
    worker.display_name = payload.display_name or payload.worker_id
    worker.version = payload.version
    worker.estimated_http_account_slots = max(0, int(payload.estimated_http_account_slots or 0))
    if is_blocked:
        worker.status = previous_status
    elif was_approved:
        worker.status = WorkerStatus.ONLINE
        worker.health_status = "passed"
        worker.health_fail_reasons = ""
        if int(worker.effective_http_account_slots or 0) <= 0:
            worker.effective_http_account_slots = int(worker.configured_http_account_slots or 0)
    else:
        worker.status = WorkerStatus.PENDING_APPROVAL
        worker.configured_http_account_slots = 0
        worker.effective_http_account_slots = 0
        worker.health_status = "pending_approval"
    worker.last_heartbeat_at = utc_now()
    add_worker_event(
        db,
        worker.worker_id,
        WorkerEventType.HEARTBEAT,
        target_version=worker.version,
        message=(
            "Worker 主动注册，保持已批准状态"
            if was_approved
            else "Worker 主动注册，但当前已禁用或拒绝"
            if is_blocked
            else "Worker 主动注册，等待人工批准"
        ),
    )
    db.flush()
    return worker


def approve_worker(db: Session, worker_id: str, payload: WorkerApproveRequest) -> Worker:
    worker = ensure_worker(db, worker_id)
    slots = max(0, int(payload.configured_http_account_slots or worker.estimated_http_account_slots or 0))
    worker.status = WorkerStatus.ONLINE
    worker.configured_http_account_slots = slots
    worker.effective_http_account_slots = slots
    worker.health_status = "passed"
    worker.health_checked_at = utc_now()
    worker.health_fail_reasons = ""
    worker.disabled_reason = ""
    add_worker_event(
        db,
        worker.worker_id,
        WorkerEventType.HEARTBEAT,
        severity="info",
        message=f"Worker 人工批准进入调度池，生效槽位 {slots}",
    )
    db.flush()
    return worker


def list_workers(db: Session) -> list[Worker]:
    return list(db.scalars(select(Worker).order_by(Worker.last_heartbeat_at.desc().nullslast(), Worker.worker_id.asc())))


def bind_worker_account(db: Session, worker_id: str, account_user_id: str, message: str = "") -> tuple[Worker, WorkerEvent]:
    worker = ensure_worker(db, worker_id)
    worker.current_account_user_id = account_user_id
    event = add_worker_event(
        db,
        worker_id,
        WorkerEventType.BIND_ACCOUNT,
        account_user_id=account_user_id,
        message=message or f"绑定账号 {account_user_id}",
    )
    db.flush()
    return worker, event


def update_worker_version(db: Session, worker_id: str, target_version: str, message: str = "") -> tuple[Worker, WorkerEvent]:
    worker = ensure_worker(db, worker_id)
    worker.version = target_version
    event = add_worker_event(
        db,
        worker_id,
        WorkerEventType.VERSION_UPDATE,
        account_user_id=worker.current_account_user_id,
        task_id=worker.current_task_id,
        target_version=target_version,
        message=message or f"更新目标版本 {target_version}",
    )
    db.flush()
    return worker, event


def claim_worker_task(db: Session, worker_id: str, task_id: str, account_user_id: str = "", message: str = "") -> tuple[Worker, WorkerEvent]:
    worker = ensure_worker(db, worker_id)
    if account_user_id:
        worker.current_account_user_id = account_user_id
    worker.current_task_id = task_id
    event = add_worker_event(
        db,
        worker_id,
        WorkerEventType.TASK_CLAIM,
        account_user_id=worker.current_account_user_id,
        task_id=task_id,
        target_version=worker.version,
        message=message or f"领取任务 {task_id}",
    )
    db.flush()
    return worker, event


def report_worker_event(db: Session, payload: WorkerEventReportRequest) -> tuple[Worker, WorkerEvent]:
    worker = ensure_worker(db, payload.worker_id)
    event_type = WorkerEventType.EVENT_REPORT
    for candidate in WorkerEventType:
        if candidate.value == payload.event_type:
            event_type = candidate
            break
    if payload.account_user_id:
        worker.current_account_user_id = payload.account_user_id
    if payload.task_id:
        worker.current_task_id = payload.task_id
    if payload.target_version:
        worker.version = payload.target_version
    message = _serialize_worker_event_message(payload)
    if payload.severity in {"error", "critical"}:
        worker.last_error = _human_worker_event_message(payload)
        if worker.status not in BLOCKED_WORKER_STATUSES:
            worker.status = WorkerStatus.DEGRADED
    event = add_worker_event(
        db,
        payload.worker_id,
        event_type,
        account_user_id=payload.account_user_id,
        task_id=payload.task_id,
        target_version=payload.target_version,
        severity=payload.severity,
        message=message,
    )
    db.flush()
    return worker, event


def _serialize_worker_event_message(payload: WorkerEventReportRequest) -> str:
    structured = {
        "message": payload.message,
        "stage": payload.stage,
        "step": payload.step,
        "error_code": payload.error_code,
        "error_detail": payload.error_detail,
        "retryable": payload.retryable,
        "duration_ms": payload.duration_ms,
    }
    has_structured_fields = any(value not in ("", None) for key, value in structured.items() if key != "message")
    if not has_structured_fields:
        return payload.message
    return json.dumps({key: value for key, value in structured.items() if value not in ("", None)}, ensure_ascii=False, separators=(",", ":"))


def _human_worker_event_message(payload: WorkerEventReportRequest) -> str:
    if payload.error_code or payload.error_detail:
        detail = payload.error_detail or payload.message
        return f"{payload.error_code or 'WORKER_ERROR'}: {detail}"
    return payload.message


def list_worker_events(db: Session, worker_id: str, limit: int = 20) -> list[WorkerEvent]:
    return list(db.scalars(select(WorkerEvent).where(WorkerEvent.worker_id == worker_id).order_by(WorkerEvent.created_at.desc(), WorkerEvent.id.desc()).limit(limit)))


def summarize_worker_logs(db: Session, worker_id: str) -> dict:
    events = list_worker_events(db, worker_id, limit=20)
    return {
        "worker_id": worker_id,
        "total_events": len(events),
        "error_events": sum(1 for event in events if event.severity in {"error", "critical"}),
        "warning_events": sum(1 for event in events if event.severity == "warning"),
        "latest_message": events[0].message if events else "暂无事件",
        "events": events,
    }
