from datetime import datetime
from typing import Optional, Union

from pydantic import BaseModel, Field


class TaskCatalogItemRead(BaseModel):
    id: int
    source_account_user_id: str
    raw_task_name: str
    task_short_name: str
    task_id: str
    task_name_id: str
    task_status_raw: str
    task_status_color: str
    pending_raw: str
    visibility: str
    last_task_page_seen_at: Optional[datetime]
    last_task_page_error: Optional[str]
    capability_available: bool = False
    capability_recording_count: int = 0
    model_config = {"from_attributes": True}


class TaskCatalogListResponse(BaseModel):
    source_account_user_id: str
    items: list[TaskCatalogItemRead]
    stale: bool = False
    last_error: Optional[str] = None


class TaskCatalogEventRead(BaseModel):
    id: int
    task_catalog_item_id: int
    source_account_user_id: str
    task_id: str
    event_type: str
    status_raw: str
    pending_raw: str
    message: str
    created_at: datetime
    model_config = {"from_attributes": True}


class TaskCatalogDetailResponse(BaseModel):
    item: TaskCatalogItemRead
    source_account_user_id: str
    covered_account_count: int
    latest_failure: Optional[str] = None
    status_history: list[TaskCatalogEventRead]
    pending_history: list[TaskCatalogEventRead]
    timeline: list[TaskCatalogEventRead]


class TaskCatalogSeedRequest(BaseModel):
    raw_task_name: str = Field(..., min_length=1)
    task_status_raw: str = "未知"
    pending_raw: str = ""
    source_account_user_id: Optional[str] = None


class TaskCatalogSeedResponse(BaseModel):
    item: TaskCatalogItemRead
    created: bool


class TaskCatalogRefreshRequest(BaseModel):
    source_account_user_id: Optional[str] = None
    use_live_readonly: bool = False
    sample_payload: Optional[Union[dict, list]] = None


class TaskCatalogRefreshResponse(BaseModel):
    source_account_user_id: str
    sample_saved: bool
    redacted_sample_path: Optional[str] = None
    imported_count: int
    message: str
    refresh_mode: str = "cached_summary"
    live_readonly_requested: bool = False
    live_readonly_ok: bool = False
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None


class TaskCatalogRefreshJobStep(BaseModel):
    key: str
    title: str
    status: str
    message: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class TaskCatalogRefreshJobResponse(BaseModel):
    job_id: str
    status: str
    source_account_user_id: str
    use_live_readonly: bool
    current_step_index: int
    steps: list[TaskCatalogRefreshJobStep]
    result: Optional[TaskCatalogRefreshResponse] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    message: str


class TaskCatalogRefreshStepRequest(BaseModel):
    job_id: str


class TaskRuleConfigResponse(BaseModel):
    prefix_rules: list[str]
    manual_short_names: dict[str, str]


class TaskRuleConfigUpdateRequest(BaseModel):
    prefix_rules: Optional[list[str]] = None
    manual_short_names: Optional[dict[str, str]] = None


class TaskSampleCaptureRequest(BaseModel):
    source_account_user_id: Optional[str] = None
    sample_payload: Optional[Union[dict, list]] = None
    use_live_readonly: bool = False


class TaskSampleCaptureResponse(BaseModel):
    source_account_user_id: str
    sample_saved: bool
    message: str
    redacted_sample_path: Optional[str] = None
