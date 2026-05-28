from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class DataQualityCheckItem(BaseModel):
    key: str
    title: str
    status: str
    expected: str
    actual: str
    evidence_path: str
    message: str


class EarningsContractItem(BaseModel):
    key: str
    title: str
    source_field: str
    display_name: str
    aggregation: str
    status: str


class DataQualitySummaryResponse(BaseModel):
    generated_at: datetime
    status: str
    expected_account_count: int
    account_count: int
    task_count: int
    earnings_row_count: int
    worker_count: int
    audit_event_count: int
    today_income_total: float
    hourly_income_total: float
    checks: list[DataQualityCheckItem]
    contracts: list[EarningsContractItem]
    risk_notes: list[str]
    next_actions: list[str]
    message: str


class DataQualityReportRequest(BaseModel):
    write_audit: bool = True
    generate_report: bool = True
    generate_excel: bool = True


class DataQualityReportResponse(BaseModel):
    generated_at: datetime
    status: str
    report_path: Optional[str] = None
    export_path: Optional[str] = None
    audit_trace_id: Optional[str] = None
    row_counts: dict[str, int]
    evidence_paths: list[str]
    summary: DataQualitySummaryResponse
    message: str


class DataQualityExportResponse(BaseModel):
    generated_at: datetime
    status: str
    export_path: str
    row_counts: dict[str, int]
    evidence_paths: list[str]
    metadata: dict[str, Any]
    message: str
