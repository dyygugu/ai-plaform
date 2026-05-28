from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum as SAEnum, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RestoreDrillStatus(str, PyEnum):
    PLANNED = "planned"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class RestoreDrill(Base):
    __tablename__ = "restore_drills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    status: Mapped[RestoreDrillStatus] = mapped_column(SAEnum(RestoreDrillStatus), default=RestoreDrillStatus.PLANNED, index=True)
    checklist_json: Mapped[str] = mapped_column(Text, default="[]")
    message: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EarningsSnapshot(Base):
    __tablename__ = "earnings_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_user_id: Mapped[str] = mapped_column(String(64), index=True)
    source_label: Mapped[str] = mapped_column(String(128), default="页面原始三项")
    income_1_name: Mapped[str] = mapped_column(String(128), default="收入项1")
    income_1_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    income_2_name: Mapped[str] = mapped_column(String(128), default="收入项2")
    income_2_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    income_3_name: Mapped[str] = mapped_column(String(128), default="收入项3")
    income_3_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    today_income: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    hourly_income: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
