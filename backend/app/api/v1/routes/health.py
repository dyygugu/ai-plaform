from fastapi import APIRouter

from app.core.settings import get_settings
from app.schemas.health import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse)
def read_health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        environment=settings.monitor_env,
        version=settings.monitor_version,
        api_prefix=settings.api_prefix,
        task_source_account_user_id=settings.task_source_account_user_id,
        public_base_url=settings.public_base_url,
        backup_local_retention_days=settings.backup_local_retention_days,
        backup_external_retention_days=settings.backup_external_retention_days,
        backup_cleanup_time=settings.backup_cleanup_time,
    )
