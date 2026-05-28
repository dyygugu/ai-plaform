from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MaintenanceJobStatus(str, PyEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    WARNING = "warning"
    FAILED = "failed"


class MaintenanceJobRun(Base):
    __tablename__ = "maintenance_job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_key: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[MaintenanceJobStatus] = mapped_column(SAEnum(MaintenanceJobStatus), default=MaintenanceJobStatus.RUNNING, index=True)
    trigger_type: Mapped[str] = mapped_column(String(64), default="manual", index=True)
    dry_run: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
