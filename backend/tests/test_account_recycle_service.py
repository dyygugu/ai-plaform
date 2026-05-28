import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.settings import get_settings
from app.db.base import Base
from app.models.account import AccountStatus, AidpAccount
from app.services.account_recycle_service import delete_account, list_deleted_accounts, restore_account
from app.services.production_dashboard_service import build_production_dashboard
from app.services.runtime_account_service import load_runtime_account


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _configure_paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    state_path = tmp_path / "production-state.json"
    session_path = tmp_path / "session-accounts.json"
    monkeypatch.setenv("AIDP_PRODUCTION_STATE_PATH", str(state_path))
    monkeypatch.setenv("AIDP_SESSION_ACCOUNTS_PATH", str(session_path))
    get_settings.cache_clear()
    return state_path, session_path


def test_delete_account_archives_without_removing_cookie_or_profile(tmp_path: Path, monkeypatch) -> None:
    state_path, session_path = _configure_paths(tmp_path, monkeypatch)
    user_id = "123456789012"
    state_path.write_text(
        json.dumps({"accounts": [{"userId": user_id, "name": "用户123", "cookie": "state-cookie", "profileDir": "profile-a", "tasks": [{"id": "task-1"}]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    session_path.write_text(json.dumps({"accounts": [{"userId": user_id, "cookie": "session-cookie", "profileDir": "profile-a"}]}, ensure_ascii=False), encoding="utf-8")
    db = _session()
    db.add(AidpAccount(user_id=user_id, display_name="用户123", status=AccountStatus.ACTIVE, auth_mode="client-cookie"))
    db.commit()

    try:
        result = delete_account(db, user_id)
        db.commit()
        deleted = list_deleted_accounts()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        db_account = db.query(AidpAccount).filter_by(user_id=user_id).one()
    finally:
        db.close()
        get_settings.cache_clear()

    assert result.ok is True
    assert state["accounts"] == []
    assert session["accounts"] == []
    assert deleted[0].user_id == user_id
    assert deleted[0].cookie_preserved is True
    assert deleted[0].profile_preserved is True
    assert state["deleted_accounts"][0]["cookie"] == "session-cookie"
    assert state["deleted_accounts"][0]["profileDir"] == "profile-a"
    assert db_account.status == AccountStatus.DISABLED


def test_restore_account_moves_deleted_record_back_to_active_without_auto_run(tmp_path: Path, monkeypatch) -> None:
    state_path, session_path = _configure_paths(tmp_path, monkeypatch)
    user_id = "123456789012"
    state_path.write_text(
        json.dumps(
            {
                "accounts": [],
                "deleted_accounts": [
                    {
                        "userId": user_id,
                        "name": "用户123",
                        "cookie": "session-cookie",
                        "profileDir": "profile-a",
                        "deletedAt": "2026-05-25T00:00:00+00:00",
                        "source": "account-recycle",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_path.write_text(json.dumps({"accounts": []}, ensure_ascii=False), encoding="utf-8")
    db = _session()
    db.add(AidpAccount(user_id=user_id, display_name="用户123", status=AccountStatus.DISABLED, auth_mode="client-cookie"))
    db.commit()

    try:
        result = restore_account(db, user_id)
        db.commit()
        restored = load_runtime_account(user_id)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        db_account = db.query(AidpAccount).filter_by(user_id=user_id).one()
    finally:
        db.close()
        get_settings.cache_clear()

    assert result.ok is True
    assert restored is not None
    assert restored["cookie"] == "session-cookie"
    assert restored["enabled"] is True
    assert "deleted_accounts" in state
    assert state["deleted_accounts"] == []
    assert db_account.status == AccountStatus.STALE


def test_deleted_account_is_excluded_from_production_dashboard(tmp_path: Path, monkeypatch) -> None:
    state_path, session_path = _configure_paths(tmp_path, monkeypatch)
    user_id = "123456789012"
    state_path.write_text(json.dumps({"accounts": [{"userId": user_id, "name": "用户123", "cookie": "state-cookie"}]}, ensure_ascii=False), encoding="utf-8")
    session_path.write_text(json.dumps({"accounts": []}, ensure_ascii=False), encoding="utf-8")
    db = _session()
    db.add(AidpAccount(user_id=user_id, display_name="用户123", status=AccountStatus.ACTIVE, auth_mode="client-cookie"))
    db.commit()

    try:
        delete_account(db, user_id)
        db.commit()
        dashboard = build_production_dashboard(db)
    finally:
        db.close()
        get_settings.cache_clear()

    assert all(account.user_id != user_id for account in dashboard.accounts)
