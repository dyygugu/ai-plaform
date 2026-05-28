from datetime import datetime
from typing import Any
from typing import Optional

from pydantic import BaseModel, Field


class SubmittedHistorySyncRequest(BaseModel):
    account_id: str = ""
    node_id: int = 1
    force: bool = False


class SubmittedHistorySyncResponse(BaseModel):
    task_id: str
    task_name: str = ""
    account_id: str = ""
    sample_count: int = 0
    sample_pool_count: int = 0
    new_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    manifest_path: str = ""
    last_synced_at: datetime


class SubmittedHistoryStatsResponse(BaseModel):
    task_id: str
    task_name: str = ""
    sample_count: int = 0
    sample_pool_count: int = 0
    last_synced_at: Optional[datetime] = None
    manifest_path: str = ""


class SubmittedHistorySampleRead(BaseModel):
    uid: str
    item_id: str
    task_id: str
    task_name: str = ""
    account_id: str = ""
    source: str = "submitted_history_http"
    submitted_nodes: list[dict[str, Any]] = Field(default_factory=list)
    primary_output: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    synced_at: datetime


class SubmittedHistoryListResponse(BaseModel):
    task_id: str
    task_name: str = ""
    sample_count: int = 0
    items: list[SubmittedHistorySampleRead] = Field(default_factory=list)


class TestsetGenerateRequest(BaseModel):
    sample_count: int = 10


class TestsetGenerateResponse(BaseModel):
    task_id: str
    task_name: str = ""
    sample_count: int
    sample_pool_count: int
    sample_ids: list[str] = Field(default_factory=list)


class TestsetSaveRequest(BaseModel):
    sample_ids: list[str] = Field(default_factory=list)


class TestsetRead(BaseModel):
    task_id: str
    task_name: str = ""
    sample_pool_count: int = 0
    testset_id: str
    sample_count: int = 0
    sample_ids: list[str] = Field(default_factory=list)
    source: str = "submitted_history"
    created_at: datetime
    path: str = ""
