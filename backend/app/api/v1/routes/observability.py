from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.observability import CollectorGuardResponse, ObservabilitySummary, ProbeRunResponse, TimelineEvent
from app.services.observability_service import build_collector_guard, build_observability_summary, list_timeline_events, run_probes

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/summary", response_model=ObservabilitySummary)
def read_observability_summary(db: Session = Depends(get_db)) -> ObservabilitySummary:
    return build_observability_summary(db)


@router.get("/collector-guard", response_model=CollectorGuardResponse)
def read_collector_guard(db: Session = Depends(get_db)) -> CollectorGuardResponse:
    return build_collector_guard(db)


@router.get("/timeline", response_model=list[TimelineEvent])
def read_timeline(limit: int = 50, db: Session = Depends(get_db)) -> list[TimelineEvent]:
    bounded_limit = max(1, min(limit, 200))
    return list_timeline_events(db, bounded_limit)


@router.post("/probes/run", response_model=ProbeRunResponse)
def run_observability_probes(db: Session = Depends(get_db)) -> ProbeRunResponse:
    return run_probes(db, persist_audit=True)
