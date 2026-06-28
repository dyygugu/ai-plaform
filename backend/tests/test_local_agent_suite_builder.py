import json
import subprocess
import tempfile
import zipfile
from pathlib import Path


def test_build_local_agent_suite_creates_integrated_zip_structure() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "build-local-agent-suite.ps1"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        helper_root = tmp_root / "helper"
        extension_root = tmp_root / "extension"
        output_root = tmp_root / "release"
        (helper_root / "config").mkdir(parents=True)
        extension_root.mkdir(parents=True)
        (helper_root / "host-launcher.ps1").write_text("Write-Output 'helper'\n", encoding="utf-8")
        (helper_root / "README.md").write_text("# Helper\n", encoding="utf-8")
        (extension_root / "manifest.json").write_text(json.dumps({"version": "0.9.0"}), encoding="utf-8")
        (extension_root / "background.js").write_text("const endpoint = 'http://127.0.0.1:8790/api/health';\n", encoding="utf-8")
        (extension_root / "content.js").write_text("console.log('content');\n", encoding="utf-8")
        (extension_root / "README.md").write_text("# Extension\n", encoding="utf-8")

        result = subprocess.run(
            [
                "pwsh.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Version",
                "0.9.1",
                "-HelperSourceRoot",
                str(helper_root),
                "-ExtensionSourceRoot",
                str(extension_root),
                "-OutputRoot",
                str(output_root),
            ],
            cwd=repo_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
            check=False,
        )

        assert result.returncode == 0, (result.stderr or "") + (result.stdout or "")
        suite = output_root / "aidp-local-suite-0.9.1.zip"
        installer = output_root / "AIDP-Local-Helper-Setup-0.9.1.exe"
        install_root = tmp_root / "installed"
        assert suite.is_file()
        assert installer.is_file()
        assert installer.read_bytes().startswith(b"MZ")
        with zipfile.ZipFile(suite) as archive:
            names = set(archive.namelist())
            assert "manifest.json" in names
            assert "AIDP 本机助手.exe" in names
            assert "AIDP-Local-Helper-Setup-0.9.1.exe" in names
            assert "code-signing/AIDP-Local-Helper-CodeSigning.cer" in names
            assert "local-agent/host-launcher.ps1" in names
            assert "local-agent/start-local-agent.ps1" in names
            assert "local-agent/apply-update.ps1" in names
            assert "local-agent/config/default-config.json" in names
            assert "local-agent/README.md" in names
            assert "browser-extension/aidp-score-helper-0.9.1.zip" in names
            assert "browser-extension/README.md" in names
            assert "install/install.ps1" in names
            assert "install/uninstall.ps1" in names
            assert "install/README.md" in names
            assert archive.read("AIDP 本机助手.exe").startswith(b"MZ")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            default_config = json.loads(archive.read("local-agent/config/default-config.json").decode("utf-8"))
            install_script = archive.read("install/install.ps1").decode("utf-8")

        install_result = subprocess.run(
            [
                str(installer),
                "--quiet",
                "--install-root",
                str(install_root),
                "--no-desktop-shortcut",
                "--no-start-menu-shortcut",
                "--no-launch",
            ],
            cwd=repo_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=60,
            check=False,
        )
        try:
            assert install_result.returncode == 0, (install_result.stderr or "") + (install_result.stdout or "")
            assert (install_root / "manifest.json").is_file()
            assert (install_root / "AIDP 本机助手.exe").is_file()
            assert (install_root / "local-agent" / "host-launcher.ps1").is_file()
        finally:
            subprocess.run(
                [
                    str(installer),
                    "--uninstall",
                    "--quiet",
                    "--remove-config",
                    "--install-root",
                    str(install_root),
                ],
                cwd=repo_root,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
                check=False,
            )

    assert manifest["suite_version"] == "0.9.1"
    assert manifest["local_agent"]["entry"] == "local-agent/host-launcher.ps1"
    assert manifest["windows_launcher"]["path"] == "AIDP 本机助手.exe"
    assert manifest["windows_launcher"]["signed"] is True
    assert manifest["windows_launcher"]["tray"] is True
    assert manifest["windows_launcher"]["single_instance"] is True
    assert manifest["windows_installer"]["path"] == "AIDP-Local-Helper-Setup-0.9.1.exe"
    assert manifest["windows_installer"]["signed"] is True
    assert manifest["windows_installer"]["embedded_suite"] is True
    assert manifest["windows_installer"]["supports_uninstall"] is True
    assert manifest["windows_installer"]["creates_desktop_shortcut"] is True
    assert manifest["windows_installer"]["creates_start_menu_shortcut"] is True
    assert manifest["browser_extension"]["path"] == "browser-extension/aidp-score-helper-0.9.1.zip"
    assert manifest["code_signing"]["mode"] == "self_signed_internal"
    assert manifest["code_signing"]["certificate_path"] == "code-signing/AIDP-Local-Helper-CodeSigning.cer"
    assert manifest["code_signing"]["thumbprint"]
    assert manifest["install"]["entry"] == "install/install.ps1"
    assert "AIDP 本机助手.exe" in install_script
    assert default_config["platform_base_url"] == "http://192.168.10.149:8789"
    assert default_config["active_platform_url_id"] == "nas-lan"
    assert default_config["platform_urls"] == [
        {
            "id": "local-dev",
            "name": "本地开发地址",
            "url": "http://127.0.0.1:8789",
            "is_builtin": True,
        },
        {
            "id": "nas-lan",
            "name": "NAS 局域网地址",
            "url": "http://192.168.10.149:8789",
            "is_builtin": True,
        },
        {
            "id": "public-domain",
            "name": "公网访问地址",
            "url": "https://platform.51gugu.uk",
            "is_builtin": True,
        },
    ]


