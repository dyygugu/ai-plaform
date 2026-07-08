import json

from sqlalchemy.orm import Session

from app.models.account import AccountStatus, AidpAccount
from app.models.worker import Worker, WorkerEvent
from app.schemas.ops import FaultDiagnosisItem, FaultDiagnosisResponse, OperationalRiskItem, OperationalRiskSummaryResponse, WorkerLogReplayItem
from app.services.alerting_service import build_slo_summary
from app.services.data_quality_service import build_data_quality_summary
from app.services.incident_service import build_incident_summary
from app.services.inspection_service import build_inspection_summary
from app.services.task_rules import utc_now


SLO_RISK_KEYS = {
    "account_health": "account_needs_login",
    "collector_freshness": "collector_guard_not_passed",
    "worker_online": "worker_offline",
    "release_gate": "release_gate_blocked",
    "backup_success": "backup_missing",
    "audit_errors": "audit_errors_present",
    "score_submit_confirmations": "score_submit_confirmation_pending",
    "score_unknown_types": "score_unknown_type_paused",
    "score_review_backlog": "score_review_backlog",
}

RISK_EVIDENCE = {
    "account_needs_login": "/accounts",
    "collector_guard_not_passed": "/observability",
    "worker_offline": "/workers",
    "release_gate_blocked": "/production",
    "backup_missing": "/backups",
    "audit_errors_present": "/security",
    "score_submit_confirmation_pending": "/ai",
    "score_unknown_type_paused": "/ability-workbench",
    "score_review_backlog": "/ai",
}


def build_operational_risk_summary(db: Session) -> OperationalRiskSummaryResponse:
    risks: dict[str, OperationalRiskItem] = {}
    slo = build_slo_summary(db)
    for indicator in slo.indicators:
        if indicator.status == "passed":
            continue
        key = SLO_RISK_KEYS.get(indicator.key, indicator.key)
        _merge_risk(
            risks,
            OperationalRiskItem(
                key=key,
                title=indicator.title,
                severity="critical" if indicator.status == "failed" else "warning",
                status=indicator.status,
                subject=indicator.title,
                reason=indicator.message,
                recommended_action="打开相关页面按 runbook 处理后重新体检。",
                evidence_path=RISK_EVIDENCE.get(key, "/alerts"),
                sources=["alerts.slo"],
            ),
        )

    incidents = build_incident_summary(db)
    for incident in incidents.incidents:
        _merge_risk(
            risks,
            OperationalRiskItem(
                key=incident.key,
                title=incident.title,
                severity=incident.severity,
                status=incident.status,
                subject=incident.subject,
                reason=incident.reason,
                recommended_action=incident.recommended_action,
                evidence_path=_incident_evidence_path(incident.key),
                sources=["incidents"],
            ),
        )

    data_quality = build_data_quality_summary(db)
    for check in data_quality.checks:
        if check.status == "passed":
            continue
        _merge_risk(
            risks,
            OperationalRiskItem(
                key=f"data_quality_{check.key}",
                title=check.title,
                severity="critical" if check.status == "failed" else "warning",
                status=check.status,
                subject="数据质量",
                reason=check.message,
                recommended_action="打开数据校验页查看失败检查项，修复后重新生成报告。",
                evidence_path=check.evidence_path,
                sources=["data-quality"],
            ),
        )

    inspection = build_inspection_summary(db)
    for check in inspection.checks:
        if check.status == "passed":
            continue
        if check.key == "slo_alerts":
            for item in risks.values():
                if item.source_has("alerts.slo"):
                    item.add_source("inspection")
            continue
        severity = "info" if check.status == "manual" else "critical" if check.status == "failed" else "warning"
        _merge_risk(
            risks,
            OperationalRiskItem(
                key=check.key,
                title=check.title,
                severity=severity,
                status=check.status,
                subject="巡检",
                reason=check.message,
                recommended_action=check.recommended_action,
                evidence_path=check.evidence,
                sources=["inspection"],
            ),
        )

    items = sorted(risks.values(), key=_risk_sort_key)
    critical_count = sum(1 for item in items if item.severity == "critical")
    warning_count = sum(1 for item in items if item.severity == "warning")
    manual_count = sum(1 for item in items if item.status == "manual")
    status = "failed" if critical_count else "warning" if warning_count else "passed"
    return OperationalRiskSummaryResponse(
        generated_at=utc_now(),
        status=status,
        risk_count=critical_count + warning_count,
        critical_count=critical_count,
        warning_count=warning_count,
        manual_todo_count=manual_count,
        items=items,
        message="运维风险已按根因聚合；同源 SLO/Incident/Inspection 只展示一次。",
    )


def build_fault_diagnosis(db: Session) -> FaultDiagnosisResponse:
    summary = build_operational_risk_summary(db)
    items = [_diagnosis_from_risk(db, risk) for risk in summary.items if risk.severity in {"critical", "warning"}]
    items.extend(_worker_log_diagnoses(db))
    items = sorted(items, key=lambda item: (-_severity_rank(item.severity), item.key))
    primary = items[0] if items else None
    return FaultDiagnosisResponse(
        generated_at=summary.generated_at,
        status=summary.status,
        fault_count=len(items),
        primary=primary,
        items=items,
        message="故障诊断已把风险转换为错误位置、准确错误、影响范围、证据和下一步动作。",
    )


