import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.paths import resolve_runtime_path
from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.schemas.production import ProductionAccountCard, ProductionDashboardSummary, ProductionIncomeItem, ProductionTaskStat
from app.services.account_service import list_account_metadata, list_accounts
from app.services.runtime_account_service import load_production_state, load_runtime_account, load_runtime_accounts, load_session_accounts
from app.services.task_rules import utc_now

TASK_PAGE_URL = "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1"
PERSONAL_CENTER_URL = "https://aidp.juejin.cn/operation/lite/setting/account/personal-center?org=AIDP%20Coding&tab=2"
OPEN_SESSION_FILE = "browser-open-sessions.json"


def build_production_dashboard(db: Session) -> ProductionDashboardSummary:
    settings = get_settings()
    runtime_accounts = load_runtime_accounts()
    monitor_state = _load_monitor_state()
    state_accounts = _index_accounts(monitor_state.get("accounts", []))
    db_accounts = _index_db_accounts(list_accounts(db))
    account_metadata = list_account_metadata()
    account_ids = sorted(set(runtime_accounts) | set(state_accounts) | set(db_accounts), key=lambda value: (value != settings.task_source_account_user_id, value))
    cards: list[ProductionAccountCard] = []
    for user_id in account_ids:
        if not _is_real_user_id(user_id):
            continue
        merged = {}
        merged.update(runtime_accounts.get(user_id, {}))
        state_account = state_accounts.get(user_id, {})
        db_account = db_accounts.get(user_id)
        card = _build_card(user_id, merged, state_account, db_account, account_metadata.get(user_id, {}))
        cards.append(card)
    pending_total = sum(item.pending_total for item in cards if not item.data_stale)
    delivered_total = sum(item.delivered_total for item in cards)
    abandoned_total = sum(item.abandoned_total for item in cards)
    processing_total = sum(item.processing_total for item in cards if not item.data_stale)
    in_progress_total = sum(item.in_progress_total for item in cards if not item.data_stale)
    repair_total = sum(item.repair_total for item in cards if not item.data_stale)
    stale_count = sum(1 for item in cards if item.data_stale)
    state_stale = _state_is_stale(monitor_state)
    global_stale = state_stale or stale_count > 0
    return ProductionDashboardSummary(
        generated_at=utc_now(),
        account_count=len(cards),
        active_account_count=sum(1 for item in cards if item.status == AccountStatus.ACTIVE.value),
        stale_account_count=stale_count,
        task_count=sum(item.task_count for item in cards),
        pending_total=pending_total,
        delivered_total=delivered_total,
        abandoned_total=abandoned_total,
        processing_total=processing_total,
        in_progress_total=in_progress_total,
        repair_total=repair_total,
        refresh_interval_minutes=int(monitor_state.get("refreshIntervalMinutes") or 15),
        last_refresh_started_at=_string_or_none(monitor_state.get("lastRefreshStartedAt")),
        last_refresh_finished_at=_string_or_none(monitor_state.get("lastRefreshFinishedAt")),
        next_refresh_at=_string_or_none(monitor_state.get("nextRefreshAt")),
        global_stale=global_stale,
        global_warning=_global_warning(state_stale, stale_count),
        task_source_account_user_id=settings.task_source_account_user_id,
        accounts=cards,
        support_pages=["任务目录", "告警", "运维", "备份", "上线验收"],
        message="多账号做题生产看板已按真实产品目标聚合账号、金额、任务和打开网页入口。",
    )


def create_browser_open_session(user_id: str, target: str) -> dict[str, Any]:
    account = load_runtime_account(user_id)
    if not account:
        raise ValueError("未找到该账号的 Cookie 运行数据，请先同步登录态。")
    cookie = str(account.get("cookie") or "")
    if not cookie:
        raise ValueError("该账号没有可注入 Cookie，请先重新登录并同步。")
    target_url = PERSONAL_CENTER_URL if target == "personal" else TASK_PAGE_URL
    token = uuid4().hex
    data = _read_open_session_store()
    data[token] = {
        "ok": True,
        "userId": user_id,
        "target": target,
        "targetUrl": target_url,
        "cookie": cookie,
        "createdAt": utc_now().isoformat(),
    }
    _write_open_session_store(data)
    return data[token] | {"token": token}


def get_browser_open_session(token: str) -> dict[str, Any]:
    data = _read_open_session_store()
    session = data.get(token)
    if not session:
        return {"ok": False, "error": "打开令牌不存在或已过期"}
    return session


