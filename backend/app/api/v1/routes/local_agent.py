from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.schemas.execution_devices import LocalAgentReleaseRead

router = APIRouter(prefix="/local-agent", tags=["local-agent"])


@router.get("/releases/latest", response_model=LocalAgentReleaseRead)
def read_local_agent_latest_release() -> LocalAgentReleaseRead:
    return LocalAgentReleaseRead()


@router.get("/releases/latest/download-suite")
def download_local_agent_suite() -> PlainTextResponse:
    return PlainTextResponse("local-agent-suite placeholder\n", media_type="text/plain")


@router.get("/releases/latest/download-agent")
def download_local_agent() -> PlainTextResponse:
    return PlainTextResponse("local-agent placeholder\n", media_type="text/plain")


@router.get("/releases/latest/download-extension")
def download_local_agent_extension() -> PlainTextResponse:
    return PlainTextResponse("browser-extension placeholder\n", media_type="text/plain")
