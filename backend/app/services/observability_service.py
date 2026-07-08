import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.models.ai import AiJob
from app.models.audit import AuditLog, AuditSeverity
from app.models.backup import BackupJob, BackupStatus
from app.models.ops_job import MaintenanceJobRun
from app.models.task import TaskCatalogItem
from app.models.worker import Worker, WorkerEvent, WorkerStatus
from app.schemas.observability import CollectorGuardResponse, ObservabilityMetric, ObservabilitySummary, ProbeResult, ProbeRunResponse, TimelineEvent
from app.services.audit_service import write_audit
from app.services.ops_job_service import build_release_gate
from app.services.runtime_account_service import load_runtime_account
from app.services.task_rules import utc_now
from app.services.task_service import get_task_source_account_user_id


def build_collector_guard(db: Session) -> CollectorGuardResponse:
    settings = get_settings()
    source = get_task_source_account_user_id(db)
    sample_path = Path(settings.task_sample_root) / "task-page-latest-summary.json"
    sample_exists = sample_path.exists()
    sample_age_minutes = None
    if sample_exists:
        mtime = datetime.fromtimestamp(sample_path.stat().st_mtime, timezone.utc)
        sample_age_minutes = round((utc_now() - mtime).total_seconds() / 60, 2)
    tasks = list(db.scalars(select(TaskCatalogItem).where(TaskCatalogItem.source_account_user_id == source)))
    stale_count = sum(1 for item in tasks if item.last_task_page_error)
    latest_error = next((item.last_task_page_error for item in tasks if item.last_task_page_error), None)
    status = "passed" if sample_exists and tasks and stale_count == 0 else "warning"
    if not sample_exists or not tasks:
        status = "failed"
    message = "只读采集守护正常；当前使用脱敏样本和任务目录快照。" if status == "passed" else "采集守护需要关注：样本缺失、目录为空或存在错误。"
    return CollectorGuardResponse(
        source_account_user_id=source,
        safe_mode=True,
        live_readonly_available=load_runtime_account(source) is not None,
        sample_summary_path=str(sample_path),
        sample_exists=sample_exists,
        sample_age_minutes=sample_age_minutes,
        task_count=len(tasks),
        stale_count=stale_count,
        error_count=stale_count,
        latest_error=latest_error,
        status=status,
        message=message,
    )


def build_observability_summary(db: Session) -> ObservabilitySummary:
    settings = get_settings()
    collector_guard = build_collector_guard(db)
    probes = run_probes(db, persist_audit=False).results
    metrics = _build_metrics(db, collector_guard)
    failed_or_warning = any(metric.status in {"failed", "warning"} for metric in metrics) or any(probe.status in {"failed", "warning"} for probe in probes)
    return ObservabilitySummary(
        generated_at=utc_now(),
        status="warning" if failed_or_warning else "passed",
        environment=settings.monitor_env,
        public_base_url=settings.public_base_url,
        metrics=metrics,
        collector_guard=collector_guard,
        recent_timeline=list_timeline_events(db, limit=12),
        probes=probes,
    )


def _build_metrics(db: Session, collector_guard: CollectorGuardResponse) -> list[ObservabilityMetric]:
    account_total = db.query(AidpAccount).count()
    account_needs_login = db.query(AidpAccount).filter(AidpAccount.status == AccountStatus.NEEDS_LOGIN).count()
    task_total = db.query(TaskCatalogItem).count()
    worker_online = db.query(Worker).filter(Worker.status == WorkerStatus.ONLINE).count()
    ai_total = db.query(AiJob).count()
    backup_completed = db.query(BackupJob).filter(BackupJob.status == BackupStatus.COMPLETED).count()
    audit_errors = db.query(AuditLog).filter(AuditLog.severity.in_([AuditSeverity.ERROR, AuditSeverity.CRITICAL])).count()
    scheduler_runs = db.query(MaintenanceJobRun).count()
    ability_counts = _task_ability_counts()
    return [
        ObservabilityMetric(key="accounts", title="账号总数", value=account_total, status="warning" if account_needs_login else "passed", message=f"需登录关注 {account_needs_login} 个"),
        ObservabilityMetric(key="task_catalog", title="任务目录", value=task_total, status="passed" if task_total else "failed", message=f"采集守护状态：{collector_guard.status}"),
        ObservabilityMetric(key="workers", title="在线 Worker", value=worker_online, status="passed" if worker_online else "warning", message="至少 1 个 Worker 在线用于验收样例"),
        ObservabilityMetric(key="ai_jobs", title="AI 队列记录", value=ai_total, status="passed" if ai_total else "warning", message="AI mock 队列用于链路占位"),
        ObservabilityMetric(key="backups", title="完成备份/清理", value=backup_completed, status="passed" if backup_completed else "warning", message="包含手动备份和清理记录"),
        ObservabilityMetric(key="audit_errors", title="严重审计", value=audit_errors, status="passed" if audit_errors == 0 else "failed", message="error/critical 审计数量"),
        ObservabilityMetric(key="scheduler_runs", title="调度运行", value=scheduler_runs, status="passed" if scheduler_runs else "warning", message="运维任务和调度 Tick 运行历史"),
        ObservabilityMetric(key="task_abilities", title="AI 标注能力", value=ability_counts["enabled"], status="passed" if ability_counts["enabled"] else "warning", message=f"草稿 {ability_counts['total']} 个，已启用 {ability_counts['enabled']} 个"),
    ]


