from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ProductionIncomeItem(BaseModel):
    name: str
    value: str
    source: str = ""


class ProductionTaskStat(BaseModel):
    task_id: str
    task_name: str
    delivered: int = 0
    abandoned: int = 0
    processing: int = 0
    in_progress: int = 0
    repair: int = 0
    pending: int = 0
    source: str = ""
    stale: bool = False
    stale_reason: str = ""
    receive_enabled: bool = False
    operation_url_ok: bool = False
    auto_receive_ready: bool = False
    auto_receive_block_reason: str = ""
    error: Optional[str] = None


class ProductionAccountCard(BaseModel):
    user_id: str
    display_name: str
    custom_name: str = ""
    note: str = ""
    real_name_ok: bool
    status: str
    status_label: str
    auth_mode: str
    auth_mode_label: str
    is_task_source: bool = False
    cookie_synced: bool = False
    data_stale: bool = False
    stale_reason: str = ""
    last_refresh_at: Optional[str] = None
    task_page_url: str
    personal_center_url: str
    task_open_url: str
    personal_open_url: str
    relogin_open_url: str
    income_items: list[ProductionIncomeItem] = Field(default_factory=list)
    total_income: str = "0.00"
    current_month_income: str = "0.00"
    withdrawable_amount: str = "0.00"
    task_stats: list[ProductionTaskStat] = Field(default_factory=list)
    task_count: int = 0
    delivered_total: int = 0
    abandoned_total: int = 0
    processing_total: int = 0
    in_progress_total: int = 0
    repair_total: int = 0
    pending_total: int = 0
    warning: str = ""


class ProductionDashboardSummary(BaseModel):
    generated_at: datetime
    account_count: int
    active_account_count: int
    stale_account_count: int
    task_count: int
    pending_total: int
    delivered_total: int
    abandoned_total: int
    processing_total: int
    in_progress_total: int
    repair_total: int
    refresh_interval_minutes: int = 15
    last_refresh_started_at: Optional[str] = None
    last_refresh_finished_at: Optional[str] = None
    next_refresh_at: Optional[str] = None
    global_stale: bool = False
    global_warning: str = ""
    task_source_account_user_id: str = ""
    accounts: list[ProductionAccountCard] = Field(default_factory=list)
    support_pages: list[str] = Field(default_factory=list)
    message: str


class BrowserOpenTargetResponse(BaseModel):
    ok: bool
    user_id: str
    target: str
    open_url: str
    message: str


class ProductionAccountRefreshItem(BaseModel):
    user_id: str
    display_name: str = ""
    status: str
    task_count: int = 0
    error: Optional[str] = None


class ProductionAccountRefreshResponse(BaseModel):
    ok: bool
    status: str
    refreshed_count: int
    failed_count: int
    started_at: str
    finished_at: str
    state_path: str
    items: list[ProductionAccountRefreshItem] = Field(default_factory=list)
    message: str


class ProductionAutoRefreshStatusRead(BaseModel):
    enabled: bool
    running: bool = False
    run_count: int = 0
    last_ok: Optional[bool] = None
    last_error: Optional[str] = None
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    interval_seconds: int = 0
    next_run_at: Optional[str] = None
    message: str = ""


class DeletedProductionAccountRead(BaseModel):
    user_id: str
    display_name: str = ""
    status_label: str = "已删除"
    deleted_at: Optional[str] = None
    delete_reason: str = ""
    cookie_preserved: bool = False
    profile_preserved: bool = False


class AccountRecycleActionResponse(BaseModel):
    ok: bool
    user_id: str
    message: str
