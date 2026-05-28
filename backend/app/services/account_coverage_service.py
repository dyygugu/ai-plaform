from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.models.audit import AuditSeverity
from app.models.task import TaskCatalogItem, TaskVisibility
from app.schemas.account_coverage import (
    AccountCoverageBaselineRequest,
    AccountCoverageBaselineResponse,
    AccountCoverageSummaryResponse,
    AccountTaskCoverageRow,
    LoginStateReviewItem,
    TaskCoverageItem,
)
from app.services.audit_service import write_audit
from app.services.task_rules import utc_now
from app.services.task_service import get_task_source_account_user_id

EXPECTED_ACCOUNT_COUNT = 7


def build_account_coverage_summary(db: Session) -> AccountCoverageSummaryResponse:
    accounts = list(db.scalars(select(AidpAccount).order_by(AidpAccount.is_task_source.desc(), AidpAccount.user_id.asc())))
    tasks = list(db.scalars(select(TaskCatalogItem).order_by(TaskCatalogItem.source_account_user_id.asc(), TaskCatalogItem.task_id.asc())))
    task_source = get_task_source_account_user_id(db)
    tasks_by_account = _group_tasks_by_account(tasks)
    matrix = [_build_account_row(account, tasks_by_account.get(account.user_id, []), task_source) for account in accounts]
    task_items = _build_task_items(tasks)
    login_reviews = [_build_login_review(account) for account in accounts]
    source_task_count = len(tasks_by_account.get(task_source, []))
    covered_account_count = sum(1 for row in matrix if row.task_count > 0)
    needs_login_count = sum(1 for account in accounts if account.status == AccountStatus.NEEDS_LOGIN)
    stale_count = sum(1 for account in accounts if account.status == AccountStatus.STALE)
    uncovered_account_count = max(len(accounts) - covered_account_count, 0)
    status = _summary_status(len(accounts), source_task_count, needs_login_count)
    risk_notes = [
        "P16 只做覆盖基线与登录态复核，不复制 Cookie 明文，不触发外部系统。",
        "非主账号任务数为 0 时视为待采集覆盖，不阻塞当前 8789 本地验收。",
        "正式域名仍等 P20 封版后再提醒用户手动切换。",
    ]
    next_actions = [
        "确认 7 个迁移账号均在账号矩阵中出现。",
        "优先保证主任务来源账号有任务目录样本；其他账号后续按安全规则逐步补只读采集。",
        "若出现 needs_login，先人工恢复登录态再重新生成覆盖基线。",
    ]
    return AccountCoverageSummaryResponse(
        generated_at=utc_now(),
        status=status,
        account_count=len(accounts),
        expected_account_count=EXPECTED_ACCOUNT_COUNT,
        task_source_account_user_id=task_source,
        source_task_count=source_task_count,
        covered_account_count=covered_account_count,
        uncovered_account_count=uncovered_account_count,
        needs_login_count=needs_login_count,
        stale_count=stale_count,
        matrix=matrix,
        task_items=task_items,
        login_reviews=login_reviews,
        risk_notes=risk_notes,
        next_actions=next_actions,
        message="7 账号覆盖基线已生成；当前阶段只复核账号、任务目录和登录态，不切换正式域名。",
    )


def create_account_coverage_baseline(db: Session, request: AccountCoverageBaselineRequest) -> AccountCoverageBaselineResponse:
    summary = build_account_coverage_summary(db)
    trace_id = uuid4().hex
    report_path = _write_baseline_report(summary, trace_id) if request.generate_report else None
    audit_trace_id = None
    if request.write_audit:
        audit = write_audit(
            db,
            event_type="account_task_coverage_baseline",
            severity=AuditSeverity.INFO if summary.status == "passed" else AuditSeverity.WARNING,
            target_type="account_coverage",
            target_id=trace_id,
            message=f"P16 account coverage status={summary.status}, accounts={summary.account_count}, source_tasks={summary.source_task_count}, needs_login={summary.needs_login_count}",
        )
        db.commit()
        audit_trace_id = audit.trace_id
    return AccountCoverageBaselineResponse(
        generated_at=utc_now(),
        status=summary.status,
        report_path=report_path,
        audit_trace_id=audit_trace_id,
        summary=summary,
        message="账号任务覆盖基线已生成；未复制 Cookie，未触发外部系统。",
    )


def _group_tasks_by_account(tasks: list[TaskCatalogItem]) -> dict[str, list[TaskCatalogItem]]:
    grouped: dict[str, list[TaskCatalogItem]] = {}
    for task in tasks:
        grouped.setdefault(task.source_account_user_id, []).append(task)
    return grouped


def _build_account_row(account: AidpAccount, tasks: list[TaskCatalogItem], task_source: str) -> AccountTaskCoverageRow:
    visible_tasks = [task for task in tasks if task.visibility == TaskVisibility.VISIBLE]
    latest_seen = max((task.last_task_page_seen_at for task in tasks if task.last_task_page_seen_at), default=None)
    latest_error = next((task.last_task_page_error for task in tasks if task.last_task_page_error), None)
    pending_total = sum(_parse_pending(task.pending_raw) for task in visible_tasks)
    coverage_status = _coverage_status(account, visible_tasks, task_source)
    login_review = _build_login_review(account)
    return AccountTaskCoverageRow(
        user_id=account.user_id,
        display_name=account.display_name,
        account_status=account.status.value,
        is_task_source=account.user_id == task_source or account.is_task_source,
        auth_mode=account.auth_mode,
        task_count=len(tasks),
        visible_task_count=len(visible_tasks),
        pending_total=pending_total,
        latest_seen_at=latest_seen,
        latest_error=latest_error,
        coverage_status=coverage_status,
        login_review_status=login_review.review_status,
        recommended_action=_coverage_action(account, coverage_status),
    )


