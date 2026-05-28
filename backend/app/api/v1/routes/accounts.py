from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.session import get_db
from app.models.account import AccountStatus
from app.schemas.account import AccountClientSessionRequest, AccountClientSessionResponse, AccountLoginSlotCreateRequest, AccountLoginSlotRead, AccountMetadataRead, AccountMetadataUpdate, AccountRead, AccountUsernameRefreshResponse, TaskSourceConfig
from app.schemas.account_coverage import AccountCoverageBaselineRequest, AccountCoverageBaselineResponse, AccountCoverageSummaryResponse
from app.schemas.production import AccountRecycleActionResponse, BrowserOpenTargetResponse, DeletedProductionAccountRead, ProductionAccountRefreshResponse, ProductionAutoRefreshStatusRead, ProductionDashboardSummary
from app.services.account_health_service import refresh_account_health
from app.services.account_recycle_service import delete_account, list_deleted_accounts, restore_account
from app.services.account_service import account_read_with_metadata, list_accounts, update_account_metadata
from app.services.login_slot_service import create_new_login_slot, create_relogin_slot, list_login_slots, register_client_session
from app.services.production_dashboard_service import build_production_dashboard, create_browser_open_session
from app.services.production_account_refresh_service import refresh_production_account_by_user_id, refresh_production_accounts
from app.services.account_coverage_service import build_account_coverage_summary, create_account_coverage_baseline
from app.services.audit_service import write_audit
from app.services.aidp_username_service import refresh_account_usernames

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/production-dashboard", response_model=ProductionDashboardSummary)
def read_production_dashboard(db: Session = Depends(get_db)) -> ProductionDashboardSummary:
    return build_production_dashboard(db)


@router.post("/refresh-usernames", response_model=AccountUsernameRefreshResponse)
def refresh_real_usernames(db: Session = Depends(get_db)) -> AccountUsernameRefreshResponse:
    return refresh_account_usernames(db)


@router.post("/refresh-production", response_model=ProductionAccountRefreshResponse)
def refresh_accounts_production_data(db: Session = Depends(get_db)) -> ProductionAccountRefreshResponse:
    result = refresh_production_accounts(display_names=_db_display_names(db))
    write_audit(db, event_type="production_accounts_refresh", message=result.message, target_type="account")
    db.commit()
    return result


@router.get("/refresh-production/status", response_model=ProductionAutoRefreshStatusRead)
def read_production_refresh_status(request: Request) -> ProductionAutoRefreshStatusRead:
    scheduler = getattr(request.app.state, "production_auto_refresh_scheduler", None)
    if scheduler is None:
        return ProductionAutoRefreshStatusRead(enabled=False, message="8789 后台自刷新未启动。")
    status = scheduler.status
    return ProductionAutoRefreshStatusRead(
        enabled=status.enabled,
        running=status.running,
        run_count=status.run_count,
        last_ok=status.last_ok,
        last_error=status.last_error,
        last_started_at=status.last_started_at,
        last_finished_at=status.last_finished_at,
        interval_seconds=status.interval_seconds,
        next_run_at=status.next_run_at,
        message="8789 后台自刷新已启用。" if status.enabled else "8789 后台自刷新未启用。",
    )


@router.post("/{user_id}/refresh-production", response_model=ProductionAccountRefreshResponse)
def refresh_account_production_data(user_id: str, db: Session = Depends(get_db)) -> ProductionAccountRefreshResponse:
    try:
        result = refresh_production_account_by_user_id(user_id, display_names=_db_display_names(db))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(db, event_type="production_account_refresh", message=result.message, target_type="account", target_id=user_id)
    db.commit()
    return result


def _db_display_names(db: Session) -> dict[str, str]:
    return {account.user_id: account.display_name for account in list_accounts(db) if account.display_name}


