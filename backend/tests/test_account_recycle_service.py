import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.settings import get_settings
from app.db.base import Base
from app.models.account import AccountStatus, AidpAccount
from app.models.task import TaskCatalogItem, TaskVisibility
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
                        "tasks": [{"id": "task-old", "name": "旧缓存任务", "pending": 99}],
                        "lastRefreshFinishedAt": "2026-05-25T00:00:00+00:00",
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
    db.add(
        TaskCatalogItem(
            source_account_user_id=user_id,
            raw_task_name="旧缓存任务 task-old",
            task_short_name="旧缓存任务",
            task_id="task-old",
            task_name_id="旧缓存任务task-old",
            pending_raw="99",
            task_status_raw="进行中",
            visibility=TaskVisibility.HIDDEN,
            last_task_page_seen_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
        )
    )
    db.commit()

    try:
        result = restore_account(db, user_id)
        db.commit()
        restored = load_runtime_account(user_id)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        db_account = db.query(AidpAccount).filter_by(user_id=user_id).one()
        catalog_row = db.query(TaskCatalogItem).filter_by(source_account_user_id=user_id, task_id="task-old").one()
    finally:
        db.close()
        get_settings.cache_clear()

    assert result.ok is True
    assert restored is not None
    assert restored["cookie"] == "session-cookie"
    assert restored["enabled"] is True
    assert restored["refreshStatus"] == "restored"
    assert restored["stale"] is True
    assert "tasks" not in restored
    assert "lastRefreshFinishedAt" not in restored
    assert "deleted_accounts" in state
    assert state["deleted_accounts"] == []
    assert db_account.status == AccountStatus.STALE
    assert catalog_row.visibility == TaskVisibility.RESTORED
    assert catalog_row.pending_raw == ""
    assert catalog_row.task_status_raw == "待刷新"
    assert catalog_row.last_task_page_seen_at is None
    assert "刷新生产数据" in (catalog_row.last_task_page_error or "")


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


def test_delete_account_hides_task_catalog_rows_for_recycled_account(tmp_path: Path, monkeypatch) -> None:
    state_path, session_path = _configure_paths(tmp_path, monkeypatch)
    user_id = "123456789012"
    state_path.write_text(json.dumps({"accounts": [{"userId": user_id, "name": "用户123", "cookie": "state-cookie"}]}, ensure_ascii=False), encoding="utf-8")
    session_path.write_text(json.dumps({"accounts": []}, ensure_ascii=False), encoding="utf-8")
    db = _session()
    db.add(AidpAccount(user_id=user_id, display_name="用户123", status=AccountStatus.ACTIVE, auth_mode="client-cookie"))
    db.add(
        TaskCatalogItem(
            source_account_user_id=user_id,
            raw_task_name="独有任务 task-1",
            task_short_name="独有任务",
            task_id="task-1",
            task_name_id="独有任务task-1",
            pending_raw="8",
            task_status_raw="进行中",
        )
    )
    db.commit()

    try:
        delete_account(db, user_id)
        db.commit()
        row = db.query(TaskCatalogItem).filter(TaskCatalogItem.source_account_user_id == user_id).one()
    finally:
        db.close()
        get_settings.cache_clear()

    assert row.visibility == TaskVisibility.HIDDEN
    assert "回收站" in (row.last_task_page_error or "")


def test_recycle_bin_includes_disabled_db_accounts_when_json_archive_is_missing(tmp_path: Path, monkeypatch) -> None:
    state_path, session_path = _configure_paths(tmp_path, monkeypatch)
    user_id = "123456789012"
    state_path.write_text(json.dumps({"accounts": []}, ensure_ascii=False), encoding="utf-8")
    session_path.write_text(json.dumps({"accounts": [], "deleted_accounts": []}, ensure_ascii=False), encoding="utf-8")
    db = _session()
    db.add(AidpAccount(user_id=user_id, display_name="用户123", status=AccountStatus.DISABLED, auth_mode="client-cookie"))
    db.commit()

    try:
        deleted = list_deleted_accounts(db)
    finally:
        db.close()
        get_settings.cache_clear()

    assert [item.user_id for item in deleted] == [user_id]
