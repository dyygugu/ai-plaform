from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.models.audit import AuditSeverity
from app.models.task import TaskCatalogItem
from app.models.worker import Worker, WorkerStatus
from app.schemas.inspection import InspectionCheckItem, InspectionChecklistResponse, InspectionRunRequest, InspectionRunResponse, InspectionSummaryResponse
from app.services.alerting_service import build_slo_summary
from app.services.audit_service import write_audit
from app.services.delivery_service import build_delivery_summary
from app.services.observability_service import build_collector_guard
from app.services.ops_job_service import build_release_gate
from app.services.task_rules import utc_now


def build_inspection_summary(db: Session) -> InspectionSummaryResponse:
    settings = get_settings()
    collector = build_collector_guard(db)
    release_checks, release_ready = build_release_gate(db)
    slo = build_slo_summary(db)
    delivery = build_delivery_summary(db)
    task_count = db.query(TaskCatalogItem).count()
    account_total = db.query(AidpAccount).count()
    needs_login = db.query(AidpAccount).filter(AidpAccount.status == AccountStatus.NEEDS_LOGIN).count()
    online_workers = db.query(Worker).filter(Worker.status == WorkerStatus.ONLINE).count()
    failed_release = [item.key for item in release_checks if item.required and item.status == "failed"]
    checks = [
        InspectionCheckItem(
            key="local_health",
            title="本地健康入口",
            status="passed",
            message=f"{settings.public_base_url}/api/v1/health 可作为本地验收健康入口。",
            evidence="/api/v1/health",
            recommended_action="保持 8789 新版容器运行，正式域名切换前继续使用本地入口复验。",
            details={"base_url": settings.public_base_url},
        ),
        InspectionCheckItem(
            key="task_catalog",
            title="任务目录基线",
            status="passed" if task_count > 0 else "warning",
            message=f"任务目录当前 {task_count} 条，账号 {account_total} 个，其中需登录 {needs_login} 个。",
            evidence="/api/v1/tasks/catalog",
            recommended_action="若任务目录为空或需登录账号非预期，先刷新只读样本并检查账号 Cookie。",
            details={"task_count": task_count, "account_total": account_total, "needs_login": needs_login},
        ),
        InspectionCheckItem(
            key="collector_guard",
            title="采集守护基线",
            status=collector.status,
            message=collector.message,
            evidence="/api/v1/observability/collector-guard",
            recommended_action="若 warning/failed，打开观测中心检查样本年龄、stale/error 和来源账号。",
            details={"sample_exists": collector.sample_exists, "sample_age_minutes": collector.sample_age_minutes or 0, "task_count": collector.task_count},
        ),
        InspectionCheckItem(
            key="worker_online",
            title="Worker 在线基线",
            status="passed" if online_workers >= 1 else "warning",
            message=f"当前在线 Worker {online_workers} 个。",
            evidence="/api/v1/workers",
            recommended_action="若为 0，检查 Worker 心跳、版本和最近日志摘要。",
            details={"online_workers": online_workers},
        ),
        InspectionCheckItem(
            key="release_gate",
            title="发布门禁基线",
            status="passed" if release_ready else "failed",
            message="发布门禁必需项通过。" if release_ready else f"发布门禁失败：{', '.join(failed_release)}",
            evidence="/api/v1/ops/release-gate",
            recommended_action="若 failed，保持正式域名指向当前稳定 upstream，逐项修复生产护栏。",
            details={"failed_required": failed_release},
        ),
        InspectionCheckItem(
            key="slo_alerts",
            title="SLO 告警基线",
            status=slo.overall_status,
            message=f"SLO 总体状态 {slo.overall_status}，指标 {len(slo.indicators)} 项。",
            evidence="/api/v1/alerts/slo",
            recommended_action="若 warning，打开告警中心查看事件和本地飞书预览；外部发送仍关闭。",
            details={"indicator_count": len(slo.indicators)},
        ),
        InspectionCheckItem(
            key="delivery_bundle",
            title="交付证据基线",
            status=delivery.status,
            message=delivery.message,
            evidence="/api/v1/delivery/summary",
            recommended_action="若 warning，通常表示正式域名仍待用户手动切换；交付前确认最新证据包。",
            details={"latest_report": delivery.latest_report.path, "screenshots": len(delivery.screenshots), "todo_unchecked": delivery.todo_unchecked_count},
        ),
        InspectionCheckItem(
            key="manual_domain_switch",
            title="正式域名手动状态",
            status="manual",
            message="manage.51gugu.uk 未由系统自动切换，仍等待用户最终手动改反代。",
            evidence="/api/v1/ops/domain-switch-runbook",
            recommended_action="人工验收完成后再手动切换正式反代，并保留旧 upstream 以便回滚。",
            details={"production_domain": "manage.51gugu.uk", "auto_switch": False},
        ),
    ]
    status = _rollup([item.status for item in checks])
    next_actions = _next_actions(checks)
    baseline = {
        "task_count": task_count,
        "account_total": account_total,
        "needs_login": needs_login,
        "online_workers": online_workers,
        "release_gate_ready": release_ready,
        "slo_status": slo.overall_status,
        "delivery_status": delivery.status,
    }
    return InspectionSummaryResponse(
        generated_at=utc_now(),
        status=status,
        base_url=settings.public_base_url,
        production_domain="manage.51gugu.uk",
        manual_domain_switch_required=True,
        checks=checks,
        next_actions=next_actions,
        baseline=baseline,
        message="日常巡检已生成；结果仅作为本地运行基线，不触发正式域名或外部系统变更。",
    )


