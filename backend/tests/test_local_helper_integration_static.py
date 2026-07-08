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
        "启动后最小化运行",
        "启动后自动连接平台",
        "启动后自动开启执行能力",
    ]:
        assert label in text

    for liquid_glass_token in [
        "linear-gradient(135deg, #f8fbff 0%, #eef4ff 100%)",
        "backdrop-filter: blur(22px)",
        "rgba(255, 255, 255, 0.72)",
        "border-radius: 24px",
        "box-shadow: 0 20px 60px rgba(15, 23, 42, 0.12)",
    ]:
        assert liquid_glass_token in text

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


def test_local_helper_does_not_hardcode_api_v1_suffix_for_platform_calls() -> None:
    helper = _helper_source()
    if not helper.is_file():
        pytest.skip(f"legacy helper source is not available: {helper}")
    text = helper.read_text(encoding="utf-8")

    assert "function Get-PlatformApiBaseUrl" in text
    assert "function Get-PlatformApiPrefix" in text
    assert "$baseUrl + '/api/v1/operation-recordings'" not in text
    assert "$baseUrl = $baseUrl + '/api/v1'" not in text


def test_local_helper_powershell_source_parses_without_duplicate_hash_keys() -> None:
    helper = _helper_source()
    if not helper.is_file():
        pytest.skip(f"legacy helper source is not available: {helper}")
    import shutil
    import subprocess

    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell, "PowerShell is required to parse local helper source"

    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-Command",
            f"$tokens = $null; $errors = $null; $null = [System.Management.Automation.Language.Parser]::ParseFile('{helper}', [ref]$tokens, [ref]$errors); if ($errors.Count) {{ $errors | ForEach-Object {{ $_.Message }}; exit 1 }}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_local_helper_default_settings_has_single_api_prefix_key() -> None:
    helper = _helper_source()
    if not helper.is_file():
        pytest.skip(f"legacy helper source is not available: {helper}")
    text = helper.read_text(encoding="utf-8")
    default_settings_start = text.index("function Get-DefaultHelperSettings")
    default_settings_end = text.index("function Normalize-PlatformUrlText", default_settings_start)
    default_settings = text[default_settings_start:default_settings_end]

    assert default_settings.count("platform_api_prefix") == 1


def test_local_helper_api_prefix_normalization_matches_backend() -> None:
    helper = _helper_source()
    if not helper.is_file():
        pytest.skip(f"legacy helper source is not available: {helper}")
    text = helper.read_text(encoding="utf-8")
    function_start = text.index("function Get-PlatformApiPrefix")
    function_end = text.index("function Get-PlatformApiBaseUrl", function_start)
    function_body = text[function_start:function_end]

    assert "-replace '/+', '/'" in function_body
    assert "'/api/v1'" in function_body


def test_suite_builder_prefers_tracked_local_agent_source() -> None:
    script = _repo_root() / "scripts" / "build-local-agent-suite.ps1"
    text = script.read_text(encoding="utf-8")

    assert "local-agent-source" in text
    assert "Join-Path $repoRoot 'local-agent-source'" in text
