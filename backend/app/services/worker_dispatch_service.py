import json
from datetime import timedelta, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.worker import (
    Worker,
    WorkerAccountTaskLease,
    WorkerCommand,
    WorkerCommandStatus,
    WorkerEventType,
    WorkerLeaseStatus,
    WorkerStatus,
)
from app.services.task_rules import utc_now
from app.services.worker_service import add_worker_event, ensure_worker


PLATFORM_WORKER_ID = "platform-worker"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 180
LEASE_FAILURE_THRESHOLD = 3
LEASE_AUTO_RECOVERY_COOLDOWN_SECONDS = 15 * 60
AUTO_RECOVERABLE_ERROR_CODES = {
    "TASK_PAGE_TIMEOUT",
    "AI_PROVIDER_502",
    "AI_PROVIDER_TIMEOUT",
    "WORKER_OFFLINE",
    "WORKER_EXCEPTION",
    "UNKNOWN_ERROR",
}
MANUAL_RECOVERY_ERROR_CODES = {
    "TASK_PAGE_AUTH_EXPIRED",
    "TASK_PARSE_FAILED",
    "AI_RESPONSE_INVALID",
    "CONFIRMATION_REJECTED",
    "SUBMIT_FAILED",
    "READBACK_MISMATCH",
}
BLOCKED_WORKER_STATUSES = {WorkerStatus.REJECTED, WorkerStatus.DISABLED}
SCHEDULABLE_WORKER_STATUSES = {WorkerStatus.ONLINE, WorkerStatus.DEGRADED}


def ensure_platform_worker(db: Session, inherited_http_account_slots: int) -> Worker:
    worker = ensure_worker(db, PLATFORM_WORKER_ID)
    now = utc_now()
    slots = max(0, int(inherited_http_account_slots or 0))
    worker.display_name = "平台本机执行器"
    worker.is_platform_worker = True
    if worker.status in BLOCKED_WORKER_STATUSES:
        worker.effective_http_account_slots = 0
    else:
        worker.status = WorkerStatus.ONLINE
        worker.configured_http_account_slots = slots
        worker.effective_http_account_slots = slots
        worker.health_status = "passed"
        worker.health_fail_reasons = ""
    worker.health_checked_at = now
    worker.last_heartbeat_at = now
    db.flush()
    return worker


def start_account_task_lease(
    db: Session,
    *,
    worker_id: str,
    account_user_id: str,
    task_id: str,
) -> WorkerAccountTaskLease:
    existing = db.scalar(
        select(WorkerAccountTaskLease).where(
            WorkerAccountTaskLease.account_user_id == account_user_id,
            WorkerAccountTaskLease.task_id == task_id,
            WorkerAccountTaskLease.status == WorkerLeaseStatus.ACTIVE,
        )
    )
    if existing is not None:
        return existing
    ensure_worker(db, worker_id)
    lease = WorkerAccountTaskLease(
        lease_id=uuid4().hex,
        worker_id=worker_id,
        account_user_id=account_user_id,
        task_id=task_id,
        status=WorkerLeaseStatus.ACTIVE,
        last_heartbeat_at=utc_now(),
    )
    db.add(lease)
    add_worker_event(
        db,
        worker_id,
        WorkerEventType.LEASE,
        account_user_id=account_user_id,
        task_id=task_id,
        message=f"创建账号任务组租约 {account_user_id}/{task_id}",
    )
    db.flush()
    return lease


def list_account_task_leases(db: Session, *, limit: int = 100) -> list[WorkerAccountTaskLease]:
    return list(
        db.scalars(
            select(WorkerAccountTaskLease)
            .order_by(WorkerAccountTaskLease.updated_at.desc(), WorkerAccountTaskLease.id.desc())
            .limit(limit)
        )
    )


