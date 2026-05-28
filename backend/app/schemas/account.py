from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AccountRead(BaseModel):
    id: int
    user_id: str
    display_name: str
    custom_name: str = ""
    note: str = ""
    status: str
    is_task_source: bool
    auth_mode: str
    last_health_at: Optional[datetime]
    last_error: Optional[str]

    model_config = {"from_attributes": True}


class TaskSourceConfig(BaseModel):
    task_source_account_user_id: str
    editable: bool = True


class AccountLoginSlotRead(BaseModel):
    login_session_id: str
    user_id: str
    display_name: str
    status: str
    auth_mode: str
    pending_login: bool
    enabled: bool
    cdp_port: int
    launcher_start_command: str
    open_profile_url: str
    sync_url: str
    monitor_url: str
    instructions: list[str]
    created_at: Optional[datetime] = None


class AccountLoginSlotCreateRequest(BaseModel):
    display_name: Optional[str] = None
    cdp_port: Optional[int] = None


class AccountClientSessionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cookie: str = ""
    user_id: Optional[str] = Field(default=None, alias="userId")
    user_id_candidates: list[str] = Field(default_factory=list, alias="userIdCandidates")
    name: Optional[str] = None
    display_name: Optional[str] = Field(default=None, alias="displayName")
    authoritative_user_id: Optional[str] = Field(default=None, alias="authoritativeUserId")
    authoritative_name: Optional[str] = Field(default=None, alias="authoritativeName")
    user_info_source: Optional[str] = Field(default=None, alias="userInfoSource")
    title: Optional[str] = None
    href: Optional[str] = None
    referer: Optional[str] = None
    cdp_port: Optional[int] = Field(default=None, alias="cdpPort")
    login_session_id: Optional[str] = Field(default=None, alias="loginSessionId")
    synced_from: Optional[str] = Field(default=None, alias="syncedFrom")
    synced_at: Optional[str] = Field(default=None, alias="syncedAt")


class AccountClientSessionResponse(BaseModel):
    ok: bool
    user_id: str
    display_name: str
    account_status: str
    session_saved: bool
    cookie_saved: bool
    audit_trace_id: Optional[str] = None
    message: str


class AccountUsernameRefreshItem(BaseModel):
    user_id: str
    display_name: str = ""
    source: str = ""
    updated: bool = False
    error: Optional[str] = None


class AccountUsernameRefreshResponse(BaseModel):
    ok: bool
    updated_count: int
    items: list[AccountUsernameRefreshItem]
    message: str


class AccountMetadataUpdate(BaseModel):
    custom_name: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=500)


class AccountMetadataRead(BaseModel):
    ok: bool = True
    user_id: str
    display_name: str
    custom_name: str = ""
    note: str = ""
    message: str
