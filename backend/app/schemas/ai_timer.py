from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AiTimerStageDuration(BaseModel):
    stage: str
    duration_ms: int = Field(default=0, ge=0)


class AiTimerEventCreate(BaseModel):
    account_user_id: str = ""
    account_name: str = ""
    task_id: str = ""
    task_name: str = ""
    item_id: str = ""
    status: str = "submitted"
    source: str = "manual_event"
    total_ms: int = Field(default=0, ge=0)
    stages: list[AiTimerStageDuration] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class AiTimerEventRead(AiTimerEventCreate):
    recorded_at: datetime


class AiTimerStageSummary(BaseModel):
    stage: str
    avg_duration_ms: int = 0
    total_duration_ms: int = 0
    sample_count: int = 0
    share_percent: float = 0


class AiTimerSummaryResponse(BaseModel):
    generated_at: datetime
    total_items: int = 0
    submitted_items: int = 0
    avg_total_ms: int = 0
    p50_total_ms: int = 0
    p95_total_ms: int = 0
    questions_per_hour: float = 0
    unit_price: float = 0
    estimated_hourly_income: float = 0
    slowest_stage: AiTimerStageSummary = Field(default_factory=lambda: AiTimerStageSummary(stage="无样本"))
    stage_breakdown: list[AiTimerStageSummary] = Field(default_factory=list)
    recent_items: list[AiTimerEventRead] = Field(default_factory=list)
    message: str
