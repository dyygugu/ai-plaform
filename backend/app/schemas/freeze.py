from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class FreezeItem(BaseModel):
    key: str
    title: str
    status: str
    evidence: str = ""
    owner: str = "system"
    action: str = ""
    rollback: str = ""
    details: dict[str, object] = Field(default_factory=dict)


class FreezeSummaryResponse(BaseModel):
    generated_at: datetime
    status: str
    base_url: str
    production_domain: str
    manual_only: bool
    ready_for_manual_switch: bool
    freeze_items: list[FreezeItem]
    rollback_items: list[FreezeItem]
    evidence_paths: list[str]
    message: str


class FreezeChecklistResponse(BaseModel):
    generated_at: datetime
    freeze_items: list[FreezeItem]
    rollback_items: list[FreezeItem]
    manual_confirmation_items: list[str]
    risk_notes: list[str]


class FreezeCreateRequest(BaseModel):
    write_audit: bool = True
    generate_report: bool = True


class FreezeCreateResponse(BaseModel):
    trace_id: str
    status: str
    generated_at: datetime
    report_path: Optional[str] = None
    audit_trace_id: Optional[str] = None
    summary: FreezeSummaryResponse
    message: str
