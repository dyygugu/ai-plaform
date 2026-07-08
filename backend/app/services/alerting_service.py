from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.ai import AiActionConfirmation, AiActionConfirmationStatus
from app.models.account import AccountStatus, AidpAccount
from app.models.audit import AuditLog, AuditSeverity
from app.models.backup import BackupJob, BackupStatus
from app.models.score_loop import ScoreLoopCase, ScoreLoopCaseStatus
from app.models.worker import Worker, WorkerStatus
from app.schemas.alerting import AlertEvaluationRequest, AlertEvaluationResponse, AlertIncident, AlertRuleRead, AlertSummaryResponse, SloIndicator, SloSummaryResponse
from app.services.alert_service import build_alert_message
from app.services.audit_service import write_audit
from app.services.notification_service import build_error_notification_text, get_notification_config_status, send_error_notification
from app.services.observability_service import build_collector_guard
from app.services.ops_job_service import build_release_gate
from app.services.task_rules import utc_now


BUILTIN_ALERT_RULES = [
    AlertRuleRead(
        key="account_needs_login",
        title="账号登录态异常",
        severity="warning",
        slo_target="需登录账号=0",
        source="accounts",
        silence_minutes=30,
        description="任一账号进入 needs_login 时生成待处理告警，避免采集中断。",
        runbook_hint="打开账号管理页，重新确认 Cookie 或禁用失效账号。",
    ),
    AlertRuleRead(
        key="collector_guard_not_passed",
        title="采集守护异常",
        severity="critical",
        slo_target="采集样本存在且任务目录无错误",
        source="observability.collector_guard",
        silence_minutes=15,
        description="脱敏样本缺失、目录为空或存在 stale/error 时告警。",
        runbook_hint="打开观测中心检查采集守护，再按任务页来源账号刷新脱敏样本。",
    ),
    AlertRuleRead(
        key="worker_offline",
        title="Worker 全部离线",
        severity="warning",
        slo_target="在线 Worker>=1",
        source="workers",
        silence_minutes=10,
        description="没有在线 Worker 时触发，防止任务领取链路不可用。",
        runbook_hint="打开 Worker 管理页检查心跳、版本和最近错误。",
    ),
    AlertRuleRead(
        key="release_gate_blocked",
        title="发布门禁未通过",
        severity="critical",
        slo_target="必需门禁全部 passed",
        source="ops.release_gate",
        silence_minutes=20,
        description="发布门禁必需项失败时触发，阻止误切正式域名。",
        runbook_hint="打开生产护栏页，按失败门禁逐项修复；不要自动切换域名。",
    ),
    AlertRuleRead(
        key="backup_missing",
        title="备份成功记录缺失",
        severity="warning",
        slo_target="至少 1 条 completed 备份",
        source="backups",
        silence_minutes=60,
        description="没有成功备份时触发，提醒先补备份再发布。",
        runbook_hint="打开备份恢复页执行手动备份，并确认备份记录为 completed。",
    ),
    AlertRuleRead(
        key="audit_errors_present",
        title="严重审计事件存在",
        severity="warning",
        slo_target="error/critical 审计=0",
        source="audit",
        silence_minutes=30,
        description="最近系统存在 error/critical 审计时触发，要求人工确认风险。",
        runbook_hint="打开权限审计页按 trace_id 排查，并记录处理结果。",
    ),
    AlertRuleRead(
        key="score_submit_confirmation_pending",
        title="评分提交确认待处理",
        severity="warning",
        slo_target="评分真实提交 pending 确认=0",
        source="score-loop.confirmations",
        silence_minutes=10,
        description="评分样本请求真实提交后必须进入高危确认队列；pending 未处理时持续提醒。",
        runbook_hint="打开 AI 页面批准或驳回确认项；批准只授权，不自动提交或进入下一题。",
    ),
    AlertRuleRead(
        key="score_unknown_type_paused",
        title="未知评分题型暂停",
        severity="warning",
        slo_target="unsupported_paused=0",
        source="score-loop.cases",
        silence_minutes=30,
        description="插件或 Worker 发现未知题型时暂停样本，避免错误草稿和误提交。",
        runbook_hint="打开 AI 标注能力工作台，补齐任务能力规则后再恢复；没有真题时不声明提交闭环完成。",
    ),
    AlertRuleRead(
        key="score_review_backlog",
        title="评分样本待人工复核",
        severity="warning",
        slo_target="captured/draft_ready=0",
        source="score-loop.cases",
        silence_minutes=15,
        description="已采集或已生成草稿的评分样本需要人工复核，防止堆积在半流程状态。",
        runbook_hint="打开 AI 页面完成草稿生成、人工确认或驳回；真实提交仍走高危确认队列。",
    ),
]


