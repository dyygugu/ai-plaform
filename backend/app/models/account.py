from datetime import datetime
from typing import Optional
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AccountStatus(str, PyEnum):
    ACTIVE = "active"
    NEEDS_LOGIN = "needs_login"
    STALE = "stale"
    DISABLED = "disabled"


class AidpAccount(Base):
    __tablename__ = "aidp_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[AccountStatus] = mapped_column(SAEnum(AccountStatus), default=AccountStatus.STALE, index=True)
    is_task_source: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    auth_mode: Mapped[str] = mapped_column(String(64), default="unknown")
    last_health_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
