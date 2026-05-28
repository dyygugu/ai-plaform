from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ai import (
    AiActionConfirmationDecisionRequest,
    AiActionConfirmationDecisionResponse,
    AiActionConfirmationRead,
    AiActionConfirmationSummary,
    AiConfigCheckResponse,
    AiChatRequest,
    AiChatResponse,
    AiIncidentReviewRequest,
    AiIncidentReviewResponse,
    AiJobRead,
    AiQueueSummary,
    AiRuntimeConfigRead,
    AiRuntimeConfigUpdate,
)
from app.services.ai_confirmation_service import approve_confirmation, confirmation_next_step, confirmation_phrase, list_confirmations, reject_confirmation
from app.services.ai_service import chat_with_ai, check_ai_runtime_config, get_ai_queue_summary, get_ai_runtime_config, review_incidents_with_ai, update_ai_runtime_config

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/queue", response_model=AiQueueSummary)
def read_ai_queue(db: Session = Depends(get_db)) -> AiQueueSummary:
    jobs, counts = get_ai_queue_summary(db)
    db.commit()
    return AiQueueSummary(
        total=len(jobs),
        planned=counts.get("planned", 0),
        mock_completed=counts.get("mock_completed", 0),
        provider_gated=counts.get("provider_gated", 0),
        failed=counts.get("failed", 0),
        items=[AiJobRead.model_validate(job) for job in jobs],
    )


@router.post("/incidents/review", response_model=AiIncidentReviewResponse)
def review_incidents(payload: AiIncidentReviewRequest, db: Session = Depends(get_db)) -> AiIncidentReviewResponse:
    return review_incidents_with_ai(db, payload)


@router.get("/config", response_model=AiRuntimeConfigRead)
def read_ai_config() -> AiRuntimeConfigRead:
    return get_ai_runtime_config()


@router.get("/config/check", response_model=AiConfigCheckResponse)
def check_ai_config() -> AiConfigCheckResponse:
    return check_ai_runtime_config()


@router.put("/config", response_model=AiRuntimeConfigRead)
def update_ai_config(payload: AiRuntimeConfigUpdate) -> AiRuntimeConfigRead:
    return update_ai_runtime_config(payload)


@router.post("/chat", response_model=AiChatResponse)
def create_ai_chat(payload: AiChatRequest, db: Session = Depends(get_db)) -> AiChatResponse:
    return chat_with_ai(db, payload)


@router.get("/confirmations", response_model=AiActionConfirmationSummary)
def read_action_confirmations(status: Optional[str] = None, limit: int = 50, db: Session = Depends(get_db)) -> AiActionConfirmationSummary:
    try:
        items = list_confirmations(db, status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    counts = {"pending": 0, "approved": 0, "rejected": 0, "expired": 0}
    for item in items:
        item_status = item.status.value if hasattr(item.status, "value") else str(item.status)
        counts[item_status] = counts.get(item_status, 0) + 1
    return AiActionConfirmationSummary(
        total=len(items),
        pending=counts.get("pending", 0),
        approved=counts.get("approved", 0),
        rejected=counts.get("rejected", 0),
        expired=counts.get("expired", 0),
        items=[_confirmation_read(item) for item in items],
    )


@router.post("/confirmations/{confirmation_id}/approve", response_model=AiActionConfirmationDecisionResponse)
def approve_action_confirmation(confirmation_id: int, payload: AiActionConfirmationDecisionRequest, db: Session = Depends(get_db)) -> AiActionConfirmationDecisionResponse:
    try:
        item, audit_trace_id = approve_confirmation(db, confirmation_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AiActionConfirmationDecisionResponse(
        item=_confirmation_read(item),
        audit_trace_id=audit_trace_id,
        message="高危动作已人工确认；当前只记录授权和审计，不自动执行破坏性动作。",
    )


@router.post("/confirmations/{confirmation_id}/reject", response_model=AiActionConfirmationDecisionResponse)
def reject_action_confirmation(confirmation_id: int, payload: AiActionConfirmationDecisionRequest, db: Session = Depends(get_db)) -> AiActionConfirmationDecisionResponse:
    try:
        item, audit_trace_id = reject_confirmation(db, confirmation_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AiActionConfirmationDecisionResponse(
        item=_confirmation_read(item),
        audit_trace_id=audit_trace_id,
        message="高危动作已驳回；AI 不得执行该动作。",
    )


def _confirmation_read(item) -> AiActionConfirmationRead:
    return AiActionConfirmationRead(
        id=item.id,
        status=item.status.value if hasattr(item.status, "value") else str(item.status),
        action_key=item.action_key,
        title=item.title,
        risk_level=item.risk_level,
        source=item.source,
        source_trace_id=item.source_trace_id,
        source_ai_job_id=item.source_ai_job_id,
        message=item.message,
        rollback_hint=item.rollback_hint,
        requested_by=item.requested_by,
        reviewed_by=item.reviewed_by,
        confirmation_note=item.confirmation_note,
        trace_id=item.trace_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
        reviewed_at=item.reviewed_at,
        expires_at=item.expires_at,
        confirm_phrase=confirmation_phrase(item),
        next_step=confirmation_next_step(item),
    )
