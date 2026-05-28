from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path
import re
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.settings import get_settings
from app.db.init_db import create_tables_for_dev
from app.db.session import SessionLocal, get_db
from app.schemas.account import AccountClientSessionRequest, AccountClientSessionResponse
from app.services.login_slot_service import register_client_session
from app.services.notification_service import send_error_notification
from app.services.account_service import list_accounts
from app.services.bon8_worker_service import Bon8RunWorkerRegistry
from app.services.production_account_refresh_service import refresh_production_accounts
from app.services.production_auto_refresh_service import ProductionAutoRefreshScheduler
from app.services.production_dashboard_service import get_browser_open_session
from app.services.task_auto_run_service import default_task_auto_run_adapters
from app.services.task_auto_run_worker_service import GenericTaskAutoRunWorkerRegistry
from sqlalchemy.orm import Session


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.auto_create_tables:
        create_tables_for_dev()
    scheduler = ProductionAutoRefreshScheduler(
        refresh_func=_refresh_production_accounts_with_db_names,
        interval_seconds=settings.production_auto_refresh_interval_minutes * 60,
        initial_delay_seconds=settings.production_auto_refresh_initial_delay_seconds,
        enabled=settings.production_auto_refresh_enabled,
    )
    app.state.production_auto_refresh_scheduler = scheduler
    app.state.bon8_run_worker_registry = Bon8RunWorkerRegistry()
    app.state.generic_task_auto_run_worker_registry = GenericTaskAutoRunWorkerRegistry()
    app.state.task_auto_run_adapters = default_task_auto_run_adapters()
    app.state.task_auto_run_state_dir = None
    scheduler.start()
    try:
        yield
    finally:
        await app.state.bon8_run_worker_registry.stop_all()
        await app.state.generic_task_auto_run_worker_registry.stop_all()
        await scheduler.stop()


def _refresh_production_accounts_with_db_names() -> None:
    db = SessionLocal()
    try:
        display_names = {account.user_id: account.display_name for account in list_accounts(db) if account.display_name}
        refresh_production_accounts(display_names=display_names)
    finally:
        db.close()


class PrivateNetworkPreflightMiddleware:
    def __init__(self, app, *, settings) -> None:
        self.app = app
        self.cors_origin_list = set(settings.cors_origin_list)
        self.cors_origin_regex = re.compile(settings.cors_origin_regex) if settings.cors_origin_regex else None

    def _origin_allowed(self, origin: str) -> bool:
        if not origin:
            return False
        if origin in self.cors_origin_list:
            return True
        return bool(self.cors_origin_regex and self.cors_origin_regex.match(origin))

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("method") == "OPTIONS":
            headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
            if headers.get("access-control-request-private-network", "").lower() == "true":
                origin = headers.get("origin", "")
                if self._origin_allowed(origin):
                    response = Response(status_code=200)
                    response.headers["Access-Control-Allow-Origin"] = origin
                    response.headers["Access-Control-Allow-Credentials"] = "true"
                    response.headers["Access-Control-Allow-Methods"] = headers.get("access-control-request-method", "POST")
                    request_headers = headers.get("access-control-request-headers", "")
                    if request_headers:
                        response.headers["Access-Control-Allow-Headers"] = request_headers
                    response.headers["Access-Control-Allow-Private-Network"] = "true"
                    response.headers["Vary"] = "Origin, Access-Control-Request-Method, Access-Control-Request-Headers, Access-Control-Request-Private-Network"
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="aidp-monitor-next",
        version=settings.monitor_version,
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(PrivateNetworkPreflightMiddleware, settings=settings)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.exception_handler(Exception)
    async def notify_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        trace_id = uuid4().hex
        send_error_notification(
            event="backend.unhandled_exception",
            level="error",
            message=str(exc),
            data={"method": request.method, "path": request.url.path},
            trace_id=trace_id,
        )
        return JSONResponse(status_code=500, content={"detail": "服务内部错误，已记录并按配置发送飞书通知。", "trace_id": trace_id})


    @app.get("/api/browser-open-session", include_in_schema=False)
    def consume_browser_open_session(token: str) -> dict:
        return get_browser_open_session(token)

    @app.post("/api/client-session", response_model=AccountClientSessionResponse)
    def register_client_session_compat(payload: AccountClientSessionRequest, db: Session = Depends(get_db)) -> AccountClientSessionResponse:
        try:
            return register_client_session(db, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    static_dir = Path(__file__).resolve().parent.parent / "frontend_dist"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="frontend-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_frontend(full_path: str) -> FileResponse:
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            requested_path = static_dir / full_path
            if full_path and requested_path.is_file():
                return FileResponse(requested_path)
            return FileResponse(static_dir / "index.html")
    return app


app = create_app()
