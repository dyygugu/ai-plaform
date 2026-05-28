from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class IncidentQueueItem(BaseModel):
    key: str
    title: str
    severity: str
    status: str
    subject: str
    reason: str
    recommended_action: str
    evidence_path: str
    runbook_steps: list[str]
    evidence: dict[str, object] = Field(default_factory=dict)


class IncidentRunbookItem(BaseModel):
    key: str
    category: str
    title: str
    severity: str
    trigger: str
    owner: str
    evidence_path: str
    steps: list[str]
    status: str


class IncidentClosureCheck(BaseModel):
    key: str
    title: str
    status: str
    required: bool
    severity: str
    evidence_path: str
    detail: str
    next_step: str


class IncidentClosurePlanResponse(BaseModel):
    generated_at: datetime
    status: str
    ready_to_close: bool
    open_incidents: int
    critical_count: int
    warning_count: int
    pending_high_risk_confirmations: int
    external_send_enabled: bool
    checks: list[IncidentClosureCheck]
    risk_notes: list[str]
    next_actions: list[str]
    message: str


class IncidentSummaryResponse(BaseModel):
    generated_at: datetime
    status: str
    total_open: int
    critical_count: int
    warning_count: int
    runbook_count: int
    external_send_enabled: bool
    incidents: list[IncidentQueueItem]
    runbooks: list[IncidentRunbookItem]
    risk_notes: list[str]
    next_actions: list[str]
    message: str


class IncidentClosureRequest(BaseModel):
    dry_run: bool = True
    write_audit: bool = True
    generate_report: bool = True


class IncidentClosureResponse(BaseModel):
    generated_at: datetime
    status: str
    dry_run: bool
    closed_count: int
    report_path: Optional[str] = None
    audit_trace_id: Optional[str] = None
    summary: IncidentSummaryResponse
    plan: IncidentClosurePlanResponse
    message: str
