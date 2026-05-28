from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RuleVersionStatus(str, PyEnum):
    DRAFT = "draft"
    CANARY = "canary"
    PUBLISHED = "published"
    ROLLED_BACK = "rolled_back"


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[RuleVersionStatus] = mapped_column(SAEnum(RuleVersionStatus), default=RuleVersionStatus.DRAFT, index=True)
    rule_json: Mapped[str] = mapped_column(Text, default="{}")
    changelog: Mapped[str] = mapped_column(Text, default="")
    canary_percent: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RulePublishEvent(Base):
    __tablename__ = "rule_publish_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    rule_version_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    from_status: Mapped[str] = mapped_column(String(64), default="")
    to_status: Mapped[str] = mapped_column(String(64), default="")
    canary_percent: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(128), default="system")
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RuleHitStat(Base):
    __tablename__ = "rule_hit_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    rule_version_id: Mapped[int] = mapped_column(Integer, index=True)
    rule_key: Mapped[str] = mapped_column(String(128), index=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    misses: Mapped[int] = mapped_column(Integer, default=0)
    sample_task_name_id: Mapped[str] = mapped_column(String(700), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
