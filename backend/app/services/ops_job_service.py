import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.audit import AuditLog
from app.models.backup import BackupJob, BackupStatus
from app.models.ops import RestoreDrill, RestoreDrillStatus
from app.models.ops_job import MaintenanceJobRun, MaintenanceJobStatus
from app.models.rule import RuleVersion, RuleVersionStatus
from app.models.task import TaskCatalogItem
from app.models.worker import Worker, WorkerStatus
from app.schemas.ops_job import DomainSwitchRunbookStep, MaintenanceJobDefinitionRead, MaintenanceJobRunRead, ReleaseGateCheck, SchedulerJobPlan
from app.services.account_health_service import refresh_account_health
from app.services.account_service import list_accounts
from app.services.backup_service import cleanup_old_backup_artifacts, create_manual_backup
from app.services.restore_service import run_restore_drill
from app.services.task_rules import utc_now
from app.services.task_service import get_task_source_account_user_id, seed_tasks_from_sample_summary

SCHEDULE_INTERVAL_MINUTES = {
    "backup_cleanup": 24 * 60,
    "account_health_refresh": 60,
    "task_catalog_refresh_sample": 15,
    "restore_drill": 24 * 60,
}

JOB_DEFINITIONS = [
    {
        "key": "backup_cleanup",
        "title": "备份清理",
        "schedule": "每天 03:30",
        "description": "按本机保留天数扫描 backup-*.json，移动到 cleanup-quarantine，不直接删除。",
        "enabled": True,
    },
    {
        "key": "manual_backup",
        "title": "手动备份",
        "schedule": "按需",
        "description": "生成本机备份包并写入备份记录。",
        "enabled": True,
    },
    {
        "key": "account_health_refresh",
        "title": "账号健康刷新",
        "schedule": "每小时",
        "description": "刷新账号登录态摘要和异常原因。",
        "enabled": True,
    },
    {
        "key": "task_catalog_refresh_sample",
        "title": "任务目录样本刷新",
        "schedule": "按需/采集后",
        "description": "使用最近脱敏摘要刷新任务目录，不触发真实写操作。",
        "enabled": True,
    },
    {
        "key": "restore_drill",
        "title": "恢复演练",
        "schedule": "每天一次",
        "description": "运行恢复演练验收项并写入审计。",
        "enabled": True,
    },
]


def _definition(key: str) -> dict[str, object]:
    for item in JOB_DEFINITIONS:
        if item["key"] == key:
            return item
    raise KeyError(key)


def _run_read(run: MaintenanceJobRun) -> MaintenanceJobRunRead:
    return MaintenanceJobRunRead.model_validate(run)


def list_maintenance_jobs(db: Session) -> tuple[list[MaintenanceJobDefinitionRead], list[MaintenanceJobRun]]:
    recent_runs = list(db.scalars(select(MaintenanceJobRun).order_by(MaintenanceJobRun.started_at.desc(), MaintenanceJobRun.id.desc()).limit(50)))
    jobs: list[MaintenanceJobDefinitionRead] = []
    for definition in JOB_DEFINITIONS:
        last_run = next((run for run in recent_runs if run.job_key == definition["key"]), None)
        jobs.append(MaintenanceJobDefinitionRead(
            key=str(definition["key"]),
            title=str(definition["title"]),
            schedule=str(definition["schedule"]),
            description=str(definition["description"]),
            enabled=bool(definition["enabled"]),
            last_run=_run_read(last_run) if last_run else None,
        ))
    return jobs, recent_runs


def run_maintenance_job(db: Session, job_key: str, dry_run: bool = False, trigger_type: str = "manual") -> MaintenanceJobRun:
    _definition(job_key)
    run = MaintenanceJobRun(
        job_key=job_key,
        status=MaintenanceJobStatus.RUNNING,
        trigger_type=trigger_type,
        dry_run=1 if dry_run else 0,
        trace_id=uuid4().hex,
        message="running",
        result_json="{}",
    )
    db.add(run)
    db.flush()
    try:
        result = _execute_job(db, job_key, dry_run)
        run.status = MaintenanceJobStatus.WARNING if result.get("warning") else MaintenanceJobStatus.COMPLETED
        run.message = str(result.get("message", "completed"))
        run.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    except Exception as exc:
        run.status = MaintenanceJobStatus.FAILED
        run.message = str(exc)
        run.result_json = json.dumps({"error": str(exc)}, ensure_ascii=False)
    run.finished_at = utc_now()
    db.flush()
    return run


