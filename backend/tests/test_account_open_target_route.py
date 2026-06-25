import importlib
import json
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.core.settings import get_settings


def test_open_target_uses_public_monitor_url_and_encodes_query(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "production-state.json"
    session_path = tmp_path / "session-accounts.json"
    db_path = tmp_path / "aidp.db"
    user_id = "7592445681051717403"
    session_path.write_text(
        json.dumps({"accounts": [{"userId": user_id, "cookie": "sessionid=redacted", "enabled": True}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    state_path.write_text(json.dumps({"accounts": []}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AIDP_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AIDP_PRODUCTION_STATE_PATH", str(state_path))
    monkeypatch.setenv("AIDP_SESSION_ACCOUNTS_PATH", str(session_path))
    monkeypatch.setenv("AIDP_PUBLIC_BASE_URL", "http://192.168.10.149:8789")
    monkeypatch.setenv("AIDP_HOST_LAUNCHER_URL", "http://127.0.0.1:8790")
    monkeypatch.setenv("AIDP_PRODUCTION_AUTO_REFRESH_ENABLED", "false")
    get_settings.cache_clear()

    try:
        main_module = importlib.import_module("app.main")
        app = main_module.create_app()
        with TestClient(app) as client:
            response = client.post(f"/api/v1/accounts/{user_id}/open-target/task")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    open_url = response.json()["open_url"]
    parsed = urlparse(open_url)
    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:8790"
    assert "monitorUrl=http%3A%2F%2F192.168.10.149%3A8789" in open_url
    assert parse_qs(parsed.query)["monitorUrl"] == ["http://192.168.10.149:8789"]
    assert parse_qs(parsed.query)["token"][0]
