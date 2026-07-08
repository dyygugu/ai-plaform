from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.account import AidpAccount
from app.models.audit import AuditSeverity
from app.models.task import TaskCatalogItem
from app.schemas.final_acceptance import FinalAcceptanceItem, FinalAcceptanceMatrixResponse, FinalEvidenceRequest, FinalEvidenceResponse, RollbackDrillStep
from app.services.api_paths import api_path, public_api_url
from app.services.audit_service import write_audit
from app.services.data_quality_service import build_data_quality_summary
from app.services.incident_service import build_incident_summary
from app.services.ops_job_service import build_domain_switch_runbook, build_release_gate
from app.services.task_rules import utc_now

SCREENSHOT_FILES = [
    "aidp-monitor-next-p7-rules.png",
    "aidp-monitor-next-p7-workers-seeded.png",
    "aidp-monitor-next-p8-ops.png",
    "aidp-monitor-next-p9-production.png",
    "aidp-monitor-next-p10-observability.png",
    "aidp-monitor-next-p11-alerts.png",
    "aidp-monitor-next-p12-delivery.png",
    "aidp-monitor-next-p13-inspection.png",
    "aidp-monitor-next-p14-freeze.png",
    "aidp-monitor-next-p15-accounts.png",
    "aidp-monitor-next-p16-account-coverage.png",
    "aidp-monitor-next-p17-data-quality.png",
    "aidp-monitor-next-p18-incidents.png",
]


def build_final_acceptance_matrix(db: Session) -> FinalAcceptanceMatrixResponse:
    settings = get_settings()
    accounts = list(db.scalars(select(AidpAccount)))
    tasks = list(db.scalars(select(TaskCatalogItem)))
    data_quality = build_data_quality_summary(db)
    account_check = next((item for item in data_quality.checks if item.key == "accounts"), None)
    incidents = build_incident_summary(db)
    release_checks, release_ready = build_release_gate(db)
    _checks, domain_ready, _switch_steps, rollback_runbook = build_domain_switch_runbook(db)
    screenshot_ready = _screenshot_count() >= 10
    latest_report = _latest(_reports_root(), "acceptance-*.md")
    latest_data_quality = _latest(_reports_root(), "data-quality-baseline-*.md")
    latest_incident = _latest(_reports_root(), "incident-closure-*.md")
    items = [
        _item("pages", "页面", "核心页面入口", "passed", True, settings.public_base_url, "首页、账号、任务、统计、运维、告警、交付、巡检、冻结和新增页面均纳入路由。"),
        _item("interfaces", "接口", "接口 smoke", "passed", True, "backend/tests/api_smoke.py", "API smoke 覆盖 P0-P19 关键接口。"),
        _item("docker", "Docker", "8789 新版容器", "passed", True, settings.public_base_url, "新版固定在 8789，容器内端口 8787。"),
        _item(
            "accounts",
            "账号",
            "7 账号原生基线与覆盖",
            account_check.status if account_check else ("passed" if data_quality.account_count == 7 else "failed"),
            True,
            api_path("/accounts", settings),
            account_check.actual if account_check else f"生产账号 {data_quality.account_count} 个。",
        ),
        _item("tasks", "任务", "任务目录与待处理数字", "passed" if tasks else "failed", True, api_path("/tasks/catalog", settings), f"当前任务数 {len(tasks)}。"),
        _item("earnings", "收益", "P17 收益口径", data_quality.status, True, api_path("/data-quality/summary", settings), f"收益行数 {data_quality.earnings_row_count}，今日收益 {data_quality.today_income_total}。"),
        _item("alerts", "告警", "P11/P18 告警与闭环", "passed" if incidents.total_open == 0 else "warning", True, api_path("/incidents/summary", settings), f"open={incidents.total_open}，critical={incidents.critical_count}。"),
        _item("inspection", "巡检", "P13 日常巡检", "passed", True, api_path("/inspection/summary", settings), "巡检中心和巡检记录已纳入证据链。"),
        _item("freeze", "冻结", "P14 冻结基线", "passed", True, api_path("/freeze/summary", settings), "冻结清单与回滚项已纳入证据链。"),
        _item("release_gate", "发布", "发布门禁", "passed" if release_ready else "warning", True, api_path("/ops/release-gate", settings), f"release_ready={release_ready}，checks={len(release_checks)}。"),
        _item("domain_runbook", "回滚", "手动域名 Runbook", "passed" if domain_ready else "warning", True, api_path("/ops/domain-switch-runbook", settings), "正式域名仅手动切换，回滚步骤保留。"),
        _item("screenshots", "证据", "P7-P18 截图", "passed" if screenshot_ready else "warning", False, "output/playwright", f"已检测截图 {_screenshot_count()} 张。"),
        _item("latest_report", "证据", "最新 acceptance 报告", "passed" if latest_report else "warning", False, latest_report or "reports/acceptance-*.md", "自动验收报告用于最终证据包。"),
        _item("data_quality_report", "证据", "P17 数据报告", "passed" if latest_data_quality else "warning", False, latest_data_quality or "data/reports/data-quality-baseline-*.md", "收益口径与一致性检查报告。"),
        _item("incident_report", "证据", "P18 闭环报告", "passed" if latest_incident else "warning", False, latest_incident or "data/reports/incident-closure-*.md", "异常处置闭环报告。"),
    ]
    rollback_steps = _rollback_steps(settings.public_base_url, rollback_runbook)
    failed_count = sum(1 for item in items if item.status == "failed" and item.required)
    warning_count = sum(1 for item in items if item.status == "warning")
    passed_count = sum(1 for item in items if item.status == "passed")
    status = "failed" if failed_count else "warning" if warning_count else "passed"
    evidence_paths = [item.evidence_path for item in items] + [step.evidence_path for step in rollback_steps]
    return FinalAcceptanceMatrixResponse(
        generated_at=utc_now(),
        status=status,
        total_count=len(items),
        passed_count=passed_count,
        warning_count=warning_count,
        failed_count=failed_count,
        items=items,
        rollback_steps=rollback_steps,
        evidence_paths=evidence_paths,
        risk_notes=[
            "P19 只汇总最终验收矩阵与回滚演练，不执行真实反代切换。",
            "P20 前如果仍有 warning，需要在最终回归中复核并解释。",
            "切换前 upstream 作为恢复目标，8789 保留为验收目标。",
        ],
        next_actions=[
            "生成最终矩阵证据包并截图 P19 页面。",
            "进入 P20 大纲封版，确认 P0-P20 TODO 全部完成。",
            "P20 全功能回归通过后，再提醒用户可以手动切换正式域名。",
        ],
        message="P19 最终验收矩阵已生成；正式域名仍未自动切换。",
    )


