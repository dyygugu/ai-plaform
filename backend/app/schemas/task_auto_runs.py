from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskAutoRunStartRequest(BaseModel):
    task_id: str
    node_id: str = "1"
    account_user_ids: list[str] = Field(default_factory=list)
    adapter_key: str = ""
    ability_version: str = ""
    write_audit: bool = True
    run_config: dict[str, Any] = Field(default_factory=dict)


class TaskAutoRunAccountState(BaseModel):
    account_user_id: str
    account_name: str = ""
    status: str
    current_item_id: str = ""
    current_stage: str = ""
    healthy: bool = True
    last_error: str = ""


class TaskAutoRunWorkerStartRequest(BaseModel):
    interval_seconds: int = Field(default=5, ge=1, le=300)


class TaskAutoRunWorkerStatusResponse(BaseModel):
    run_id: str
    adapter_run_id: str = ""
    active: bool = False
    running: bool = False
    cycle_count: int = 0
    last_ok: Optional[bool] = None
    last_error: Optional[str] = None
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    interval_seconds: int = 0
    next_run_at: Optional[str] = None


class TaskAutoRunPreflightCheck(BaseModel):
    key: str
    title: str
    status: str
    required: bool = True
    detail: str = ""
    next_step: str = ""


class TaskAutoRunPreflightResponse(BaseModel):
    generated_at: datetime
    task_id: str
    node_id: str = "1"
    adapter_key: str = ""
    status: str
    can_start: bool = False
    runnable_account_count: int = 0
    checks: list[TaskAutoRunPreflightCheck] = Field(default_factory=list)
    message: str = ""
    next_step: str = ""


class TaskAutoRunResponse(BaseModel):
    generated_at: datetime
    run_id: str
    adapter_key: str
    adapter_run_id: str = ""
    task_id: str
    node_id: str = "1"
    ability_version: str = ""
    status: str
    stop_requested: bool = False
    selected_account_count: int = 0
    healthy_account_count: int = 0
    abnormal_account_count: int = 0
    health_ok: bool = True
    accounts: list[TaskAutoRunAccountState] = Field(default_factory=list)
    last_error: str = ""
    next_step: str = ""
    message: str = ""
    raw_adapter_run: dict[str, Any] = Field(default_factory=dict)
