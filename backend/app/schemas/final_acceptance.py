from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FinalAcceptanceItem(BaseModel):
    key: str
    category: str
    title: str
    status: str
    required: bool
    evidence_path: str
    message: str


class RollbackDrillStep(BaseModel):
    key: str
    order: int
    title: str
    status: str
    operator_action: str
    expected_result: str
    rollback_action: str
    evidence_path: str


class FinalAcceptanceMatrixResponse(BaseModel):
    generated_at: datetime
    status: str
    total_count: int
    passed_count: int
    warning_count: int
    failed_count: int
    items: list[FinalAcceptanceItem]
    rollback_steps: list[RollbackDrillStep]
    evidence_paths: list[str]
    risk_notes: list[str]
    next_actions: list[str]
    message: str


class FinalEvidenceRequest(BaseModel):
    write_audit: bool = True
    generate_report: bool = True


class FinalEvidenceResponse(BaseModel):
    generated_at: datetime
    status: str
    report_path: Optional[str] = None
    audit_trace_id: Optional[str] = None
    matrix: FinalAcceptanceMatrixResponse
    message: str
