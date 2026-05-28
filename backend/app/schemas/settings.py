from pydantic import BaseModel


class PermissionMatrixResponse(BaseModel):
    roles: dict[str, dict[str, bool]]


class RuntimeSettingsResponse(BaseModel):
    task_source_account_user_id: str
    public_base_url: str
    backup_local_retention_days: int
    backup_external_retention_days: int
    backup_cleanup_time: str
    production_domain: str = "manage.51gugu.uk"
    production_domain_switch_deferred: bool = True


class TaskSourceUpdateRequest(BaseModel):
    task_source_account_user_id: str
    updated_by: str = "operator"


class TaskSourceUpdateResponse(BaseModel):
    task_source_account_user_id: str
    message: str