def build_inspection_checklist(db: Session) -> InspectionChecklistResponse:
    summary = build_inspection_summary(db)
    risk_notes = [
        "巡检状态为 warning 时先看推荐动作，不要直接切换正式域名。",
        "样例账号 needs_login 会导致 SLO/巡检 warning，需由人工判断是否为预期样本状态。",
        "巡检报告写入本地 reports，不发送外部通知，不修改 manage.51gugu.uk。",
    ]
    rollback_notes = [
        "正式域名切换后异常时，立即恢复切换前 upstream。",
        "保留 8789 容器和巡检报告，用于定位差异。",
        "回滚后重新运行交付证据包、告警评估和日常巡检。",
    ]
    return InspectionChecklistResponse(generated_at=utc_now(), items=summary.checks, risk_notes=risk_notes, rollback_notes=rollback_notes)


def run_inspection(db: Session, request: InspectionRunRequest) -> InspectionRunResponse:
    trace_id = uuid4().hex
    summary = build_inspection_summary(db)
    report_path = None
    if request.generate_report:
        report_path = _write_inspection_report(summary, trace_id)
    audit_trace_id = None
    if request.write_audit:
        audit = write_audit(
            db,
            event_type="daily_inspection_run",
            severity=_audit_severity(summary.status),
            target_type="inspection",
            target_id=trace_id,
            message=f"P13 inspection status={summary.status}, checks={len(summary.checks)}, manual_domain_switch=true",
        )
        db.commit()
        audit_trace_id = audit.trace_id
    return InspectionRunResponse(
        trace_id=trace_id,
        status=summary.status,
        generated_at=utc_now(),
        report_path=report_path,
        audit_trace_id=audit_trace_id,
        summary=summary,
        message="日常巡检运行完成；已生成本地证据，不触发外部系统。",
    )


def _write_inspection_report(summary: InspectionSummaryResponse, trace_id: str) -> str:
    reports_root = _reports_root()
    reports_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = reports_root / f"inspection-run-{stamp}.md"
    lines = [
        "# aidp-monitor-next 日常巡检记录",
        "",
        f"生成时间：{summary.generated_at.isoformat()}",
        f"trace_id：{trace_id}",
        f"状态：{summary.status}",
        f"本地入口：{summary.base_url}",
        f"正式域名：{summary.production_domain}（手动切换：{summary.manual_domain_switch_required}）",
        "",
        "## 巡检项",
    ]
    for item in summary.checks:
        lines.append(f"- [{item.status}] {item.title}：{item.message}；建议：{item.recommended_action}；证据：{item.evidence}")
    lines.extend(["", "## 下一步动作"])
    for action in summary.next_actions:
        lines.append(f"- {action}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return _display_path(path)


def _next_actions(checks: list[InspectionCheckItem]) -> list[str]:
    actions = [item.recommended_action for item in checks if item.status in {"warning", "failed"} and item.recommended_action]
    actions.append("正式域名仍由用户在最终验收后手动切换；切换前保留旧 upstream 以便回滚。")
    return actions


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
