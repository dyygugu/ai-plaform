from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.incident import IncidentClosurePlanResponse, IncidentClosureRequest, IncidentClosureResponse, IncidentRunbookItem, IncidentSummaryResponse
from app.services.incident_service import build_incident_closure_plan, build_incident_summary, create_incident_closure, list_incident_runbooks

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("/summary", response_model=IncidentSummaryResponse)
def read_incident_summary(db: Session = Depends(get_db)) -> IncidentSummaryResponse:
    summary = build_incident_summary(db)
    db.commit()
    return summary


@router.get("/runbooks", response_model=list[IncidentRunbookItem])
def read_incident_runbooks(db: Session = Depends(get_db)) -> list[IncidentRunbookItem]:
    runbooks = list_incident_runbooks(db)
    db.commit()
    return runbooks


@router.get("/closure-plan", response_model=IncidentClosurePlanResponse)
def read_incident_closure_plan(db: Session = Depends(get_db)) -> IncidentClosurePlanResponse:
    plan = build_incident_closure_plan(db)
    db.commit()
    return plan


@router.post("/close-loop", response_model=IncidentClosureResponse)
def close_incident_loop(request: IncidentClosureRequest, db: Session = Depends(get_db)) -> IncidentClosureResponse:
    return create_incident_closure(db, request)
