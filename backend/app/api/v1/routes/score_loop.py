from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.score_loop import (
    ScoreLoopActionResponse,
    ScoreLoopAutoSubmitRequest,
    ScoreLoopCaptureRequest,
    ScoreLoopCaseListResponse,
    ScoreLoopDraftRequest,
    ScoreLoopManualStableRequest,
    ScoreLoopReviewRequest,
    ScoreLoopSummaryResponse,
)
from app.services.score_loop_service import (
    add_manual_stable_count,
    build_score_loop_summary,
    capture_score_case,
    case_to_read,
    create_ai_draft,
    list_score_loop_cases,
    review_score_case,
    set_auto_submit_gate,
)

router = APIRouter(prefix="/score-loop", tags=["score-loop"])


@router.get("/summary", response_model=ScoreLoopSummaryResponse)
def read_score_loop_summary(db: Session = Depends(get_db)) -> ScoreLoopSummaryResponse:
    return build_score_loop_summary(db)


@router.get("/cases", response_model=ScoreLoopCaseListResponse)
def read_score_loop_cases(limit: int = 50, db: Session = Depends(get_db)) -> ScoreLoopCaseListResponse:
    return list_score_loop_cases(db, limit=limit)


@router.post("/cases/capture", response_model=ScoreLoopActionResponse)
def capture_case(payload: ScoreLoopCaptureRequest, db: Session = Depends(get_db)) -> ScoreLoopActionResponse:
    try:
        item, audit_trace_id = capture_score_case(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScoreLoopActionResponse(item=case_to_read(item), audit_trace_id=audit_trace_id, message="题面已采集；未知题型会保持暂停，不自动提交。")


@router.post("/cases/{case_id}/draft", response_model=ScoreLoopActionResponse)
def draft_case(case_id: int, payload: ScoreLoopDraftRequest, db: Session = Depends(get_db)) -> ScoreLoopActionResponse:
    try:
        item, audit_trace_id = create_ai_draft(db, case_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScoreLoopActionResponse(item=case_to_read(item), audit_trace_id=audit_trace_id, message="AI 草稿已生成；必须人工确认后才可进入提交确认队列。")


@router.post("/cases/{case_id}/review", response_model=ScoreLoopActionResponse)
def review_case(case_id: int, payload: ScoreLoopReviewRequest, db: Session = Depends(get_db)) -> ScoreLoopActionResponse:
    try:
        item, audit_trace_id = review_score_case(db, case_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScoreLoopActionResponse(item=case_to_read(item), audit_trace_id=audit_trace_id, message="人工复核已记录；真实提交请求已进入高危确认队列。" if payload.request_submit else "人工复核已记录。")


@router.post("/gate/manual-stable", response_model=ScoreLoopActionResponse)
def add_manual_stable(payload: ScoreLoopManualStableRequest, db: Session = Depends(get_db)) -> ScoreLoopActionResponse:
    gate = add_manual_stable_count(db, payload)
    return ScoreLoopActionResponse(gate=gate, audit_trace_id=gate.audit_trace_id, message="人工稳定样本计数已更新。")


@router.post("/gate/auto-submit", response_model=ScoreLoopActionResponse)
def update_auto_submit(payload: ScoreLoopAutoSubmitRequest, db: Session = Depends(get_db)) -> ScoreLoopActionResponse:
    gate = set_auto_submit_gate(db, payload)
    return ScoreLoopActionResponse(gate=gate, audit_trace_id=gate.audit_trace_id, message="自动提交闸门已更新；真实提交仍必须经过高危确认队列。")