from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.ai import AiActionConfirmation, AiActionConfirmationStatus
from app.models.audit import AuditLog, AuditSeverity
from app.models.score_loop import ScoreLoopCase, ScoreLoopCaseStatus
from app.schemas.alerting import AlertEvaluationRequest, AlertIncident
from app.schemas.incident import IncidentClosureCheck, IncidentClosurePlanResponse, IncidentClosureRequest, IncidentClosureResponse, IncidentQueueItem, IncidentRunbookItem, IncidentSummaryResponse
from app.services.alerting_service import evaluate_alerts, list_alert_rules
from app.services.api_paths import api_path
from app.services.audit_service import write_audit
from app.services.data_quality_service import build_data_quality_summary
from app.services.task_rules import utc_now


def build_incident_summary(db: Session) -> IncidentSummaryResponse:
    evaluation = evaluate_alerts(db, AlertEvaluationRequest(dry_run=True, write_audit=False, send_external=False))
    runbooks = _build_runbooks(db)
    incidents = [_queue_item(incident, _steps_for_key(incident.key)) for incident in evaluation.incidents]
    critical_count = sum(1 for item in incidents if item.severity == "critical")
    warning_count = sum(1 for item in incidents if item.severity == "warning")
    status = "failed" if critical_count else "warning" if warning_count else "passed"
    return IncidentSummaryResponse(
        generated_at=utc_now(),
        status=status,
        total_open=len(incidents),
        critical_count=critical_count,
        warning_count=warning_count,
        runbook_count=len(runbooks),
        external_send_enabled=False,
        incidents=incidents,
        runbooks=runbooks,
        risk_notes=[
            "P18 只生成本地异常处置记录和审计 trace，不发送飞书、不触发外部系统。",
            "critical 异常必须先按 runbook 处理，再进入 P19 最终矩阵。",
            "所有删除候选仍只移动到 delete，禁止直接删除。",
        ],
        next_actions=[
            "在异常处置中心检查账号、采集、Worker、备份、审计和数据质量 runbook。",
            "生成本地闭环记录，确认 report_path 与 audit_trace_id 可追溯。",
            "P19 汇总最终验收矩阵和回滚演练证据。",
        ],
        message="异常处置闭环已汇总；外部发送保持禁用。",
    )


def list_incident_runbooks(db: Session) -> list[IncidentRunbookItem]:
    return _build_runbooks(db)


def build_incident_closure_plan(db: Session) -> IncidentClosurePlanResponse:
    summary = build_incident_summary(db)
    pending_confirmations = _pending_high_risk_confirmation_count(db)
    checks = _build_closure_checks(db, summary, pending_confirmations)
    required_failed = [item for item in checks if item.required and item.status != "passed"]
    status = "passed" if not required_failed else "failed" if any(item.severity == "critical" for item in required_failed) else "warning"
    ready_to_close = not required_failed
    return IncidentClosurePlanResponse(
        generated_at=utc_now(),
        status=status,
        ready_to_close=ready_to_close,
        open_incidents=summary.total_open,
        critical_count=summary.critical_count,
        warning_count=summary.warning_count,
        pending_high_risk_confirmations=pending_confirmations,
        external_send_enabled=False,
        checks=checks,
        risk_notes=[
            "该闭环不依赖真实评分题，不探测题目、不自动提交、不触碰继续下一题 checkbox。",
            "pending 高危确认项必须先批准或驳回，否则运维闭环不能视为干净。",
            "外部发送保持禁用；当前只生成本地报告、审计 trace 和可见检查清单。",
        ],
        next_actions=_closure_next_actions(summary, pending_confirmations, ready_to_close),
        message="运维告警闭环验收清单已生成；可在页面直接查看阻塞项和证据入口。",
    )