def _execute_job(db: Session, job_key: str, dry_run: bool) -> dict[str, object]:
    if job_key == "backup_cleanup":
        result = cleanup_old_backup_artifacts(db, dry_run=dry_run)
        result["message"] = "备份清理已执行" if not dry_run else "备份清理预演已执行"
        return result
    if job_key == "manual_backup":
        if dry_run:
            return {"message": "手动备份预演通过", "dry_run": True}
        job = create_manual_backup(db)
        return {"message": job.message, "backup_job_id": job.id, "trace_id": job.trace_id}
    if job_key == "account_health_refresh":
        accounts = list_accounts(db)
        for account in accounts:
            refresh_account_health(account)
        return {"message": f"账号健康已刷新：{len(accounts)} 个账号", "account_count": len(accounts)}
    if job_key == "task_catalog_refresh_sample":
        settings = get_settings()
        source = get_task_source_account_user_id(db)
        summary_path = Path(settings.task_sample_root) / "task-page-latest-summary.json"
        if not summary_path.exists():
            return {"message": "未找到最近脱敏摘要，跳过刷新", "warning": True, "path": str(summary_path)}
        payload = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        if dry_run:
            task_count = len(payload.get("tasks", [])) if isinstance(payload, dict) else 0
            return {"message": f"任务目录刷新预演通过：{task_count} 条", "task_count": task_count, "source_account_user_id": source}
        items = seed_tasks_from_sample_summary(db, payload, source)
        return {"message": f"任务目录已刷新：{len(items)} 条", "imported_count": len(items), "source_account_user_id": source}
    if job_key == "restore_drill":
        if dry_run:
            return {"message": "恢复演练预演通过", "dry_run": True}
        drill = run_restore_drill(db)
        return {"message": drill.message, "restore_drill_id": drill.id, "trace_id": drill.trace_id}
    raise KeyError(job_key)


def build_release_gate(db: Session) -> tuple[list[ReleaseGateCheck], bool]:
    settings = get_settings()
    checks = [
        _check("health", "健康接口", True, "passed", "FastAPI 应用可响应。", {"environment": settings.monitor_env}),
        _task_sample_check(db),
        _rules_check(db),
        _worker_check(db),
        _backup_check(db),
        _restore_check(db),
        _audit_check(db),
        _check(
            "domain_switch_manual",
            "正式域名人工切换",
            True,
            "blocked" if "manage.51gugu.uk" in settings.public_base_url else "passed",
            "当前不会自动切换 manage.51gugu.uk；验收后需要用户手动改反代。",
            {"production_domain": "manage.51gugu.uk", "public_base_url": settings.public_base_url},
        ),
    ]
    ready = all(check.status == "passed" for check in checks if check.required and check.key != "domain_switch_manual")
    return checks, ready


def _check(key: str, title: str, required: bool, status: str, message: str, details: dict[str, object]) -> ReleaseGateCheck:
    return ReleaseGateCheck(key=key, title=title, required=required, status=status, message=message, details=details)


def _task_sample_check(db: Session) -> ReleaseGateCheck:
    count = db.scalar(select(TaskCatalogItem.id).limit(1))
    total = db.query(TaskCatalogItem).count()
    status = "passed" if count else "failed"
    return _check("task_catalog", "任务目录样本", True, status, f"任务目录当前 {total} 条。", {"count": total})


def _rules_check(db: Session) -> ReleaseGateCheck:
    active = db.scalar(select(RuleVersion).where(RuleVersion.status == RuleVersionStatus.PUBLISHED).order_by(RuleVersion.id.desc()))
    return _check("rule_version", "规则发布版本", True, "passed" if active else "failed", active.version if active else "未找到 published 规则版本。", {"version": active.version if active else ""})


def _worker_check(db: Session) -> ReleaseGateCheck:
    online_count = db.query(Worker).filter(Worker.status == WorkerStatus.ONLINE).count()
    status = "passed" if online_count >= 1 else "warning"
    return _check("worker_online", "Worker 在线", False, status, f"在线 Worker {online_count} 个。", {"online_count": online_count})


def _backup_check(db: Session) -> ReleaseGateCheck:
    completed = db.query(BackupJob).filter(BackupJob.status == BackupStatus.COMPLETED).count()
    status = "passed" if completed >= 1 else "warning"
    return _check("backup_completed", "备份记录", False, status, f"已完成备份/清理记录 {completed} 条。", {"completed_count": completed})


def _restore_check(db: Session) -> ReleaseGateCheck:
    passed = db.query(RestoreDrill).filter(RestoreDrill.status == RestoreDrillStatus.PASSED).count()
    status = "passed" if passed >= 1 else "warning"
    return _check("restore_drill", "恢复演练", False, status, f"通过演练 {passed} 次。", {"passed_count": passed})


def _audit_check(db: Session) -> ReleaseGateCheck:
    count = db.query(AuditLog).count()
    return _check("audit_logs", "审计日志", True, "passed" if count >= 1 else "failed", f"审计日志 {count} 条。", {"count": count})


