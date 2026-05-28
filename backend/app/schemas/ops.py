from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class RestoreDrillRead(BaseModel):
    id: int
    status: str
    checklist_json: str
    message: str
    trace_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AlertPreviewRequest(BaseModel):
    title: str = "采集连续失败"
    severity: str = "warning"
    subject: str = "主账号未配置"
    reason: str = "任务页采集连续失败 3 次"


class AlertPreviewResponse(BaseModel):
    text: str
    trace_id: str


class OperationalRiskItem(BaseModel):
    key: str
    title: str
    severity: str
    status: str
    subject: str
    reason: str
    recommended_action: str
    evidence_path: str
    sources: list[str]

    def add_source(self, source: str) -> None:
        self.sources = sorted(set(self.sources) | {source})

    def source_has(self, source: str) -> bool:
        return source in self.sources


class OperationalRiskSummaryResponse(BaseModel):
    generated_at: datetime
    status: str
    risk_count: int
    critical_count: int
    warning_count: int
    manual_todo_count: int
    items: list[OperationalRiskItem]
    message: str


class WorkerLogReplayItem(BaseModel):
    worker_id: str
    severity: str
    message: str
    trace_id: str
    account_user_id: str
    task_id: str
    created_at: datetime
    stage: str = ""
    step: str = ""
    error_code: str = ""
    error_detail: str = ""
    retryable: Optional[bool] = None
    duration_ms: Optional[int] = None


class FaultDiagnosisItem(BaseModel):
    key: str
    severity: str
    status: str
    error_location: str
    accurate_error: str
    affected_scope: str
    first_seen_source: str
    evidence_links: list[str]
    next_actions: list[str]
    escalation_hint: str
    sources: list[str]
    worker_log_replay: list[WorkerLogReplayItem] = Field(default_factory=list)


class FaultDiagnosisResponse(BaseModel):
    generated_at: datetime
    status: str
    fault_count: int
    primary: Optional[FaultDiagnosisItem]
    items: list[FaultDiagnosisItem]
    message: str
