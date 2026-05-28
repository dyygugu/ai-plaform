from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditSeverity(str, PyEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    severity: Mapped[AuditSeverity] = mapped_column(SAEnum(AuditSeverity), default=AuditSeverity.INFO, index=True)
    actor: Mapped[str] = mapped_column(String(128), default="system")
    target_type: Mapped[str] = mapped_column(String(128), default="")
    target_id: Mapped[str] = mapped_column(String(128), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
