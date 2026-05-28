from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.session import get_db
from app.schemas.alerting import AlertEvaluationRequest, AlertEvaluationResponse, AlertRuleRead, AlertSummaryResponse, SloSummaryResponse
from app.schemas.ops import AlertPreviewRequest, AlertPreviewResponse
from app.services.alert_service import build_alert_message
from app.services.alerting_service import build_alert_summary, build_slo_summary, evaluate_alerts, list_alert_rules

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("/preview", response_model=AlertPreviewResponse)
def preview_alert(payload: AlertPreviewRequest) -> AlertPreviewResponse:
    settings = get_settings()
    alert = build_alert_message(payload.title, payload.severity, payload.subject, payload.reason, settings.public_base_url)
    return AlertPreviewResponse(text=alert.render_feishu_text(), trace_id=alert.trace_id)


@router.get("/rules", response_model=list[AlertRuleRead])
def read_alert_rules() -> list[AlertRuleRead]:
    return list_alert_rules()


@router.get("/slo", response_model=SloSummaryResponse)
def read_slo_summary(db: Session = Depends(get_db)) -> SloSummaryResponse:
    return build_slo_summary(db)


@router.get("/summary", response_model=AlertSummaryResponse)
def read_alert_summary(db: Session = Depends(get_db)) -> AlertSummaryResponse:
    return build_alert_summary(db)


@router.post("/evaluate", response_model=AlertEvaluationResponse)
def evaluate_alert_closure(payload: Optional[AlertEvaluationRequest] = None, db: Session = Depends(get_db)) -> AlertEvaluationResponse:
    return evaluate_alerts(db, payload or AlertEvaluationRequest())

