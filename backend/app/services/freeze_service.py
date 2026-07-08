from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.audit import AuditSeverity
from app.schemas.freeze import FreezeChecklistResponse, FreezeCreateRequest, FreezeCreateResponse, FreezeItem, FreezeSummaryResponse
from app.services.api_paths import api_path, public_api_url
from app.services.audit_service import write_audit
from app.services.delivery_service import build_delivery_summary
from app.services.inspection_service import build_inspection_summary
from app.services.ops_job_service import build_domain_switch_runbook, build_release_gate
from app.services.task_rules import utc_now


def build_freeze_summary(db: Session) -> FreezeSummaryResponse:
    settings = get_settings()
    release_checks, release_ready = build_release_gate(db)
    delivery = build_delivery_summary(db)
    inspection = build_inspection_summary(db)
    _, runbook_ready, runbook_steps, rollback_steps = build_domain_switch_runbook(db)
    required_failed = [item.key for item in release_checks if item.required and item.status == "failed"]
    domain_runbook_path = api_path("/ops/domain-switch-runbook", settings)
    freeze_items = [
        FreezeItem(
            key="local_acceptance_entry",
            title="固定本地验收入口",
            status="passed",
            evidence=public_api_url("/health", settings),
            owner="system",
            action=f"继续以 {settings.public_base_url} 作为切换前验收入口。",
            rollback="若验收失败，不修改正式反代。",
            details={"base_url": settings.public_base_url},
        ),
        FreezeItem(
            key="release_gate",
            title="发布门禁冻结",
            status="passed" if release_ready else "failed",
            evidence=api_path("/ops/release-gate", settings),
            owner="operator",
            action="确认所有必需门禁通过。" if release_ready else f"先修复失败门禁：{', '.join(required_failed)}",
            rollback="任一必需门禁失败时，正式域名保持当前 upstream。",
            details={"ready": release_ready, "failed_required": required_failed},
        ),
        FreezeItem(
            key="delivery_bundle",
            title="交付证据冻结",
            status=delivery.status,
            evidence=delivery.latest_report.path,
            owner="system",
            action="确认最新验收报告、截图索引和证据包存在。",
            rollback="若证据缺失，重新运行交付中心生成证据包。",
            details={"todo_unchecked": delivery.todo_unchecked_count, "screenshots": len(delivery.screenshots)},
        ),
        FreezeItem(
            key="daily_inspection",
            title="巡检基线冻结",
            status=inspection.status,
            evidence=api_path("/inspection/summary", settings),
            owner="system",
            action="确认巡检 warning 是否为预期样例账号/手动域名提醒。",
            rollback="若存在非预期 failed，先修复再重新生成冻结清单。",
            details=inspection.baseline,
        ),
        FreezeItem(
            key="domain_runbook",
            title="正式域名 Runbook 冻结",
            status="manual" if runbook_ready else "failed",
            evidence=domain_runbook_path,
            owner="user",
            action="用户最终手动修改 manage.51gugu.uk 反代。",
            rollback="保留切换前 upstream，异常时立即恢复。",
            details={"manual_only": True, "step_count": len(runbook_steps)},
        ),
    ]
    rollback_items = [
        FreezeItem(
            key=f"rollback_{step.order}",
            title=step.title,
            status="manual",
            evidence=domain_runbook_path,
            owner="user",
            action=step.command_or_action,
            rollback=step.expected_result,
            details={"order": step.order},
        )
        for step in rollback_steps
    ]
    status = _rollup([item.status for item in freeze_items])
    evidence_paths = [delivery.latest_report.path] + [item.path for item in delivery.screenshots if item.exists]
    return FreezeSummaryResponse(
        generated_at=utc_now(),
        status=status,
        base_url=settings.public_base_url,
        production_domain="manage.51gugu.uk",
        manual_only=True,
        ready_for_manual_switch=release_ready and not any(item.status == "failed" for item in freeze_items),
        freeze_items=freeze_items,
        rollback_items=rollback_items,
        evidence_paths=evidence_paths,
        message="冻结基线已生成；系统不会自动切换正式域名，等待用户手动确认反代。",
    )


