from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ScoreLoopStep(BaseModel):
    key: str
    title: str
    status: str
    detail: str
    source: str


class ScoreLoopGate(BaseModel):
    required_stable_count: int
    manual_stable_count: int
    auto_submit_enabled: bool
    force_enabled: bool
    ready_for_auto_submit: bool
    blocked_reason: str
    last_enabled_at: Optional[datetime] = None
    last_disabled_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    audit_trace_id: Optional[str] = None


class ScoreLoopLogContract(BaseModel):
    source: str
    storage_key: str
    max_entries: int
    required_events: list[str]
    ingestion_status: str
    note: str


class ScoreLoopReadinessCheck(BaseModel):
    key: str
    title: str
    status: str
    required: bool
    detail: str
    next_step: str


class ScoreLoopCaseRead(BaseModel):
    id: int
    status: str
    task_type_key: str
    task_type_name: str
    task_catalog_item_id: Optional[int] = None
    account_user_id: str
    question_hash: str
    question_text: str
    choices: list[str]
    ai_answer: str
    ai_reason: str
    final_answer: str
    manual_decision: str
    manual_note: str
    submit_confirmation_id: Optional[int] = None
    trace_id: str
    created_at: datetime
    updated_at: datetime
    reviewed_at: Optional[datetime] = None
    next_step: str


class ScoreLoopCaseListResponse(BaseModel):
    total: int
    items: list[ScoreLoopCaseRead]


class ScoreLoopSummaryResponse(BaseModel):
    generated_at: datetime
    task_type_key: str
    task_type_name: str
    plugin_version: str
    supported: bool
    mode: str
    gate: ScoreLoopGate
    plugin_workflow: list[ScoreLoopStep]
    http_dry_run_plan: list[ScoreLoopStep]
    guardrails: list[str]
    log_contract: ScoreLoopLogContract
    readiness_checks: list[ScoreLoopReadinessCheck] = Field(default_factory=list)
    source_files: list[str]
    case_counts: dict[str, int] = Field(default_factory=dict)
    message: str


class ScoreLoopCaptureRequest(BaseModel):
    task_type_key: str = "rft_aesthetic_v1"
    task_type_name: str = "RFT人标_美观度"
    task_catalog_item_id: Optional[int] = None
    account_user_id: str = ""
    question_text: str
    choices: list[str] = Field(default_factory=list)
    write_audit: bool = True


class ScoreLoopDraftRequest(BaseModel):
    use_provider: bool = True
    write_audit: bool = True


class ScoreLoopReviewRequest(BaseModel):
    decision: str = "approve"
    final_answer: str = ""
    note: str = "人工确认评分结果"
    request_submit: bool = False
    write_audit: bool = True


class ScoreLoopManualStableRequest(BaseModel):
    count_delta: int = Field(default=1, ge=-20, le=20)
    note: str = "人工提交稳定样本"


class ScoreLoopAutoSubmitRequest(BaseModel):
    enabled: bool
    force_confirmed: bool = False
    reason: str = "operator request"


class ScoreLoopActionResponse(BaseModel):
    item: Optional[ScoreLoopCaseRead] = None
    gate: Optional[ScoreLoopGate] = None
    audit_trace_id: Optional[str] = None
    message: str
