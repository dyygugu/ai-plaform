from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.operation_recording import OperationRecordingRequest, OperationRecordingResponse
from app.services.audit_service import write_audit
from app.services.operation_recording_service import save_operation_recording


router = APIRouter(prefix="/operation-recordings", tags=["operation-recordings"])


@router.post("", response_model=OperationRecordingResponse)
def upload_operation_recording(payload: OperationRecordingRequest, db: Session = Depends(get_db)) -> OperationRecordingResponse:
    response = save_operation_recording(db, payload)
    write_audit(
        db,
        event_type="operation_recording_upload",
        message=f"上传操作录制 {response.recording_id}",
        target_type="operation_recording",
        target_id=response.recording_id,
    )
    db.commit()
    return response
