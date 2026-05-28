from typing import Optional

from pydantic import BaseModel, Field


class NotificationConfigRead(BaseModel):
    ok: bool = True
    enabled: bool
    provider: str = "feishu-webhook"
    webhook_url: str = ""
    webhook_configured: bool
    secret_configured: bool
    min_level: str
    events: list[str] = Field(default_factory=list)
    dry_run: bool
    cooldown_seconds: int
    sends_network: bool
    config_path: str
    source: str
    message: str


class NotificationConfigUpdate(BaseModel):
    enabled: bool = True
    webhook_url: Optional[str] = None
    secret: Optional[str] = None
    min_level: str = "warn"
    events: list[str] = Field(default_factory=list)
    dry_run: bool = True
    cooldown_seconds: int = 300


class NotificationTestRequest(BaseModel):
    send: bool = False


class NotificationSendResponse(BaseModel):
    ok: bool
    sent: bool
    skipped: bool = False
    dry_run: bool = False
    level: str
    event: str
    trace_id: str
    reason: str = ""
    status_code: Optional[int] = None
    message: str