def _build_card(user_id: str, account: dict[str, Any], state_account: dict[str, Any], db_account: Optional[AidpAccount], metadata: dict[str, str]) -> ProductionAccountCard:
    source = {}
    source.update(account)
    source.update(state_account)
    display_name = _display_name(user_id, (state_account, account, source), db_account)
    status = _status(source, db_account)
    auth_mode = str(source.get("authMode") or (db_account.auth_mode if db_account else "unknown") or "unknown")
    income_items = _income_items(source.get("income") if isinstance(source.get("income"), dict) else {})
    data_stale = _account_is_stale(source)
    tasks = [_task_stat(task, data_stale, source) for task in source.get("tasks", []) if isinstance(task, dict) and not task.get("hidden")]
    task_page_url = _task_page_url(source)
    personal_center_url = PERSONAL_CENTER_URL
    stale_reason = _stale_reason(source, data_stale)
    return ProductionAccountCard(
        user_id=user_id,
        display_name=display_name,
        custom_name=str(metadata.get("custom_name") or ""),
        note=str(metadata.get("note") or ""),
        real_name_ok=bool(_looks_like_real_user_name(display_name)),
        status=status,
        status_label=_status_label(status),
        auth_mode=auth_mode,
        auth_mode_label=_auth_mode_label(auth_mode),
        is_task_source=bool(db_account.is_task_source if db_account else False),
        cookie_synced=bool(source.get("cookie") or source.get("hasCookie") or auth_mode == "client-cookie"),
        data_stale=data_stale,
        stale_reason=stale_reason,
        last_refresh_at=_string_or_none(source.get("lastRefreshFinishedAt") or source.get("lastSyncedAt") or source.get("savedAt")),
        task_page_url=task_page_url,
        personal_center_url=personal_center_url,
        task_open_url=_host_open_url(user_id, "task"),
        personal_open_url=_host_open_url(user_id, "personal"),
        relogin_open_url=_host_profile_url(user_id, source),
        income_items=income_items,
        total_income=_income_value(source, "totalIncome"),
        current_month_income=_income_value(source, "currentMonthIncome"),
        withdrawable_amount=_income_value(source, "withdrawableAmount"),
        task_stats=tasks,
        task_count=len(tasks),
        delivered_total=sum(task.delivered for task in tasks),
        abandoned_total=sum(task.abandoned for task in tasks),
        processing_total=sum(task.processing for task in tasks),
        in_progress_total=sum(task.in_progress for task in tasks),
        repair_total=sum(task.repair for task in tasks),
        pending_total=0 if data_stale else sum(task.pending for task in tasks),
        warning=_warning(display_name, source, data_stale),
    )


def _load_session_accounts() -> dict[str, dict[str, Any]]:
    return load_session_accounts()


def _load_monitor_state() -> dict[str, Any]:
    state = load_production_state()
    return state if state.get("accounts") else {}


def _read_open_session_store() -> dict[str, Any]:
    path = _open_session_path()
    data = _load_json(path)
    return data if isinstance(data, dict) else {}


def _write_open_session_store(data: dict[str, Any]) -> None:
    path = _open_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _open_session_path() -> Path:
    session_path = _resolve_path(get_settings().session_accounts_path)
    return session_path.with_name(OPEN_SESSION_FILE)


def _load_json(path: Path) -> Any:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _resolve_path(value: str) -> Path:
    return resolve_runtime_path(value)


