from datetime import datetime

from pydantic import BaseModel


class AuditLogRead(BaseModel):
    id: int
    event_type: str
    severity: str
    actor: str
    target_type: str
    target_id: str
    message: str
    trace_id: str
    created_at: datetime

    model_config = {"from_attributes": True}
