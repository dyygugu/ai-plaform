from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.rule import (
    RuleCenterSummary,
    RuleHitStatRead,
    RulePublishEventRead,
    RuleVersionActionRequest,
    RuleVersionCreateRequest,
    RuleVersionDiffResponse,
    RuleVersionRead,
)
from app.services.audit_service import write_audit
from app.services.rule_service import (
    compare_rule_versions,
    create_rule_version,
    get_active_rule_version,
    get_rule_version,
    hit_rate,
    list_hit_stats,
    list_publish_events,
    list_rule_versions,
    publish_rule_version,
    rollback_to_rule_version,
    set_rule_canary,
)

router = APIRouter(prefix="/rules", tags=["rules"])


def _hit_stat_read(stat) -> RuleHitStatRead:
    return RuleHitStatRead(
        id=stat.id,
        rule_version_id=stat.rule_version_id,
        rule_key=stat.rule_key,
        hits=stat.hits,
        misses=stat.misses,
        sample_task_name_id=stat.sample_task_name_id,
        updated_at=stat.updated_at,
        hit_rate=hit_rate(stat),
    )


@router.get("/center", response_model=RuleCenterSummary)
def read_rule_center(db: Session = Depends(get_db)) -> RuleCenterSummary:
    versions = list_rule_versions(db)
    active = get_active_rule_version(db)
    events = list_publish_events(db)
    stats = list_hit_stats(db)
    db.commit()
    return RuleCenterSummary(
        versions=[RuleVersionRead.model_validate(item) for item in versions],
        active_version=RuleVersionRead.model_validate(active) if active else None,
        publish_events=[RulePublishEventRead.model_validate(item) for item in events],
        hit_stats=[_hit_stat_read(item) for item in stats],
    )


@router.post("/versions", response_model=RuleVersionRead)
def create_rule(payload: RuleVersionCreateRequest, db: Session = Depends(get_db)) -> RuleVersionRead:
    item = create_rule_version(db, payload)
    write_audit(db, event_type="rule_version_create", message=f"Created rule version {item.version}", target_type="rule", target_id=item.version)
    db.commit()
    db.refresh(item)
    return RuleVersionRead.model_validate(item)


@router.get("/versions/{version_id}/diff", response_model=RuleVersionDiffResponse)
def read_rule_diff(version_id: int, base_id: Optional[int] = None, db: Session = Depends(get_db)) -> RuleVersionDiffResponse:
    target = get_rule_version(db, version_id)
    if not target:
        raise HTTPException(status_code=404, detail="Rule version not found")
    base, items = compare_rule_versions(db, target, base_id)
    return RuleVersionDiffResponse(
        base_version=RuleVersionRead.model_validate(base) if base else None,
        target_version=RuleVersionRead.model_validate(target),
        items=items,
    )


@router.post("/versions/{version_id}/canary", response_model=RulePublishEventRead)
def canary_rule(version_id: int, payload: RuleVersionActionRequest, db: Session = Depends(get_db)) -> RulePublishEventRead:
    item = get_rule_version(db, version_id)
    if not item:
        raise HTTPException(status_code=404, detail="Rule version not found")
    event = set_rule_canary(db, item, payload.canary_percent, payload.message)
    write_audit(db, event_type="rule_canary", message=f"Canary rule version {item.version} at {payload.canary_percent}%", target_type="rule", target_id=item.version)
    db.commit()
    db.refresh(event)
    return RulePublishEventRead.model_validate(event)


@router.post("/versions/{version_id}/publish", response_model=RulePublishEventRead)
def publish_rule(version_id: int, payload: Optional[RuleVersionActionRequest] = None, db: Session = Depends(get_db)) -> RulePublishEventRead:
    item = get_rule_version(db, version_id)
    if not item:
        raise HTTPException(status_code=404, detail="Rule version not found")
    event = publish_rule_version(db, item, payload.message if payload else "")
    write_audit(db, event_type="rule_publish", message=f"Published rule version {item.version}", target_type="rule", target_id=item.version)
    db.commit()
    db.refresh(event)
    return RulePublishEventRead.model_validate(event)


@router.post("/versions/{version_id}/rollback", response_model=RulePublishEventRead)
def rollback_rule(version_id: int, payload: Optional[RuleVersionActionRequest] = None, db: Session = Depends(get_db)) -> RulePublishEventRead:
    item = get_rule_version(db, version_id)
    if not item:
        raise HTTPException(status_code=404, detail="Rule version not found")
    event = rollback_to_rule_version(db, item, payload.message if payload else "")
    write_audit(db, event_type="rule_rollback", message=f"Rollback to rule version {item.version}", target_type="rule", target_id=item.version)
    db.commit()
    db.refresh(event)
    return RulePublishEventRead.model_validate(event)