def _index_accounts(accounts: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(accounts, list):
        return result
    for account in accounts:
        if not isinstance(account, dict):
            continue
        user_id = str(account.get("userId") or account.get("user_id") or "").strip()
        if _is_real_user_id(user_id) and account.get("enabled", True) is not False:
            result[user_id] = account
    return result


def _index_db_accounts(accounts: list[AidpAccount]) -> dict[str, AidpAccount]:
    result = {}
    for account in accounts:
        if _is_real_user_id(account.user_id) and account.status != AccountStatus.DISABLED:
            result[account.user_id] = account
    return result


def _is_real_user_id(value: str) -> bool:
    return value.isdigit() and 12 <= len(value) <= 24


def _display_name(user_id: str, sources: tuple[dict[str, Any], ...], db_account: Optional[AidpAccount]) -> str:
    for source in sources:
        for key in ("authoritativeName", "name", "displayName", "customName"):
            value = str(source.get(key) or "").strip()
            if value and not _is_placeholder_display_name(value):
                return value
    if db_account and db_account.display_name and not _is_placeholder_display_name(db_account.display_name):
        return db_account.display_name
    return "未同步真实用户名"


def _is_placeholder_display_name(value: str) -> bool:
    text = value.strip()
    lower = text.lower()
    return (
        not text
        or lower.startswith(("account-", "new account", "pending-"))
        or text.startswith(("账号-", "主账号", "新账号待登录"))
        or text == "未命名账号"
    )


def _looks_like_real_user_name(value: str) -> bool:
    return value.startswith("用户") and any(char.isdigit() for char in value)


def _status(source: dict[str, Any], db_account: Optional[AidpAccount]) -> str:
    if _has_successful_login_state(source):
        return AccountStatus.ACTIVE.value
    if source.get("error") or source.get("needsRelogin"):
        return AccountStatus.NEEDS_LOGIN.value
    if source.get("cookie") or source.get("hasCookie") or str(source.get("authMode") or "") == "client-cookie":
        return AccountStatus.ACTIVE.value
    if db_account:
        return db_account.status.value if hasattr(db_account.status, "value") else str(db_account.status)
    return AccountStatus.STALE.value


def _status_label(status: str) -> str:
    return {"active": "已登录", "needs_login": "需重新登录", "stale": "待刷新", "disabled": "已停用"}.get(status, status)


def _has_successful_login_state(source: dict[str, Any]) -> bool:
    if source.get("error"):
        return False
    has_cookie = bool(source.get("cookie") or source.get("hasCookie") or str(source.get("authMode") or "") == "client-cookie")
    if not has_cookie:
        return False
    return bool(source.get("loginOk") is True or source.get("refreshStatus") == "ok")


def _auth_mode_label(value: str) -> str:
    labels = {"client-cookie": "Cookie 已同步", "local-profile-pending": "待登录", "local-profile-bound": "已绑定登录会话", "unknown": "未知"}
    return labels.get(value, value or "未知")


def _income_items(income: dict[str, Any]) -> list[ProductionIncomeItem]:
    names = [("totalIncome", "总收入"), ("currentMonthIncome", "本月收入"), ("lastMonthIncome", "上月收入"), ("withdrawableAmount", "可提现"), ("afterTaxWithdrawableAmount", "税后可提现")]
    return [ProductionIncomeItem(name=name, value=str(income.get(key) or "0.00"), source=str(income.get("source") or "")) for key, name in names]


def _income_value(source: dict[str, Any], key: str) -> str:
    income = source.get("income") if isinstance(source.get("income"), dict) else {}
    return str(income.get(key) or "0.00")


def _task_stat(task: dict[str, Any], stale: bool, source: Optional[dict[str, Any]] = None) -> ProductionTaskStat:
    source = source or {}
    progress = task.get("frontendProgress") if isinstance(task.get("frontendProgress"), dict) else {}
    delivered = _num(task.get("aiDelivery"), progress.get("submittedCount"), task.get("delivery"), task.get("delivered"))
    abandoned = _num(task.get("aiAbandoned"), progress.get("abandonedCount"), task.get("abandoned"), task.get("deprecated"))
    category = task.get("frontendSubmittedCategory") if isinstance(task.get("frontendSubmittedCategory"), dict) else {}
    category_current = (task.get("frontendCategoryTotalMap") or {}).get("0") if isinstance(task.get("frontendCategoryTotalMap"), dict) else None
    processing = 0
    if not stale and not category.get("error") and ("frontendNotSubmitted" in task or category_current is not None):
        processing = _num(task.get("frontendNotSubmitted"), category_current)
    elif not stale:
        processing = _num(task.get("processing"), task.get("personalProcessing"))
    in_progress = 0 if stale else _num(progress.get("inProgressCount"), task.get("aiInProgress"))
    repair = 0 if stale else _num(task.get("frontendRepairCount"), task.get("repair"), task.get("modify"))
    pending = 0 if stale else _num(task.get("poolPendingSubmit"), task.get("pending"), task.get("todo"))
    task_id = str(task.get("id") or task.get("taskId") or task.get("TaskID") or "")
    receive_enabled = _task_receive_enabled(task)
    operation_url_ok = _operation_url_is_task_page(str(source.get("operationUrl") or source.get("referer") or ""))
    has_current_item = processing > 0 or repair > 0
    auto_receive_ready = bool(not stale and (has_current_item or pending > 0))
    return ProductionTaskStat(
        task_id=task_id,
        task_name=str(task.get("name") or task.get("title") or task_id or "未命名任务"),
        delivered=delivered,
        abandoned=abandoned,
        processing=processing,
        in_progress=in_progress,
        repair=repair,
        pending=pending,
        source=str(task.get("source") or task.get("status") or "http"),
        stale=stale,
        stale_reason="旧缓存待处理数已隐藏，等待真实 HTTP 刷新确认" if stale else "",
        receive_enabled=receive_enabled,
        operation_url_ok=operation_url_ok,
        auto_receive_ready=auto_receive_ready,
        auto_receive_block_reason=_auto_receive_block_reason(stale=stale, operation_url_ok=operation_url_ok, has_current_item=has_current_item, pending=pending),
        error=str(task.get("error") or "") or None,
    )


def _task_receive_enabled(task: dict[str, Any]) -> bool:
    category = task.get("frontendSubmittedCategory") if isinstance(task.get("frontendSubmittedCategory"), dict) else {}
    if "receiveEnable" in category:
        return bool(category.get("receiveEnable"))
    return bool(task.get("receiveEnable"))


def _operation_url_is_task_page(operation_url: str) -> bool:
    return "/operation/task-v2" in str(operation_url or "")


def _auto_receive_block_reason(*, stale: bool, operation_url_ok: bool, has_current_item: bool, pending: int) -> str:
    if stale:
        return "账号任务数据未完成真实刷新，先刷新后再判断是否可自动领题。"
    if has_current_item:
        return ""
    if pending > 0:
        return "启动时会先自动点击“处理”领取 1 道题，再继续自动做题。"
    if not operation_url_ok:
        return "账号 operationUrl 未指向任务页，当前不能自动领题。"
    return ""


def _num(*values: Any) -> int:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(float(str(value).replace(",", "").strip()))
        except ValueError:
            continue
    return 0


def _task_page_url(source: dict[str, Any]) -> str:
    operation_url = str(source.get("operationUrl") or source.get("referer") or "")
    if "/operation/task-v2" in operation_url:
        return operation_url
    return TASK_PAGE_URL


def _host_open_url(user_id: str, target: str) -> str:
    settings = get_settings()
    token_seed = f"{user_id}:{target}"
    # token is created lazily by /api/v1/accounts/{user_id}/open-target/{target}; this URL is the user-facing action endpoint.
    return f"{settings.public_base_url.rstrip('/')}/api/v1/accounts/{quote(user_id)}/open-target/{quote(target)}"


def _host_profile_url(user_id: str, source: dict[str, Any]) -> str:
    settings = get_settings()
    port = _num(source.get("cdpPort")) or 9323 + (sum(ord(char) for char in user_id) % 100)
    return f"{settings.host_launcher_url.rstrip('/')}/api/open-profile?userId={quote(user_id)}&port={port}"


def _account_is_stale(source: dict[str, Any]) -> bool:
    if source.get("error"):
        return True
    if _has_successful_http_task_counts(source):
        return False
    return bool(source.get("stale") or source.get("refreshStatus") == "restored")


def _has_successful_http_task_counts(source: dict[str, Any]) -> bool:
    if str(source.get("source") or "") != "http":
        return False
    tasks = [task for task in source.get("tasks", []) if isinstance(task, dict) and not task.get("hidden")]
    if not tasks:
        return False
    for task in tasks:
        if task.get("error"):
            return False
        task_source = str(task.get("source") or task.get("status") or "")
        category = task.get("frontendSubmittedCategory") if isinstance(task.get("frontendSubmittedCategory"), dict) else {}
        progress = task.get("frontendProgress") if isinstance(task.get("frontendProgress"), dict) else {}
        category_source = str(category.get("source") or "")
        progress_source = str(progress.get("source") or "")
        if "http" not in task_source:
            return False
        if category.get("error") or progress.get("error"):
            return False
        if "http" not in category_source and "http" not in progress_source:
            return False
    return True


def _state_is_stale(state: dict[str, Any]) -> bool:
    if not state:
        return False
    finished = str(state.get("lastRefreshFinishedAt") or "")
    if not finished:
        return True
    try:
        dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (utc_now() - dt).total_seconds() > 3600
    except ValueError:
        return True


def _global_warning(state_stale: bool, stale_account_count: int) -> str:
    if stale_account_count > 0:
        return "当前含旧缓存数据：真实刷新失败时，不把旧待处理数当作当前真实剩余题量。"
    if state_stale:
        return "生产刷新时间过旧；账号任务卡片已按任务级 HTTP 成功响应展示，请刷新全部账号数据校准。"
    return ""


def _stale_reason(source: dict[str, Any], stale: bool) -> str:
    if not stale:
        return ""
    if source.get("error"):
        return str(source.get("error"))
    if source.get("refreshStatus") == "restored":
        return "刷新失败后恢复的旧缓存，不代表当前真实待处理。"
    return "旧缓存或刷新时间过旧，请执行真实 HTTP 刷新。"


def _warning(display_name: str, source: dict[str, Any], stale: bool) -> str:
    warnings = []
    if not _looks_like_real_user_name(display_name):
        warnings.append("未同步个人中心真实用户名")
    if stale:
        warnings.append("任务数字可能是旧缓存")
    if source.get("needsRelogin") and not _has_successful_login_state(source):
        warnings.append("需要重新登录")
    return "；".join(warnings)


def _string_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None
