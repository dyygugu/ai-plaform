from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ExecutionDeviceRead(BaseModel):
    worker_id: str
    agent_id: str = ""
    device_name: str
    status: str
    approval_status: str
    current_state: str
    manual_slots: int
    running_slots: int
    effective_slots: int
    available_slots: int
    local_agent_version: str
    worker_runtime_version: str
    extension_version: str = ""
    update_status: str = "unknown"
    last_seen_at: Optional[datetime] = None
    current_run: dict = Field(default_factory=dict)
    needs_attention: str = ""
    can_receive_tasks: bool
    usable_for_production: bool


class ExecutionDeviceListResponse(BaseModel):
    items: list[ExecutionDeviceRead] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class ExecutionDeviceSummaryResponse(BaseModel):
    total: int
    online: int
    running: int
    pending_approval: int
    abnormal: int
    update_needed: int


class ExecutionDeviceDeleteResponse(BaseModel):
    worker_id: str
    deleted: bool
    message: str


class DeletedExecutionDeviceRead(BaseModel):
    worker_id: str
    device_name: str
    status_label: str = "已删除"
    deleted_at: Optional[datetime] = None
    delete_reason: str = ""
    last_seen_at: Optional[datetime] = None


class ExecutionDeviceRenameRequest(BaseModel):
    device_name: str


class ExecutionDeviceCapacityRequest(BaseModel):
    manual_slots: int = Field(ge=1)


class ExecutionDeviceApproveRequest(BaseModel):
    manual_slots: int = Field(default=1, ge=1)


class LocalAgentReleaseRead(BaseModel):
    version: str = "0.9.0"
    suite_name: str = "aidp-local-suite-0.9.0.zip"
    message: str = "本机助手套件下载入口已就绪。"
