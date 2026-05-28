from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.session import get_db
from app.schemas.ops import FaultDiagnosisResponse, OperationalRiskSummaryResponse
from app.schemas.ops_job import DomainSwitchRunbookResponse, MaintenanceJobRunRead, MaintenanceJobRunRequest, MaintenanceJobSummary, ReleaseGateResponse, SchedulerPlanResponse, SchedulerTickRequest, SchedulerTickResponse
from app.services.audit_service import write_audit
from app.services.ops_job_service import build_domain_switch_runbook, build_release_gate, build_scheduler_plan, list_maintenance_jobs, run_maintenance_job, run_scheduler_tick
from app.services.ops_risk_service import build_fault_diagnosis, build_operational_risk_summary

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/jobs", response_model=MaintenanceJobSummary)
def read_jobs(db: Session = Depends(get_db)) -> MaintenanceJobSummary:
    jobs, recent_runs = list_maintenance_jobs(db)
    return MaintenanceJobSummary(jobs=jobs, recent_runs=[MaintenanceJobRunRead.model_validate(run) for run in recent_runs])


@router.post("/jobs/{job_key}/run", response_model=MaintenanceJobRunRead)
def run_job(job_key: str, payload: MaintenanceJobRunRequest, db: Session = Depends(get_db)) -> MaintenanceJobRunRead:
    try:
        run = run_maintenance_job(db, job_key, dry_run=payload.dry_run, trigger_type=payload.trigger_type)
    except KeyError:
        raise HTTPException(status_code=404, detail="Maintenance job not found")
    write_audit(db, event_type="maintenance_job_run", message=f"Run {job_key}: {run.message}", target_type="maintenance_job", target_id=job_key)
    db.commit()
    db.refresh(run)
    return MaintenanceJobRunRead.model_validate(run)


@router.get("/release-gate", response_model=ReleaseGateResponse)
def read_release_gate(db: Session = Depends(get_db)) -> ReleaseGateResponse:
    settings = get_settings()
    checks, ready = build_release_gate(db)
    return ReleaseGateResponse(
        ready_for_manual_domain_switch=ready,
        production_domain="manage.51gugu.uk",
        public_base_url=settings.public_base_url,
        manual_switch_required=True,
        message="自动门禁通过后也不会自动切换正式域名；最终由用户手动修改反代。" if ready else "仍有必需门禁未通过，暂不建议切换正式域名。",
        checks=checks,
    )


@router.get("/risk-summary", response_model=OperationalRiskSummaryResponse)
def read_risk_summary(db: Session = Depends(get_db)) -> OperationalRiskSummaryResponse:
    return build_operational_risk_summary(db)


@router.get("/fault-diagnosis", response_model=FaultDiagnosisResponse)
def read_fault_diagnosis(db: Session = Depends(get_db)) -> FaultDiagnosisResponse:
    return build_fault_diagnosis(db)


@router.get("/scheduler/plan", response_model=SchedulerPlanResponse)
def read_scheduler_plan(db: Session = Depends(get_db)) -> SchedulerPlanResponse:
    from app.services.task_rules import utc_now

    jobs, due_count = build_scheduler_plan(db)
    return SchedulerPlanResponse(now=utc_now(), due_count=due_count, jobs=jobs)


@router.post("/scheduler/tick", response_model=SchedulerTickResponse)
def run_scheduler_tick_endpoint(payload: SchedulerTickRequest, db: Session = Depends(get_db)) -> SchedulerTickResponse:
    from app.services.task_rules import utc_now

    runs, due_count, skipped_count = run_scheduler_tick(db, dry_run=payload.dry_run, limit=payload.limit)
    write_audit(db, event_type="scheduler_tick", message=f"Scheduler tick executed {len(runs)} of {due_count} due jobs", target_type="scheduler")
    db.commit()
    return SchedulerTickResponse(
        now=utc_now(),
        dry_run=payload.dry_run,
        due_count=due_count,
        executed_count=len(runs),
        skipped_count=skipped_count,
        runs=[MaintenanceJobRunRead.model_validate(run) for run in runs],
        message=f"调度 Tick 完成：到期 {due_count} 个，执行 {len(runs)} 个，跳过 {skipped_count} 个。",
    )


@router.get("/domain-switch-runbook", response_model=DomainSwitchRunbookResponse)
def read_domain_switch_runbook(db: Session = Depends(get_db)) -> DomainSwitchRunbookResponse:
    settings = get_settings()
    checks, ready, steps, rollback_steps = build_domain_switch_runbook(db)
    return DomainSwitchRunbookResponse(
        production_domain="manage.51gugu.uk",
        target_base_url=settings.public_base_url,
        ready_for_manual_domain_switch=ready,
        manual_only=True,
        summary="Runbook 只生成手动切换步骤，不会自动修改正式反代。",
        pre_checks=checks,
        steps=steps,
        rollback_steps=rollback_steps,
    )
