from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.delivery import DeliveryBundleResponse, DeliveryChecklistResponse, DeliverySummaryResponse
from app.services.delivery_service import build_delivery_checklist, build_delivery_summary, generate_delivery_bundle

router = APIRouter(prefix="/delivery", tags=["delivery"])


@router.get("/summary", response_model=DeliverySummaryResponse)
def read_delivery_summary(db: Session = Depends(get_db)) -> DeliverySummaryResponse:
    return build_delivery_summary(db)


@router.get("/checklist", response_model=DeliveryChecklistResponse)
def read_delivery_checklist(db: Session = Depends(get_db)) -> DeliveryChecklistResponse:
    return build_delivery_checklist(db)


@router.post("/bundle", response_model=DeliveryBundleResponse)
def create_delivery_bundle(db: Session = Depends(get_db)) -> DeliveryBundleResponse:
    return generate_delivery_bundle(db)
