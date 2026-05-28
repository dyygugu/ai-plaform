from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.data_quality import DataQualityCheckItem, DataQualityExportResponse, DataQualityReportRequest, DataQualityReportResponse, DataQualitySummaryResponse
from app.services.data_quality_service import build_data_quality_summary, create_data_quality_report, export_data_quality_workbook, list_data_quality_checks

router = APIRouter(prefix="/data-quality", tags=["data-quality"])


@router.get("/summary", response_model=DataQualitySummaryResponse)
def read_data_quality_summary(db: Session = Depends(get_db)) -> DataQualitySummaryResponse:
    summary = build_data_quality_summary(db)
    db.commit()
    return summary


@router.get("/checks", response_model=list[DataQualityCheckItem])
def read_data_quality_checks(db: Session = Depends(get_db)) -> list[DataQualityCheckItem]:
    checks = list_data_quality_checks(db)
    db.commit()
    return checks


@router.post("/export", response_model=DataQualityExportResponse)
def export_data_quality(db: Session = Depends(get_db)) -> DataQualityExportResponse:
    response = export_data_quality_workbook(db)
    db.commit()
    return response


@router.post("/report", response_model=DataQualityReportResponse)
def create_data_quality_baseline(request: DataQualityReportRequest, db: Session = Depends(get_db)) -> DataQualityReportResponse:
    return create_data_quality_report(db, request)
