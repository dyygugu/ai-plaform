from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TaskAbilityDraftCreateRequest(BaseModel):
    task_name: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    specific_rules: str = Field(min_length=1)
    sample_data: str = Field(min_length=1)
    related_content: str = ""
    system_ai_draft: str = Field(min_length=1)
    system_ai_trace_id: str = ""
    provider_status: str = ""


class TaskAbilityDraftRead(BaseModel):
    id: str
    version: str
    status: str
    task_name: str
    task_id: str
    specific_rules: str
    sample_data: str
    related_content: str
    system_ai_draft: str
    system_ai_trace_id: str
    provider_status: str
    next_step: str
    created_at: datetime
    updated_at: datetime
    flow_stage: str = "draft_ready"
    capability_enabled: bool = False
    real_no_submit_review: dict = Field(default_factory=dict)
    task_queue_snapshot: dict = Field(default_factory=dict)


class TaskAbilityDraftListResponse(BaseModel):
    generated_at: datetime
    total: int
    latest_draft: Optional[TaskAbilityDraftRead]
    items: list[TaskAbilityDraftRead]
    message: str
