from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AccountTaskCoverageRow(BaseModel):
    user_id: str
    display_name: str
    account_status: str
    is_task_source: bool
    auth_mode: str
    task_count: int
    visible_task_count: int
    pending_total: int
    latest_seen_at: Optional[datetime]
    latest_error: Optional[str]
    coverage_status: str
    login_review_status: str
    recommended_action: str


class TaskCoverageItem(BaseModel):
    task_id: str
    task_short_name: str
    task_name_id: str
    covered_account_count: int
    source_account_user_ids: list[str]
    pending_total: int
    status_raw: str


class LoginStateReviewItem(BaseModel):
    user_id: str
    display_name: str
    status: str
    review_status: str
    reason: str
    recommended_action: str


class AccountCoverageSummaryResponse(BaseModel):
    generated_at: datetime
    status: str
    account_count: int
    expected_account_count: int
    task_source_account_user_id: str
    source_task_count: int
    covered_account_count: int
    uncovered_account_count: int
    needs_login_count: int
    stale_count: int
    matrix: list[AccountTaskCoverageRow]
    task_items: list[TaskCoverageItem]
    login_reviews: list[LoginStateReviewItem]
    risk_notes: list[str]
    next_actions: list[str]
    message: str


class AccountCoverageBaselineRequest(BaseModel):
    write_audit: bool = True
    generate_report: bool = True


class AccountCoverageBaselineResponse(BaseModel):
    generated_at: datetime
    status: str
    report_path: Optional[str] = None
    audit_trace_id: Optional[str] = None
    summary: AccountCoverageSummaryResponse
    message: str