def build_freeze_checklist(db: Session) -> FreezeChecklistResponse:
    summary = build_freeze_summary(db)
    manual_confirmation_items = [
        "确认 http://127.0.0.1:8789 首页、任务、规则、Worker、生产护栏、观测、告警、交付、巡检、冻结中心均可打开。",
        "确认 manage.51gugu.uk 切换动作由用户手动执行。",
        "确认已保存切换前 upstream/反代配置，异常时可恢复。",
        "确认当前 warning 是否均为样例账号需登录或手动域名提醒。",
    ]
    risk_notes = [
        "冻结清单不是切换命令；它只保存切换前状态和证据。",
        "若冻结项出现 failed，不要切换正式域名。",
        "切换后优先验证 health、tasks、production、delivery、inspection 页面。",
    ]
    return FreezeChecklistResponse(
        generated_at=utc_now(),
        freeze_items=summary.freeze_items,
        rollback_items=summary.rollback_items,
        manual_confirmation_items=manual_confirmation_items,
        risk_notes=risk_notes,
    )


def create_freeze_baseline(db: Session, request: FreezeCreateRequest) -> FreezeCreateResponse:
    trace_id = uuid4().hex
    summary = build_freeze_summary(db)
    report_path = None
    if request.generate_report:
        report_path = _write_freeze_report(summary, trace_id)
    audit_trace_id = None
    if request.write_audit:
        audit = write_audit(
            db,
            event_type="manual_switch_freeze_baseline",
            severity=_audit_severity(summary.status),
            target_type="freeze",
            target_id=trace_id,
            message=f"P14 freeze baseline status={summary.status}, manual_only=true, ready={summary.ready_for_manual_switch}",
        )
        db.commit()
        audit_trace_id = audit.trace_id
    return FreezeCreateResponse(
        trace_id=trace_id,
        status=summary.status,
        generated_at=utc_now(),
        report_path=report_path,
        audit_trace_id=audit_trace_id,
        summary=summary,
        message="冻结基线已生成；未修改正式域名，未触发外部系统。",
    )


def _write_freeze_report(summary: FreezeSummaryResponse, trace_id: str) -> str:
    reports_root = _reports_root()
    reports_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = reports_root / f"freeze-baseline-{stamp}.md"
    lines = [
        "# aidp-monitor-next 手动切换前冻结基线",
        "",
        f"生成时间：{summary.generated_at.isoformat()}",
        f"trace_id：{trace_id}",
        f"状态：{summary.status}",
        f"本地入口：{summary.base_url}",
        f"正式域名：{summary.production_domain}",
        f"仅手动切换：{summary.manual_only}",
        f"建议进入手动切换：{summary.ready_for_manual_switch}",
        "",
        "## 冻结项",
    ]
    for item in summary.freeze_items:
        lines.append(f"- [{item.status}] {item.title}：{item.action}；证据：{item.evidence}；回滚：{item.rollback}")
    lines.extend(["", "## 回滚项"])
    for item in summary.rollback_items:
        lines.append(f"- {item.title}：{item.action}；预期：{item.rollback}")
    lines.extend(["", "## 证据路径"])
    for evidence in summary.evidence_paths:
        lines.append(f"- {evidence}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return _display_path(path)


def _rollup(statuses: list[str]) -> str:
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "warning" for status in statuses):
        return "warning"
    return "passed"


def _audit_severity(status: str) -> AuditSeverity:
    if status == "failed":
        return AuditSeverity.ERROR
    if status == "warning":
        return AuditSeverity.WARNING
    return AuditSeverity.INFO


def _find_project_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "TODO.md").exists() or (candidate / "reports").exists():
            return candidate
    return Path.cwd()


def _reports_root() -> Path:
    project_root = _find_project_root()
    source_root = project_root / "reports"
    if source_root.exists() or (project_root / "TODO.md").exists():
        return source_root
    settings = get_settings()
    return Path(settings.task_sample_root).resolve().parent / "reports"


def _display_path(path: Path) -> str:
    project_root = _find_project_root()
    workspace = project_root.parents[1] if project_root.name == "aidp-monitor-next" and len(project_root.parents) > 1 else project_root
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path)
