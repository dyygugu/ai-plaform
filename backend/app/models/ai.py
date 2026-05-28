from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AiJobStatus(str, PyEnum):
    PLANNED = "planned"
    MOCK_COMPLETED = "mock_completed"
    PROVIDER_GATED = "provider_gated"
    FAILED = "failed"


class AiJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    status: Mapped[AiJobStatus] = mapped_column(SAEnum(AiJobStatus), default=AiJobStatus.PLANNED, index=True)
    prompt_summary: Mapped[str] = mapped_column(String(256), default="")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    queue_wait_ms: Mapped[int] = mapped_column(Integer, default=0)
    upstream_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiActionConfirmationStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AiActionConfirmation(Base):
    __tablename__ = "ai_action_confirmations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    status: Mapped[AiActionConfirmationStatus] = mapped_column(SAEnum(AiActionConfirmationStatus), default=AiActionConfirmationStatus.PENDING, index=True)
    action_key: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    risk_level: Mapped[str] = mapped_column(String(32), default="high", index=True)
    source: Mapped[str] = mapped_column(String(64), default="incident_ai", index=True)
    source_trace_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    source_ai_job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    rollback_hint: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    requested_by: Mapped[str] = mapped_column(String(128), default="incident-ai")
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    confirmation_note: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)