def list_alert_rules() -> list[AlertRuleRead]:
    return BUILTIN_ALERT_RULES


def build_slo_summary(db: Session) -> SloSummaryResponse:
    collector = build_collector_guard(db)
    release_checks, release_ready = build_release_gate(db)
    accounts = [account for account in db.query(AidpAccount).all() if _is_real_user_id(account.user_id) and account.status != AccountStatus.DISABLED]
    account_total = len(accounts)
    needs_login = sum(1 for account in accounts if account.status == AccountStatus.NEEDS_LOGIN)
    online_workers = db.query(Worker).filter(Worker.status == WorkerStatus.ONLINE).count()
    backup_total = db.query(BackupJob).count()
    backup_completed = db.query(BackupJob).filter(BackupJob.status == BackupStatus.COMPLETED).count()
    severe_audit = db.query(AuditLog).filter(AuditLog.severity.in_([AuditSeverity.ERROR, AuditSeverity.CRITICAL])).count()
    score_submit_pending = _score_submit_confirmation_pending_count(db)
    unsupported_paused = db.query(ScoreLoopCase).filter(ScoreLoopCase.status == ScoreLoopCaseStatus.UNSUPPORTED_PAUSED).count()
    review_backlog = (
        db.query(ScoreLoopCase)
        .filter(ScoreLoopCase.status.in_([ScoreLoopCaseStatus.CAPTURED, ScoreLoopCaseStatus.DRAFT_READY]))
        .count()
    )
    backup_rate = round((backup_completed / backup_total) * 100, 2) if backup_total else 0
    release_failed = [item.key for item in release_checks if item.required and item.status == "failed"]
    indicators = [
        SloIndicator(
            key="account_health",
            title="账号登录态",
            target="需登录账号=0",
            current=f"{needs_login}/{account_total} 需登录",
            status="passed" if needs_login == 0 else "warning",
            message="账号登录态满足 SLO" if needs_login == 0 else f"{needs_login} 个账号需要重新登录",
        ),
        SloIndicator(
            key="collector_freshness",
            title="采集新鲜度",
            target="采集守护 passed",
            current=f"{collector.status}，样本年龄 {collector.sample_age_minutes if collector.sample_age_minutes is not None else '-'} 分钟",
            status=collector.status,
            message=collector.message,
        ),
        SloIndicator(
            key="worker_online",
            title="Worker 在线",
            target="在线 Worker>=1",
            current=f"{online_workers} 个在线",
            status="passed" if online_workers >= 1 else "warning",
            message="Worker 心跳正常" if online_workers >= 1 else "没有在线 Worker",
        ),
        SloIndicator(
            key="release_gate",
            title="发布门禁",
            target="必需门禁全部通过",
            current="ready" if release_ready else f"blocked: {','.join(release_failed)}",
            status="passed" if release_ready else "failed",
            message="可进入人工域名切换前检查" if release_ready else "发布门禁仍有必需项失败",
        ),
        SloIndicator(
            key="backup_success",
            title="备份成功率",
            target="completed>=1",
            current=f"{backup_completed}/{backup_total} completed，{backup_rate}%",
            status="passed" if backup_completed >= 1 else "warning",
            message="存在成功备份记录" if backup_completed >= 1 else "缺少成功备份记录",
        ),
        SloIndicator(
            key="audit_errors",
            title="严重审计",
            target="error/critical=0",
            current=f"{severe_audit} 条",
            status="passed" if severe_audit == 0 else "warning",
            message="未发现严重审计" if severe_audit == 0 else "存在需人工确认的严重审计",
        ),
        SloIndicator(
            key="score_submit_confirmations",
            title="评分提交确认",
            target="pending=0",
            current=f"{score_submit_pending} 个 pending",
            status="passed" if score_submit_pending == 0 else "warning",
            message="评分真实提交确认队列为空" if score_submit_pending == 0 else f"{score_submit_pending} 个评分提交确认待批准或驳回",
        ),
        SloIndicator(
            key="score_unknown_types",
            title="评分题型识别",
            target="unsupported_paused=0",
            current=f"{unsupported_paused} 个暂停样本",
            status="passed" if unsupported_paused == 0 else "warning",
            message="未发现未知题型暂停样本" if unsupported_paused == 0 else f"{unsupported_paused} 个未知题型样本已暂停，需补规则",
        ),
        SloIndicator(
            key="score_review_backlog",
            title="评分人工复核",
            target="待复核样本=0",
            current=f"{review_backlog} 个待处理",
            status="passed" if review_backlog == 0 else "warning",
            message="没有半流程评分样本" if review_backlog == 0 else f"{review_backlog} 个评分样本等待草稿或人工复核",
        ),
    ]
    overall_status = _rollup_status([item.status for item in indicators])
    return SloSummaryResponse(generated_at=utc_now(), overall_status=overall_status, indicators=indicators)