def _build_task_items(tasks: list[TaskCatalogItem]) -> list[TaskCoverageItem]:
    by_task_id: dict[str, list[TaskCatalogItem]] = {}
    for task in tasks:
        by_task_id.setdefault(task.task_id, []).append(task)
    items: list[TaskCoverageItem] = []
    for task_id, rows in sorted(by_task_id.items(), key=lambda pair: pair[0]):
        first = rows[0]
        source_ids = sorted({row.source_account_user_id for row in rows})
        items.append(
            TaskCoverageItem(
                task_id=task_id,
                task_short_name=first.task_short_name,
                task_name_id=first.task_name_id,
                covered_account_count=len(source_ids),
                source_account_user_ids=source_ids,
                pending_total=sum(_parse_pending(row.pending_raw) for row in rows),
                status_raw=first.task_status_raw,
            )
        )
    return items


def _build_login_review(account: AidpAccount) -> LoginStateReviewItem:
    if account.status == AccountStatus.NEEDS_LOGIN:
        review_status = "needs_action"
        reason = account.last_error or "账号标记为需要重新登录"
        action = "人工恢复登录态后重新运行 P16 覆盖基线。"
    elif account.status == AccountStatus.DISABLED:
        review_status = "disabled"
        reason = account.last_error or "账号已禁用"
        action = "确认是否仍需参与任务覆盖。"
    elif account.status == AccountStatus.ACTIVE:
        review_status = "passed"
        reason = "账号登录态已标记 active。"
        action = "保持巡检。"
    else:
        review_status = "review"
        reason = "迁移后等待只读采集或人工登录态复核。"
        action = "不复制 Cookie，按账号管理页提示做人工复核。"
    return LoginStateReviewItem(
        user_id=account.user_id,
        display_name=account.display_name,
        status=account.status.value,
        review_status=review_status,
        reason=reason,
        recommended_action=action,
    )


def _coverage_status(account: AidpAccount, visible_tasks: list[TaskCatalogItem], task_source: str) -> str:
    if account.status == AccountStatus.NEEDS_LOGIN:
        return "needs_login"
    if visible_tasks:
        return "covered"
    if account.user_id == task_source or account.is_task_source:
        return "source_empty"
    return "pending_sampling"


def _coverage_action(account: AidpAccount, coverage_status: str) -> str:
    if coverage_status == "covered":
        return "已有任务覆盖证据，继续巡检。"
    if coverage_status == "needs_login":
        return "先人工恢复登录态，再重新采集任务目录。"
    if coverage_status == "source_empty":
        return "主账号缺少任务目录样本，先运行任务页只读刷新。"
    return "待后续按安全规则补充该账号只读采集，不阻塞当前本地验收。"


def _summary_status(account_count: int, source_task_count: int, needs_login_count: int) -> str:
    if account_count != EXPECTED_ACCOUNT_COUNT or source_task_count == 0:
        return "warning"
    if needs_login_count > 0:
        return "warning"
    return "passed"


def _parse_pending(value: str) -> int:
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return 0


def _reports_root() -> Path:
    settings = get_settings()
    root = Path(settings.task_sample_root).resolve().parent / "reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_baseline_report(summary: AccountCoverageSummaryResponse, trace_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = _reports_root() / f"account-coverage-baseline-{stamp}.md"
    lines = [
        "# aidp-monitor-next P16 多账号任务覆盖与登录态基线",
        "",
        f"生成时间：{summary.generated_at.isoformat()}",
        f"trace_id：{trace_id}",
        f"状态：{summary.status}",
        f"账号数量：{summary.account_count}/{summary.expected_account_count}",
        f"主任务来源账号：{summary.task_source_account_user_id}",
        f"主账号任务数：{summary.source_task_count}",
        f"需登录账号：{summary.needs_login_count}",
        "",
        "## 账号覆盖矩阵",
    ]
    for row in summary.matrix:
        source_flag = "，任务来源" if row.is_task_source else ""
        lines.append(f"- {row.user_id} {row.display_name}{source_flag}：tasks={row.task_count}，pending={row.pending_total}，coverage={row.coverage_status}，login={row.login_review_status}")
    lines.extend(["", "## 任务覆盖"])
    for item in summary.task_items:
        lines.append(f"- {item.task_id} {item.task_short_name}：covered_accounts={item.covered_account_count}，pending_total={item.pending_total}")
    lines.extend(["", "## 风险提示"])
    for note in summary.risk_notes:
        lines.append(f"- {note}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return _display_path(path)


def _display_path(path: Path) -> str:
    settings = get_settings()
    runtime_root = Path(settings.task_sample_root).resolve().parent.parent
    try:
        return path.resolve().relative_to(runtime_root.resolve()).as_posix()
    except ValueError:
        pass
    for candidate in Path(__file__).resolve().parents:
        if candidate.name == "aidp-monitor-next":
            workspace = candidate.parents[1] if len(candidate.parents) > 1 else candidate
            try:
                return path.resolve().relative_to(workspace.resolve()).as_posix()
            except ValueError:
                break
    return str(path)