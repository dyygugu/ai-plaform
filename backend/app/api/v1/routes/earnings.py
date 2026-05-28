from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.earnings import EarningsLedgerPriceUpdate, EarningsLedgerRunItem, EarningsPriceConfigRead, EarningsPriceConfigUpdate, EarningsSummary
from app.services.audit_service import write_audit
from app.services.earnings_service import build_earnings_summary, export_earnings_excel, update_earnings_price_config
from app.services.earnings_ledger_service import update_earnings_ledger_run_price

router = APIRouter(prefix="/earnings", tags=["earnings"])


@router.get("/summary", response_model=EarningsSummary)
def read_earnings_summary(db: Session = Depends(get_db)) -> EarningsSummary:
    summary = build_earnings_summary(db)
    db.commit()
    return summary


@router.put("/price-config", response_model=EarningsPriceConfigRead)
def update_price_config(payload: EarningsPriceConfigUpdate, db: Session = Depends(get_db)) -> EarningsPriceConfigRead:
    result = update_earnings_price_config(payload)
    write_audit(db, event_type="earnings_price_config_update", message=f"Updated earnings unit price to {result.unit_price}", target_type="earnings")
    db.commit()
    return result


@router.put("/ledger/runs/{run_id}/price", response_model=EarningsLedgerRunItem)
def update_ledger_run_price(run_id: str, payload: EarningsLedgerPriceUpdate, db: Session = Depends(get_db)) -> EarningsLedgerRunItem:
    result = update_earnings_ledger_run_price(run_id, payload.unit_price)
    write_audit(db, event_type="earnings_ledger_price_update", message=f"Updated ledger run {run_id} unit price to {payload.unit_price}", target_type="earnings", target_id=run_id)
    db.commit()
    return result


@router.post("/export", response_model=EarningsSummary)
def export_earnings(db: Session = Depends(get_db)) -> EarningsSummary:
    path = export_earnings_excel(db)
    write_audit(db, event_type="earnings_export", message=f"Exported earnings report {path}", target_type="earnings", target_id=str(path))
    db.commit()
    return build_earnings_summary(db, export_path=str(path))
