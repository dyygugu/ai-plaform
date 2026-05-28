import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.schemas.account import AccountMetadataRead, AccountMetadataUpdate


def ensure_default_task_source_account(db: Session) -> AidpAccount:
    settings = get_settings()
    account = db.scalar(select(AidpAccount).where(AidpAccount.user_id == settings.task_source_account_user_id))
    if account is None:
        account = AidpAccount(
            user_id=settings.task_source_account_user_id,
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
    return list(db.scalars(select(AidpAccount).order_by(AidpAccount.is_task_source.desc(), AidpAccount.user_id.asc())))


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
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path
