from datetime import datetime
from typing import Optional
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskVisibility(str, PyEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    RESTORED = "restored"


class TaskStatusColor(str, PyEnum):
    GREEN = "green"
    BLUE = "blue"
    GRAY = "gray"
    RED = "red"
    YELLOW = "yellow"


class TaskCatalogItem(Base):
    __tablename__ = "task_catalog_items"
    __table_args__ = (UniqueConstraint("source_account_user_id", "task_id", name="uq_task_catalog_source_task"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_account_user_id: Mapped[str] = mapped_column(String(64), index=True)
    raw_task_name: Mapped[str] = mapped_column(String(512))
    task_short_name: Mapped[str] = mapped_column(String(512))
    task_id: Mapped[str] = mapped_column(String(128), index=True)
    task_name_id: Mapped[str] = mapped_column(String(700), index=True)
    task_status_raw: Mapped[str] = mapped_column(String(128), default="未知")
    task_status_color: Mapped[TaskStatusColor] = mapped_column(SAEnum(TaskStatusColor), default=TaskStatusColor.YELLOW)
    pending_raw: Mapped[str] = mapped_column(String(128), default="")
    visibility: Mapped[TaskVisibility] = mapped_column(SAEnum(TaskVisibility), default=TaskVisibility.VISIBLE, index=True)
    last_task_page_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_task_page_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TaskCatalogEvent(Base):
    __tablename__ = "task_catalog_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_catalog_item_id: Mapped[int] = mapped_column(Integer, index=True)
    source_account_user_id: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    status_raw: Mapped[str] = mapped_column(String(128), default="")
    pending_raw: Mapped[str] = mapped_column(String(128), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TaskRuleConfig(Base):
    __tablename__ = "task_rule_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    prefix_rules_json: Mapped[str] = mapped_column(Text, default="[]")
    manual_short_names_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RuntimeConfig(Base):
    __tablename__ = "runtime_configs"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(128), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
