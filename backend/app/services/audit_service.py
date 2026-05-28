from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.audit import AuditLog, AuditSeverity
from app.services.notification_service import send_error_notification


def write_audit(
    db: Session,
    event_type: str,
    message: str,
    severity: AuditSeverity = AuditSeverity.INFO,
    actor: str = "system",
    target_type: str = "",
    target_id: str = "",
) -> AuditLog:
    entry = AuditLog(
        event_type=event_type,
        severity=severity,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        message=message,
        trace_id=uuid4().hex,
    )
    db.add(entry)
    db.flush()
    if severity in {AuditSeverity.ERROR, AuditSeverity.CRITICAL}:
        send_error_notification(
            event="audit.error",
            level=severity.value,
            message=message,
            data={"event_type": event_type, "target_type": target_type, "target_id": target_id, "actor": actor},
            trace_id=entry.trace_id,
        )
    return entry
