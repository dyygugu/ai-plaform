from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MaintenanceJobRunRequest(BaseModel):
    dry_run: bool = False
    trigger_type: str = "manual"


class MaintenanceJobRunRead(BaseModel):
    id: int
    job_key: str
    status: str
    trigger_type: str
    dry_run: int
    message: str
    result_json: str
    trace_id: str
    started_at: datetime
    finished_at: Optional[datetime]

    model_config = {"from_attributes": True}


class MaintenanceJobDefinitionRead(BaseModel):
    key: str
    title: str
    schedule: str
    description: str
    enabled: bool
    last_run: Optional[MaintenanceJobRunRead] = None


class MaintenanceJobSummary(BaseModel):
    jobs: list[MaintenanceJobDefinitionRead]
    recent_runs: list[MaintenanceJobRunRead]


class ReleaseGateCheck(BaseModel):
    key: str
    title: str
    status: str
    required: bool
    message: str
    details: dict[str, object] = {}


class ReleaseGateResponse(BaseModel):
    ready_for_manual_domain_switch: bool
    production_domain: str
    public_base_url: str
    manual_switch_required: bool
    message: str
    checks: list[ReleaseGateCheck]


class SchedulerJobPlan(BaseModel):
    job_key: str
    title: str
    enabled: bool
    interval_minutes: int
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    due: bool
    last_status: Optional[str] = None
    last_message: str = ""


class SchedulerPlanResponse(BaseModel):
    now: datetime
    due_count: int
    jobs: list[SchedulerJobPlan]


class SchedulerTickRequest(BaseModel):
    dry_run: bool = True
    limit: int = 10


class SchedulerTickResponse(BaseModel):
    now: datetime
    dry_run: bool
    due_count: int
    executed_count: int
    skipped_count: int
    runs: list[MaintenanceJobRunRead]
    message: str


class DomainSwitchRunbookStep(BaseModel):
    order: int
    title: str
    command_or_action: str
    expected_result: str
    rollback_note: str = ""


class DomainSwitchRunbookResponse(BaseModel):
    production_domain: str
    target_base_url: str
    ready_for_manual_domain_switch: bool
    manual_only: bool
    summary: str
    pre_checks: list[ReleaseGateCheck]
    steps: list[DomainSwitchRunbookStep]
    rollback_steps: list[DomainSwitchRunbookStep]