def build_alert_summary(db: Session) -> AlertSummaryResponse:
    evaluation = evaluate_alerts(db, AlertEvaluationRequest(dry_run=True, write_audit=False, send_external=False))
    notification_status = get_notification_config_status()
    return AlertSummaryResponse(
        generated_at=evaluation.generated_at,
        status=evaluation.status,
        rules=evaluation.rules,
        slo=evaluation.slo,
        incidents=evaluation.incidents,
        notification_preview=evaluation.notification_preview,
        external_send_enabled=notification_status.sends_network,
    )


def evaluate_alerts(db: Session, request: AlertEvaluationRequest) -> AlertEvaluationResponse:
    trace_id = uuid4().hex
    slo = build_slo_summary(db)
    incidents = _build_incidents(db, slo)
    status = "passed" if not incidents else _rollup_status([item.severity for item in incidents])
    preview = _build_notification_preview(status, incidents, trace_id=trace_id)
    audit_trace_id = None
    notification_send = None
    notification_result_text = ""
    notification_status = get_notification_config_status()
    if request.write_audit:
        severity = _audit_severity(status)
        audit = write_audit(
            db,
            event_type="alert_evaluation",
            severity=severity,
            target_type="alerting",
            target_id=trace_id,
            message=f"alert evaluation status={status}, incidents={len(incidents)}, external_send={request.send_external and notification_status.sends_network}",
        )
        db.commit()
        audit_trace_id = audit.trace_id
    if request.send_external and incidents:
        notify_event = "alert.evaluation.failed" if status == "failed" else "alert.evaluation.warning"
        if request.dry_run:
            notification_result_text = "\n飞书结果：本次为 dry-run，未发送飞书。 sent=False skipped=True"
        else:
            notification_send = send_error_notification(
                event=notify_event,
                level="critical" if status == "failed" else "warn",
                message=f"告警评估发现 {len(incidents)} 条待处理：{incidents[0].title} / {incidents[0].reason}",
                data={"incidents": [item.model_dump() for item in incidents[:5]], "status": status},
                trace_id=trace_id,
            )
            notification_result_text = _format_notification_result(notification_send)
    return AlertEvaluationResponse(
        trace_id=trace_id,
        generated_at=utc_now(),
        status=status,
        dry_run=request.dry_run,
        external_send_enabled=notification_status.sends_network,
        rules=list_alert_rules(),
        slo=slo,
        incidents=incidents,
        notification_preview=preview + notification_result_text,
        audit_trace_id=audit_trace_id,
        message=_evaluation_message(request),
    )