def create_incident_closure(db: Session, request: IncidentClosureRequest) -> IncidentClosureResponse:
    summary = build_incident_summary(db)
    plan = build_incident_closure_plan(db)
    trace_id = uuid4().hex
    report_path = _write_closure_report(summary, plan, trace_id, request.dry_run) if request.generate_report else None
    audit_trace_id = None
    if request.write_audit:
        audit = write_audit(
            db,
            event_type="incident_response_closure",
            severity=_audit_severity(summary.status),
            target_type="incident_response",
            target_id=trace_id,
            message=f"P18 incident closure status={summary.status}, open={summary.total_open}, dry_run={request.dry_run}, external_send=false",
        )
        db.commit()
        audit_trace_id = audit.trace_id
    return IncidentClosureResponse(
        generated_at=utc_now(),
        status=summary.status,
        dry_run=request.dry_run,
        closed_count=0 if request.dry_run else summary.total_open,
        report_path=report_path,
        audit_trace_id=audit_trace_id,
        summary=summary,
        plan=plan,
        message="异常处置闭环记录已生成；当前仅本地证据，不触发外部系统。",
    )


def _build_runbooks(db: Session) -> list[IncidentRunbookItem]:
    rules = list_alert_rules()
    data_quality = build_data_quality_summary(db)
    items = [
        IncidentRunbookItem(
            key=rule.key,
            category=rule.source,
            title=rule.title,
            severity=rule.severity,
            trigger=rule.slo_target,
            owner="运维值守",
            evidence_path=_evidence_for_rule(rule.key),
            steps=_steps_for_key(rule.key),
            status="ready",
        )
        for rule in rules
    ]
    items.append(
        IncidentRunbookItem(
            key="data_quality_mismatch",
            category="data-quality",
            title="数据正确性异常",
            severity="warning",
            trigger="P17 数据质量 status=passed",
            owner="数据值守",
            evidence_path=api_path("/data-quality/summary"),
            steps=[
                "打开数据校验页确认失败检查项。",
                "优先修复账号数、任务目录、收益行数或待处理数字口径。",
                "重新生成 P17 数据质量报告，并记录 audit_trace_id。",
            ],
            status="ready" if data_quality.status == "passed" else "needs_review",
        )
    )
    return items


def _build_closure_checks(db: Session, summary: IncidentSummaryResponse, pending_confirmations: int) -> list[IncidentClosureCheck]:
    last_closure = db.scalars(select(AuditLog).where(AuditLog.event_type == "incident_response_closure").order_by(AuditLog.created_at.desc()).limit(1)).first()
    runbook_keys = {item.key for item in summary.runbooks}
    unmapped_incidents = [item.key for item in summary.incidents if item.key not in runbook_keys]
    score_submit_pending = _score_submit_confirmation_pending_count(db)
    unsupported_paused = db.query(ScoreLoopCase).filter(ScoreLoopCase.status == ScoreLoopCaseStatus.UNSUPPORTED_PAUSED).count()
    score_review_backlog = (
        db.query(ScoreLoopCase)
        .filter(ScoreLoopCase.status.in_([ScoreLoopCaseStatus.CAPTURED, ScoreLoopCaseStatus.DRAFT_READY]))
        .count()
    )
    checks = [
        IncidentClosureCheck(
            key="slo_evaluated",
            title="SLO 与告警已评估",
            status="passed",
            required=True,
            severity="info",
            evidence_path="/alerts",
            detail=f"当前 open={summary.total_open}，critical={summary.critical_count}，warning={summary.warning_count}。",
            next_step="继续按下方 runbook 处理 open 项；无 open 项时可生成闭环记录。",
        ),
        IncidentClosureCheck(
            key="runbook_mapped",
            title="异常均有 Runbook 映射",
            status="passed" if not unmapped_incidents else "failed",
            required=True,
            severity="critical" if unmapped_incidents else "info",
            evidence_path="/incidents",
            detail="所有 open 异常都有处置步骤。" if not unmapped_incidents else f"缺少映射：{', '.join(unmapped_incidents)}。",
            next_step="补齐 runbook 映射后再生成闭环记录。" if unmapped_incidents else "保持 runbook 与告警规则同步。",
        ),
        IncidentClosureCheck(
            key="high_risk_queue_empty",
            title="高危确认队列已清空",
            status="passed" if pending_confirmations == 0 else "warning",
            required=True,
            severity="warning" if pending_confirmations else "info",
            evidence_path="/ai",
            detail=f"pending 高危动作确认项：{pending_confirmations}。",
            next_step="先到 AI 页面批准或驳回 pending 高危动作。" if pending_confirmations else "无需处理。",
        ),
        IncidentClosureCheck(
            key="external_send_disabled",
            title="外部发送保持禁用",
            status="passed",
            required=True,
            severity="info",
            evidence_path="/alerts",
            detail="飞书/webhook 外部发送为 false，只保留本地审计和报告。",
            next_step="如需外发必须另行显式授权。",
        ),
        IncidentClosureCheck(
            key="score_loop_ops_guard",
            title="评分闭环运维护栏",
            status="passed" if score_submit_pending == 0 and unsupported_paused == 0 and score_review_backlog == 0 else "warning",
            required=True,
            severity="warning" if score_submit_pending or unsupported_paused or score_review_backlog else "info",
            evidence_path="/ai",
            detail=f"评分提交 pending={score_submit_pending}，未知题型暂停={unsupported_paused}，待复核样本={score_review_backlog}。",
            next_step="处理 AI 确认队列、未知题型和人工复核积压后再生成闭环记录。" if score_submit_pending or unsupported_paused or score_review_backlog else "评分闭环无待处理运维项。",
        ),
        IncidentClosureCheck(
            key="local_evidence_ready",
            title="本地闭环证据可生成",
            status="passed" if last_closure else "warning",
            required=False,
            severity="warning" if not last_closure else "info",
            evidence_path="reports/incident-closure-*.md",
            detail=f"最近闭环审计 trace：{last_closure.trace_id}。" if last_closure else "尚未生成过闭环审计记录。",
            next_step="点击生成闭环记录，创建报告和 audit_trace_id。" if not last_closure else "需要时重新生成最新闭环记录。",
        ),
        IncidentClosureCheck(
            key="real_question_not_required",
            title="不依赖真实题目",
            status="passed",
            required=True,
            severity="info",
            evidence_path="/incidents",
            detail="当前运维闭环只检查系统告警、确认队列、runbook 和本地证据。",
            next_step="等有真实题目后再恢复提交/回读闭环。",
        ),
    ]
    return checks


