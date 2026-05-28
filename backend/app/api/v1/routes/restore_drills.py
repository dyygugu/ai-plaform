from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ops import RestoreDrillRead
from app.services.audit_service import write_audit
from app.services.restore_service import run_restore_drill

router = APIRouter(prefix="/restore-drills", tags=["restore-drills"])


@router.post("/run", response_model=RestoreDrillRead)
def run_restore_drill_endpoint(db: Session = Depends(get_db)) -> RestoreDrillRead:
    drill = run_restore_drill(db)
    write_audit(db, event_type="restore_drill", message=drill.message, target_type="restore_drill", target_id=str(drill.id))
    db.commit()
    db.refresh(drill)
    return RestoreDrillRead.model_validate(drill)