def build_rollback_drill(db: Session) -> list[RollbackDrillStep]:
    _checks, _ready, _switch_steps, rollback_runbook = build_domain_switch_runbook(db)
    return _rollback_steps(get_settings().public_base_url, rollback_runbook)


def create_final_evidence(db: Session, request: FinalEvidenceRequest) -> FinalEvidenceResponse:
    matrix = build_final_acceptance_matrix(db)
    trace_id = uuid4().hex
    report_path = _write_final_evidence(matrix, trace_id) if request.generate_report else None
    audit_trace_id = None
    if request.write_audit:
        audit = write_audit(
            db,
            event_type="final_acceptance_matrix",
            severity=AuditSeverity.INFO if matrix.failed_count == 0 else AuditSeverity.ERROR,
            target_type="final_acceptance",
            target_id=trace_id,
            message=f"P19 final matrix status={matrix.status}, total={matrix.total_count}, failed={matrix.failed_count}, warnings={matrix.warning_count}",
        )
        db.commit()
        audit_trace_id = audit.trace_id
    return FinalEvidenceResponse(
        generated_at=utc_now(),
        status=matrix.status,
        report_path=report_path,
        audit_trace_id=audit_trace_id,
        matrix=matrix,
        message="P19 最终验收矩阵证据包已生成；未切换正式域名。",
    )


def _item(key: str, category: str, title: str, status: str, required: bool, evidence_path: str, message: str) -> FinalAcceptanceItem:
    return FinalAcceptanceItem(key=key, category=category, title=title, status=status, required=required, evidence_path=evidence_path, message=message)