def _merge_risk(risks: dict[str, OperationalRiskItem], item: OperationalRiskItem) -> None:
    existing = risks.get(item.key)
    if not existing:
        risks[item.key] = item
        return
    existing.sources = sorted(set(existing.sources) | set(item.sources))
    if _severity_rank(item.severity) > _severity_rank(existing.severity):
        existing.severity = item.severity
    if existing.status == "passed" or item.status == "failed":
        existing.status = item.status
    if not existing.recommended_action and item.recommended_action:
        existing.recommended_action = item.recommended_action
    if not existing.evidence_path and item.evidence_path:
        existing.evidence_path = item.evidence_path


def _risk_sort_key(item: OperationalRiskItem) -> tuple[int, str]:
    return (-_severity_rank(item.severity), item.key)


def _severity_rank(value: str) -> int:
    return {"critical": 3, "warning": 2, "info": 1}.get(value, 0)


def _incident_evidence_path(key: str) -> str:
    return RISK_EVIDENCE.get(key, "/incidents")


def _diagnosis_from_risk(db: Session, risk: OperationalRiskItem) -> FaultDiagnosisItem:
    return FaultDiagnosisItem(
        key=risk.key,
        severity=risk.severity,
        status=risk.status,
        error_location=_error_location(risk),
        accurate_error=risk.reason,
        affected_scope=_affected_scope(db, risk),
        first_seen_source=_first_seen_source(risk),
        evidence_links=_evidence_links(risk),
        next_actions=_next_actions(risk),
        escalation_hint=_escalation_hint(risk),
        sources=risk.sources,
        worker_log_replay=_worker_log_replay(db) if risk.key == "worker_offline" else [],
    )


def _error_location(risk: OperationalRiskItem) -> str:
    if risk.key == "account_needs_login":
        return "账号健康"
    if risk.key == "collector_guard_not_passed":
        return "任务采集"
    if risk.key == "worker_offline":
        return "Worker 心跳"
    if risk.key == "release_gate_blocked":
        return "发布门禁"
    if risk.key == "backup_missing":
        return "备份恢复"
    if risk.key.startswith("data_quality_"):
        return "数据质量"
    return risk.subject or risk.title


def _affected_scope(db: Session, risk: OperationalRiskItem) -> str:
    if risk.key == "account_needs_login":
        accounts = db.query(AidpAccount).filter(AidpAccount.status == AccountStatus.NEEDS_LOGIN).all()
        real_accounts = [account for account in accounts if _is_real_user_id(account.user_id)]
        if not real_accounts:
            return "没有真实生产账号受影响"
        names = [account.display_name or f"用户{account.user_id[-8:]}" for account in real_accounts[:3]]
        suffix = f" 等 {len(real_accounts)} 个账号" if len(real_accounts) > 3 else f" 共 {len(real_accounts)} 个账号"
        return "用户：" + "、".join(names) + suffix
    if risk.key == "worker_offline":
        return "全部 Worker 领取任务链路"
    if risk.key == "collector_guard_not_passed":
        return "任务目录刷新和待处理题量同步"
    if risk.key == "backup_missing":
        return "发布前恢复能力"
    return risk.subject or "生产闭环"


def _first_seen_source(risk: OperationalRiskItem) -> str:
    if "alerts.slo" in risk.sources:
        return "SLO 告警"
    if "incidents" in risk.sources:
        return "异常处置"
    if "data-quality" in risk.sources:
        return "数据校验"
    if "inspection" in risk.sources:
        return "生产体检"
    return risk.sources[0] if risk.sources else "风险聚合"


def _evidence_links(risk: OperationalRiskItem) -> list[str]:
    links = [risk.evidence_path] if risk.evidence_path else []
    for source in risk.sources:
        if source == "alerts.slo":
            links.append("/alerts")
        elif source == "incidents":
            links.append("/incidents")
        elif source == "data-quality":
            links.append("/data-quality")
        elif source == "inspection":
            links.append("/inspection")
    return list(dict.fromkeys(link for link in links if link))


def _next_actions(risk: OperationalRiskItem) -> list[str]:
    action_map = {
        "account_needs_login": ["打开账号与登录页", "重新登录异常账号或禁用非生产账号", "返回故障定位台刷新确认风险消失"],
        "collector_guard_not_passed": ["打开任务与待处理页刷新真实任务目录", "查看日志观测 trace_id 和采集守护错误", "重新运行生产体检确认采集恢复"],
        "worker_offline": ["打开多 Worker 页检查心跳和最近错误", "重启离线 Worker 或切换可用 Worker", "确认任务领取链路恢复"],
        "release_gate_blocked": ["打开生产护栏详情查看失败门禁", "按门禁提示修复后重新体检", "未通过前不要切换正式域名"],
        "backup_missing": ["打开备份恢复页执行手动备份", "确认最近备份状态为 completed", "备份完成后再继续发布或高风险操作"],
    }
    return action_map.get(risk.key, [risk.recommended_action or "打开证据页面处理", "处理后返回故障定位台刷新确认"])


