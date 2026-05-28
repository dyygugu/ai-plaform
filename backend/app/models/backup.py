from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BackupStatus(str, PyEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    backup_type: Mapped[str] = mapped_column(String(64), default="manual", index=True)
    status: Mapped[BackupStatus] = mapped_column(SAEnum(BackupStatus), default=BackupStatus.PLANNED, index=True)
    local_retention_days: Mapped[int] = mapped_column(Integer, default=7)
    external_retention_days: Mapped[int] = mapped_column(Integer, default=30)
    cleanup_time: Mapped[str] = mapped_column(String(16), default="03:30")
    target_path: Mapped[str] = mapped_column(String(512), default="/home/admin/aidp监控平台备份")
    message: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
