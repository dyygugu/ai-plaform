from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.roadmap_final import RoadmapFinalReportRequest, RoadmapFinalReportResponse, RoadmapFinalSummaryResponse
from app.services.roadmap_final_service import build_roadmap_final_summary, create_roadmap_final_report

router = APIRouter(prefix="/roadmap-final", tags=["roadmap-final"])


@router.get("/summary", response_model=RoadmapFinalSummaryResponse)
def read_roadmap_final_summary(db: Session = Depends(get_db)) -> RoadmapFinalSummaryResponse:
    summary = build_roadmap_final_summary(db)
    db.commit()
    return summary


@router.post("/report", response_model=RoadmapFinalReportResponse)
def create_roadmap_finalization_report(request: RoadmapFinalReportRequest, db: Session = Depends(get_db)) -> RoadmapFinalReportResponse:
    return create_roadmap_final_report(db, request)
