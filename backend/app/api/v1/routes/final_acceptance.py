from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.final_acceptance import FinalAcceptanceMatrixResponse, FinalEvidenceRequest, FinalEvidenceResponse, RollbackDrillStep
from app.services.final_acceptance_service import build_final_acceptance_matrix, build_rollback_drill, create_final_evidence

router = APIRouter(prefix="/final-acceptance", tags=["final-acceptance"])


@router.get("/matrix", response_model=FinalAcceptanceMatrixResponse)
def read_final_acceptance_matrix(db: Session = Depends(get_db)) -> FinalAcceptanceMatrixResponse:
    matrix = build_final_acceptance_matrix(db)
    db.commit()
    return matrix


@router.get("/rollback", response_model=list[RollbackDrillStep])
def read_rollback_drill(db: Session = Depends(get_db)) -> list[RollbackDrillStep]:
    steps = build_rollback_drill(db)
    db.commit()
    return steps


@router.post("/evidence", response_model=FinalEvidenceResponse)
def create_final_acceptance_evidence(request: FinalEvidenceRequest, db: Session = Depends(get_db)) -> FinalEvidenceResponse:
    return create_final_evidence(db, request)
