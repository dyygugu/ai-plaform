import json
from pathlib import Path

from app.core.settings import get_settings
from app.services.runtime_account_service import load_runtime_account, load_runtime_accounts


def test_runtime_accounts_use_production_state_without_legacy(tmp_path: Path, monkeypatch) -> None:
    state_path = tmp_path / "production-state.json"
    session_path = tmp_path / "session-accounts.json"
    state_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "userId": "123456789012",
                        "name": "用户123456789012",
                        "cookie": "state-cookie",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_path.write_text(json.dumps({"accounts": []}), encoding="utf-8")
    monkeypatch.setenv("AIDP_PRODUCTION_STATE_PATH", str(state_path))
    monkeypatch.setenv("AIDP_SESSION_ACCOUNTS_PATH", str(session_path))
    get_settings.cache_clear()

    try:
        accounts = load_runtime_accounts()
    finally:
        get_settings.cache_clear()

    assert accounts["123456789012"]["cookie"] == "state-cookie"


def test_runtime_session_account_overrides_state_cookie(tmp_path: Path, monkeypatch) -> None:
    state_path = tmp_path / "production-state.json"
    session_path = tmp_path / "session-accounts.json"
    state_path.write_text(json.dumps({"accounts": [{"userId": "123456789012", "cookie": "old-cookie"}]}), encoding="utf-8")
    session_path.write_text(json.dumps({"accounts": [{"userId": "123456789012", "cookie": "new-cookie"}]}), encoding="utf-8")
    monkeypatch.setenv("AIDP_PRODUCTION_STATE_PATH", str(state_path))
    monkeypatch.setenv("AIDP_SESSION_ACCOUNTS_PATH", str(session_path))
    get_settings.cache_clear()

    try:
        account = load_runtime_account("123456789012")
    finally:
        get_settings.cache_clear()

    assert account is not None
    assert account["cookie"] == "new-cookie"


def test_settings_do_not_expose_legacy_account_path(monkeypatch) -> None:
    monkeypatch.delenv("AIDP_LEGACY_ACCOUNTS_PATH", raising=False)
    get_settings.cache_clear()

    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert not hasattr(settings, "legacy_accounts_path")
