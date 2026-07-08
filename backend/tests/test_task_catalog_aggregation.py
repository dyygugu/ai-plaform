import importlib
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.task import TaskCatalogItem
from app.services.task_service import list_task_catalog


def _create_app_with_reloaded_db():
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    for module_name in [
        "app.db.session",
        "app.db.init_db",
        "app.api.v1.routes.tasks",
        "app.api.v1.routes.accounts",
        "app.api.v1.router",
        "app.main",
    ]:
        importlib.reload(importlib.import_module(module_name))
    session_module = importlib.import_module("app.db.session")
    Base.metadata.create_all(session_module.engine)
    main_module = importlib.import_module("app.main")
    return main_module.create_app(), settings_module


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _task(source: str, task_id: str, pending_raw: str, name: str = "任务") -> TaskCatalogItem:
    return TaskCatalogItem(
        source_account_user_id=source,
        raw_task_name=f"{name} {task_id}",
        task_short_name=name,
        task_name_id=f"{name}{task_id}",
        task_id=task_id,
        pending_raw=pending_raw,
        task_status_raw="进行中",
    )


def test_default_catalog_lists_all_accounts_and_deduplicates_task_id() -> None:
    db = _session()
    try:
        db.add(_task("account-a", "task-1", "1", "重复任务"))
        db.add(_task("account-b", "task-1", "9", "重复任务"))
        db.add(_task("account-c", "task-2", "3", "独立任务"))
        db.commit()

        items = list_task_catalog(db)

        assert [item.task_id for item in items] == ["task-1", "task-2"]
        assert items[0].source_account_user_id == "account-b"
        assert items[0].pending_raw == "9"
    finally:
        db.close()


def test_explicit_source_catalog_keeps_single_account_debug_view() -> None:
    db = _session()
    try:
        db.add(_task("account-a", "task-1", "1", "重复任务"))
        db.add(_task("account-b", "task-1", "9", "重复任务"))
        db.add(_task("account-b", "task-2", "3", "独立任务"))
        db.commit()

        items = list_task_catalog(db, "account-b")

        assert [item.task_id for item in items] == ["task-1", "task-2"]
        assert {item.source_account_user_id for item in items} == {"account-b"}
    finally:
        db.close()


def test_catalog_api_defaults_to_all_accounts_when_task_source_is_empty() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmp = Path(tmpdir)
        os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{tmp / 'aidp-test.db'}"
        os.environ["AIDP_TASK_SOURCE_ACCOUNT_USER_ID"] = ""
        os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
        app, settings_module = _create_app_with_reloaded_db()

        try:
            with TestClient(app) as client:
                for source, task_id, pending_raw in [
                    ("account-a", "7630000000000000001", "1"),
                    ("account-b", "7630000000000000001", "9"),
                    ("account-b", "7630000000000000002", "3"),
                ]:
                    response = client.post(
                        "/api/v1/tasks/catalog/seed",
                        json={
                            "source_account_user_id": source,
                            "raw_task_name": f"任务 {task_id}",
                            "task_status_raw": "进行中",
                            "pending_raw": pending_raw,
                        },
                    )
                    assert response.status_code == 200, response.text

                catalog = client.get("/api/v1/tasks/catalog")

                assert catalog.status_code == 200, catalog.text
                assert [item["task_id"] for item in catalog.json()["items"]] == ["7630000000000000001", "7630000000000000002"]
                assert catalog.json()["items"][0]["source_account_user_id"] == "account-b"
        finally:
            settings_module.get_settings.cache_clear()


def test_catalog_api_backfills_from_production_dashboard_accounts() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmp = Path(tmpdir)
        state_path = tmp / "production-state.json"
        state_path.write_text(
            """{
  "accounts": [
    {
      "userId": "7630000000000000010",
      "name": "用户0010",
      "enabled": true,
      "cookie": "sessionid=a",
      "tasks": [
        {"id": "7630000000000000101", "name": "重复任务", "pending": 1},
        {"id": "7630000000000000102", "name": "独立任务", "pending": 3}
      ]
    },
    {
      "userId": "7630000000000000011",
      "name": "用户0011",
      "enabled": true,
      "cookie": "sessionid=b",
      "tasks": [
        {"id": "7630000000000000101", "name": "重复任务", "pending": 9}
      ]
    }
  ]
}""",
            encoding="utf-8",
        )
        os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{tmp / 'aidp-test.db'}"
        os.environ["AIDP_TASK_SOURCE_ACCOUNT_USER_ID"] = ""
        os.environ["AIDP_PRODUCTION_STATE_PATH"] = str(state_path)
        os.environ["AIDP_SESSION_ACCOUNTS_PATH"] = str(tmp / "session-accounts.json")
        os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
        app, settings_module = _create_app_with_reloaded_db()

        try:
            with TestClient(app) as client:
                catalog = client.get("/api/v1/tasks/catalog")

                assert catalog.status_code == 200, catalog.text
                assert [item["task_id"] for item in catalog.json()["items"]] == ["7630000000000000101", "7630000000000000102"]
                assert catalog.json()["items"][0]["source_account_user_id"] == "7630000000000000011"
        finally:
            settings_module.get_settings.cache_clear()
