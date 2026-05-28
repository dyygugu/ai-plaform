from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.inspection import InspectionChecklistResponse, InspectionRunRequest, InspectionRunResponse, InspectionSummaryResponse
from app.services.inspection_service import build_inspection_checklist, build_inspection_summary, run_inspection

router = APIRouter(prefix="/inspection", tags=["inspection"])


@router.get("/summary", response_model=InspectionSummaryResponse)
def read_inspection_summary(db: Session = Depends(get_db)) -> InspectionSummaryResponse:
    return build_inspection_summary(db)


@router.get("/checklist", response_model=InspectionChecklistResponse)
def read_inspection_checklist(db: Session = Depends(get_db)) -> InspectionChecklistResponse:
    return build_inspection_checklist(db)


@router.post("/run", response_model=InspectionRunResponse)
def run_daily_inspection(payload: InspectionRunRequest, db: Session = Depends(get_db)) -> InspectionRunResponse:
    return run_inspection(db, payload)
