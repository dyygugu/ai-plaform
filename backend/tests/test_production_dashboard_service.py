import json

from app.core.settings import get_settings
from app.models.account import AccountStatus
from app.services.production_dashboard_service import _account_is_stale, _global_warning, _load_monitor_state, _status, _task_stat, _warning


def test_restored_account_with_successful_http_task_counts_is_stale_until_refresh() -> None:
    source = {
        "name": "用户22449629285",
        "source": "http",
        "refreshStatus": "restored",
        "stale": True,
        "error": None,
        "tasks": [
            {
                "id": "7630735313958653746",
                "name": "RFT人标支持VLM Coding",
                "source": "configured-http-agw-category-progress",
                "frontendNotSubmitted": 3,
                "frontendSubmittedCategory": {
                    "source": "http-agw-cookie-category",
                    "error": None,
                },
                "frontendProgress": {
                    "submittedCount": 8,
                    "abandonedCount": 1,
                    "inProgressCount": 2,
                    "source": "http-agw-cookie-progress",
                    "error": None,
                },
            }
        ],
    }

    stale = _account_is_stale(source)
    task = _task_stat(source["tasks"][0], stale)

    assert stale is True
    assert task.stale is True
    assert task.processing == 0
    assert task.in_progress == 0
    assert task.pending == 0
    assert "任务数字可能是旧缓存" in _warning("用户22449629285", source, stale)


def test_successful_refresh_clears_legacy_needs_relogin_for_dashboard_status() -> None:
    source = {
        "userId": "7633857103195918123",
        "name": "用户612876981132",
        "cookie": "sessionid=redacted",
        "hasCookie": True,
        "authMode": "client-cookie",
        "needsRelogin": True,
        "loginOk": True,
        "refreshStatus": "ok",
        "error": None,
    }

    assert _status(source, None) == AccountStatus.ACTIVE.value
    assert "需要重新登录" not in _warning("用户612876981132", source, stale=False)


def test_task_stat_keeps_processing_and_in_progress_separate() -> None:
    task = _task_stat(
        {
            "id": "7637771731901861641",
            "name": "RFT人标支持VLM Coding（bon8草图与流程图）-正式队列",
            "frontendNotSubmitted": 7,
            "frontendCategoryTotalMap": {"0": 9},
            "frontendSubmittedCategory": {
                "source": "http-agw-cookie-category",
                "error": None,
            },
            "frontendProgress": {
                "submittedCount": 0,
                "abandonedCount": 0,
                "inProgressCount": 4,
                "source": "http-agw-cookie-progress",
                "error": None,
            },
            "poolPendingSubmit": 53,
        },
        stale=False,
    )

    assert task.processing == 7
    assert task.in_progress == 4
    assert task.pending == 53


def test_task_stat_keeps_repair_separate_from_pending_and_processing() -> None:
    task = _task_stat(
        {
            "id": "7637771731901861641",
            "frontendNotSubmitted": 1,
            "frontendRepairCount": 1,
            "frontendCategoryTotalMap": {"0": 1, "1": 210, "3": 26},
            "frontendSubmittedCategory": {
                "source": "http-agw-cookie-category",
                "error": None,
            },
            "frontendProgress": {
                "inProgressCount": 211,
                "source": "http-agw-cookie-progress",
                "error": None,
            },
            "poolPendingSubmit": 0,
        },
        stale=False,
    )

    assert task.repair == 1
    assert task.pending == 0
    assert task.processing == 1
    assert task.in_progress == 211


def test_task_stat_does_not_fallback_not_submitted_to_pending() -> None:
    task = _task_stat(
        {
            "id": "task-without-pool-pending",
            "frontendNotSubmitted": 5,
            "frontendSubmittedCategory": {
                "source": "http-agw-cookie-category",
                "error": None,
            },
            "frontendProgress": {
                "inProgressCount": 2,
                "source": "http-agw-cookie-progress",
                "error": None,
            },
        },
        stale=False,
    )

    assert task.processing == 5
    assert task.in_progress == 2
    assert task.pending == 0


def test_global_warning_distinguishes_production_refresh_stale_from_account_cache() -> None:
    warning = _global_warning(state_stale=True, stale_account_count=0)

    assert "生产刷新" in warning
    assert "旧缓存" not in warning


def test_load_monitor_state_uses_only_native_production_state(tmp_path, monkeypatch) -> None:
    native_state = tmp_path / "production-state.json"
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    legacy_accounts = legacy_dir / "accounts.docker-local.json"
    legacy_monitor = legacy_dir / "monitor-state.json"
    native_state.write_text(json.dumps({"accounts": [{"userId": "111111111111", "name": "native"}]}), encoding="utf-8")
    legacy_accounts.write_text(json.dumps({"accounts": []}), encoding="utf-8")
    legacy_monitor.write_text(json.dumps({"accounts": [{"userId": "222222222222", "name": "legacy"}]}), encoding="utf-8")
    monkeypatch.setenv("AIDP_PRODUCTION_STATE_PATH", str(native_state))
    monkeypatch.setenv("AIDP_LEGACY_ACCOUNTS_PATH", str(legacy_accounts))
    get_settings.cache_clear()

    try:
        state = _load_monitor_state()
    finally:
        get_settings.cache_clear()

    assert state["accounts"][0]["name"] == "native"

    native_state.write_text(json.dumps({"accounts": []}), encoding="utf-8")
    monkeypatch.setenv("AIDP_PRODUCTION_STATE_PATH", str(native_state))
    monkeypatch.setenv("AIDP_LEGACY_ACCOUNTS_PATH", str(legacy_accounts))
    get_settings.cache_clear()
    try:
        empty_state = _load_monitor_state()
    finally:
        get_settings.cache_clear()

    assert empty_state == {}
