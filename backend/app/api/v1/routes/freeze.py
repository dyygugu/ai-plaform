from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.freeze import FreezeChecklistResponse, FreezeCreateRequest, FreezeCreateResponse, FreezeSummaryResponse
from app.services.freeze_service import build_freeze_checklist, build_freeze_summary, create_freeze_baseline

router = APIRouter(prefix="/freeze", tags=["freeze"])


@router.get("/summary", response_model=FreezeSummaryResponse)
def read_freeze_summary(db: Session = Depends(get_db)) -> FreezeSummaryResponse:
    return build_freeze_summary(db)


@router.get("/checklist", response_model=FreezeChecklistResponse)
def read_freeze_checklist(db: Session = Depends(get_db)) -> FreezeChecklistResponse:
    return build_freeze_checklist(db)


@router.post("/baseline", response_model=FreezeCreateResponse)
def create_manual_switch_freeze(payload: FreezeCreateRequest, db: Session = Depends(get_db)) -> FreezeCreateResponse:
    return create_freeze_baseline(db, payload)
