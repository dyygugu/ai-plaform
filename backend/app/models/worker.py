from datetime import datetime
from typing import Optional
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkerStatus(str, PyEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"
    DISABLED = "disabled"


class WorkerLeaseStatus(str, PyEnum):
    ACTIVE = "active"
    RELEASED = "released"
    RECLAIMED = "reclaimed"
    TIMED_OUT = "timed_out"
    SUSPENDED = "suspended"


class WorkerCommandStatus(str, PyEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class WorkerEventType(str, PyEnum):
    HEARTBEAT = "heartbeat"
    BIND_ACCOUNT = "bind_account"
    VERSION_UPDATE = "version_update"
    TASK_CLAIM = "task_claim"
    EVENT_REPORT = "event_report"
    LOG_SUMMARY = "log_summary"
    COMMAND = "command"
    LEASE = "lease"


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    worker_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[WorkerStatus] = mapped_column(SAEnum(WorkerStatus), default=WorkerStatus.OFFLINE, index=True)
    version: Mapped[str] = mapped_column(String(64), default="unknown")
    current_account_user_id: Mapped[str] = mapped_column(String(64), default="")
    current_task_id: Mapped[str] = mapped_column(String(128), default="")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    is_platform_worker: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    estimated_http_account_slots: Mapped[int] = mapped_column(Integer, default=0)
    configured_http_account_slots: Mapped[int] = mapped_column(Integer, default=0)
    effective_http_account_slots: Mapped[int] = mapped_column(Integer, default=0)
    health_status: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    health_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    health_fail_reasons: Mapped[str] = mapped_column(Text, default="")
    disabled_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkerEvent(Base):
    __tablename__ = "worker_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    worker_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[WorkerEventType] = mapped_column(SAEnum(WorkerEventType), default=WorkerEventType.EVENT_REPORT, index=True)
    account_user_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    task_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    target_version: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[str] = mapped_column(String(32), default="info", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class WorkerAccountTaskLease(Base):
    __tablename__ = "worker_account_task_leases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    lease_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    worker_id: Mapped[str] = mapped_column(String(128), index=True)
    account_user_id: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[WorkerLeaseStatus] = mapped_column(SAEnum(WorkerLeaseStatus), default=WorkerLeaseStatus.ACTIVE, index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    recovery_type: Mapped[str] = mapped_column(String(64), default="", index=True)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    recovery_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    recovered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_reason: Mapped[str] = mapped_column(Text, default="")
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reclaimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkerCommand(Base):
    __tablename__ = "worker_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    command_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    retry_of_command_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    worker_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    command_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[WorkerCommandStatus] = mapped_column(SAEnum(WorkerCommandStatus), default=WorkerCommandStatus.QUEUED, index=True)
    account_user_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    task_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[str] = mapped_column(Text, default="")
    last_renewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    timed_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=180)
    audit_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
