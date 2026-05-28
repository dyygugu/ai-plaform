from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from pydantic import BaseModel

from app.schemas.bon8_production import Bon8ProductionRunResponse, Bon8ProductionStartRequest, Bon8ProductionStatusResponse, Bon8RunWorkerStartRequest, Bon8RunWorkerStatusResponse
from app.services.bon8_ai_judgement_service import execute_bon8_account_tick_with_ai, execute_bon8_run_tick_with_ai, prepare_bon8_first_item_review_with_ai
from app.services.bon8_production_service import (
    approve_bon8_run_confirmation,
    build_bon8_production_status,
    get_bon8_production_run,
    mark_bon8_account_operation_needed,
    plan_bon8_parallel_account_ticks,
    reject_bon8_run_confirmation,
    start_bon8_production,
    stop_bon8_production_run,
    submit_approved_bon8_first_item,
)
from app.services.bon8_worker_service import Bon8RunWorkerRegistry, Bon8RunWorkerStatus

router = APIRouter(prefix="/bon8-production", tags=["bon8-production"])


class Bon8ConfirmationRejectRequest(BaseModel):
    rejected_reason: str = ""


@router.get("/status", response_model=Bon8ProductionStatusResponse)
def read_bon8_production_status(db: Session = Depends(get_db)) -> Bon8ProductionStatusResponse:
    return build_bon8_production_status(db)


@router.post("/start", response_model=Bon8ProductionRunResponse)
def start_bon8_production_run(payload: Bon8ProductionStartRequest, db: Session = Depends(get_db)) -> Bon8ProductionRunResponse:
    try:
        return start_bon8_production(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}", response_model=Bon8ProductionRunResponse)
def read_bon8_production_run(run_id: str) -> Bon8ProductionRunResponse:
    try:
        return get_bon8_production_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/confirmations/{confirmation_id}/approve", response_model=Bon8ProductionRunResponse)
def approve_bon8_production_run(run_id: str, confirmation_id: str) -> Bon8ProductionRunResponse:
    try:
        return approve_bon8_run_confirmation(run_id, confirmation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/confirmations/{confirmation_id}/reject", response_model=Bon8ProductionRunResponse)
def reject_bon8_production_run(run_id: str, confirmation_id: str, payload: Bon8ConfirmationRejectRequest) -> Bon8ProductionRunResponse:
    try:
        return reject_bon8_run_confirmation(run_id, confirmation_id, rejected_reason=payload.rejected_reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/stop", response_model=Bon8ProductionRunResponse)
def stop_bon8_run(run_id: str) -> Bon8ProductionRunResponse:
    try:
        return stop_bon8_production_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/submit-first-item", response_model=Bon8ProductionRunResponse)
def submit_bon8_first_item(run_id: str) -> Bon8ProductionRunResponse:
    try:
        return submit_approved_bon8_first_item(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/prepare-first-review", response_model=Bon8ProductionRunResponse)
def prepare_bon8_first_review(run_id: str) -> Bon8ProductionRunResponse:
    try:
        return prepare_bon8_first_item_review_with_ai(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/plan-account-ticks", response_model=Bon8ProductionRunResponse)
def plan_bon8_account_ticks(run_id: str) -> Bon8ProductionRunResponse:
    try:
        return plan_bon8_parallel_account_ticks(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/execute-tick", response_model=Bon8ProductionRunResponse)
def execute_bon8_run_tick(run_id: str) -> Bon8ProductionRunResponse:
    try:
        return execute_bon8_run_tick_with_ai(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/worker/start", response_model=Bon8RunWorkerStatusResponse)
async def start_bon8_run_worker(run_id: str, payload: Bon8RunWorkerStartRequest, request: Request) -> Bon8RunWorkerStatusResponse:
    status = _bon8_worker_registry(request).start(run_id, interval_seconds=payload.interval_seconds)
    return _worker_status_response(status)


@router.get("/runs/{run_id}/worker/status", response_model=Bon8RunWorkerStatusResponse)
def read_bon8_run_worker_status(run_id: str, request: Request) -> Bon8RunWorkerStatusResponse:
    status = _bon8_worker_registry(request).status(run_id)
    return _worker_status_response(status)


@router.post("/runs/{run_id}/worker/stop", response_model=Bon8RunWorkerStatusResponse)
async def stop_bon8_run_worker(run_id: str, request: Request) -> Bon8RunWorkerStatusResponse:
    status = await _bon8_worker_registry(request).stop(run_id)
    return _worker_status_response(status)


@router.post("/runs/{run_id}/accounts/{account_user_id}/operation-needed", response_model=Bon8ProductionRunResponse)
def mark_bon8_operation_needed(run_id: str, account_user_id: str) -> Bon8ProductionRunResponse:
    try:
        return mark_bon8_account_operation_needed(run_id, account_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/accounts/{account_user_id}/execute-tick", response_model=Bon8ProductionRunResponse)
def execute_bon8_account_tick(run_id: str, account_user_id: str) -> Bon8ProductionRunResponse:
    try:
        return execute_bon8_account_tick_with_ai(run_id, account_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _bon8_worker_registry(request: Request) -> Bon8RunWorkerRegistry:
    registry = getattr(request.app.state, "bon8_run_worker_registry", None)
    if registry is None:
        registry = Bon8RunWorkerRegistry()
        request.app.state.bon8_run_worker_registry = registry
    return registry


def _worker_status_response(status: Bon8RunWorkerStatus) -> Bon8RunWorkerStatusResponse:
    return Bon8RunWorkerStatusResponse(**asdict(status))
