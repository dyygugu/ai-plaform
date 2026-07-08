import json
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.paths import resolve_runtime_path
from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.models.task import TaskCatalogItem, TaskVisibility
from app.schemas.production import AccountRecycleActionResponse, DeletedProductionAccountRead
from app.services.task_rules import utc_now


DELETED_ACCOUNTS_KEY = "deleted_accounts"


def list_deleted_accounts(db: Optional[Session] = None) -> list[DeletedProductionAccountRead]:
    seen: set[str] = set()
    items: list[DeletedProductionAccountRead] = []
    for store in (_load_store(_production_state_path()), _load_store(_session_accounts_path())):
        for account in _as_list(store.get(DELETED_ACCOUNTS_KEY)):
            user_id = _account_user_id(account)
            if not user_id or user_id in seen:
                continue
            seen.add(user_id)
            items.append(_deleted_read(account))
    if db is not None:
        for account in db.scalars(select(AidpAccount).where(AidpAccount.status == AccountStatus.DISABLED).order_by(AidpAccount.updated_at.desc(), AidpAccount.id.desc())):
            if account.user_id in seen:
                continue
            seen.add(account.user_id)
            items.append(
                DeletedProductionAccountRead(
                    user_id=account.user_id,
                    display_name=account.display_name or account.user_id,
                    deleted_at=account.updated_at.isoformat() if account.updated_at else None,
                    delete_reason=account.last_error or "数据库中该账号已停用。",
                    cookie_preserved=False,
                    profile_preserved=False,
                )
            )
    return sorted(items, key=lambda item: item.deleted_at or "", reverse=True)


def delete_account(db: Session, user_id: str) -> AccountRecycleActionResponse:
    normalized = _normalize_user_id(user_id)
    production_path = _production_state_path()
    session_path = _session_accounts_path()
    production_store = _load_store(production_path)
    session_store = _load_store(session_path)

    state_account = _remove_active_account(production_store, normalized)
    session_account = _remove_active_account(session_store, normalized)
    existing_deleted = _remove_deleted_account(production_store, normalized) or _remove_deleted_account(session_store, normalized)
    db_account = db.scalar(select(AidpAccount).where(AidpAccount.user_id == normalized))
    source_account = _merge_accounts(existing_deleted, state_account, session_account, _db_account_snapshot(db_account))
    if source_account is None:
        raise ValueError("账号不存在或已删除。")

    archived = dict(source_account)
    archived["userId"] = normalized
    archived["enabled"] = False
    archived["deletedAt"] = utc_now().isoformat()
    archived["deletedBy"] = "monitor-platform"
    archived["deleteReason"] = "用户在真实账号列表执行删除"
    archived["source"] = "account-recycle"
    production_store.setdefault(DELETED_ACCOUNTS_KEY, []).append(archived)

    if db_account is not None:
        db_account.status = AccountStatus.DISABLED
    hidden_task_count = _hide_recycled_account_tasks(db, normalized)
    db.flush()

    _write_store(production_path, production_store)
    _write_store(session_path, session_store)
    return AccountRecycleActionResponse(ok=True, user_id=normalized, message=f"账号已移入回收站，Cookie 和本机 profile 未清理；已隐藏该账号任务目录 {hidden_task_count} 条。")


def restore_account(db: Session, user_id: str) -> AccountRecycleActionResponse:
    normalized = _normalize_user_id(user_id)
    production_path = _production_state_path()
    session_path = _session_accounts_path()
    production_store = _load_store(production_path)
    session_store = _load_store(session_path)

    deleted = _remove_deleted_account(production_store, normalized) or _remove_deleted_account(session_store, normalized)
    if deleted is None:
        raise ValueError("回收站中未找到该账号。")

    restored = dict(deleted)
    for key in ("deletedAt", "deletedBy", "deleteReason"):
        restored.pop(key, None)
    _clear_restored_account_runtime_cache(restored)
    restored["userId"] = normalized
    restored["enabled"] = True
    restored["stale"] = True
    restored["restoredAt"] = utc_now().isoformat()
    restored["refreshStatus"] = "restored"
    _upsert_active_account(session_store, restored)

    db_account = db.scalar(select(AidpAccount).where(AidpAccount.user_id == normalized))
    if db_account is None:
        db_account = AidpAccount(
            user_id=normalized,
            display_name=_display_name(restored, normalized),
            status=AccountStatus.STALE,
            auth_mode=str(restored.get("authMode") or ("client-cookie" if restored.get("cookie") else "unknown")),
        )
        db.add(db_account)
    else:
        db_account.status = AccountStatus.STALE
        if not db_account.display_name:
            db_account.display_name = _display_name(restored, normalized)
    restored_task_count = _restore_recycled_account_tasks(db, normalized)
    db.flush()

    _write_store(production_path, production_store)
    _write_store(session_path, session_store)
    return AccountRecycleActionResponse(ok=True, user_id=normalized, message=f"账号已从回收站恢复，请按需刷新账号数据；已恢复任务目录 {restored_task_count} 条为待校准状态。")