@router.post("/{user_id}/open-target/{target}", response_model=BrowserOpenTargetResponse)
def open_account_target(user_id: str, target: str, db: Session = Depends(get_db)) -> BrowserOpenTargetResponse:
    if target not in {"task", "personal"}:
        raise HTTPException(status_code=400, detail="只能打开任务页或个人中心。")
    try:
        session = create_browser_open_session(user_id, target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    settings = get_settings()
    open_url = f"{settings.host_launcher_url.rstrip('/')}/api/open-with-cookie?monitorUrl={settings.public_base_url.rstrip('/')}&token={session['token']}"
    return BrowserOpenTargetResponse(ok=True, user_id=user_id, target=target, open_url=open_url, message="已生成对应账号 Cookie 的打开链接，请用本机助手打开独立窗口。")


@router.get("/deleted", response_model=list[DeletedProductionAccountRead])
def read_deleted_accounts() -> list[DeletedProductionAccountRead]:
    return list_deleted_accounts()


@router.post("/{user_id}/delete", response_model=AccountRecycleActionResponse)
def delete_real_account(user_id: str, db: Session = Depends(get_db)) -> AccountRecycleActionResponse:
    try:
        result = delete_account(db, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit(db, event_type="account_delete", message=result.message, target_type="account", target_id=user_id)
    db.commit()
    return result


@router.post("/{user_id}/restore", response_model=AccountRecycleActionResponse)
def restore_deleted_account(user_id: str, db: Session = Depends(get_db)) -> AccountRecycleActionResponse:
    try:
        result = restore_account(db, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit(db, event_type="account_restore", message=result.message, target_type="account", target_id=user_id)
    db.commit()
    return result






@router.get("/login-slots", response_model=list[AccountLoginSlotRead])
def read_login_slots(db: Session = Depends(get_db)) -> list[AccountLoginSlotRead]:
    return list_login_slots(db)


@router.post("/login-slots/new", response_model=AccountLoginSlotRead)
def create_login_slot(payload: Optional[AccountLoginSlotCreateRequest] = None, db: Session = Depends(get_db)) -> AccountLoginSlotRead:
    return create_new_login_slot(db, payload)


@router.post("/{user_id}/login-slots/relogin", response_model=AccountLoginSlotRead)
def create_account_relogin_slot(user_id: str, payload: Optional[AccountLoginSlotCreateRequest] = None, db: Session = Depends(get_db)) -> AccountLoginSlotRead:
    return create_relogin_slot(db, user_id, payload)


@router.post("/client-session", response_model=AccountClientSessionResponse)
def register_account_client_session(payload: AccountClientSessionRequest, db: Session = Depends(get_db)) -> AccountClientSessionResponse:
    try:
        return register_client_session(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.get("/task-coverage/summary", response_model=AccountCoverageSummaryResponse)
def read_task_coverage_summary(db: Session = Depends(get_db)) -> AccountCoverageSummaryResponse:
    return build_account_coverage_summary(db)


@router.get("/task-coverage/matrix", response_model=AccountCoverageSummaryResponse)
def read_task_coverage_matrix(db: Session = Depends(get_db)) -> AccountCoverageSummaryResponse:
    return build_account_coverage_summary(db)


@router.post("/task-coverage/baseline", response_model=AccountCoverageBaselineResponse)
def create_task_coverage_baseline(payload: AccountCoverageBaselineRequest, db: Session = Depends(get_db)) -> AccountCoverageBaselineResponse:
    return create_account_coverage_baseline(db, payload)

@router.get("", response_model=list[AccountRead])
def read_accounts(db: Session = Depends(get_db)) -> list[AccountRead]:
    return [AccountRead.model_validate(account_read_with_metadata(account)) for account in list_accounts(db) if account.status != AccountStatus.DISABLED]


@router.put("/{user_id}/metadata", response_model=AccountMetadataRead)
def update_account_custom_metadata(user_id: str, payload: AccountMetadataUpdate, db: Session = Depends(get_db)) -> AccountMetadataRead:
    try:
        result = update_account_metadata(db, user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    write_audit(db, event_type="account_metadata_update", message="Updated account custom name and note", target_type="account", target_id=user_id)
    db.commit()
    return result


@router.post("/refresh-health", response_model=list[AccountRead])
def refresh_accounts_health(db: Session = Depends(get_db)) -> list[AccountRead]:
    accounts = list_accounts(db)
    for account in accounts:
        refresh_account_health(account)
    write_audit(db, event_type="account_health_refresh", message="Refreshed account health", target_type="account")
    db.commit()
    return accounts


@router.get("/task-source", response_model=TaskSourceConfig)
def read_task_source_config() -> TaskSourceConfig:
    settings = get_settings()
    return TaskSourceConfig(task_source_account_user_id=settings.task_source_account_user_id)