def test_windows_launcher_source_exposes_p0_tray_behaviour() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source = repo_root / "local-agent-launcher" / "AidpLocalHelperLauncher.cs"

    text = source.read_text(encoding="utf-8")

    for token in [
        "new Mutex(",
        "NotifyIcon",
        "打开控制台",
        "测试平台连接",
        "重启本机助手",
        "开启开机自启动",
        "关闭开机自启动",
        "退出本机助手",
        "/api/worker-runtime/stop",
        "/api/assistant/test-platform-connection",
        "/api/assistant/check-updates",
        "AIDP 本机助手.cmd",
        "--exit",
        "Win32_Process",
        "KillLauncherProcessesInAppRoot",
        "正在恢复本机助手",
        "netstat",
    ]:
        assert token in text


def test_windows_setup_source_exposes_p1_installer_behaviour() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source = repo_root / "local-agent-launcher" / "AidpLocalHelperSetup.cs"

    text = source.read_text(encoding="utf-8")

    for token in [
        "AIDP 本机助手安装向导",
        "--uninstall",
        "ExtractEmbeddedPayloadZip",
        "CreateShortcut",
        "DesktopDirectory",
        "Programs",
        "CurrentVersion\\Uninstall",
        "DisplayName",
        "UninstallString",
        "AIDP 本机助手卸载.exe",
        "ScheduleSelfCleanup",
        "是否保留本机配置",
        "AIDP 本机助手.cmd",
        "LaunchAfterInstall",
    ]:
        assert token in text


def test_suite_builder_exposes_code_signing_workflow() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "build-local-agent-suite.ps1"
    text = script.read_text(encoding="utf-8")

    for token in [
        "New-SelfSignedCertificate",
        "Set-AuthenticodeSignature",
        "Get-AuthenticodeSignature",
        "TrustedPublisher",
        "Cert:\\CurrentUser\\Root",
        "AIDP-Local-Helper-CodeSigning.cer",
        "Sign-WindowsBinary",
        "Export-CodeSigningCertificate",
    ]:
        assert token in text