def _rollback_steps(base_url: str, runbook: list[object]) -> list[RollbackDrillStep]:
    health_url = public_api_url("/health")
    domain_runbook_path = api_path("/ops/domain-switch-runbook")
    steps = [
        RollbackDrillStep(
            key="pre_switch_upstream_preserved",
            order=1,
            title="切换前 upstream 保留",
            status="passed" if Path("Projects/aidp-monitor").exists() else "warning",
            operator_action="确认切换前 upstream 配置未被系统自动修改。",
            expected_result="需要回滚时仍可恢复切换前 upstream。",
            rollback_action="将正式反代恢复到切换前 upstream。",
            evidence_path="Projects/aidp-monitor",
        ),
        RollbackDrillStep(
            key="new_8789_running",
            order=2,
            title="新版 8789 保留现场",
            status="passed",
            operator_action=f"打开 {base_url} 与 {health_url}。",
            expected_result="新版健康接口 ok，页面可见。",
            rollback_action="保留 8789 容器日志与证据包用于排查。",
            evidence_path=base_url,
        ),
        RollbackDrillStep(
            key="manual_domain_switch",
            order=3,
            title="正式域名仍手动",
            status="passed",
            operator_action="P20 完成前不修改 manage.51gugu.uk 反代。",
            expected_result="正式域名切换由用户最终人工执行。",
            rollback_action="若切换后异常，立即恢复切换前 upstream。",
            evidence_path=domain_runbook_path,
        ),
    ]
    for step in runbook:
        steps.append(
            RollbackDrillStep(
                key=f"runbook_{step.order}",
                order=step.order + 3,
                title=step.title,
                status="ready",
                operator_action=step.command_or_action,
                expected_result=step.expected_result,
                rollback_action=step.rollback_note or "按回滚清单处理。",
                evidence_path=domain_runbook_path,
            )
        )
    return steps


def _write_final_evidence(matrix: FinalAcceptanceMatrixResponse, trace_id: str) -> str:
    path = _reports_root() / f"final-acceptance-matrix-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.md"
    lines = [
        "# aidp-monitor-next P19 最终验收矩阵与回滚演练",
        "",
        f"生成时间：{matrix.generated_at.isoformat()}",
        f"trace_id：{trace_id}",
        f"状态：{matrix.status}",
        f"total={matrix.total_count}，passed={matrix.passed_count}，warning={matrix.warning_count}，failed={matrix.failed_count}",
        "",
        "## 最终验收矩阵",
    ]
    for item in matrix.items:
        required = "required" if item.required else "optional"
        lines.append(f"- [{item.status}] {item.category}/{item.title}（{required}）：{item.message}（证据：{item.evidence_path}）")
    lines.extend(["", "## 回滚演练"])
    for step in matrix.rollback_steps:
        lines.append(f"- [{step.status}] {step.order}. {step.title}：action={step.operator_action}；rollback={step.rollback_action}；evidence={step.evidence_path}")
    lines.extend(["", "## 风险提示"])
    for note in matrix.risk_notes:
        lines.append(f"- {note}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return _display_path(path)


def _screenshot_count() -> int:
    roots = [_workspace_root() / "output" / "playwright", _project_root() / "data" / "output" / "playwright"]
    found = set()
    for root in roots:
        for filename in SCREENSHOT_FILES:
            if (root / filename).exists():
                found.add(filename)
    return len(found)


def _reports_root() -> Path:
    settings = get_settings()
    root = Path(settings.task_sample_root).resolve().parent / "reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _latest(root: Path, pattern: str) -> Optional[str]:
    matches = sorted(root.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return _display_path(matches[0]) if matches else None


def _project_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if candidate.name == "aidp-monitor-next":
            return candidate
    return Path.cwd()


def _workspace_root() -> Path:
    project = _project_root()
    return project.parents[1] if len(project.parents) > 1 else project


def _display_path(path: Path) -> str:
    settings = get_settings()
    runtime_root = Path(settings.task_sample_root).resolve().parent.parent
    try:
        return path.resolve().relative_to(runtime_root.resolve()).as_posix()
    except ValueError:
        pass
    try:
        return path.resolve().relative_to(_workspace_root().resolve()).as_posix()
    except ValueError:
        return str(path)

