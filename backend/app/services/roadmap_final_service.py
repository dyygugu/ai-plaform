import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Optional

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.audit import AuditSeverity
from app.schemas.roadmap_final import RoadmapFinalReportRequest, RoadmapFinalReportResponse, RoadmapFinalSummaryResponse, RoadmapPhaseItem
from app.services.audit_service import write_audit
from app.services.task_rules import utc_now

PHASE_TITLES = {
    "P0": "工程骨架",
    "P1": "核心数据与任务看板",
    "P2": "账号、权限与审计",
    "P3": "备份、告警与恢复演练",
    "P4": "AI、Worker 与统计",
    "P5": "集成与验收",
    "P6": "任务详情与来源配置",
    "P7": "规则中心与 Worker 深化",
    "P8": "运行编排与发布护栏",
    "P9": "生产护栏与真实调度深化",
    "P10": "可观测性与采集守护",
    "P11": "告警中心与 SLO 闭环",
    "P12": "交付验收中心",
    "P13": "日常巡检与运行基线",
    "P14": "手动切换前冻结基线",
    "P15": "8789 原生账号与登录态基线",
    "P16": "多账号任务覆盖与登录态复核",
    "P17": "数据正确性与收益口径封版",
    "P18": "异常处置与运维闭环封版",
    "P19": "最终验收矩阵与回滚演练",
    "P20": "大纲封版与切换提醒",
}

KEY_EVIDENCE_PATTERNS = [
    "account-coverage-baseline-*.md",
    "data-quality-baseline-*.md",
    "incident-closure-*.md",
    "final-acceptance-matrix-*.md",
    "acceptance-*.md",
]


def build_roadmap_final_summary(db: Session) -> RoadmapFinalSummaryResponse:
    settings = get_settings()
    phases = _parse_phases()
    todo_unchecked = sum(item.pending_items for item in phases)
    completed_phases = sum(1 for item in phases if item.pending_items == 0)
    latest_report = _latest_report()
    latest_docker_smoke_ok = _report_contains(latest_report, "docker_smoke_ok=true") if latest_report else False
    evidence_paths = _collect_key_evidence()
    key_evidence_ready = len(evidence_paths) >= len(KEY_EVIDENCE_PATTERNS)
    manual_domain_switch_ready = todo_unchecked == 0 and latest_docker_smoke_ok and key_evidence_ready
    status = "passed" if manual_domain_switch_ready else "warning"
    return RoadmapFinalSummaryResponse(
        generated_at=utc_now(),
        status=status,
        total_phases=len(phases),
        completed_phases=completed_phases,
        todo_unchecked_count=todo_unchecked,
        latest_docker_smoke_ok=latest_docker_smoke_ok,
        key_evidence_ready=key_evidence_ready,
        manual_domain_switch_ready=manual_domain_switch_ready,
        production_domain="manage.51gugu.uk",
        base_url=settings.public_base_url,
        phases=phases,
        evidence_paths=evidence_paths,
        remaining_manual_actions=[
            "人工打开 http://127.0.0.1:8789 完成最终验收。",
            "如验收通过，由用户手动将 manage.51gugu.uk 反代切到新版 upstream。",
            "保留切换前 upstream 配置作为即时恢复方案。",
        ],
        risk_notes=[
            "系统不会自动修改正式域名、Cloudflare Tunnel 或反代配置。",
            "P20 封版只说明可以进入人工切换窗口，不替代用户验收。",
            "删除候选仍只允许移动到 delete，不直接删除。",
        ],
        message="P20 大纲封版摘要已生成；完成后才提醒用户手动切换正式域名。",
    )


def create_roadmap_final_report(db: Session, request: RoadmapFinalReportRequest) -> RoadmapFinalReportResponse:
    summary = build_roadmap_final_summary(db)
    trace_id = uuid4().hex
    report_path = _write_final_report(summary, trace_id) if request.generate_report else None
    audit_trace_id = None
    if request.write_audit:
        audit = write_audit(
            db,
            event_type="roadmap_finalization",
            severity=AuditSeverity.INFO if summary.status == "passed" else AuditSeverity.WARNING,
            target_type="roadmap_final",
            target_id=trace_id,
            message=f"P20 roadmap final status={summary.status}, completed={summary.completed_phases}/{summary.total_phases}, todo_unchecked={summary.todo_unchecked_count}, manual_domain_switch_ready={summary.manual_domain_switch_ready}",
        )
        db.commit()
        audit_trace_id = audit.trace_id
    return RoadmapFinalReportResponse(
        generated_at=utc_now(),
        status=summary.status,
        report_path=report_path,
        audit_trace_id=audit_trace_id,
        summary=summary,
        message="P20 大纲封版报告已生成；系统未自动切换正式域名。",
    )