def _hide_recycled_account_tasks(db: Session, user_id: str) -> int:
    rows = list(db.scalars(select(TaskCatalogItem).where(TaskCatalogItem.source_account_user_id == user_id)))
    for row in rows:
        row.visibility = TaskVisibility.HIDDEN
        row.last_task_page_error = "来源账号已移入回收站；该账号独有任务默认隐藏，恢复账号或重新刷新后再参与展示。"
    return len(rows)


def _restore_recycled_account_tasks(db: Session, user_id: str) -> int:
    rows = list(db.scalars(select(TaskCatalogItem).where(TaskCatalogItem.source_account_user_id == user_id)))
    for row in rows:
        if row.visibility == TaskVisibility.HIDDEN:
            row.visibility = TaskVisibility.RESTORED
        row.pending_raw = ""
        row.task_status_raw = "待刷新"
        row.last_task_page_seen_at = None
        row.last_task_page_error = "账号已从回收站恢复；请刷新生产数据后确认当前真实待处理。"
    return len(rows)


def _clear_restored_account_runtime_cache(account: dict[str, Any]) -> None:
    for key in (
        "tasks",
        "taskStats",
        "task_stats",
        "taskSummary",
        "income",
        "lastRefreshStartedAt",
        "lastRefreshFinishedAt",
        "lastSyncedAt",
        "nextRefreshAt",
        "savedAt",
        "lastTaskPageSeenAt",
        "lastTaskPageError",
        "refreshError",
        "error",
    ):
        account.pop(key, None)


def _load_store(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {"accounts": []}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"accounts": []}
    if isinstance(data, list):
        return {"accounts": data}
    if isinstance(data, dict):
        data.setdefault("accounts", [])
        return data
    return {"accounts": []}


def _write_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _remove_active_account(store: dict[str, Any], user_id: str) -> Optional[dict[str, Any]]:
    accounts = _as_list(store.get("accounts"))
    kept: list[Any] = []
    removed: Optional[dict[str, Any]] = None
    for account in accounts:
        if isinstance(account, dict) and _account_user_id(account) == user_id:
            removed = dict(account)
            continue
        kept.append(account)
    store["accounts"] = kept
    return removed


def _remove_deleted_account(store: dict[str, Any], user_id: str) -> Optional[dict[str, Any]]:
    accounts = _as_list(store.get(DELETED_ACCOUNTS_KEY))
    kept: list[Any] = []
    removed: Optional[dict[str, Any]] = None
    for account in accounts:
        if isinstance(account, dict) and _account_user_id(account) == user_id:
            removed = dict(account)
            continue
        kept.append(account)
    store[DELETED_ACCOUNTS_KEY] = kept
    return removed


def _upsert_active_account(store: dict[str, Any], account: dict[str, Any]) -> None:
    user_id = _account_user_id(account)
    accounts = [item for item in _as_list(store.get("accounts")) if not (isinstance(item, dict) and _account_user_id(item) == user_id)]
    accounts.append(account)
    store["accounts"] = accounts


def _merge_accounts(*accounts: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    merged: dict[str, Any] = {}
    found = False
    for account in accounts:
        if account:
            merged.update(account)
            found = True
    return merged if found else None


def _db_account_snapshot(account: Optional[AidpAccount]) -> Optional[dict[str, Any]]:
    if account is None or account.status == AccountStatus.DISABLED:
        return None
    return {
        "userId": account.user_id,
        "name": account.display_name,
        "displayName": account.display_name,
        "authMode": account.auth_mode,
    }


def _deleted_read(account: dict[str, Any]) -> DeletedProductionAccountRead:
    user_id = _account_user_id(account)
    return DeletedProductionAccountRead(
        user_id=user_id,
        display_name=_display_name(account, user_id),
        deleted_at=str(account.get("deletedAt") or "") or None,
        delete_reason=str(account.get("deleteReason") or ""),
        cookie_preserved=bool(account.get("cookie") or account.get("hasCookie")),
        profile_preserved=bool(account.get("profileDir") or account.get("profilePath") or account.get("userDataDir")),
    )


def _display_name(account: dict[str, Any], fallback: str) -> str:
    for key in ("authoritativeName", "name", "displayName", "customName"):
        value = str(account.get(key) or "").strip()
        if value:
            return value
    return fallback


def _account_user_id(account: dict[str, Any]) -> str:
    return str(account.get("userId") or account.get("user_id") or "").strip()


def _normalize_user_id(user_id: str) -> str:
    normalized = str(user_id or "").strip()
    if not normalized:
        raise ValueError("账号 ID 不能为空。")
    return normalized


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _production_state_path() -> Path:
    return _resolve_path(get_settings().production_state_path)


def _session_accounts_path() -> Path:
    return _resolve_path(get_settings().session_accounts_path)


def _resolve_path(value: str) -> Path:
    return resolve_runtime_path(value)
