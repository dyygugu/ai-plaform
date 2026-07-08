from dataclasses import asdict

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.security import legacy_production_routes_blocked
from app.db.session import get_db
from app.schemas.task_auto_runs import TaskAutoRunPreflightResponse, TaskAutoRunResponse, TaskAutoRunStartRequest, TaskAutoRunWorkerStartRequest, TaskAutoRunWorkerStatusResponse
from app.services.bon8_worker_service import Bon8RunWorkerRegistry, Bon8RunWorkerStatus
from app.services.task_auto_run_service import check_task_auto_run_preflight, default_task_auto_run_adapters, find_active_task_auto_run, get_task_auto_run, start_task_auto_run, stop_task_auto_run, tick_task_auto_run
from app.services.task_rules import utc_now
from app.services.task_auto_run_worker_service import GenericTaskAutoRunWorkerRegistry, GenericTaskAutoRunWorkerStatus

router = APIRouter(prefix="/task-auto-runs", tags=["task-auto-runs"])


@router.post("/start", response_model=TaskAutoRunResponse)
def start_auto_run(payload: TaskAutoRunStartRequest, request: Request, db: Session = Depends(get_db)) -> TaskAutoRunResponse:
    if legacy_production_routes_blocked():
        raise HTTPException(status_code=410, detail="旧自动做题启动入口已关闭。请使用 AI 标注能力工作台 Step4 的生产运行入口。")
    try:
        return start_task_auto_run(db, payload, adapters=_adapters(request), state_dir=_state_dir(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/preflight", response_model=TaskAutoRunPreflightResponse)
def preflight_auto_run(payload: TaskAutoRunStartRequest, request: Request) -> TaskAutoRunPreflightResponse:
    try:
        return check_task_auto_run_preflight(payload, adapters=_adapters(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}", response_model=TaskAutoRunResponse)
def read_auto_run(run_id: str, request: Request) -> TaskAutoRunResponse:
    try:
        return get_task_auto_run(run_id, adapters=_adapters(request), state_dir=_state_dir(request))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/active", response_model=Optional[TaskAutoRunResponse])
def read_active_auto_run(task_id: str, request: Request, account_user_ids: list[str] = Query(default_factory=list)) -> Optional[TaskAutoRunResponse]:
    return find_active_task_auto_run(task_id, account_ids=account_user_ids, adapters=_adapters(request), state_dir=_state_dir(request))


@router.post("/runs/{run_id}/stop", response_model=TaskAutoRunResponse)
def stop_auto_run(run_id: str, request: Request) -> TaskAutoRunResponse:
    try:
        return stop_task_auto_run(run_id, adapters=_adapters(request), state_dir=_state_dir(request))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/worker/start", response_model=TaskAutoRunWorkerStatusResponse)
async def start_auto_run_worker(run_id: str, payload: TaskAutoRunWorkerStartRequest, request: Request) -> TaskAutoRunWorkerStatusResponse:
    if legacy_production_routes_blocked():
        raise HTTPException(status_code=410, detail="旧后台循环启动入口已关闭。请使用 AI 标注能力工作台 Step4 的试运行/生产运行入口，并等待专用执行器门禁放行。")
    run = read_auto_run(run_id, request)
    if run.adapter_key != "bon8":
        adapter = _adapter_by_key(request, run.adapter_key)
        if adapter is None or not hasattr(adapter, "tick"):
            return TaskAutoRunWorkerStatusResponse(run_id=run.run_id, adapter_run_id=run.adapter_run_id, active=False, last_ok=False, last_error="该题型自动执行器尚未接入后台循环。")
        registry = _generic_worker_registry(request)
        worker = registry.ensure(
            run.run_id,
            tick_func=lambda: _run_generic_auto_tick(request, run.run_id),
            interval_seconds=payload.interval_seconds,
        )
        await worker.run_once()
        if worker.status.last_ok:
            worker.start()
        return _generic_worker_status_response(run.run_id, run.adapter_run_id, worker.snapshot())
    registry = _bon8_worker_registry(request)
    worker = registry.ensure(run.adapter_run_id, interval_seconds=payload.interval_seconds)
    await worker.run_once()
    if worker.status.last_ok:
        worker.start(run_immediately=False)
    return _worker_status_response(run.run_id, run.adapter_run_id, worker.snapshot())


@router.get("/runs/{run_id}/worker/status", response_model=TaskAutoRunWorkerStatusResponse)
def read_auto_run_worker_status(run_id: str, request: Request) -> TaskAutoRunWorkerStatusResponse:
    run = read_auto_run(run_id, request)
    if run.adapter_key != "bon8":
        status = _generic_worker_registry(request).status(run.run_id)
        return _generic_worker_status_response(run.run_id, run.adapter_run_id, status)
    status = _bon8_worker_registry(request).status(run.adapter_run_id)
    return _worker_status_response(run.run_id, run.adapter_run_id, status)


@router.post("/runs/{run_id}/worker/stop", response_model=TaskAutoRunWorkerStatusResponse)
async def stop_auto_run_worker(run_id: str, request: Request) -> TaskAutoRunWorkerStatusResponse:
    run = read_auto_run(run_id, request)
    if run.adapter_key != "bon8":
        status = await _generic_worker_registry(request).stop(run.run_id)
        return _generic_worker_status_response(run.run_id, run.adapter_run_id, status)
    status = await _bon8_worker_registry(request).stop(run.adapter_run_id)
    return _worker_status_response(run.run_id, run.adapter_run_id, status)


def _adapters(request: Request):
    return getattr(request.app.state, "task_auto_run_adapters", None)


def _state_dir(request: Request):
    return getattr(request.app.state, "task_auto_run_state_dir", None)


def _adapter_by_key(request: Request, adapter_key: str):
    adapters = _adapters(request)
    if adapters is None:
        adapters = default_task_auto_run_adapters()
    for adapter in list(adapters or []):
        if getattr(adapter, "adapter_key", "") == adapter_key:
            return adapter
    return None


def _bon8_worker_registry(request: Request) -> Bon8RunWorkerRegistry:
    registry = getattr(request.app.state, "bon8_run_worker_registry", None)
    if registry is None:
        registry = Bon8RunWorkerRegistry()
        request.app.state.bon8_run_worker_registry = registry
    return registry


def _generic_worker_registry(request: Request) -> GenericTaskAutoRunWorkerRegistry:
    registry = getattr(request.app.state, "generic_task_auto_run_worker_registry", None)
    if registry is None:
        registry = GenericTaskAutoRunWorkerRegistry()
        request.app.state.generic_task_auto_run_worker_registry = registry
    return registry


def _run_generic_auto_tick(request: Request, run_id: str) -> None:
    run = read_auto_run(run_id, request)
    adapter = _adapter_by_key(request, run.adapter_key)
    if adapter is None or not hasattr(adapter, "tick"):
        raise ValueError("该题型自动执行器尚未接入后台循环。")
    tick_task_auto_run(run_id, adapters=_adapters(request), state_dir=_state_dir(request))


def _worker_status_response(run_id: str, adapter_run_id: str, status: Bon8RunWorkerStatus) -> TaskAutoRunWorkerStatusResponse:
    payload = asdict(status)
    payload["run_id"] = run_id
    payload["adapter_run_id"] = adapter_run_id
    return TaskAutoRunWorkerStatusResponse(**payload)


def _generic_worker_status_response(run_id: str, adapter_run_id: str, status: GenericTaskAutoRunWorkerStatus) -> TaskAutoRunWorkerStatusResponse:
    payload = asdict(status)
    payload["run_id"] = run_id
    payload["adapter_run_id"] = adapter_run_id
    return TaskAutoRunWorkerStatusResponse(**payload)