def run_probes(db: Session, persist_audit: bool = True) -> ProbeRunResponse:
    trace_id = uuid4().hex
    started = utc_now()
    results = [
        _probe_database(db),
        _probe_task_catalog(db),
        _probe_ability_workbench(),
        _probe_worker(db),
        _probe_release_gate(db),
        _probe_collector_guard(db),
    ]
    status = "failed" if any(item.status == "failed" for item in results) else "warning" if any(item.status == "warning" for item in results) else "passed"
    finished = utc_now()
    if persist_audit:
        write_audit(db, event_type="observability_probe", message=f"Observability probes finished with {status}", target_type="observability", target_id=trace_id)
        db.commit()
    return ProbeRunResponse(trace_id=trace_id, status=status, started_at=started, finished_at=finished, results=results)


def _timed_probe(key: str, title: str, func):
    start = time.perf_counter()
    try:
        status, message, details = func()
    except Exception as exc:
        status, message, details = "failed", str(exc), {}
    latency_ms = int((time.perf_counter() - start) * 1000)
    return ProbeResult(key=key, title=title, status=status, latency_ms=latency_ms, message=message, details=details)


def _probe_database(db: Session) -> ProbeResult:
    return _timed_probe("database", "数据库连通", lambda: ("passed", "SELECT 1 ok", {"value": db.execute(text("select 1")).scalar_one()}))


def _probe_task_catalog(db: Session) -> ProbeResult:
    def check():
        count = db.query(TaskCatalogItem).count()
        return ("passed" if count else "failed", f"任务目录 {count} 条", {"count": count})
    return _timed_probe("task_catalog", "任务目录", check)


def _probe_ability_workbench() -> ProbeResult:
    def check():
        counts = _task_ability_counts()
        status = "passed" if counts["enabled"] else "warning"
        message = f"AI 标注能力工作台草稿 {counts['total']} 个，已启用 {counts['enabled']} 个。"
        return (status, message, counts)
    return _timed_probe("task_ability_workbench", "AI 标注能力工作台", check)


def _probe_worker(db: Session) -> ProbeResult:
    def check():
        online = db.query(Worker).filter(Worker.status == WorkerStatus.ONLINE).count()
        return ("passed" if online else "warning", f"在线 Worker {online} 个", {"online": online})
    return _timed_probe("workers", "Worker 在线", check)


def _probe_release_gate(db: Session) -> ProbeResult:
    def check():
        checks, ready = build_release_gate(db)
        failed = [item.key for item in checks if item.required and item.status == "failed"]
        return ("passed" if ready else "failed", "发布门禁通过" if ready else "发布门禁未通过", {"failed": failed, "checks": len(checks)})
    return _timed_probe("release_gate", "发布门禁", check)


def _probe_collector_guard(db: Session) -> ProbeResult:
    def check():
        guard = build_collector_guard(db)
        return (guard.status, guard.message, {"task_count": guard.task_count, "sample_exists": guard.sample_exists, "sample_age_minutes": guard.sample_age_minutes or 0})
    return _timed_probe("collector_guard", "采集守护", check)


def list_timeline_events(db: Session, limit: int = 50) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for item in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)):
        events.append(TimelineEvent(
            id=f"audit-{item.id}",
            source="audit",
            severity=item.severity.value,
            title=item.event_type,
            message=item.message,
            trace_id=item.trace_id,
            created_at=item.created_at,
            target_type=item.target_type,
            target_id=item.target_id,
        ))
    for item in db.scalars(select(MaintenanceJobRun).order_by(MaintenanceJobRun.started_at.desc()).limit(limit)):
        events.append(TimelineEvent(
            id=f"job-{item.id}",
            source="maintenance",
            severity="info" if item.status.value == "completed" else item.status.value,
            title=item.job_key,
            message=item.message,
            trace_id=item.trace_id,
            created_at=item.started_at,
            target_type="maintenance_job",
            target_id=item.job_key,
        ))
    for item in db.scalars(select(WorkerEvent).order_by(WorkerEvent.created_at.desc()).limit(limit)):
        events.append(TimelineEvent(
            id=f"worker-{item.id}",
            source="worker",
            severity=item.severity,
            title=item.event_type.value,
            message=item.message,
            trace_id=item.trace_id,
            created_at=item.created_at,
            target_type="worker",
            target_id=item.worker_id,
        ))
    for item in db.scalars(select(BackupJob).order_by(BackupJob.created_at.desc()).limit(limit)):
        events.append(TimelineEvent(
            id=f"backup-{item.id}",
            source="backup",
            severity="info" if item.status.value == "completed" else item.status.value,
            title=item.backup_type,
            message=item.message,
            trace_id=item.trace_id,
            created_at=item.created_at,
            target_type="backup",
            target_id=str(item.id),
        ))
    return sorted(events, key=lambda item: item.created_at, reverse=True)[: max(1, min(limit, 200))]


def _task_ability_counts() -> dict[str, int]:
    try:
        from app.services.task_ability_service import list_task_ability_drafts

        drafts = list_task_ability_drafts().items
    except Exception:
        return {"total": 0, "enabled": 0}
    enabled = sum(1 for item in drafts if item.capability_enabled and item.flow_stage == "capability_enabled")
    return {"total": len(drafts), "enabled": enabled}
