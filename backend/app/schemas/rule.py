from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class RuleVersionCreateRequest(BaseModel):
    version: Optional[str] = None
    title: str = Field(default="规则草稿", min_length=1)
    rule_json: dict = Field(default_factory=dict)
    changelog: str = ""


class RuleVersionActionRequest(BaseModel):
    canary_percent: int = Field(default=100, ge=0, le=100)
    message: str = ""


class RuleVersionRead(BaseModel):
    id: int
    version: str
    title: str
    status: str
    rule_json: str
    changelog: str
    canary_percent: int
    created_by: str
    published_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RulePublishEventRead(BaseModel):
    id: int
    rule_version_id: int
    action: str
    from_status: str
    to_status: str
    canary_percent: int
    message: str
    actor: str
    trace_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RuleHitStatRead(BaseModel):
    id: int
    rule_version_id: int
    rule_key: str
    hits: int
    misses: int
    sample_task_name_id: str
    updated_at: datetime
    hit_rate: float


class RuleDiffItem(BaseModel):
    key: str
    change_type: str
    base_value: str
    target_value: str


class RuleVersionDiffResponse(BaseModel):
    base_version: Optional[RuleVersionRead]
    target_version: RuleVersionRead
    items: list[RuleDiffItem]


class RuleCenterSummary(BaseModel):
    versions: list[RuleVersionRead]
    active_version: Optional[RuleVersionRead]
    publish_events: list[RulePublishEventRead]
    hit_stats: list[RuleHitStatRead]
