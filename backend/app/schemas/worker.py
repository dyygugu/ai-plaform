from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


WORKER_EVENT_STEPS_BY_STAGE: dict[str, list[str]] = {
    "task_refresh": ["start", "fetch_task_page", "parse_task_catalog", "sync_pending", "finish"],
    "ai_draft": ["prepare_context", "call_provider", "parse_answer", "save_draft"],
    "manual_confirmation": ["queue_confirmation", "approve", "reject", "expire"],
    "submit_readback": ["submit_answer", "readback_result", "confirm_delivered", "rollback"],
    "3d_http_answer": ["prepare_context", "call_provider", "parse_answer", "temp_save", "submit_answer", "readback_result", "ledger_update"],
    "worker_runtime": ["heartbeat", "bind_account", "claim_task", "version_update", "log_summary"],
}

WORKER_EVENT_ERROR_CODES: list[str] = [
    "TASK_PAGE_TIMEOUT",
    "TASK_PAGE_AUTH_EXPIRED",
    "TASK_PARSE_FAILED",
    "AI_PROVIDER_502",
    "AI_PROVIDER_TIMEOUT",
    "AI_RESPONSE_INVALID",
    "DUPLICATE_SUBMITTED",
    "CONFIRMATION_PENDING",
    "CONFIRMATION_REJECTED",
    "LEDGER_IN_PROGRESS_UNKNOWN",
    "LOW_CONFIDENCE",
    "MISSING_REQUIRED_IMAGE",
    "NO_CURRENT_ITEM",
    "SUBMIT_FAILED",
    "READBACK_MISMATCH",
    "WORKER_OFFLINE",
    "WORKER_EXCEPTION",
    "UNKNOWN_ERROR",
]

WORKER_EVENT_SEVERITY_LEVELS: list[str] = ["info", "warning", "error", "critical"]


class WorkerHeartbeatRequest(BaseModel):
    worker_id: str
    display_name: str = ""
    version: str = "unknown"
    current_account_user_id: str = ""
    current_task_id: str = ""
    last_error: Optional[str] = None


class WorkerRegisterRequest(BaseModel):
    worker_id: str
    display_name: str = ""
    version: str = "unknown"
    estimated_http_account_slots: int = 0


class WorkerApproveRequest(BaseModel):
    configured_http_account_slots: int = 0


class WorkerBindRequest(BaseModel):
    account_user_id: str
    message: str = ""


class WorkerVersionUpdateRequest(BaseModel):
    target_version: str
    message: str = ""


class WorkerTaskClaimRequest(BaseModel):
    task_id: str
    account_user_id: str = ""
    message: str = ""


class WorkerEventReportRequest(BaseModel):
    worker_id: str
    event_type: str = "event_report"
    account_user_id: str = ""
    task_id: str = ""
    target_version: str = ""
    severity: str = "info"
    message: str = ""
    stage: str = ""
    step: str = ""
    error_code: str = ""
    error_detail: str = ""
    retryable: Optional[bool] = None
    duration_ms: Optional[int] = None

    @model_validator(mode="after")
    def validate_event_contract(self) -> "WorkerEventReportRequest":
        if self.severity not in WORKER_EVENT_SEVERITY_LEVELS:
            raise ValueError(f"severity 必须是 {', '.join(WORKER_EVENT_SEVERITY_LEVELS)}")
        if self.stage and self.stage not in WORKER_EVENT_STEPS_BY_STAGE:
            raise ValueError(f"stage 必须是 {', '.join(WORKER_EVENT_STEPS_BY_STAGE.keys())}")
        if self.step:
            if not self.stage:
                raise ValueError("填写 step 时必须同时填写 stage")
            allowed_steps = WORKER_EVENT_STEPS_BY_STAGE[self.stage]
            if self.step not in allowed_steps:
                raise ValueError(f"stage={self.stage} 时 step 必须是 {', '.join(allowed_steps)}")
        if self.error_code and self.error_code not in WORKER_EVENT_ERROR_CODES:
            raise ValueError(f"error_code 必须是 {', '.join(WORKER_EVENT_ERROR_CODES)}")
        return self


