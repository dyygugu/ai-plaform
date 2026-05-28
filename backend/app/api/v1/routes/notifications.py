from typing import Optional

from fastapi import APIRouter

from app.schemas.notification import NotificationConfigRead, NotificationConfigUpdate, NotificationSendResponse, NotificationTestRequest
from app.services.notification_service import get_notification_config_status, send_test_notification, update_notification_config


router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationConfigRead)
def read_notifications() -> NotificationConfigRead:
    return get_notification_config_status()


@router.put("", response_model=NotificationConfigRead)
def update_notifications(payload: NotificationConfigUpdate) -> NotificationConfigRead:
    return update_notification_config(payload)


@router.post("/test", response_model=NotificationSendResponse)
def test_notifications(payload: Optional[NotificationTestRequest] = None) -> NotificationSendResponse:
    return send_test_notification(bool(payload.send) if payload else False)
