from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.audit import AuditLog
from app.schemas.audit import AuditLogRead

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs", response_model=list[AuditLogRead])
def read_audit_logs(limit: int = 50, db: Session = Depends(get_db)) -> list[AuditLogRead]:
    bounded_limit = max(1, min(limit, 200))
    logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(bounded_limit)).all()
    return [AuditLogRead.model_validate(log) for log in logs]
