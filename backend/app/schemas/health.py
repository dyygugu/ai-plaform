from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str
    api_prefix: str
    task_source_account_user_id: str
    public_base_url: str
    backup_local_retention_days: int
    backup_external_retention_days: int
    backup_cleanup_time: str
