import re
from pathlib import Path

import pytest


def _legacy_root() -> Path:
    return Path(__file__).resolve().parents[3] / "aidp-monitor"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _helper_source() -> Path:
    tracked_helper = _repo_root() / "local-agent-source" / "host-launcher.ps1"
    if tracked_helper.is_file():
        return tracked_helper
    return _legacy_root() / "tools" / "local-helper-package" / "host-launcher.ps1"


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
    helper = _helper_source()
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


def test_local_helper_exposes_chinese_console_routes_and_safe_copy() -> None:
    helper = _helper_source()
    if not helper.is_file():
        pytest.skip(f"legacy helper source is not available: {helper}")
    text = helper.read_text(encoding="utf-8")

    assert "function Write-HtmlResponse" in text
    assert "function Get-AssistantConsoleHtml" in text
    assert "$path -eq '/'" in text

    for label in [
        "本机助手控制台",
        "首页",
        "连接设置",
        "开机自启动",
        "浏览器插件",
        "执行能力",
        "上传队列",
        "更新管理",
        "问题诊断",
        "高级设置",
        "NAS 局域网地址",
        "本机助手访问地址",
        "一键诊断",
        "导出诊断包",
        "已发现新版，但当前正在执行任务。系统会等空闲后再更新",
    ]:
        assert label in text

    for route in [
        "/api/assistant/autostart",
        "/api/assistant/diagnostics",
        "/api/assistant/diagnostics/run",
        "/api/assistant/diagnostics/export",
        "/api/assistant/open-folder",
    ]:
        assert route in text

    console_start = text.index("function Get-AssistantConsoleHtml")
    console_end = text.index("function Get-HelperSettingsPath")
    console_text = text[console_start:console_end]
    for forbidden in [
        "platform_base_url",
        "WorkerRuntime",
        "heartbeat",
        "command_id",
        "payload",
        "stack_trace",
        "traceback",
        "HTTPException",
        "Authorization",
    ]:
        assert forbidden not in console_text
    for forbidden_word in ["lease", "claim", "renew"]:
        assert re.search(rf"(?<![A-Za-z_]){forbidden_word}(?![A-Za-z_])", console_text) is None


def test_local_helper_defaults_to_editable_nas_platform_urls() -> None:
    helper = _helper_source()
    if not helper.is_file():
        pytest.skip(f"legacy helper source is not available: {helper}")
    text = helper.read_text(encoding="utf-8")

    assert "active_platform_url_id" in text
    assert "platform_urls" in text
    assert "http://192.168.10.149:8789" in text
    assert "http://127.0.0.1:8789" in text
    assert "https://platform.51gugu.uk" in text
    assert "NAS 局域网地址" in text
    assert "本地开发地址" in text
    assert "公网访问地址" in text

    default_settings_start = text.index("function Get-DefaultHelperSettings")
    default_settings_end = text.index("function Normalize-PlatformUrlText", default_settings_start)
    default_settings = text[default_settings_start:default_settings_end]
    assert "else { 'http://192.168.10.149:8789' }" in default_settings
    assert "$settings = @{" in default_settings
    assert "Normalize-AssistantSettings -Settings $defaults" in text
    assert "$Settings.PSObject.Properties['Keys']" in text
    assert "$fallbackSettings = @{" in text


def test_suite_builder_prefers_tracked_local_agent_source() -> None:
    script = _repo_root() / "scripts" / "build-local-agent-suite.ps1"
    text = script.read_text(encoding="utf-8")

    assert "local-agent-source" in text
    assert "Join-Path $repoRoot 'local-agent-source'" in text
