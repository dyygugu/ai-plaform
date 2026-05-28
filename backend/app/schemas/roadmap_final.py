from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class RoadmapPhaseItem(BaseModel):
    phase: str
    title: str
    status: str
    completed_items: int
    pending_items: int
    evidence_path: str


class RoadmapFinalSummaryResponse(BaseModel):
    generated_at: datetime
    status: str
    total_phases: int
    completed_phases: int
    todo_unchecked_count: int
    latest_docker_smoke_ok: bool
    key_evidence_ready: bool
    manual_domain_switch_ready: bool
    production_domain: str
    base_url: str
    phases: list[RoadmapPhaseItem]
    evidence_paths: list[str]
    remaining_manual_actions: list[str]
    risk_notes: list[str]
    message: str


class RoadmapFinalReportRequest(BaseModel):
    write_audit: bool = True
    generate_report: bool = True


class RoadmapFinalReportResponse(BaseModel):
    generated_at: datetime
    status: str
    report_path: Optional[str] = None
    audit_trace_id: Optional[str] = None
    summary: RoadmapFinalSummaryResponse
    message: str