def _build_incidents(db: Session, slo: SloSummaryResponse) -> list[AlertIncident]:
    latest_severe_audit = db.scalars(select(AuditLog).where(AuditLog.severity.in_([AuditSeverity.ERROR, AuditSeverity.CRITICAL])).order_by(AuditLog.created_at.desc()).limit(1)).first()
    incidents: list[AlertIncident] = []
    for indicator in slo.indicators:
        if indicator.status == "passed":
            continue
        if indicator.key == "account_health":
            incidents.append(_incident("account_needs_login", "账号登录态异常", "warning", "账号", indicator.message, "打开账号管理页重新登录或禁用异常账号。", {"current": indicator.current}))
        elif indicator.key == "collector_freshness":
            severity = "critical" if indicator.status == "failed" else "warning"
            incidents.append(_incident("collector_guard_not_passed", "采集守护异常", severity, "任务页采集", indicator.message, "打开观测中心和任务看板，刷新只读脱敏样本。", {"current": indicator.current}))
        elif indicator.key == "worker_online":
            incidents.append(_incident("worker_offline", "Worker 全部离线", "warning", "Worker", indicator.message, "打开 Worker 管理页检查心跳和最近日志。", {"current": indicator.current}))
        elif indicator.key == "release_gate":
            incidents.append(_incident("release_gate_blocked", "发布门禁未通过", "critical", "发布门禁", indicator.message, "打开生产护栏页修复失败门禁，正式域名仍保持手动。", {"current": indicator.current}))
        elif indicator.key == "backup_success":
            incidents.append(_incident("backup_missing", "备份成功记录缺失", "warning", "备份恢复", indicator.message, "先执行手动备份并确认 completed 记录。", {"current": indicator.current}))
        elif indicator.key == "audit_errors":
            evidence = {"current": indicator.current}
            if latest_severe_audit:
                evidence["latest_trace_id"] = latest_severe_audit.trace_id
                evidence["latest_event_type"] = latest_severe_audit.event_type
            incidents.append(_incident("audit_errors_present", "严重审计事件存在", "warning", "权限审计", indicator.message, "打开权限审计页按 trace_id 排查并记录处理结果。", evidence))
        elif indicator.key == "score_submit_confirmations":
            incidents.append(_incident("score_submit_confirmation_pending", "评分提交确认待处理", "warning", "评分题生产闭环", indicator.message, "打开 AI 页面处理 pending 确认项；批准只授权，不自动执行真实提交。", {"current": indicator.current, "evidence_path": "/ai"}))
        elif indicator.key == "score_unknown_types":
            incidents.append(_incident("score_unknown_type_paused", "未知评分题型暂停", "warning", "题型识别", indicator.message, "打开 AI 标注能力工作台补规则；没有真题前不声明提交/回读完成。", {"current": indicator.current, "evidence_path": "/ability-workbench"}))
        elif indicator.key == "score_review_backlog":
            incidents.append(_incident("score_review_backlog", "评分样本待人工复核", "warning", "评分题生产闭环", indicator.message, "打开 AI 页面完成草稿、人工确认或驳回。", {"current": indicator.current, "evidence_path": "/ai"}))
    return incidents


def _score_submit_confirmation_pending_count(db: Session) -> int:
    return (
        db.query(AiActionConfirmation)
        .filter(AiActionConfirmation.status == AiActionConfirmationStatus.PENDING)
        .filter(AiActionConfirmation.action_key.like("real_submit_score_case_%"))
        .count()
    )


def _incident(key: str, title: str, severity: str, subject: str, reason: str, action: str, evidence: dict[str, object]) -> AlertIncident:
    return AlertIncident(key=key, title=title, severity=severity, status="open", subject=subject, reason=reason, recommended_action=action, evidence=evidence)


def _build_notification_preview(status: str, incidents: list[AlertIncident], trace_id: str = "") -> str:
    settings = get_settings()
    if incidents:
        first = incidents[0]
        notify_event = "alert.evaluation.failed" if status == "failed" else "alert.evaluation.warning"
        notify_level = "critical" if status == "failed" else "warn"
        message_text = f"告警评估发现 {len(incidents)} 条待处理：{first.title} / {first.reason}"
        data = {"incidents": [item.model_dump() for item in incidents[:5]], "status": status}
        text = build_error_notification_text(notify_event, notify_level, message_text, data, trace_id=trace_id)
    else:
        title = "AIDP Monitor 告警评估：SLO 正常"
        subject = "生产护栏与采集守护"
        reason = "所有本地 SLO 指标通过"
        severity = "info"
        message = build_alert_message(title, severity, subject, reason, f"{settings.public_base_url}/alerts")
        text = message.render_feishu_text()
    notification_status = get_notification_config_status()
    send_state = "已配置，可发送" if notification_status.sends_network else notification_status.message
    return text + f"\n状态：{status}\n发送：{send_state}"


def _format_notification_result(result) -> str:
    return f"\n飞书结果：{result.message} sent={result.sent} skipped={result.skipped}"


def _evaluation_message(request: AlertEvaluationRequest) -> str:
    if not request.send_external:
        return "告警评估已完成；本次未请求外部发送。"
    if request.dry_run:
        return "告警评估已完成；本次为 dry-run，未发送飞书。"
    return "告警评估已完成；飞书通知已按配置处理。"


def _is_real_user_id(value: str) -> bool:
    text = str(value or "").strip()
    return text.isdigit() and 12 <= len(text) <= 24


def _rollup_status(values: list[str]) -> str:
    if any(value in {"critical", "failed", "error"} for value in values):
        return "failed"
    if any(value in {"warning", "blocked"} for value in values):
        return "warning"
    return "passed"


def _audit_severity(status: str) -> AuditSeverity:
    if status == "failed":
        return AuditSeverity.ERROR
    if status == "warning":
        return AuditSeverity.WARNING
    return AuditSeverity.INFO
