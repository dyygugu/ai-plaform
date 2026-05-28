from fastapi import APIRouter, HTTPException

from app.schemas.submitted_history import (
    SubmittedHistoryListResponse,
    SubmittedHistorySampleRead,
    SubmittedHistoryStatsResponse,
    SubmittedHistorySyncRequest,
    SubmittedHistorySyncResponse,
    TestsetGenerateRequest,
    TestsetGenerateResponse,
    TestsetRead,
    TestsetSaveRequest,
)
from app.services import submitted_history_service as service


router = APIRouter(prefix="/tasks/{task_id}", tags=["submitted-history"])


@router.post("/submitted-history/sync", response_model=SubmittedHistorySyncResponse)
def sync_submitted_history(task_id: str, payload: SubmittedHistorySyncRequest = SubmittedHistorySyncRequest()) -> SubmittedHistorySyncResponse:
    try:
        return service.sync_submitted_history(task_id, account_id=payload.account_id, node_id=payload.node_id, force=payload.force)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/submitted-history/stats", response_model=SubmittedHistoryStatsResponse)
def read_submitted_history_stats(task_id: str) -> SubmittedHistoryStatsResponse:
    try:
        return service.read_submitted_history_stats(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/submitted-history", response_model=SubmittedHistoryListResponse)
def read_submitted_history(task_id: str) -> SubmittedHistoryListResponse:
    try:
        return service.list_submitted_history(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/submitted-history/{uid}", response_model=SubmittedHistorySampleRead)
def read_submitted_history_sample(task_id: str, uid: str) -> SubmittedHistorySampleRead:
    try:
        return service.get_submitted_history_sample(task_id, uid)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/testset/generate", response_model=TestsetGenerateResponse)
def generate_testset(task_id: str, payload: TestsetGenerateRequest = TestsetGenerateRequest()) -> TestsetGenerateResponse:
    try:
        return service.generate_testset(task_id, sample_count=payload.sample_count)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/testset/save", response_model=TestsetRead)
def save_testset(task_id: str, payload: TestsetSaveRequest) -> TestsetRead:
    try:
        return service.save_testset(task_id, payload.sample_ids)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/testset", response_model=TestsetRead)
def read_testset(task_id: str) -> TestsetRead:
    try:
        return service.read_testset(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
