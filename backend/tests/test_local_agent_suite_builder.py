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
                "0.9.0",
                "-HelperSourceRoot",
                str(helper_root),
                "-ExtensionSourceRoot",
                str(extension_root),
                "-OutputRoot",
                str(output_root),
            ],
            cwd=repo_root,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )

        assert result.returncode == 0, result.stderr + result.stdout
        suite = output_root / "aidp-local-suite-0.9.0.zip"
        assert suite.is_file()
        with zipfile.ZipFile(suite) as archive:
            names = set(archive.namelist())
            assert "manifest.json" in names
            assert "local-agent/host-launcher.ps1" in names
            assert "local-agent/start-local-agent.ps1" in names
            assert "local-agent/apply-update.ps1" in names
            assert "local-agent/config/default-config.json" in names
            assert "local-agent/README.md" in names
            assert "browser-extension/aidp-score-helper-0.9.0.zip" in names
            assert "browser-extension/README.md" in names
            assert "install/install.ps1" in names
            assert "install/uninstall.ps1" in names
            assert "install/README.md" in names
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

    assert manifest["suite_version"] == "0.9.0"
    assert manifest["local_agent"]["entry"] == "local-agent/host-launcher.ps1"
    assert manifest["browser_extension"]["path"] == "browser-extension/aidp-score-helper-0.9.0.zip"
    assert manifest["install"]["entry"] == "install/install.ps1"