def create_worker_command(
    db: Session,
    *,
    worker_id: str,
    command_type: str,
    account_user_id: str = "",
    task_id: str = "",
    payload: Optional[dict] = None,
    retry_of_command_id: str = "",
) -> WorkerCommand:
    if worker_id:
        ensure_worker(db, worker_id)
    command = WorkerCommand(
        command_id=uuid4().hex,
        retry_of_command_id=retry_of_command_id,
        worker_id=worker_id,
        command_type=command_type,
        status=WorkerCommandStatus.QUEUED,
        account_user_id=account_user_id,
        task_id=task_id,
        payload_json=_json(payload or {}),
        timeout_seconds=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    db.add(command)
    add_worker_event(
        db,
        worker_id or "scheduler",
        WorkerEventType.COMMAND,
        account_user_id=account_user_id,
        task_id=task_id,
        message=f"创建命令 {command.command_type}",
    )
    db.flush()
    return command


def assign_unbound_worker_commands(db: Session) -> list[WorkerCommand]:
    commands = list(
        db.scalars(
            select(WorkerCommand)
            .where(
                WorkerCommand.worker_id == "",
                WorkerCommand.status == WorkerCommandStatus.QUEUED,
            )
            .order_by(WorkerCommand.created_at.asc(), WorkerCommand.id.asc())
        )
    )
    if not commands:
        return []
    workers = _available_workers(db)
    assigned: list[WorkerCommand] = []
    for command in commands:
        worker = _select_worker_for_command(db, workers)
        if worker is None:
            break
        command.worker_id = worker.worker_id
        command.audit_note = f"调度扫描分配给 {worker.worker_id}"
        add_worker_event(
            db,
            worker.worker_id,
            WorkerEventType.COMMAND,
            account_user_id=command.account_user_id,
            task_id=command.task_id,
            message=f"调度扫描分配命令 {command.command_id}",
        )
        assigned.append(command)
    db.flush()
    return assigned


def check_worker_command_execution_gate(db: Session, command_id: str, *, worker_id: str, now=None) -> dict:
    command = _get_command(db, command_id)
    now = now or utc_now()
    checks = []

    def add_check(key: str, title: str, passed: bool, detail: str) -> None:
        checks.append({"key": key, "title": title, "status": "passed" if passed else "failed", "detail": detail})

    owner_ok = command.worker_id == worker_id
    add_check("command_owner", "命令归属", owner_ok, "命令归属当前 Worker" if owner_ok else f"命令归属 {command.worker_id or '未分配'}")

    worker = db.scalar(select(Worker).where(Worker.worker_id == worker_id))
    worker_status = worker.status if worker is not None else None
    worker_ok = worker_status in SCHEDULABLE_WORKER_STATUSES
    worker_status_text = worker_status.value if hasattr(worker_status, "value") else str(worker_status or "missing")
    add_check("worker_status", "Worker 状态", worker_ok, f"当前状态 {worker_status_text}")

    status_ok = command.status in {WorkerCommandStatus.CLAIMED, WorkerCommandStatus.RUNNING}
    add_check("command_status", "命令状态", status_ok, f"当前状态 {command.status.value if hasattr(command.status, 'value') else command.status}")

    last_seen = command.last_renewed_at or command.claimed_at or command.created_at
    fresh_ok = bool(last_seen) and (_as_utc(now) - _as_utc(last_seen)).total_seconds() < command.timeout_seconds
    add_check("command_fresh", "命令续约", fresh_ok, "命令未超时" if fresh_ok else "命令已超时或从未续约")

    type_ok = command.command_type == "produce_account_task"
    add_check("command_type", "命令类型", type_ok, command.command_type)

    payload = _loads(command.payload_json)
    mode = str(payload.get("mode") or "")
    mode_ok = mode in {"execute_once", "preflight_only"}
    add_check("execution_mode", "执行模式", mode_ok, mode or "未填写")

    lease = None
    if command.account_user_id and command.task_id:
        lease = db.scalar(
            select(WorkerAccountTaskLease).where(
                WorkerAccountTaskLease.worker_id == worker_id,
                WorkerAccountTaskLease.account_user_id == command.account_user_id,
                WorkerAccountTaskLease.task_id == command.task_id,
                WorkerAccountTaskLease.status == WorkerLeaseStatus.ACTIVE,
            )
        )
    lease_ok = lease is not None
    add_check("active_lease", "账号任务组租约", lease_ok, "存在活跃租约" if lease_ok else "缺少当前 Worker 的活跃账号任务组租约")

    conflict = db.scalar(
        select(WorkerAccountTaskLease).where(
            WorkerAccountTaskLease.account_user_id == command.account_user_id,
            WorkerAccountTaskLease.task_id == command.task_id,
            WorkerAccountTaskLease.status == WorkerLeaseStatus.ACTIVE,
            WorkerAccountTaskLease.worker_id != worker_id,
        )
    ) if command.account_user_id and command.task_id else None
    no_conflict = conflict is None
    add_check("lease_conflict", "重复执行防护", no_conflict, "无其他 Worker 活跃租约" if no_conflict else f"其他 Worker 持有租约 {conflict.worker_id}")

    can_execute = all(item["status"] == "passed" for item in checks)
    return {
        "status": "ready" if can_execute else "blocked",
        "can_execute": can_execute,
        "command_id": command.command_id,
        "worker_id": worker_id,
        "lease_id": lease.lease_id if lease is not None else "",
        "account_user_id": command.account_user_id,
        "task_id": command.task_id,
        "checks": checks,
        "writes_remote": False,
        "submits_remote": False,
        "starts_run": False,
        "message": "真实执行前置闸门通过；仍未启动真实执行。" if can_execute else "真实执行前置闸门未通过，禁止执行。",
    }


def claim_next_worker_command(db: Session, worker_id: str) -> Optional[WorkerCommand]:
    command = db.scalar(
        select(WorkerCommand)
        .where(
            WorkerCommand.worker_id == worker_id,
            WorkerCommand.status == WorkerCommandStatus.QUEUED,
        )
        .order_by(WorkerCommand.created_at.asc(), WorkerCommand.id.asc())
    )
    if command is None:
        return None
    now = utc_now()
    command.status = WorkerCommandStatus.RUNNING
    command.claimed_at = now
    command.last_renewed_at = now
    add_worker_event(
        db,
        worker_id,
        WorkerEventType.COMMAND,
        account_user_id=command.account_user_id,
        task_id=command.task_id,
        message=f"领取命令 {command.command_id}",
    )
    db.flush()
    return command


def renew_worker_command(db: Session, command_id: str) -> WorkerCommand:
    command = _get_command(db, command_id)
    if command.status in {WorkerCommandStatus.CLAIMED, WorkerCommandStatus.RUNNING}:
        command.status = WorkerCommandStatus.RUNNING
        command.last_renewed_at = utc_now()
        db.flush()
    return command


def timeout_and_requeue_worker_commands(db: Session, *, now=None) -> list[WorkerCommand]:
    now = now or utc_now()
    active_commands = list(
        db.scalars(
            select(WorkerCommand).where(
                WorkerCommand.status.in_([WorkerCommandStatus.CLAIMED, WorkerCommandStatus.RUNNING])
            )
        )
    )
    requeued: list[WorkerCommand] = []
    for command in active_commands:
        last_seen = command.last_renewed_at or command.claimed_at or command.created_at
        if last_seen and (_as_utc(now) - _as_utc(last_seen)).total_seconds() < command.timeout_seconds:
            continue
        command.status = WorkerCommandStatus.TIMED_OUT
        command.timed_out_at = now
        command.audit_note = "180 秒无续约，自动重派"
        retry = create_worker_command(
            db,
            worker_id=command.worker_id,
            command_type=command.command_type,
            account_user_id=command.account_user_id,
            task_id=command.task_id,
            payload=_loads(command.payload_json),
            retry_of_command_id=command.command_id,
        )
        requeued.append(retry)
    db.flush()
    return requeued


def handle_worker_command_result(db: Session, command_id: str, *, success: bool, result: Optional[dict] = None) -> dict:
    command = _get_command(db, command_id)
    if command.status == WorkerCommandStatus.TIMED_OUT:
        add_worker_event(
            db,
            command.worker_id,
            WorkerEventType.COMMAND,
            account_user_id=command.account_user_id,
            task_id=command.task_id,
            severity="warning",
            message=f"迟到{'成功' if success else '失败'}回执只审计不采纳 {command.command_id}",
        )
        db.flush()
        return {"disposition": "late_success_audit_only" if success else "late_failure_audit_only"}
    command.status = WorkerCommandStatus.SUCCEEDED if success else WorkerCommandStatus.FAILED
    command.result_json = _json(result or {})
    command.finished_at = utc_now()
    _apply_account_task_lease_result(db, command, success)
    db.flush()
    return {"disposition": "accepted"}


def disable_worker_and_reclaim(db: Session, worker_id: str, *, reason: str = "") -> dict:
    worker = ensure_worker(db, worker_id)
    worker.status = WorkerStatus.DISABLED
    worker.disabled_reason = reason
    worker.effective_http_account_slots = 0
    active_leases = list(
        db.scalars(
            select(WorkerAccountTaskLease).where(
                WorkerAccountTaskLease.worker_id == worker_id,
                WorkerAccountTaskLease.status == WorkerLeaseStatus.ACTIVE,
            )
        )
    )
    now = utc_now()
    for lease in active_leases:
        lease.status = WorkerLeaseStatus.RECLAIMED
        lease.stop_reason = reason or "worker_disabled"
        lease.reclaimed_at = now
    active_commands = list(
        db.scalars(
            select(WorkerCommand).where(
                WorkerCommand.worker_id == worker_id,
                WorkerCommand.status.in_([WorkerCommandStatus.QUEUED, WorkerCommandStatus.CLAIMED, WorkerCommandStatus.RUNNING]),
            )
        )
    )
    new_commands: list[WorkerCommand] = []
    for command in active_commands:
        if command.status == WorkerCommandStatus.QUEUED:
            command.status = WorkerCommandStatus.CANCELLED
        else:
            command.status = WorkerCommandStatus.TIMED_OUT
            command.timed_out_at = now
            new_commands.append(
                create_worker_command(
                    db,
                    worker_id="",
                    command_type=command.command_type,
                    account_user_id=command.account_user_id,
                    task_id=command.task_id,
                    payload=_loads(command.payload_json),
                    retry_of_command_id=command.command_id,
                )
            )
    add_worker_event(
        db,
        worker_id,
        WorkerEventType.LEASE,
        severity="warning",
        message=f"Worker 禁用立即回收：{reason or '未填写原因'}",
    )
    db.flush()
    return {
        "worker_status": worker.status.value,
        "reclaimed_task_leases": len(active_leases),
        "requeued_commands": len(new_commands),
        "new_commands": new_commands,
    }


def recover_cooldown_account_task_leases(db: Session, *, now=None) -> list[WorkerAccountTaskLease]:
    now = now or utc_now()
    suspended_leases = list(
        db.scalars(
            select(WorkerAccountTaskLease).where(
                WorkerAccountTaskLease.status == WorkerLeaseStatus.SUSPENDED,
                WorkerAccountTaskLease.recovery_type == "auto_recoverable",
                WorkerAccountTaskLease.cooldown_until.is_not(None),
            )
        )
    )
    recovered: list[WorkerAccountTaskLease] = []
    for lease in suspended_leases:
        if lease.cooldown_until and _as_utc(lease.cooldown_until) > _as_utc(now):
            continue
        _recover_account_task_lease(
            db,
            lease,
            now=now,
            stop_reason="15 分钟冷却到期，自动恢复调度",
            event_message="账号任务组冷却到期，自动恢复调度",
        )
        recovered.append(lease)
    db.flush()
    return recovered


def manually_recover_account_task_lease(db: Session, lease_id: str, *, reason: str = "") -> WorkerAccountTaskLease:
    lease = db.scalar(select(WorkerAccountTaskLease).where(WorkerAccountTaskLease.lease_id == lease_id))
    if lease is None:
        raise ValueError(f"账号任务组租约不存在：{lease_id}")
    _recover_account_task_lease(
        db,
        lease,
        now=utc_now(),
        stop_reason=f"人工恢复：{reason or '未填写原因'}",
        event_message=f"账号任务组人工恢复：{reason or '未填写原因'}",
    )
    db.flush()
    return lease


def _get_command(db: Session, command_id: str) -> WorkerCommand:
    command = db.scalar(select(WorkerCommand).where(WorkerCommand.command_id == command_id))
    if command is None:
        raise ValueError(f"命令不存在：{command_id}")
    return command


def _apply_account_task_lease_result(db: Session, command: WorkerCommand, success: bool) -> None:
    if not command.account_user_id or not command.task_id:
        return
    lease = db.scalar(
        select(WorkerAccountTaskLease).where(
            WorkerAccountTaskLease.account_user_id == command.account_user_id,
            WorkerAccountTaskLease.task_id == command.task_id,
            WorkerAccountTaskLease.status == WorkerLeaseStatus.ACTIVE,
        )
    )
    if lease is None:
        return
    if success:
        lease.failure_count = 0
        lease.last_error_code = ""
        lease.recovery_type = ""
        lease.cooldown_until = None
        lease.stop_reason = ""
        return
    error_code = _extract_error_code(command.result_json)
    lease.last_error_code = error_code
    lease.failure_count += 1
    if lease.failure_count >= LEASE_FAILURE_THRESHOLD:
        now = utc_now()
        lease.status = WorkerLeaseStatus.SUSPENDED
        lease.stop_reason = "连续失败 3 次，账号任务组停派"
        lease.reclaimed_at = now
        lease.recovery_type = _classify_recovery_type(error_code)
        lease.cooldown_until = now + timedelta(seconds=LEASE_AUTO_RECOVERY_COOLDOWN_SECONDS) if lease.recovery_type == "auto_recoverable" else None
        add_worker_event(
            db,
            lease.worker_id,
            WorkerEventType.LEASE,
            account_user_id=lease.account_user_id,
            task_id=lease.task_id,
            severity="warning",
            message=f"{lease.stop_reason}；{lease.last_error_code or 'UNKNOWN_ERROR'}；{lease.recovery_type}",
        )


def _recover_account_task_lease(
    db: Session,
    lease: WorkerAccountTaskLease,
    *,
    now,
    stop_reason: str,
    event_message: str,
) -> None:
    lease.status = WorkerLeaseStatus.ACTIVE
    lease.failure_count = 0
    lease.recovery_type = ""
    lease.cooldown_until = None
    lease.recovery_attempt_count += 1
    lease.recovered_at = now
    lease.reclaimed_at = None
    lease.stop_reason = stop_reason
    lease.last_heartbeat_at = now
    add_worker_event(
        db,
        lease.worker_id,
        WorkerEventType.LEASE,
        account_user_id=lease.account_user_id,
        task_id=lease.task_id,
        severity="info",
        message=event_message,
    )


def _extract_error_code(result_json: str) -> str:
    result = _loads(result_json)
    error_code = str(result.get("error_code") or "").strip()
    return error_code or "UNKNOWN_ERROR"


def _classify_recovery_type(error_code: str) -> str:
    if error_code in AUTO_RECOVERABLE_ERROR_CODES:
        return "auto_recoverable"
    if error_code in MANUAL_RECOVERY_ERROR_CODES:
        return "manual_recovery_required"
    return "manual_recovery_required"


def _available_workers(db: Session) -> list[Worker]:
    workers = list(
        db.scalars(
            select(Worker)
            .where(
                Worker.status.in_(SCHEDULABLE_WORKER_STATUSES),
                Worker.effective_http_account_slots > 0,
            )
            .order_by(Worker.is_platform_worker.desc(), Worker.id.asc())
        )
    )
    return workers


def _select_worker_for_command(db: Session, workers: list[Worker]) -> Optional[Worker]:
    for worker in workers:
        active_count = len(
            list(
                db.scalars(
                    select(WorkerCommand).where(
                        WorkerCommand.worker_id == worker.worker_id,
                        WorkerCommand.status.in_([WorkerCommandStatus.QUEUED, WorkerCommandStatus.CLAIMED, WorkerCommandStatus.RUNNING]),
                    )
                )
            )
        )
        if active_count < worker.effective_http_account_slots:
            return worker
    return None


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