def build_scheduler_plan(db: Session) -> tuple[list[SchedulerJobPlan], int]:
    now = utc_now()
    plans: list[SchedulerJobPlan] = []
    for definition in JOB_DEFINITIONS:
        key = str(definition["key"])
        interval = SCHEDULE_INTERVAL_MINUTES.get(key, 0)
        enabled = bool(definition["enabled"]) and interval > 0
        last_run = _latest_run_for_job(db, key)
        last_at = last_run.finished_at or last_run.started_at if last_run else None
        next_at = _next_run_at(last_at, interval) if enabled else None
        due = bool(enabled and next_at and next_at <= now)
        plans.append(SchedulerJobPlan(
            job_key=key,
            title=str(definition["title"]),
            enabled=enabled,
            interval_minutes=interval,
            last_run_at=last_at,
            next_run_at=next_at,
            due=due,
            last_status=last_run.status.value if last_run else None,
            last_message=last_run.message if last_run else "未运行",
        ))
    return plans, sum(1 for plan in plans if plan.due)


def run_scheduler_tick(db: Session, dry_run: bool = True, limit: int = 10) -> tuple[list[MaintenanceJobRun], int, int]:
    plans, due_count = build_scheduler_plan(db)
    bounded_limit = max(1, min(limit, 50))
    runs: list[MaintenanceJobRun] = []
    for plan in [item for item in plans if item.due][:bounded_limit]:
        run = run_maintenance_job(db, plan.job_key, dry_run=dry_run, trigger_type="scheduler_tick")
        runs.append(run)
    skipped_count = max(due_count - len(runs), 0)
    return runs, due_count, skipped_count


def build_domain_switch_runbook(db: Session) -> tuple[list[ReleaseGateCheck], bool, list[DomainSwitchRunbookStep], list[DomainSwitchRunbookStep]]:
    checks, ready = build_release_gate(db)
    settings = get_settings()
    target_base_url = settings.public_base_url
    steps = [
        DomainSwitchRunbookStep(
            order=1,
            title="确认本地验收入口",
            command_or_action=f"打开 {target_base_url} 并按 acceptance-checklist 完成人工验收。",
            expected_result="首页、任务看板、规则中心、Worker、运维中枢均可见。",
            rollback_note="若不通过，不修改正式反代。",
        ),
        DomainSwitchRunbookStep(
            order=2,
            title="确认发布门禁",
            command_or_action="刷新 /ops 生产护栏，确认 release-gate 必需项通过。",
            expected_result="ready_for_manual_domain_switch=true，manual_switch_required=true。",
            rollback_note="如任一必需项失败，保持 manage.51gugu.uk 指向当前稳定 upstream。",
        ),
        DomainSwitchRunbookStep(
            order=3,
            title="手动修改正式反代",
            command_or_action="由用户在反代/Cloudflare Tunnel 配置中，将 manage.51gugu.uk 指向新服务端口或新 upstream。",
            expected_result="正式域名打开后显示新版 AIDP Monitor。",
            rollback_note="保存切换前 upstream 配置，便于立即恢复。",
        ),
        DomainSwitchRunbookStep(
            order=4,
            title="切换后验证",
            command_or_action="访问 manage.51gugu.uk/api/v1/health、/tasks、/ops，并检查审计日志。",
            expected_result="健康接口 ok，任务待处理数字正确，发布门禁可读。",
            rollback_note="若页面或接口异常，立即执行回滚步骤。",
        ),
    ]
    rollback_steps = [
        DomainSwitchRunbookStep(
            order=1,
            title="恢复切换前 upstream",
            command_or_action="将 manage.51gugu.uk 反代目标恢复到切换前保存的 upstream。",
            expected_result="正式域名恢复到切换前稳定服务。",
        ),
        DomainSwitchRunbookStep(
            order=2,
            title="保留新版本现场",
            command_or_action="保持 8789 新版容器运行，导出 /ops/release-gate 和 Docker logs 用于排查。",
            expected_result="不丢失新版本故障现场。",
        ),
        DomainSwitchRunbookStep(
            order=3,
            title="记录审计与复验",
            command_or_action="在运维中枢运行恢复演练和备份清理预演，记录结果后再安排二次切换。",
            expected_result="回滚过程可追溯。",
        ),
    ]
    return checks, ready, steps, rollback_steps


def _latest_run_for_job(db: Session, job_key: str) -> Optional[MaintenanceJobRun]:
    return db.scalar(select(MaintenanceJobRun).where(MaintenanceJobRun.job_key == job_key).order_by(MaintenanceJobRun.started_at.desc(), MaintenanceJobRun.id.desc()).limit(1))


def _next_run_at(last_at, interval_minutes: int):
    now = utc_now()
    if interval_minutes <= 0:
        return None
    if last_at is None:
        return now
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=now.tzinfo)
    return last_at + timedelta(minutes=interval_minutes)

