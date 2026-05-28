from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScoreLoopCaseStatus(str, PyEnum):
    CAPTURED = "captured"
    UNSUPPORTED_PAUSED = "unsupported_paused"
    DRAFT_READY = "draft_ready"
    MANUAL_APPROVED = "manual_approved"
    MANUAL_REJECTED = "manual_rejected"
    SUBMIT_CONFIRMATION_REQUIRED = "submit_confirmation_required"


class ScoreLoopCase(Base):
    __tablename__ = "score_loop_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    status: Mapped[ScoreLoopCaseStatus] = mapped_column(SAEnum(ScoreLoopCaseStatus), default=ScoreLoopCaseStatus.CAPTURED, index=True)
    task_type_key: Mapped[str] = mapped_column(String(128), index=True)
    task_type_name: Mapped[str] = mapped_column(String(256), default="")
    task_catalog_item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    account_user_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    question_hash: Mapped[str] = mapped_column(String(64), index=True)
    question_text: Mapped[str] = mapped_column(Text, default="")
    choices_json: Mapped[str] = mapped_column(Text, default="[]")
    ai_answer: Mapped[str] = mapped_column(Text, default="")
    ai_reason: Mapped[str] = mapped_column(Text, default="")
    final_answer: Mapped[str] = mapped_column(Text, default="")
    manual_decision: Mapped[str] = mapped_column(String(32), default="", index=True)
    manual_note: Mapped[str] = mapped_column(Text, default="")
    submit_confirmation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)