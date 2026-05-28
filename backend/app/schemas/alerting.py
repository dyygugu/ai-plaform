from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AlertRuleRead(BaseModel):
    key: str
    title: str
    severity: str
    slo_target: str
    source: str
    silence_minutes: int
    description: str
    runbook_hint: str


class SloIndicator(BaseModel):
    key: str
    title: str
    target: str
    current: str
    status: str
    message: str


class SloSummaryResponse(BaseModel):
    generated_at: datetime
    overall_status: str
    indicators: list[SloIndicator]


class AlertIncident(BaseModel):
    key: str
    title: str
    severity: str
    status: str
    subject: str
    reason: str
    recommended_action: str
    evidence: dict[str, object] = Field(default_factory=dict)


class AlertEvaluationRequest(BaseModel):
    dry_run: bool = True
    write_audit: bool = True
    send_external: bool = False


class AlertEvaluationResponse(BaseModel):
    trace_id: str
    generated_at: datetime
    status: str
    dry_run: bool
    external_send_enabled: bool
    rules: list[AlertRuleRead]
    slo: SloSummaryResponse
    incidents: list[AlertIncident]
    notification_preview: str
    audit_trace_id: Optional[str] = None
    message: str


class AlertSummaryResponse(BaseModel):
    generated_at: datetime
    status: str
    rules: list[AlertRuleRead]
    slo: SloSummaryResponse
    incidents: list[AlertIncident]
    notification_preview: str
    external_send_enabled: bool
