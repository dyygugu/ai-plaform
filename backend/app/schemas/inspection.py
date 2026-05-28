from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class InspectionCheckItem(BaseModel):
    key: str
    title: str
    status: str
    message: str
    evidence: str = ""
    recommended_action: str = ""
    details: dict[str, object] = Field(default_factory=dict)


class InspectionSummaryResponse(BaseModel):
    generated_at: datetime
    status: str
    base_url: str
    production_domain: str
    manual_domain_switch_required: bool
    checks: list[InspectionCheckItem]
    next_actions: list[str]
    baseline: dict[str, object] = Field(default_factory=dict)
    message: str


class InspectionChecklistResponse(BaseModel):
    generated_at: datetime
    items: list[InspectionCheckItem]
    risk_notes: list[str]
    rollback_notes: list[str]


class InspectionRunRequest(BaseModel):
    write_audit: bool = True
    generate_report: bool = True


class InspectionRunResponse(BaseModel):
    trace_id: str
    status: str
    generated_at: datetime
    report_path: Optional[str] = None
    audit_trace_id: Optional[str] = None
    summary: InspectionSummaryResponse
    message: str
