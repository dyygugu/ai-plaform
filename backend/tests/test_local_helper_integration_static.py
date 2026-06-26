from pathlib import Path

import pytest


def _legacy_root() -> Path:
    return Path(__file__).resolve().parents[3] / "aidp-monitor"


def test_browser_extension_only_connects_to_local_helper() -> None:
    extension_root = _legacy_root() / "browser-extension" / "aidp-score-helper"
    if not extension_root.is_dir():
        pytest.skip(f"legacy extension source is not available: {extension_root}")
    text = "\n".join((extension_root / name).read_text(encoding="utf-8") for name in ["background.js", "content.js", "manifest.json"])

    assert "127.0.0.1:8789" not in text
    assert "platform.51gugu.uk" not in text
    assert "127.0.0.1:8790" in text
    assert "/api/assistant/plugin-version" in text
    assert "/api/assistant/release-status" in text


def test_local_helper_exposes_worker_runtime_and_update_status_routes() -> None:
    helper = _legacy_root() / "tools" / "local-helper-package" / "host-launcher.ps1"
    if not helper.is_file():
        pytest.skip(f"legacy helper source is not available: {helper}")
    text = helper.read_text(encoding="utf-8")

    for route in [
        "/api/assistant/release-status",
        "/api/assistant/check-updates",
        "/api/assistant/apply-update-if-idle",
        "/api/assistant/downloads",
        "/api/assistant/plugin-status",
        "/api/assistant/plugin-version",
        "/api/worker-runtime/status",
        "/api/worker-runtime/start",
        "/api/worker-runtime/stop",
    ]:
        assert route in text
    assert "pending_idle" in text
    assert "production_run_account_task_group" in text
