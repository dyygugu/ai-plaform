import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.worker import (
    PlatformWorkerEnsureRequest,
    WORKER_EVENT_ERROR_CODES,
    WORKER_EVENT_SEVERITY_LEVELS,
    WORKER_EVENT_STEPS_BY_STAGE,
    WorkerBindRequest,
    WorkerApproveRequest,
    WorkerAccountTaskLeaseCreateRequest,
    WorkerAccountTaskLeaseManualRecoverRequest,
    WorkerAccountTaskLeaseRead,
    WorkerAccountTaskLeaseRecoveryScanResponse,
    WorkerCommandCreateRequest,
    WorkerCommandAssignScanResponse,
    WorkerCommandExecutionGateRequest,
    WorkerCommandExecutionGateResponse,
    WorkerCommandRead,
    WorkerCommandResultRequest,
    WorkerCommandResultResponse,
    WorkerCommandTimeoutScanResponse,
    WorkerDetailResponse,
    WorkerDisableReclaimRequest,
    WorkerDisableReclaimResponse,
    WorkerEventContractResponse,
    WorkerEventRead,
    WorkerEventReportRequest,
    WorkerEventStageContract,
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
    WorkerLogSummary,
    WorkerRead,
    WorkerTaskClaimRequest,
    WorkerVersionUpdateRequest,
)
from app.services.audit_service import write_audit
from app.services.worker_dispatch_service import (
    assign_unbound_worker_commands,
    check_worker_command_execution_gate,
    claim_next_worker_command,
    create_worker_command,
    disable_worker_and_reclaim,
    ensure_platform_worker,
    handle_worker_command_result,
    list_account_task_leases,
    manually_recover_account_task_lease,
    recover_cooldown_account_task_leases,
    renew_worker_command,
    start_account_task_lease,
    timeout_and_requeue_worker_commands,
)
from app.services.worker_service import (
    approve_worker,
    bind_worker_account,
    claim_worker_task,
    ensure_worker,
    list_workers,
    report_worker_event,
    register_worker,
    summarize_worker_logs,
    update_worker_version,
    upsert_worker_heartbeat,
)

router = APIRouter(prefix="/workers", tags=["workers"])


def _summary_read(summary: dict) -> WorkerLogSummary:
    return WorkerLogSummary(
        worker_id=summary["worker_id"],
        total_events=summary["total_events"],
        error_events=summary["error_events"],
        warning_events=summary["warning_events"],
        latest_message=summary["latest_message"],
        events=[WorkerEventRead.model_validate(event) for event in summary["events"]],
    )