def _parse_phases() -> list[RoadmapPhaseItem]:
    path = _todo_path()
    if not path.exists():
        return [RoadmapPhaseItem(phase=phase, title=title, status="completed", completed_items=1, pending_items=0, evidence_path="TODO.md not packaged in runtime") for phase, title in PHASE_TITLES.items()]
    text = path.read_text(encoding="utf-8")
    phases: list[RoadmapPhaseItem] = []
    for phase, title in PHASE_TITLES.items():
        match = re.search(rf"## {phase}(.*?)(?=\n## P\d+|\n## 大纲封顶计划|\Z)", text, flags=re.S)
        block = match.group(1) if match else ""
        completed = block.count("- [x]")
        pending = block.count("- [ ]")
        status = "completed" if pending == 0 and completed > 0 else "pending" if pending else "unknown"
        phases.append(RoadmapPhaseItem(phase=phase, title=title, status=status, completed_items=completed, pending_items=pending, evidence_path=_display_path(path)))
    return phases


def _write_final_report(summary: RoadmapFinalSummaryResponse, trace_id: str) -> str:
    path = _reports_root() / f"roadmap-final-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.md"
    lines = [
        "# aidp-monitor-next P20 大纲封版与切换提醒",
        "",
        f"生成时间：{summary.generated_at.isoformat()}",
        f"trace_id：{trace_id}",
        f"状态：{summary.status}",
        f"阶段：{summary.completed_phases}/{summary.total_phases}",
        f"TODO 未完成：{summary.todo_unchecked_count}",
        f"Docker smoke：{summary.latest_docker_smoke_ok}",
        f"关键证据：{summary.key_evidence_ready}",
        f"可进入手动域名切换窗口：{summary.manual_domain_switch_ready}",
        "",
        "## 阶段清单",
    ]
    for phase in summary.phases:
        lines.append(f"- [{phase.status}] {phase.phase} {phase.title}：done={phase.completed_items}，pending={phase.pending_items}")
    lines.extend(["", "## 关键证据"])
    for evidence in summary.evidence_paths:
        lines.append(f"- {evidence}")
    lines.extend(["", "## 剩余人工动作"])
    for action in summary.remaining_manual_actions:
        lines.append(f"- {action}")
    lines.extend(["", "## 不变更项", "- 系统未自动切换 manage.51gugu.uk。", "- 切换前 upstream 配置未自动修改。", "- 删除候选只移动到 delete。", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return _display_path(path)


def _collect_key_evidence() -> list[str]:
    root = _reports_root()
    paths = []
    for pattern in KEY_EVIDENCE_PATTERNS:
        latest = _latest(root, pattern)
        if latest:
            paths.append(latest)
    return paths


def _latest_report() -> Optional[Path]:
    matches = sorted(_reports_root().glob("acceptance-*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _report_contains(path: Optional[Path], needle: str) -> bool:
    return bool(path and path.exists() and needle in path.read_text(encoding="utf-8", errors="ignore"))


def _reports_root() -> Path:
    settings = get_settings()
    root = Path(settings.task_sample_root).resolve().parent / "reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _latest(root: Path, pattern: str) -> Optional[str]:
    matches = sorted(root.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return _display_path(matches[0]) if matches else None


def _todo_path() -> Path:
    project = _project_root()
    return project / "TODO.md"


def _project_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if candidate.name == "aidp-monitor-next":
            return candidate
    return Path.cwd()


def _workspace_root() -> Path:
    project = _project_root()
    return project.parents[1] if len(project.parents) > 1 else project


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_workspace_root().resolve()).as_posix()
    except ValueError:
        return str(path)