def _pending_high_risk_confirmation_count(db: Session) -> int:
    return db.query(AiActionConfirmation).filter(AiActionConfirmation.status == AiActionConfirmationStatus.PENDING).count()


def _score_submit_confirmation_pending_count(db: Session) -> int:
    return (
        db.query(AiActionConfirmation)
        .filter(AiActionConfirmation.status == AiActionConfirmationStatus.PENDING)
        .filter(AiActionConfirmation.action_key.like("real_submit_score_case_%"))
        .count()
    )


def _closure_next_actions(summary: IncidentSummaryResponse, pending_confirmations: int, ready_to_close: bool) -> list[str]:
    if pending_confirmations:
        return ["先处理 AI 高危动作确认队列。", "回到异常处置页刷新闭环验收清单。", "确认无 pending 后生成闭环记录。"]
    if summary.total_open:
        return ["按异常队列逐项打开证据入口。", "执行对应 runbook 并记录处理结果。", "复核告警状态后生成闭环记录。"]
    if ready_to_close:
        return ["当前无阻塞项，可生成最新闭环记录。", "保留 report_path 与 audit_trace_id 作为可视证据。"]
    return ["按检查清单处理阻塞项。", "处理后刷新本页。"]


def _queue_item(incident: AlertIncident, steps: list[str]) -> IncidentQueueItem:
    return IncidentQueueItem(
        key=incident.key,
        title=incident.title,
        severity=incident.severity,
        status=incident.status,
        subject=incident.subject,
        reason=incident.reason,
        recommended_action=incident.recommended_action,
        evidence_path=_evidence_for_rule(incident.key),
        runbook_steps=steps,
        evidence=incident.evidence,
    )