def _loads_dict(raw: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _command_read(command) -> WorkerCommandRead:
    return WorkerCommandRead(
        id=command.id,
        command_id=command.command_id,
        retry_of_command_id=command.retry_of_command_id,
        worker_id=command.worker_id,
        command_type=command.command_type,
        status=command.status.value if hasattr(command.status, "value") else str(command.status),
        account_user_id=command.account_user_id,
        task_id=command.task_id,
        payload=_loads_dict(command.payload_json),
        result=_loads_dict(command.result_json),
        last_renewed_at=command.last_renewed_at,
        claimed_at=command.claimed_at,
        finished_at=command.finished_at,
        timed_out_at=command.timed_out_at,
        timeout_seconds=command.timeout_seconds,
        audit_note=command.audit_note,
        created_at=command.created_at,
    )


@router.get("", response_model=list[WorkerRead])
def read_workers(db: Session = Depends(get_db)) -> list[WorkerRead]:
    return [WorkerRead.model_validate(worker) for worker in list_workers(db)]


@router.get("/event-contract", response_model=WorkerEventContractResponse)
def read_worker_event_contract() -> WorkerEventContractResponse:
    return WorkerEventContractResponse(
        stages=[WorkerEventStageContract(stage=stage, steps=steps) for stage, steps in WORKER_EVENT_STEPS_BY_STAGE.items()],
        error_codes=WORKER_EVENT_ERROR_CODES,
        severity_levels=WORKER_EVENT_SEVERITY_LEVELS,
        message="做题链路 Worker 日志必须使用固定 stage/step/error_code，避免故障定位字符串不一致。",
    )


@router.post("/platform-worker/ensure", response_model=WorkerRead)
def ensure_platform_worker_route(payload: PlatformWorkerEnsureRequest, db: Session = Depends(get_db)) -> WorkerRead:
    worker = ensure_platform_worker(db, inherited_http_account_slots=payload.inherited_http_account_slots)
    write_audit(
        db,
        event_type="worker_platform_ensure",
        message=f"platform-worker capacity inherited as {payload.inherited_http_account_slots}",
        target_type="worker",
        target_id=worker.worker_id,
    )
    db.commit()
    db.refresh(worker)
    return WorkerRead.model_validate(worker)


@router.post("/register", response_model=WorkerRead)
def register_worker_route(payload: WorkerRegisterRequest, db: Session = Depends(get_db)) -> WorkerRead:
    worker = register_worker(db, payload)
    write_audit(
        db,
        event_type="worker_register_pending",
        message=f"Worker {worker.worker_id} registered and waits for approval",
        target_type="worker",
        target_id=worker.worker_id,
    )
    db.commit()
    db.refresh(worker)
    return WorkerRead.model_validate(worker)


@router.post("/leases/account-task", response_model=WorkerAccountTaskLeaseRead)
def create_account_task_lease(payload: WorkerAccountTaskLeaseCreateRequest, db: Session = Depends(get_db)) -> WorkerAccountTaskLeaseRead:
    lease = start_account_task_lease(
        db,
        worker_id=payload.worker_id,
        account_user_id=payload.account_user_id,
        task_id=payload.task_id,
    )
    write_audit(
        db,
        event_type="worker_account_task_lease_create",
        message=f"Lease {payload.account_user_id}/{payload.task_id} to {payload.worker_id}",
        target_type="worker",
        target_id=payload.worker_id,
    )
    db.commit()
    db.refresh(lease)
    return WorkerAccountTaskLeaseRead.model_validate(lease)


@router.get("/leases/account-task", response_model=list[WorkerAccountTaskLeaseRead])
def read_account_task_leases(db: Session = Depends(get_db)) -> list[WorkerAccountTaskLeaseRead]:
    return [WorkerAccountTaskLeaseRead.model_validate(lease) for lease in list_account_task_leases(db)]


@router.post("/leases/recovery-scan", response_model=WorkerAccountTaskLeaseRecoveryScanResponse)
def scan_account_task_lease_recovery(db: Session = Depends(get_db)) -> WorkerAccountTaskLeaseRecoveryScanResponse:
    leases = recover_cooldown_account_task_leases(db)
    for lease in leases:
        write_audit(
            db,
            event_type="worker_account_task_lease_auto_recover",
            message=f"Auto recovered lease {lease.lease_id}",
            target_type="worker_account_task_lease",
            target_id=lease.lease_id,
        )
    db.commit()
    for lease in leases:
        db.refresh(lease)
    return WorkerAccountTaskLeaseRecoveryScanResponse(
        recovered_leases=len(leases),
        leases=[WorkerAccountTaskLeaseRead.model_validate(lease) for lease in leases],
    )


@router.post("/leases/{lease_id}/recover", response_model=WorkerAccountTaskLeaseRead)
def manually_recover_account_task_lease_route(
    lease_id: str,
    payload: WorkerAccountTaskLeaseManualRecoverRequest,
    db: Session = Depends(get_db),
) -> WorkerAccountTaskLeaseRead:
    try:
        lease = manually_recover_account_task_lease(db, lease_id, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit(
        db,
        event_type="worker_account_task_lease_manual_recover",
        message=f"Manually recovered lease {lease.lease_id}: {payload.reason or 'no reason'}",
        target_type="worker_account_task_lease",
        target_id=lease.lease_id,
    )
    db.commit()
    db.refresh(lease)
    return WorkerAccountTaskLeaseRead.model_validate(lease)


@router.post("/commands", response_model=WorkerCommandRead)
def create_dispatch_command(payload: WorkerCommandCreateRequest, db: Session = Depends(get_db)) -> WorkerCommandRead:
    command = create_worker_command(
        db,
        worker_id=payload.worker_id,
        command_type=payload.command_type,
        account_user_id=payload.account_user_id,
        task_id=payload.task_id,
        payload=payload.payload,
    )
    write_audit(
        db,
        event_type="worker_command_create",
        message=f"Created command {command.command_id} for {payload.worker_id or 'scheduler'}",
        target_type="worker_command",
        target_id=command.command_id,
    )
    db.commit()
    db.refresh(command)
    return _command_read(command)


@router.post("/commands/timeout-scan", response_model=WorkerCommandTimeoutScanResponse)
def scan_worker_command_timeouts(db: Session = Depends(get_db)) -> WorkerCommandTimeoutScanResponse:
    new_commands = timeout_and_requeue_worker_commands(db)
    for command in new_commands:
        write_audit(
            db,
            event_type="worker_command_timeout_requeue",
            message=f"Requeued command {command.command_id} from {command.retry_of_command_id}",
            target_type="worker_command",
            target_id=command.command_id,
        )
    db.commit()
    for command in new_commands:
        db.refresh(command)
    return WorkerCommandTimeoutScanResponse(
        requeued_commands=len(new_commands),
        new_commands=[_command_read(command) for command in new_commands],
    )


@router.post("/commands/assign-scan", response_model=WorkerCommandAssignScanResponse)
def scan_worker_command_assignments(db: Session = Depends(get_db)) -> WorkerCommandAssignScanResponse:
    commands = assign_unbound_worker_commands(db)
    for command in commands:
        write_audit(
            db,
            event_type="worker_command_assign",
            message=f"Assigned command {command.command_id} to {command.worker_id}",
            target_type="worker_command",
            target_id=command.command_id,
        )
    db.commit()
    for command in commands:
        db.refresh(command)
    return WorkerCommandAssignScanResponse(
        assigned_commands=len(commands),
        commands=[_command_read(command) for command in commands],
    )


@router.post("/commands/{command_id}/renew", response_model=WorkerCommandRead)
def renew_dispatch_command(command_id: str, db: Session = Depends(get_db)) -> WorkerCommandRead:
    try:
        command = renew_worker_command(db, command_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    db.refresh(command)
    return _command_read(command)


@router.post("/commands/{command_id}/result", response_model=WorkerCommandResultResponse)
def report_dispatch_command_result(command_id: str, payload: WorkerCommandResultRequest, db: Session = Depends(get_db)) -> WorkerCommandResultResponse:
    try:
        result = handle_worker_command_result(db, command_id, success=payload.success, result=payload.result)
        command = renew_worker_command(db, command_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit(
        db,
        event_type="worker_command_result",
        message=f"Command {command_id} result disposition {result['disposition']}",
        target_type="worker_command",
        target_id=command_id,
    )
    db.commit()
    db.refresh(command)
    return WorkerCommandResultResponse(disposition=result["disposition"], command=_command_read(command))


@router.post("/commands/{command_id}/execution-gate", response_model=WorkerCommandExecutionGateResponse)
def check_dispatch_command_execution_gate(
    command_id: str,
    payload: WorkerCommandExecutionGateRequest,
    db: Session = Depends(get_db),
) -> WorkerCommandExecutionGateResponse:
    try:
        gate = check_worker_command_execution_gate(db, command_id, worker_id=payload.worker_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit(
        db,
        event_type="worker_command_execution_gate",
        message=f"Command {command_id} execution gate {gate['status']}",
        target_type="worker_command",
        target_id=command_id,
    )
    db.commit()
    return WorkerCommandExecutionGateResponse(**gate)


@router.post("/heartbeat", response_model=WorkerRead)
def post_worker_heartbeat(payload: WorkerHeartbeatRequest, db: Session = Depends(get_db)) -> WorkerRead:
    worker = upsert_worker_heartbeat(db, payload)
    write_audit(db, event_type="worker_heartbeat", message=f"Worker {worker.worker_id} heartbeat", target_type="worker", target_id=worker.worker_id)
    db.commit()
    db.refresh(worker)
    return WorkerRead.model_validate(worker)


@router.post("/events", response_model=WorkerEventRead)
def post_worker_event(payload: WorkerEventReportRequest, db: Session = Depends(get_db)) -> WorkerEventRead:
    worker, event = report_worker_event(db, payload)
    write_audit(db, event_type="worker_event_report", message=f"Worker {worker.worker_id} reported {event.event_type.value}", target_type="worker", target_id=worker.worker_id)
    db.commit()
    db.refresh(event)
    return WorkerEventRead.model_validate(event)


@router.post("/{worker_id}/commands/claim", response_model=WorkerCommandRead)
def claim_dispatch_command(worker_id: str, db: Session = Depends(get_db)) -> WorkerCommandRead:
    worker = ensure_worker(db, worker_id)
    if worker.status.value not in {"online", "degraded"}:
        raise HTTPException(status_code=403, detail="Worker 未通过批准或不可调度，不能领取命令")
    command = claim_next_worker_command(db, worker_id)
    if command is None:
        raise HTTPException(status_code=404, detail="没有可领取命令")
    write_audit(
        db,
        event_type="worker_command_claim",
        message=f"Worker {worker_id} claimed command {command.command_id}",
        target_type="worker_command",
        target_id=command.command_id,
    )
    db.commit()
    db.refresh(command)
    return _command_read(command)


@router.post("/{worker_id}/approve", response_model=WorkerRead)
def approve_worker_route(worker_id: str, payload: WorkerApproveRequest, db: Session = Depends(get_db)) -> WorkerRead:
    worker = approve_worker(db, worker_id, payload)
    write_audit(
        db,
        event_type="worker_approve",
        message=f"Worker {worker_id} approved with {worker.effective_http_account_slots} slots",
        target_type="worker",
        target_id=worker_id,
    )
    db.commit()
    db.refresh(worker)
    return WorkerRead.model_validate(worker)


@router.post("/{worker_id}/disable-reclaim", response_model=WorkerDisableReclaimResponse)
def disable_worker_reclaim(worker_id: str, payload: WorkerDisableReclaimRequest, db: Session = Depends(get_db)) -> WorkerDisableReclaimResponse:
    summary = disable_worker_and_reclaim(db, worker_id, reason=payload.reason)
    write_audit(
        db,
        event_type="worker_disable_reclaim",
        message=f"Disabled {worker_id}; reclaimed {summary['reclaimed_task_leases']} leases",
        target_type="worker",
        target_id=worker_id,
    )
    db.commit()
    for command in summary["new_commands"]:
        db.refresh(command)
    return WorkerDisableReclaimResponse(
        worker_status=summary["worker_status"],
        reclaimed_task_leases=summary["reclaimed_task_leases"],
        requeued_commands=summary["requeued_commands"],
        new_commands=[_command_read(command) for command in summary["new_commands"]],
    )


@router.get("/{worker_id}", response_model=WorkerDetailResponse)
def read_worker_detail(worker_id: str, db: Session = Depends(get_db)) -> WorkerDetailResponse:
    worker = ensure_worker(db, worker_id)
    summary = summarize_worker_logs(db, worker_id)
    db.commit()
    return WorkerDetailResponse(worker=WorkerRead.model_validate(worker), log_summary=_summary_read(summary))


@router.get("/{worker_id}/logs", response_model=WorkerLogSummary)
def read_worker_logs(worker_id: str, db: Session = Depends(get_db)) -> WorkerLogSummary:
    return _summary_read(summarize_worker_logs(db, worker_id))


@router.post("/{worker_id}/bind-account", response_model=WorkerDetailResponse)
def bind_account(worker_id: str, payload: WorkerBindRequest, db: Session = Depends(get_db)) -> WorkerDetailResponse:
    worker, _event = bind_worker_account(db, worker_id, payload.account_user_id, payload.message)
    write_audit(db, event_type="worker_bind_account", message=f"Worker {worker_id} bound account {payload.account_user_id}", target_type="worker", target_id=worker_id)
    db.commit()
    db.refresh(worker)
    return WorkerDetailResponse(worker=WorkerRead.model_validate(worker), log_summary=_summary_read(summarize_worker_logs(db, worker_id)))


@router.post("/{worker_id}/version", response_model=WorkerDetailResponse)
def update_version(worker_id: str, payload: WorkerVersionUpdateRequest, db: Session = Depends(get_db)) -> WorkerDetailResponse:
    worker, _event = update_worker_version(db, worker_id, payload.target_version, payload.message)
    write_audit(db, event_type="worker_version_update", message=f"Worker {worker_id} updated to {payload.target_version}", target_type="worker", target_id=worker_id)
    db.commit()
    db.refresh(worker)
    return WorkerDetailResponse(worker=WorkerRead.model_validate(worker), log_summary=_summary_read(summarize_worker_logs(db, worker_id)))


@router.post("/{worker_id}/claim-task", response_model=WorkerDetailResponse)
def claim_task(worker_id: str, payload: WorkerTaskClaimRequest, db: Session = Depends(get_db)) -> WorkerDetailResponse:
    worker, _event = claim_worker_task(db, worker_id, payload.task_id, payload.account_user_id, payload.message)
    write_audit(db, event_type="worker_task_claim", message=f"Worker {worker_id} claimed task {payload.task_id}", target_type="worker", target_id=worker_id)
    db.commit()
    db.refresh(worker)
    return WorkerDetailResponse(worker=WorkerRead.model_validate(worker), log_summary=_summary_read(summarize_worker_logs(db, worker_id)))