class WorkerRead(BaseModel):
    id: int
    worker_id: str
    display_name: str
    status: str
    version: str
    current_account_user_id: str
    current_task_id: str
    last_error: Optional[str]
    last_heartbeat_at: Optional[datetime]
    is_platform_worker: bool = False
    estimated_http_account_slots: int = 0
    configured_http_account_slots: int = 0
    effective_http_account_slots: int = 0
    health_status: str = "unknown"
    health_checked_at: Optional[datetime] = None
    health_fail_reasons: str = ""
    disabled_reason: str = ""

    model_config = {"from_attributes": True}


class WorkerEventRead(BaseModel):
    id: int
    worker_id: str
    event_type: str
    account_user_id: str
    task_id: str
    target_version: str
    severity: str
    message: str
    trace_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkerLogSummary(BaseModel):
    worker_id: str
    total_events: int
    error_events: int
    warning_events: int
    latest_message: str
    events: list[WorkerEventRead]


class WorkerDetailResponse(BaseModel):
    worker: WorkerRead
    log_summary: WorkerLogSummary


class WorkerEventStageContract(BaseModel):
    stage: str
    steps: list[str]


class WorkerEventContractResponse(BaseModel):
    stages: list[WorkerEventStageContract]
    error_codes: list[str]
    severity_levels: list[str]
    message: str


class PlatformWorkerEnsureRequest(BaseModel):
    inherited_http_account_slots: int


class WorkerAccountTaskLeaseCreateRequest(BaseModel):
    worker_id: str
    account_user_id: str
    task_id: str


class WorkerAccountTaskLeaseRead(BaseModel):
    id: int
    lease_id: str
    worker_id: str
    account_user_id: str
    task_id: str
    status: str
    failure_count: int
    last_error_code: str = ""
    recovery_type: str = ""
    cooldown_until: Optional[datetime] = None
    recovery_attempt_count: int = 0
    recovered_at: Optional[datetime] = None
    stop_reason: str
    last_heartbeat_at: Optional[datetime]
    reclaimed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkerAccountTaskLeaseManualRecoverRequest(BaseModel):
    reason: str = ""


class WorkerAccountTaskLeaseRecoveryScanResponse(BaseModel):
    recovered_leases: int
    leases: list[WorkerAccountTaskLeaseRead]


class WorkerCommandCreateRequest(BaseModel):
    worker_id: str = ""
    command_type: str
    account_user_id: str = ""
    task_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkerCommandRead(BaseModel):
    id: int
    command_id: str
    retry_of_command_id: str
    worker_id: str
    command_type: str
    status: str
    account_user_id: str
    task_id: str
    payload: dict[str, Any]
    result: dict[str, Any]
    last_renewed_at: Optional[datetime]
    claimed_at: Optional[datetime]
    finished_at: Optional[datetime]
    timed_out_at: Optional[datetime]
    timeout_seconds: int
    audit_note: str
    created_at: datetime


class WorkerCommandResultRequest(BaseModel):
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)


class WorkerCommandResultResponse(BaseModel):
    disposition: str
    command: WorkerCommandRead


class WorkerCommandExecutionGateRequest(BaseModel):
    worker_id: str


class WorkerCommandExecutionGateCheck(BaseModel):
    key: str
    title: str
    status: str
    detail: str = ""


class WorkerCommandExecutionGateResponse(BaseModel):
    status: str
    can_execute: bool
    command_id: str
    worker_id: str
    lease_id: str
    account_user_id: str
    task_id: str
    checks: list[WorkerCommandExecutionGateCheck]
    writes_remote: bool = False
    submits_remote: bool = False
    starts_run: bool = False
    message: str


class WorkerCommandTimeoutScanResponse(BaseModel):
    requeued_commands: int
    new_commands: list[WorkerCommandRead]


class WorkerCommandAssignScanResponse(BaseModel):
    assigned_commands: int
    commands: list[WorkerCommandRead]


class WorkerDisableReclaimRequest(BaseModel):
    reason: str = ""


class WorkerDisableReclaimResponse(BaseModel):
    worker_status: str
    reclaimed_task_leases: int
    requeued_commands: int
    new_commands: list[WorkerCommandRead]