def _steps_for_key(key: str) -> list[str]:
    steps = {
        "account_needs_login": ["打开账号管理页定位 needs_login 账号。", "人工恢复登录态或禁用失效账号。", "重新运行 P16 账号覆盖基线。"],
        "collector_guard_not_passed": ["打开观测中心查看采集守护详情。", "在任务看板按主账号执行只读刷新。", "确认任务目录有样本且无 last_task_page_error。"],
        "worker_offline": ["打开 Worker 管理页检查心跳时间。", "确认 worker 版本和绑定账号。", "恢复心跳后重新运行 Docker smoke。"],
        "release_gate_blocked": ["打开生产护栏页查看失败门禁。", "逐项修复必需项。", "不要自动切换正式域名，等待 P20 封版。"],
        "backup_missing": ["打开备份恢复页执行手动备份。", "确认备份记录为 completed。", "将备份路径写入最终验收矩阵。"],
        "audit_errors_present": ["打开权限审计页按 trace_id 筛查。", "确认 error/critical 原因与影响面。", "记录处置结果后再进入最终验收。"],
        "score_submit_confirmation_pending": ["打开 AI 页面查看高危动作确认队列。", "批准或驳回评分提交确认；批准只授权，不自动提交。", "处理后刷新异常处置页确认 pending=0。"],
        "score_unknown_type_paused": ["打开 AI 标注能力工作台记录未知题型特征。", "补齐题型识别规则和人工确认说明。", "没有真实题前只完成规则和告警闭环，不声明提交/回读完成。"],
        "score_review_backlog": ["打开 AI 页面查看评分样本表。", "对 captured 样本生成草稿，对 draft_ready 样本人工确认或驳回。", "真实提交仍必须进入高危确认队列。"],
    }
    return steps.get(key, ["查看相关页面证据。", "按推荐动作处理。", "重新生成本地闭环记录。"])


def _evidence_for_rule(key: str) -> str:
    evidence = {
        "account_needs_login": "/accounts",
        "collector_guard_not_passed": "/observability",
        "worker_offline": "/workers",
        "release_gate_blocked": "/production",
        "backup_missing": "/backups",
        "audit_errors_present": "/security",
        "data_quality_mismatch": "/data-quality",
        "score_submit_confirmation_pending": "/ai",
        "score_unknown_type_paused": "/ability-workbench",
        "score_review_backlog": "/ai",
    }
    return evidence.get(key, "/alerts")


def _write_closure_report(summary: IncidentSummaryResponse, plan: IncidentClosurePlanResponse, trace_id: str, dry_run: bool) -> str:
    path = _reports_root() / f"incident-closure-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.md"
    lines = [
        "# aidp-monitor-next P18 异常处置与运维闭环",
        "",
        f"生成时间：{summary.generated_at.isoformat()}",
        f"trace_id：{trace_id}",
        f"状态：{summary.status}",
        f"dry_run：{dry_run}",
        f"open_incidents：{summary.total_open}",
        f"external_send_enabled：{summary.external_send_enabled}",
        "",
        "## 异常队列",
    ]
    if summary.incidents:
        for item in summary.incidents:
            lines.append(f"- [{item.severity}] {item.title}：{item.reason}；action={item.recommended_action}；evidence={item.evidence_path}")
    else:
        lines.append("- 当前无 open 异常。")
    lines.extend(["", "## 闭环验收清单"])
    lines.append(f"- ready_to_close：{plan.ready_to_close}")
    lines.append(f"- pending_high_risk_confirmations：{plan.pending_high_risk_confirmations}")
    for check in plan.checks:
        required = "必需" if check.required else "可选"
        lines.append(f"- [{check.status}] {check.title}（{required}/{check.severity}）：{check.detail}；next={check.next_step}；evidence={check.evidence_path}")
    lines.extend(["", "## Runbook"])
    for item in summary.runbooks:
        lines.append(f"- {item.title}（{item.category}/{item.severity}）：trigger={item.trigger}，evidence={item.evidence_path}")
        for step in item.steps:
            lines.append(f"  - {step}")
    lines.extend(["", "## 不变更项", "- 不发送飞书或外部 webhook。", "- 不自动切换正式域名。", "- 不依赖真实评分题，不探测题目。", "- 删除候选只移动到 delete。", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return _display_path(path)


def _reports_root() -> Path:
    settings = get_settings()
    root = Path(settings.task_sample_root).resolve().parent / "reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


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


def _audit_severity(status: str) -> AuditSeverity:
    if status == "failed":
        return AuditSeverity.ERROR
    if status == "warning":
        return AuditSeverity.WARNING
    return AuditSeverity.INFO
