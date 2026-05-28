import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.backup import BackupJob, BackupStatus


def get_backup_plan() -> dict[str, object]:
    settings = get_settings()
    return {
        "local_retention_days": settings.backup_local_retention_days,
        "external_retention_days": settings.backup_external_retention_days,
        "cleanup_time": settings.backup_cleanup_time,
        "external_target_path": settings.backup_external_root,
        "cleanup_failure_alert": "面板告警 + 飞书告警",
    }


def test_local_backup_target() -> dict[str, object]:
    settings = get_settings()
    root = Path(settings.backup_local_root)
    root.mkdir(parents=True, exist_ok=True)
    probe = root / ".write-test"
    probe.write_text("ok", encoding="utf-8", newline="")
    probe.unlink(missing_ok=True)
    return {"ok": True, "path": str(root), "message": "本机备份目录可写"}


def create_manual_backup(db: Session) -> BackupJob:
    settings = get_settings()
    root = Path(settings.backup_local_root)
    root.mkdir(parents=True, exist_ok=True)
    trace_id = uuid4().hex
    job = BackupJob(
        backup_type="manual",
        status=BackupStatus.RUNNING,
        local_retention_days=settings.backup_local_retention_days,
        external_retention_days=settings.backup_external_retention_days,
        cleanup_time=settings.backup_cleanup_time,
        target_path=str(root),
        trace_id=trace_id,
        message="manual backup started",
    )
    db.add(job)
    db.flush()
    artifact = root / f"backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{trace_id[:8]}.json"
    artifact.write_text(
        json.dumps(
            {
                "traceId": trace_id,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "type": "manual",
                "contents": ["config-summary", "schema-version", "audit-summary", "restore-metadata"],
                "redacted": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="",
    )
    job.status = BackupStatus.COMPLETED
    job.message = f"manual backup completed: {artifact}"
    db.flush()
    return job


def cleanup_old_backup_artifacts(db: Session, dry_run: bool = False) -> dict[str, object]:
    settings = get_settings()
    root = Path(settings.backup_local_root)
    root.mkdir(parents=True, exist_ok=True)
    quarantine = root / "cleanup-quarantine"
    now = datetime.now(timezone.utc)
    moved: list[str] = []
    candidates: list[str] = []
    for artifact in root.glob("backup-*.json"):
        age_days = (now - datetime.fromtimestamp(artifact.stat().st_mtime, timezone.utc)).days
        if age_days < settings.backup_local_retention_days:
            continue
        candidates.append(str(artifact))
        if not dry_run:
            quarantine.mkdir(parents=True, exist_ok=True)
            target = quarantine / artifact.name
            suffix = 1
            while target.exists():
                target = quarantine / f"{artifact.stem}-{suffix}{artifact.suffix}"
                suffix += 1
            shutil.move(str(artifact), str(target))
            moved.append(str(target))
    trace_id = uuid4().hex
    job = BackupJob(
        backup_type="cleanup-dry-run" if dry_run else "cleanup",
        status=BackupStatus.COMPLETED,
        local_retention_days=settings.backup_local_retention_days,
        external_retention_days=settings.backup_external_retention_days,
        cleanup_time=settings.backup_cleanup_time,
        target_path=str(root),
        trace_id=trace_id,
        message=f"backup cleanup {'dry-run ' if dry_run else ''}completed: {len(candidates)} candidate(s), {len(moved)} moved",
    )
    db.add(job)
    db.flush()
    return {
        "trace_id": trace_id,
        "root": str(root),
        "quarantine": str(quarantine),
        "retention_days": settings.backup_local_retention_days,
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "moved_count": len(moved),
        "candidates": candidates,
        "moved": moved,
        "backup_job_id": job.id,
    }
