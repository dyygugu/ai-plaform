from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AiProviderConfigRead(BaseModel):
    role: str
    title: str
    base_url: str = ""
    model: str = "gpt-4.1-mini"
    api_key_configured: bool = False
    timeout_seconds: int = 30
    permission_scope: str = ""
    call_scope: str = ""
    pre_prompt: str = ""
    skills: list[str] = Field(default_factory=list)
    md_files: list[str] = Field(default_factory=list)
    source: str = "runtime"
    message: str = ""


class AiProviderConfigUpdate(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-4.1-mini"
    timeout_seconds: int = 30
    pre_prompt: str = ""
    skills: list[str] = Field(default_factory=list)
    md_files: list[str] = Field(default_factory=list)


class AiRuntimeConfigRead(BaseModel):
    system_ai: AiProviderConfigRead
    task_ai: AiProviderConfigRead
    task_ai_managed_by_system_ai: bool = True
    source: str = "runtime"
    message: str = ""


class AiRuntimeConfigUpdate(BaseModel):
    system_ai: AiProviderConfigUpdate = Field(default_factory=AiProviderConfigUpdate)
    task_ai: AiProviderConfigUpdate = Field(default_factory=AiProviderConfigUpdate)
    task_ai_managed_by_system_ai: bool = True


class AiConfigCheckItem(BaseModel):
    key: str
    title: str
    status: str
    detail: str
    next_step: str


class AiConfigCheckResponse(BaseModel):
    status: str
    ready_for_system_chat: bool
    ready_for_task_draft: bool
    source: str
    system_model: str
    task_model: str
    checks: list[AiConfigCheckItem]
    message: str


class AiChatMessage(BaseModel):
    role: str
    content: str


class AiChatRequest(BaseModel):
    message: str
    account_user_id: str = ""
    task_id: str = ""
    history: list[AiChatMessage] = Field(default_factory=list)
    use_provider: bool = True


class AiChatResponse(BaseModel):
    trace_id: str
    provider_status: str
    answer: str
    context_summary: dict[str, object]
    message: str


class AiJobRead(BaseModel):
    id: int
    status: str
    prompt_summary: str
    result_summary: str
    queue_wait_ms: int
    upstream_ms: int
    total_ms: int
    trace_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AiQueueSummary(BaseModel):
    total: int
    planned: int
    mock_completed: int
    provider_gated: int
    failed: int
    items: list[AiJobRead]


class AiIncidentAction(BaseModel):
    key: str
    title: str
    risk_level: str
    status: str
    requires_confirmation: bool
    allowed_by_policy: bool
    message: str
    rollback_hint: str


class AiIncidentReviewRequest(BaseModel):
    dry_run: bool = True
    allow_high_risk: bool = False
    write_audit: bool = True
    generate_report: bool = True
    use_provider: bool = True


class AiIncidentReviewResponse(BaseModel):
    trace_id: str
    generated_at: datetime
    status: str
    provider_status: str
    permission_model: str
    guardrail_summary: str
    incident_count: int
    action_count: int
    auto_executed_count: int
    confirmation_required_count: int
    confirmation_request_count: int = 0
    confirmation_ids: list[int] = Field(default_factory=list)
    feishu_notification_preview: str
    report_path: Optional[str] = None
    audit_trace_id: Optional[str] = None
    ai_job_id: Optional[int] = None
    context_summary: dict[str, object]
    actions: list[AiIncidentAction]
    message: str


class AiActionConfirmationRead(BaseModel):
    id: int
    status: str
    action_key: str
    title: str
    risk_level: str
    source: str
    source_trace_id: str
    source_ai_job_id: Optional[int] = None
    message: str
    rollback_hint: str
    requested_by: str
    reviewed_by: Optional[str] = None
    confirmation_note: str
    trace_id: str
    created_at: datetime
    updated_at: datetime
    reviewed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    confirm_phrase: str
    next_step: str

    model_config = {"from_attributes": True}


class AiActionConfirmationSummary(BaseModel):
    total: int
    pending: int
    approved: int
    rejected: int
    expired: int
    items: list[AiActionConfirmationRead]


class AiActionConfirmationDecisionRequest(BaseModel):
    operator: str = "admin"
    note: str = ""
    confirm_text: str = ""
    write_audit: bool = True


class AiActionConfirmationDecisionResponse(BaseModel):
    item: AiActionConfirmationRead
    audit_trace_id: Optional[str] = None
    message: str
