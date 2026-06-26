import hashlib
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.router import api_router
from app.core.settings import get_settings


@contextmanager
def _api_client_with_release_root(release_root: Path):
    get_settings.cache_clear()
    try:
        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        with TestClient(app) as client:
            yield client
    finally:
        get_settings.cache_clear()


def _write_zip_stub(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK\x03\x04" + content)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_latest_release_manifest_exposes_packages_hashes_and_download_urls(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        release_root = Path(tmp)
        suite = release_root / "aidp-local-suite-0.9.1.zip"
        installer = release_root / "AIDP-Local-Helper-Setup-0.9.1.exe"
        agent = release_root / "aidp-local-helper.zip"
        extension = release_root / "aidp-score-helper-0.9.1.zip"
        _write_zip_stub(suite, b"suite")
        installer.write_bytes(b"MZsetup")
        _write_zip_stub(agent, b"agent")
        _write_zip_stub(extension, b"extension")
        suite_sha256 = _sha256(suite)
        suite_size = suite.stat().st_size
        installer_sha256 = _sha256(installer)
        agent_sha256 = _sha256(agent)
        extension_sha256 = _sha256(extension)

        monkeypatch.setenv("AIDP_LOCAL_AGENT_RELEASE_ROOT", str(release_root))
        with _api_client_with_release_root(release_root) as client:
            response = client.get("/api/v1/local-agent/releases/latest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["suite_version"] == "0.9.1"
    assert payload["version"] == "0.9.1"
    assert payload["suite_name"] == "aidp-local-suite-0.9.1.zip"
    assert payload["suite"] == {
        "package_name": "aidp-local-suite-0.9.1.zip",
        "download_url": "/api/v1/local-agent/releases/latest/download-suite",
        "sha256": suite_sha256,
        "size_bytes": suite_size,
    }
    assert payload["local_agent"]["download_url"] == "/api/v1/local-agent/releases/latest/download-agent"
    assert payload["local_agent"]["sha256"] == agent_sha256
    assert payload["windows_launcher"]["version"] == "0.9.1"
    assert payload["windows_launcher"]["download_url"] == "/api/v1/local-agent/releases/latest/download-suite"
    assert payload["windows_launcher"]["sha256"] == suite_sha256
    assert payload["windows_installer"]["version"] == "0.9.1"
    assert payload["windows_installer"]["download_url"] == "/api/v1/local-agent/releases/latest/download-installer"
    assert payload["windows_installer"]["sha256"] == installer_sha256
    assert payload["browser_extension"]["version"] == "0.9.1"
    assert payload["browser_extension"]["download_url"] == "/api/v1/local-agent/releases/latest/download-extension"
    assert payload["browser_extension"]["sha256"] == extension_sha256
    assert payload["release_notes"] == []
    assert payload["mandatory"] is False


def test_download_endpoints_return_clear_404_when_packages_are_missing(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("AIDP_LOCAL_AGENT_RELEASE_ROOT", tmp)
        with _api_client_with_release_root(Path(tmp)) as client:
            suite = client.get("/api/v1/local-agent/releases/latest/download-suite")
            installer = client.get("/api/v1/local-agent/releases/latest/download-installer")
            agent = client.get("/api/v1/local-agent/releases/latest/download-agent")
            extension = client.get("/api/v1/local-agent/releases/latest/download-extension")

    for response in [suite, installer, agent, extension]:
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "LOCAL_AGENT_PACKAGE_NOT_FOUND"
