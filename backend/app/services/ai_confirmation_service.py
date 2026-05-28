import json
import re
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai import AiActionConfirmation, AiActionConfirmationStatus
from app.models.audit import AuditSeverity
from app.schemas.ai import AiActionConfirmationDecisionRequest, AiIncidentAction
from app.services.audit_service import write_audit
from app.services.task_rules import utc_now

_SECRET_PATTERNS = [
    re.compile(r"(cookie|api[_-]?key|token|secret|password|主密钥|恢复码)\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
]

CONFIRMATION_SOURCE = "incident_ai"


def create_confirmation_requests(
    db: Session,
    source_trace_id: str,
    source_ai_job_id: Optional[int],
    actions: list[AiIncidentAction],
    context: dict[str, object],
    write_audit_enabled: bool = True,
) -> list[AiActionConfirmation]:
    confirmations: list[AiActionConfirmation] = []
    for action in actions:
        if not action.requires_confirmation:
            continue
        confirmation = _find_pending_confirmation(db, action.key)
        payload_json = _confirmation_payload(action, context)
        if confirmation:
            confirmation.title = action.title
            confirmation.risk_level = action.risk_level
            confirmation.source_trace_id = source_trace_id
            confirmation.source_ai_job_id = source_ai_job_id
            confirmation.message = _redact(action.message)
            confirmation.rollback_hint = _redact(action.rollback_hint)
            confirmation.payload_json = payload_json
            confirmation.updated_at = utc_now()
        else:
            confirmation = AiActionConfirmation(
                status=AiActionConfirmationStatus.PENDING,
                action_key=action.key,
                title=action.title,
                risk_level=action.risk_level,
                source=CONFIRMATION_SOURCE,
                source_trace_id=source_trace_id,
                source_ai_job_id=source_ai_job_id,
                message=_redact(action.message),
                rollback_hint=_redact(action.rollback_hint),
                payload_json=payload_json,
                requested_by="incident-ai",
                trace_id=uuid4().hex,
            )
            db.add(confirmation)
        db.flush()
        if write_audit_enabled:
            write_audit(
                db,
                event_type="ai_action_confirmation_requested",
                severity=AuditSeverity.WARNING,
                actor="incident-ai",
                target_type="ai_action_confirmation",
                target_id=str(confirmation.id),
                message=_redact(f"AI high-risk action queued confirmation id={confirmation.id}, action={action.key}, source_trace={source_trace_id}"),
            )
        confirmations.append(confirmation)
    return confirmations


def list_confirmations(db: Session, status: Optional[str] = None, limit: int = 50) -> list[AiActionConfirmation]:
    bounded_limit = max(1, min(limit, 200))
    query = select(AiActionConfirmation)
    if status:
        query = query.where(AiActionConfirmation.status == _parse_status(status))
    query = query.order_by(AiActionConfirmation.created_at.desc()).limit(bounded_limit)
    return list(db.scalars(query))


def approve_confirmation(db: Session, confirmation_id: int, request: AiActionConfirmationDecisionRequest) -> tuple[AiActionConfirmation, Optional[str]]:
    confirmation = _get_confirmation(db, confirmation_id)
    _ensure_pending(confirmation)
    expected = confirmation_phrase(confirmation)
    if request.confirm_text.strip() != expected:
        raise ValueError(f"确认短语不匹配，请输入：{expected}")
    confirmation.status = AiActionConfirmationStatus.APPROVED
    confirmation.reviewed_by = _redact(request.operator.strip() or "admin")
    confirmation.confirmation_note = _redact(request.note)
    confirmation.reviewed_at = utc_now()
    confirmation.updated_at = utc_now()
    audit_trace_id = _write_decision_audit(db, confirmation, "approved", request.write_audit)
    db.commit()
    return confirmation, audit_trace_id


def reject_confirmation(db: Session, confirmation_id: int, request: AiActionConfirmationDecisionRequest) -> tuple[AiActionConfirmation, Optional[str]]:
    confirmation = _get_confirmation(db, confirmation_id)
    _ensure_pending(confirmation)
    confirmation.status = AiActionConfirmationStatus.REJECTED
    confirmation.reviewed_by = _redact(request.operator.strip() or "admin")
    confirmation.confirmation_note = _redact(request.note or "人工驳回高危动作")
    confirmation.reviewed_at = utc_now()
    confirmation.updated_at = utc_now()
    audit_trace_id = _write_decision_audit(db, confirmation, "rejected", request.write_audit)
    db.commit()
    return confirmation, audit_trace_id


def confirmation_phrase(confirmation: AiActionConfirmation) -> str:
    return f"CONFIRM-{confirmation.id}"


def confirmation_next_step(confirmation: AiActionConfirmation) -> str:
    status = confirmation.status.value if hasattr(confirmation.status, "value") else str(confirmation.status)
    if status == AiActionConfirmationStatus.PENDING.value:
        return "输入确认短语后只记录人工授权，不自动执行高危动作。"
    if status == AiActionConfirmationStatus.APPROVED.value:
        return "已授权，等待单独的受控执行入口或人工 runbook。"
    if status == AiActionConfirmationStatus.REJECTED.value:
        return "已驳回，AI 不得执行该高危动作。"
    return "已过期，需要重新运行事故 AI 评估。"


def _find_pending_confirmation(db: Session, action_key: str) -> Optional[AiActionConfirmation]:
    return db.scalars(
        select(AiActionConfirmation)
        .where(AiActionConfirmation.source == CONFIRMATION_SOURCE)
        .where(AiActionConfirmation.action_key == action_key)
        .where(AiActionConfirmation.status == AiActionConfirmationStatus.PENDING)
        .order_by(AiActionConfirmation.created_at.desc())
        .limit(1)
    ).first()


def _get_confirmation(db: Session, confirmation_id: int) -> AiActionConfirmation:
    confirmation = db.get(AiActionConfirmation, confirmation_id)
    if not confirmation:
        raise ValueError(f"确认项不存在：{confirmation_id}")
    return confirmation


def _ensure_pending(confirmation: AiActionConfirmation) -> None:
    status = confirmation.status.value if hasattr(confirmation.status, "value") else str(confirmation.status)
    if status != AiActionConfirmationStatus.PENDING.value:
        raise ValueError(f"确认项当前状态为 {status}，不能重复处理。")


def _parse_status(status: str) -> AiActionConfirmationStatus:
    normalized = status.strip().lower()
    for item in AiActionConfirmationStatus:
        if item.value == normalized:
            return item
    raise ValueError(f"未知确认状态：{status}")


def _confirmation_payload(action: AiIncidentAction, context: dict[str, object]) -> str:
    payload = {
        "action": action.model_dump(),
        "context": {
            "permission_model": context.get("permission_model"),
            "operator_context_loaded": context.get("operator_context_loaded"),
            "incident_status": context.get("incident_status"),
            "open_incidents": context.get("open_incidents"),
            "incident_keys": context.get("incident_keys", []),
        },
    }
    return _redact(json.dumps(payload, ensure_ascii=False))


def _write_decision_audit(db: Session, confirmation: AiActionConfirmation, decision: str, write_audit_enabled: bool) -> Optional[str]:
    if not write_audit_enabled:
        return None
    audit = write_audit(
        db,
        event_type=f"ai_action_confirmation_{decision}",
        severity=AuditSeverity.WARNING if decision == "approved" else AuditSeverity.INFO,
        actor=confirmation.reviewed_by or "admin",
        target_type="ai_action_confirmation",
        target_id=str(confirmation.id),
        message=_redact(f"AI high-risk action confirmation {decision}: id={confirmation.id}, action={confirmation.action_key}, note={confirmation.confirmation_note}"),
    )
    return audit.trace_id


def _redact(value: str) -> str:
    result = value or ""
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda match: match.group(0).split("=")[0].split(":")[0] + "=<redacted>", result)
    return result