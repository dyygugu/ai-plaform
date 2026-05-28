from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ObservabilityMetric(BaseModel):
    key: str
    title: str
    value: object
    status: str
    message: str


class CollectorGuardResponse(BaseModel):
    source_account_user_id: str
    safe_mode: bool
    live_readonly_available: bool
    sample_summary_path: str
    sample_exists: bool
    sample_age_minutes: Optional[float]
    task_count: int
    stale_count: int
    error_count: int
    latest_error: Optional[str]
    status: str
    message: str


class ProbeResult(BaseModel):
    key: str
    title: str
    status: str
    latency_ms: int
    message: str
    details: dict[str, object] = {}


class ProbeRunResponse(BaseModel):
    trace_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    results: list[ProbeResult]


class TimelineEvent(BaseModel):
    id: str
    source: str
    severity: str
    title: str
    message: str
    trace_id: str
    created_at: datetime
    target_type: str = ""
    target_id: str = ""


class ObservabilitySummary(BaseModel):
    generated_at: datetime
    status: str
    environment: str
    public_base_url: str
    metrics: list[ObservabilityMetric]
    collector_guard: CollectorGuardResponse
    recent_timeline: list[TimelineEvent]
    probes: list[ProbeResult]

