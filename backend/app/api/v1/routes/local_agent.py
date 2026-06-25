from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.paths import resolve_runtime_path
from app.core.settings import get_settings
from app.schemas.execution_devices import LocalAgentReleaseRead

router = APIRouter(prefix="/local-agent", tags=["local-agent"])

LOCAL_AGENT_VERSION = "0.9.0"
DEFAULT_SUITE_NAME = f"aidp-local-suite-{LOCAL_AGENT_VERSION}.zip"
DEFAULT_AGENT_NAME = "aidp-local-helper.zip"
DEFAULT_EXTENSION_NAME = "aidp-score-helper-0.6.1.zip"


def _release_root() -> Path:
    return resolve_runtime_path(get_settings().local_agent_release_root)


def _latest_matching_file(pattern: str, fallback_name: str) -> Path:
    root = _release_root()
    candidates = sorted((item for item in root.glob(pattern) if item.is_file()), key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else root / fallback_name


def _latest_suite_file() -> Path:
    return _latest_matching_file("aidp-local-suite-*.zip", DEFAULT_SUITE_NAME)


def _latest_extension_file() -> Path:
    return _latest_matching_file("aidp-score-helper-*.zip", DEFAULT_EXTENSION_NAME)


def _release_version_from_suite(path: Path) -> str:
    prefix = "aidp-local-suite-"
    suffix = ".zip"
    name = path.name
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix) : -len(suffix)]
    return LOCAL_AGENT_VERSION


def _download_file(path: Path) -> FileResponse:
    root = _release_root().resolve()
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        raise HTTPException(status_code=400, detail={"code": "INVALID_RELEASE_PATH", "message": "无效的套件文件路径。"})
    if not resolved.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "LOCAL_AGENT_RELEASE_NOT_FOUND", "message": f"未找到套件文件：{path.name}。"},
        )
    return FileResponse(path=resolved, media_type="application/zip", filename=resolved.name)


@router.get("/releases/latest", response_model=LocalAgentReleaseRead)
def read_local_agent_latest_release() -> LocalAgentReleaseRead:
    suite = _latest_suite_file()
    message = "本机助手套件下载入口已就绪。" if suite.is_file() else "本机助手套件尚未发布，请先生成或上传套件 ZIP。"
    return LocalAgentReleaseRead(version=_release_version_from_suite(suite), suite_name=suite.name, message=message)


@router.get("/releases/latest/download-suite")
def download_local_agent_suite() -> FileResponse:
    return _download_file(_latest_suite_file())


@router.get("/releases/latest/download-agent")
def download_local_agent() -> FileResponse:
    return _download_file(_release_root() / DEFAULT_AGENT_NAME)


@router.get("/releases/latest/download-extension")
def download_local_agent_extension() -> FileResponse:
    return _download_file(_latest_extension_file())
