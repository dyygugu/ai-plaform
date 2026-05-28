import json
from pathlib import Path
from typing import Any, Optional

from app.core.settings import get_settings


def load_runtime_accounts() -> dict[str, dict[str, Any]]:
    accounts = {}
    accounts.update(load_production_state_accounts())
    accounts.update(load_session_accounts())
    return accounts


def load_runtime_account(user_id: str) -> Optional[dict[str, Any]]:
    return load_runtime_accounts().get(str(user_id or "").strip())


def load_session_accounts() -> dict[str, dict[str, Any]]:
    data = _load_json(_resolve_path(get_settings().session_accounts_path))
    accounts = data.get("accounts") if isinstance(data, dict) else data if isinstance(data, list) else []
    return _index_accounts(accounts)


def load_production_state_accounts() -> dict[str, dict[str, Any]]:
    data = _load_json(_resolve_path(get_settings().production_state_path))
    accounts = data.get("accounts") if isinstance(data, dict) else []
    return _index_accounts(accounts)


def load_production_state() -> dict[str, Any]:
    data = _load_json(_resolve_path(get_settings().production_state_path))
    return data if isinstance(data, dict) else {}


def _load_json(path: Path) -> Any:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path.cwd() / path


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


def _is_real_user_id(value: str) -> bool:
    return value.isdigit() and 12 <= len(value) <= 24
