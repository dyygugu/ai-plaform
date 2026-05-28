from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.v1.routes.task_auto_runs import _adapters, _state_dir
from app.db.session import get_db
from app.schemas.auto_production import AutoProductionStatusResponse, StartAutoProductionRequest
from app.schemas.task_auto_runs import TaskAutoRunResponse
from app.services.auto_production_service import AutoProductionError, get_auto_production_status, start_auto_production

router = APIRouter(prefix="/tasks/{task_id}/auto-production", tags=["auto-production"])


@router.get("/status", response_model=AutoProductionStatusResponse)
def read_auto_production_status(task_id: str, db: Session = Depends(get_db)) -> AutoProductionStatusResponse:
    return get_auto_production_status(db, task_id)


@router.post("/production/start", response_model=TaskAutoRunResponse)
def start_task_production(task_id: str, payload: StartAutoProductionRequest, request: Request, db: Session = Depends(get_db)) -> TaskAutoRunResponse:
    try:
        return start_auto_production(db, task_id, payload, adapters=_adapters(request), state_dir=_state_dir(request))
    except AutoProductionError as exc:
        if exc.code != "INVALID_MAX_ITEMS_TOTAL":
            raise HTTPException(status_code=400, detail=exc.message) from exc
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
