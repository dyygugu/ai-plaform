from pathlib import Path

from app.core.settings import get_settings
from app.db.base import Base
from app.models.account import AidpAccount
from app.services import account_recycle_service, account_service, login_slot_service, production_account_refresh_service, production_dashboard_service, runtime_account_service
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_runtime_account_paths_are_project_root_relative_when_cwd_is_backend(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(project_root / "backend")

    expected = project_root / "data" / "session-accounts.json"

    assert runtime_account_service._resolve_path("./data/session-accounts.json") == expected
    assert account_recycle_service._resolve_path("./data/session-accounts.json") == expected
    assert production_dashboard_service._resolve_path("./data/session-accounts.json") == expected
    assert login_slot_service._resolve_data_path("./data/session-accounts.json") == expected


def test_account_metadata_and_production_refresh_paths_are_project_root_relative(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(project_root / "backend")
    monkeypatch.setenv("AIDP_ACCOUNT_METADATA_PATH", "./data/account-metadata.json")
    monkeypatch.setenv("AIDP_PRODUCTION_STATE_PATH", "./data/production-state.json")
    get_settings.cache_clear()

    try:
        assert account_service._metadata_path() == project_root / "data" / "account-metadata.json"
        assert production_account_refresh_service._production_state_path() == project_root / "data" / "production-state.json"
    finally:
        get_settings.cache_clear()


def test_list_accounts_imports_runtime_accounts_when_database_has_no_accounts(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "production-state.json"
    session_path = tmp_path / "session-accounts.json"
    state_path.write_text(
        '{"accounts":[{"userId":"123456789012","name":"用户123","enabled":true,"authMode":"client-cookie","cookie":"redacted"}]}',
        encoding="utf-8",
    )
    session_path.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("AIDP_PRODUCTION_STATE_PATH", str(state_path))
    monkeypatch.setenv("AIDP_SESSION_ACCOUNTS_PATH", str(session_path))
    monkeypatch.setenv("AIDP_TASK_SOURCE_ACCOUNT_USER_ID", "")
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    try:
        accounts = account_service.list_accounts(db)
        db.commit()
        account_ids = [account.user_id for account in accounts]
        persisted = db.query(AidpAccount).filter_by(user_id="123456789012").one_or_none()
        persisted_display_name = persisted.display_name if persisted else None
    finally:
        db.close()
        get_settings.cache_clear()

    assert account_ids == ["123456789012"]
    assert persisted is not None
    assert persisted_display_name == "用户123"
