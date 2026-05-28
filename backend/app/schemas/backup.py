from datetime import datetime

from pydantic import BaseModel


class BackupPlanResponse(BaseModel):
    local_retention_days: int
    external_retention_days: int
    cleanup_time: str
    external_target_path: str
    cleanup_failure_alert: str


class BackupTargetTestResponse(BaseModel):
    ok: bool
    path: str
    message: str


class BackupJobRead(BaseModel):
    id: int
    backup_type: str
    status: str
    local_retention_days: int
    external_retention_days: int
    cleanup_time: str
    target_path: str
    message: str
    trace_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
