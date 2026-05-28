from fastapi import APIRouter

from app.schemas.ai_timer import AiTimerEventCreate, AiTimerEventRead, AiTimerSummaryResponse
from app.services.ai_timer_service import ai_timer_event_log_path, build_ai_timer_summary, record_ai_timer_event
from app.services.earnings_service import read_earnings_price_config

router = APIRouter(prefix="/ai-timer", tags=["ai-timer"])


@router.get("/summary", response_model=AiTimerSummaryResponse)
def read_ai_timer_summary() -> AiTimerSummaryResponse:
    price = read_earnings_price_config()
    return build_ai_timer_summary(event_log_path=ai_timer_event_log_path(), unit_price=price.unit_price)


@router.post("/events", response_model=AiTimerEventRead)
def create_ai_timer_event(payload: AiTimerEventCreate) -> AiTimerEventRead:
    return record_ai_timer_event(payload, event_log_path=ai_timer_event_log_path())
