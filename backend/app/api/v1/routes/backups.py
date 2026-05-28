from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.backup import BackupJob
from app.schemas.backup import BackupJobRead, BackupPlanResponse, BackupTargetTestResponse
from app.services.audit_service import write_audit
from app.services.backup_service import create_manual_backup, get_backup_plan, test_local_backup_target

router = APIRouter(prefix="/backups", tags=["backups"])


@router.get("/plan", response_model=BackupPlanResponse)
def read_backup_plan() -> BackupPlanResponse:
    return BackupPlanResponse(**get_backup_plan())


@router.post("/test-local", response_model=BackupTargetTestResponse)
def test_local_backup() -> BackupTargetTestResponse:
    return BackupTargetTestResponse(**test_local_backup_target())


@router.post("/manual", response_model=BackupJobRead)
def run_manual_backup(db: Session = Depends(get_db)) -> BackupJobRead:
    job = create_manual_backup(db)
    write_audit(db, event_type="backup_manual", message=job.message, target_type="backup", target_id=str(job.id))
    db.commit()
    db.refresh(job)
    return BackupJobRead.model_validate(job)


@router.get("/jobs", response_model=list[BackupJobRead])
def read_backup_jobs(limit: int = 50, db: Session = Depends(get_db)) -> list[BackupJobRead]:
    bounded_limit = max(1, min(limit, 200))
    jobs = db.scalars(select(BackupJob).order_by(BackupJob.created_at.desc()).limit(bounded_limit)).all()
    return [BackupJobRead.model_validate(job) for job in jobs]
