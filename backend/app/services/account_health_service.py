from typing import Optional

from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.services.task_rules import utc_now


def classify_account_health(account: AidpAccount) -> tuple[AccountStatus, str]:
    if account.last_error:
        return AccountStatus.NEEDS_LOGIN, account.last_error
    if account.is_task_source:
        return AccountStatus.STALE, "等待主账号任务页真实只读采集验证"
    return AccountStatus.STALE, "等待登录态健康检查"


def refresh_account_health(account: AidpAccount) -> AidpAccount:
    status, message = classify_account_health(account)
    account.status = status
    account.last_error = message
    account.last_health_at = utc_now()
    return account


def alert_source_account_if_needed(account: AidpAccount) -> Optional[str]:
    settings = get_settings()
    if account.user_id == settings.task_source_account_user_id and account.status == AccountStatus.NEEDS_LOGIN:
        return f"主任务来源账号 {account.user_id} 登录态异常：{account.last_error}"
    return None