def _escalation_hint(risk: OperationalRiskItem) -> str:
    if risk.severity == "critical":
        return "Critical：先停止相关高风险动作，再按证据页处理。"
    if risk.status == "manual":
        return "Manual：需要人工确认，系统不会自动执行。"
    return "Warning：优先处理；若 10 分钟内未恢复，发送飞书通知并记录异常处置。"


def _is_real_user_id(user_id: str) -> bool:
    return user_id.isdigit() and len(user_id) >= 10


def _worker_log_diagnoses(db: Session) -> list[FaultDiagnosisItem]:
    latest_events = _latest_worker_problem_events(db)
    items: list[FaultDiagnosisItem] = []
    for event in latest_events:
        worker = db.query(Worker).filter(Worker.worker_id == event.worker_id).first()
        worker_label = worker.display_name if worker and worker.display_name else event.worker_id
        payload = _parse_worker_message(event.message)
        stage = payload.get("stage", "")
        step = payload.get("step", "")
        stage_text = f" · 阶段 {stage}/{step}" if stage or step else ""
        affected = f"{worker_label} · 账号 {event.account_user_id or '-'} · 任务 {event.task_id or '-'}{stage_text}"
        severity = "critical" if event.severity in {"critical", "error"} else "warning"
        items.append(
            FaultDiagnosisItem(
                key=f"worker_error_{event.worker_id}",
                severity=severity,
                status=event.severity,
                error_location="Worker 日志",
                accurate_error=_worker_accurate_error(event),
                affected_scope=affected,
                first_seen_source="Worker 日志回放",
                evidence_links=["/workers"],
                next_actions=[
                    "打开多 Worker 页查看该 Worker 详情",
                    "按 trace_id 回放最近日志并确认账号/任务是否仍在处理",
                    "修复 Worker 或切换可用 Worker 后返回故障定位台刷新",
                ],
                escalation_hint="Worker 错误：若 10 分钟内重复出现，发送飞书通知并暂停该 Worker 领取新任务。",
                sources=["workers.events"],
                worker_log_replay=_worker_log_replay(db, event.worker_id),
            )
        )
    return items


def _latest_worker_problem_events(db: Session) -> list[WorkerEvent]:
    events = db.query(WorkerEvent).filter(WorkerEvent.severity.in_(["critical", "error", "warning"])).order_by(WorkerEvent.created_at.desc(), WorkerEvent.id.desc()).limit(30).all()
    seen: set[str] = set()
    latest: list[WorkerEvent] = []
    for event in events:
        if _is_sample_worker_event(event):
            continue
        if event.worker_id in seen:
            continue
        seen.add(event.worker_id)
        latest.append(event)
    return latest


def _worker_log_replay(db: Session, worker_id: str = "", limit: int = 5) -> list[WorkerLogReplayItem]:
    query = db.query(WorkerEvent).filter(WorkerEvent.severity.in_(["critical", "error", "warning"]))
    if worker_id:
        query = query.filter(WorkerEvent.worker_id == worker_id)
    events = query.order_by(WorkerEvent.created_at.desc(), WorkerEvent.id.desc()).limit(limit).all()
    return [
        _worker_log_replay_item(event)
        for event in events
        if not _is_sample_worker_event(event)
    ]


def _is_sample_worker_event(event: WorkerEvent) -> bool:
    payload = _parse_worker_message(event.message)
    text = f"{event.worker_id} {event.message} {payload.get('message', '')}".lower()
    return "验收样例" in text or "sample-task" in text or "sample" in text


def _worker_log_replay_item(event: WorkerEvent) -> WorkerLogReplayItem:
    payload = _parse_worker_message(event.message)
    return WorkerLogReplayItem(
        worker_id=event.worker_id,
        severity=event.severity,
        message=str(payload.get("message") or event.message),
        trace_id=event.trace_id,
        account_user_id=event.account_user_id,
        task_id=event.task_id,
        created_at=event.created_at,
        stage=str(payload.get("stage") or ""),
        step=str(payload.get("step") or ""),
        error_code=str(payload.get("error_code") or ""),
        error_detail=str(payload.get("error_detail") or ""),
        retryable=payload.get("retryable") if isinstance(payload.get("retryable"), bool) else None,
        duration_ms=payload.get("duration_ms") if isinstance(payload.get("duration_ms"), int) else None,
    )


def _worker_accurate_error(event: WorkerEvent) -> str:
    payload = _parse_worker_message(event.message)
    error_code = str(payload.get("error_code") or "").strip()
    error_detail = str(payload.get("error_detail") or "").strip()
    message = str(payload.get("message") or event.message or "").strip()
    parts = [part for part in [error_code, error_detail, message] if part]
    return "；".join(parts) if parts else "Worker 上报异常但未提供 message"


def _parse_worker_message(message: str) -> dict:
    try:
        value = json.loads(message)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
