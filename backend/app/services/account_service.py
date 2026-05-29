import json
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.paths import resolve_runtime_path
from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.schemas.account import AccountMetadataRead, AccountMetadataUpdate
from app.services.runtime_account_service import load_runtime_accounts


def ensure_default_task_source_account(db: Session) -> Optional[AidpAccount]:
    settings = get_settings()
    task_source_user_id = str(settings.task_source_account_user_id or "").strip()
    if not task_source_user_id:
        return None
    account = db.scalar(select(AidpAccount).where(AidpAccount.user_id == task_source_user_id))
    if account is None:
        account = AidpAccount(
            user_id=task_source_user_id,
            display_name="主账号",
            status=AccountStatus.STALE,
            is_task_source=True,
            auth_mode="client-cookie",
        )
        db.add(account)
        db.flush()
    elif not account.is_task_source:
        account.is_task_source = True
        db.flush()
    return account


def list_accounts(db: Session) -> list[AidpAccount]:
    ensure_default_task_source_account(db)
    _sync_runtime_accounts_to_db(db)
    return list(db.scalars(select(AidpAccount).order_by(AidpAccount.is_task_source.desc(), AidpAccount.user_id.asc())))


def _sync_runtime_accounts_to_db(db: Session) -> None:
    for user_id, runtime_account in load_runtime_accounts().items():
        account = db.scalar(select(AidpAccount).where(AidpAccount.user_id == user_id))
        display_name = _display_name(runtime_account, user_id)
        auth_mode = str(runtime_account.get("authMode") or ("client-cookie" if runtime_account.get("cookie") or runtime_account.get("hasCookie") else "unknown"))
        status = AccountStatus.ACTIVE if runtime_account.get("cookie") or runtime_account.get("hasCookie") or auth_mode == "client-cookie" else AccountStatus.STALE
        if account is None:
            db.add(AidpAccount(user_id=user_id, display_name=display_name, status=status, is_task_source=False, auth_mode=auth_mode))
            continue
        if account.status == AccountStatus.DISABLED:
            continue
        if display_name and (not account.display_name or account.display_name == account.user_id):
            account.display_name = display_name
        account.auth_mode = auth_mode
        if status == AccountStatus.ACTIVE:
            account.status = status
    db.flush()


def list_account_metadata() -> dict[str, dict[str, str]]:
    data = _load_metadata()
    result: dict[str, dict[str, str]] = {}
    for user_id, item in data.items():
        if not isinstance(item, dict):
            continue
        result[str(user_id)] = {
            "custom_name": str(item.get("custom_name") or "").strip(),
            "note": str(item.get("note") or "").strip(),
        }
    return result


def get_account_metadata(user_id: str) -> dict[str, str]:
    return list_account_metadata().get(user_id, {"custom_name": "", "note": ""})


def update_account_metadata(db: Session, user_id: str, payload: AccountMetadataUpdate) -> AccountMetadataRead:
    account = db.scalar(select(AidpAccount).where(AidpAccount.user_id == user_id))
    if account is None:
        raise ValueError("账号不存在，请先同步真实账号。")
    metadata = _load_metadata()
    metadata[user_id] = {
        "custom_name": payload.custom_name.strip(),
        "note": payload.note.strip(),
    }
    _write_metadata(metadata)
    return AccountMetadataRead(
        user_id=user_id,
        display_name=account.display_name or user_id,
        custom_name=metadata[user_id]["custom_name"],
        note=metadata[user_id]["note"],
        message="账号自定义名和备注已保存。",
    )


def account_read_with_metadata(account: AidpAccount) -> dict[str, Any]:
    metadata = get_account_metadata(account.user_id)
    return {
        "id": account.id,
        "user_id": account.user_id,
        "display_name": account.display_name,
        "custom_name": metadata.get("custom_name", ""),
        "note": metadata.get("note", ""),
        "status": account.status.value if hasattr(account.status, "value") else str(account.status),
        "is_task_source": account.is_task_source,
        "auth_mode": account.auth_mode,
        "last_health_at": account.last_health_at,
        "last_error": account.last_error,
    }


def _display_name(account: dict[str, Any], fallback: str) -> str:
    for key in ("authoritativeName", "name", "displayName", "customName"):
        value = str(account.get(key) or "").strip()
        if value:
            return value
    return fallback


def _load_metadata() -> dict[str, Any]:
    path = _metadata_path()
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_metadata(data: dict[str, Any]) -> None:
    path = _metadata_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _metadata_path() -> Path:
    value = get_settings().account_metadata_path
    return resolve_runtime_path(value)
