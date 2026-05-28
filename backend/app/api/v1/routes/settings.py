from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.permissions import get_permission_matrix
from app.core.settings import get_settings
from app.db.session import get_db
from app.schemas.settings import PermissionMatrixResponse, RuntimeSettingsResponse, TaskSourceUpdateRequest, TaskSourceUpdateResponse
from app.services.audit_service import write_audit
from app.services.task_service import get_task_source_account_user_id, set_task_source_account_user_id

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/runtime", response_model=RuntimeSettingsResponse)
def read_runtime_settings(db: Session = Depends(get_db)) -> RuntimeSettingsResponse:
    settings = get_settings()
    return RuntimeSettingsResponse(
        task_source_account_user_id=get_task_source_account_user_id(db),
        public_base_url=settings.public_base_url,
        backup_local_retention_days=settings.backup_local_retention_days,
        backup_external_retention_days=settings.backup_external_retention_days,
        backup_cleanup_time=settings.backup_cleanup_time,
    )


@router.put("/task-source", response_model=TaskSourceUpdateResponse)
def update_task_source(payload: TaskSourceUpdateRequest, db: Session = Depends(get_db)) -> TaskSourceUpdateResponse:
    config = set_task_source_account_user_id(db, payload.task_source_account_user_id, payload.updated_by)
    write_audit(
        db,
        event_type="task_source_account_update",
        message=f"Updated task source account to {config.value}; trigger task catalog refresh next",
        target_type="account",
        target_id=config.value,
        actor=payload.updated_by,
    )
    db.commit()
    return TaskSourceUpdateResponse(task_source_account_user_id=config.value, message="任务页来源账号已更新，请执行任务页手动刷新。")


@router.get("/permissions", response_model=PermissionMatrixResponse)
def read_permission_matrix() -> PermissionMatrixResponse:
    return PermissionMatrixResponse(roles=get_permission_matrix())
