from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Bon8ProductionStartRequest(BaseModel):
    account_user_ids: list[str] = Field(default_factory=list)
    task_id: str = "7637771731901861641"
    node_id: str = "1"
    max_items_per_account: int = Field(default=20, ge=1, le=20)
    manual_first_count: int = Field(default=1, ge=1, le=20)
    write_audit: bool = True


class Bon8ProductionItemResult(BaseModel):
    account_user_id: str
    account_name: str = ""
    task_id: str
    node_id: str
    item_id: str = ""
    status: str
    mode: str
    confirmation_id: Optional[int] = None
    writes_remote: bool = False
    base_resp_status_code: Optional[int] = None
    elapsed_ms: int = 0
    message: str


class Bon8ProductionStatusResponse(BaseModel):
    generated_at: datetime
    manual_first_count: int
    manual_confirmed_count: int
    remaining_manual_confirmations: int
    auto_submit_allowed: bool
    next_mode: str
    guardrails: list[str]
    message: str


class Bon8RunWorkerStartRequest(BaseModel):
    interval_seconds: int = Field(default=5, ge=1, le=300)


class Bon8RunWorkerStatusResponse(BaseModel):
    run_id: str
    active: bool = False
    running: bool = False
    cycle_count: int = 0
    last_ok: Optional[bool] = None
    last_error: Optional[str] = None
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    interval_seconds: int = 0
    next_run_at: Optional[str] = None


class Bon8ProductionAccountRunState(BaseModel):
    account_user_id: str
    account_name: str = ""
    status: str
    current_item_id: str = ""
    current_stage: str = ""
    success_count: int = 0
    failed_count: int = 0
    no_item_count: int = 0
    last_claim_at: Optional[datetime] = None
    last_submit_at: Optional[datetime] = None
    last_timer_event_id: str = ""
    backoff_until: Optional[datetime] = None
    isolated_reason: str = ""
    last_error: str = ""


class Bon8ProductionItemAttemptState(BaseModel):
    attempt_id: str
    run_id: str
    account_user_id: str
    task_id: str
    item_id: str
    stage: str
    is_first_review_item: bool = False
    ai_result_summary: dict = Field(default_factory=dict)
    payload_check_status: str = ""
    temp_save_status: str = ""
    verify_submit_status: str = ""
    submit_status: str = ""
    readback_status: str = ""
    timer_status: str = ""
    evidence_path: str = ""
    started_at: datetime
    updated_at: datetime
    finished_at: Optional[datetime] = None
    error_code: str = ""
    error_message: str = ""


class Bon8FirstConfirmationSheet(BaseModel):
    confirmation_id: str
    run_id: str
    attempt_id: str
    account_user_id: str
    item_id: str
    status: str
    review_payload_path: str = ""
    ai_scores: dict = Field(default_factory=dict)
    model_order: list[str] = Field(default_factory=list)
    issue_options: dict = Field(default_factory=dict)
    reasons: dict = Field(default_factory=dict)
    payload_check: dict = Field(default_factory=dict)
    temp_save_result: dict = Field(default_factory=dict)
    verify_submit_result: dict = Field(default_factory=dict)
    submit_result: dict = Field(default_factory=dict)
    readback_result: dict = Field(default_factory=dict)
    timings: dict = Field(default_factory=dict)
    evidence_path: str = ""
    approved_by: str = ""
    approved_at: Optional[datetime] = None
    rejected_reason: str = ""


class Bon8ProductionRunResponse(BaseModel):
    generated_at: datetime
    run_id: str = ""
    status: str = ""
    gate_status: str = ""
    seed_account_id: str = ""
    confirmation_id: str = ""
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    started_by: str = "operator"
    stop_requested: bool = False
    mode: str
    task_id: str
    node_id: str
    selected_account_count: int
    manual_first_count: int
    manual_confirmed_count: int
    remaining_manual_confirmations: int
    auto_submit_allowed: bool
    confirmation_count: int
    submit_count: int
    blocked_count: int
    items: list[Bon8ProductionItemResult] = Field(default_factory=list)
    accounts: list[Bon8ProductionAccountRunState] = Field(default_factory=list)
    attempts: list[Bon8ProductionItemAttemptState] = Field(default_factory=list)
    confirmation_sheet: Optional[Bon8FirstConfirmationSheet] = None
    last_error: str = ""
    next_step: str = ""
    guardrails: list[str]
    message: str
