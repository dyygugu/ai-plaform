import json
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.models.ops import EarningsSnapshot
from app.models.task import TaskCatalogItem, TaskVisibility
from app.schemas.earnings import EarningsPriceConfigRead, EarningsPriceConfigUpdate, EarningsSnapshotRead, EarningsSummary, EarningsTaskIncomeItem
from app.services.task_rules import utc_now


def ensure_demo_earnings(db: Session) -> list[EarningsSnapshot]:
    existing = list(db.scalars(select(EarningsSnapshot).order_by(EarningsSnapshot.account_user_id.asc(), EarningsSnapshot.captured_at.desc())))
    accounts = _production_accounts(db)
    if not accounts:
        settings = get_settings()
        accounts = [AidpAccount(user_id=settings.task_source_account_user_id, display_name="主账号", is_task_source=True)]
    existing_by_account = {item.account_user_id: item for item in existing}
    missing_accounts = [account for account in accounts if account.user_id not in existing_by_account]
    for account in missing_accounts:
        item = EarningsSnapshot(
            account_user_id=account.user_id,
            source_label="页面原始三项",
            income_1_name="收入项1",
            income_1_value=0,
            income_2_name="收入项2",
            income_2_value=0,
            income_3_name="收入项3",
            income_3_value=0,
            today_income=0,
            hourly_income=0,
        )
        db.add(item)
        existing.append(item)
    db.flush()
    account_ids = {account.user_id for account in accounts}
    return [row for row in db.scalars(select(EarningsSnapshot).order_by(EarningsSnapshot.account_user_id.asc(), EarningsSnapshot.captured_at.desc())) if row.account_user_id in account_ids]


def list_earnings(db: Session) -> list[EarningsSnapshot]:
    return ensure_demo_earnings(db)


def read_earnings_price_config() -> EarningsPriceConfigRead:
    data = _load_json(_config_path())
    if not isinstance(data, dict):
        data = {}
    return EarningsPriceConfigRead(
        unit_price=_float(data.get("unit_price"), 0),
        currency=str(data.get("currency") or "CNY"),
        billable_unit=str(data.get("billable_unit") or "交付题"),
        updated_at=str(data.get("updated_at") or "") or None,
    )


def update_earnings_price_config(payload: EarningsPriceConfigUpdate) -> EarningsPriceConfigRead:
    config = EarningsPriceConfigRead(
        unit_price=max(0, float(payload.unit_price or 0)),
        currency=(payload.currency or "CNY").strip() or "CNY",
        billable_unit=(payload.billable_unit or "交付题").strip() or "交付题",
        updated_at=utc_now().isoformat(),
    )
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config


def build_earnings_summary(db: Session, export_path: Optional[str] = None) -> EarningsSummary:
    items = list_earnings(db)
    price_config = read_earnings_price_config()
    task_income_items = _build_task_income_items(db, price_config)
    from app.services.earnings_ledger_service import build_earnings_ledger_summary

    ledger = build_earnings_ledger_summary()
    return EarningsSummary(
        items=[EarningsSnapshotRead.model_validate(item) for item in items],
        today_income_total=sum(float(item.today_income) for item in items),
        hourly_income_total=sum(float(item.hourly_income) for item in items),
        price_config=price_config,
        task_income_items=task_income_items,
        estimated_task_income_total=round(sum(item.estimated_income for item in task_income_items), 2),
        ledger_items=ledger.tasks,
        ledger_total_amount=ledger.total_amount,
        export_path=export_path,
    )


def export_earnings_excel(db: Session) -> Path:
    settings = get_settings()
    rows = list_earnings(db)
    summary = build_earnings_summary(db)
    export_root = Path(settings.backup_local_root) / "exports"
    export_root.mkdir(parents=True, exist_ok=True)
    path = export_root / f"earnings-{uuid4().hex[:8]}.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "收益总览"
    sheet.append(["账号", "来源", "收入项1", "值1", "收入项2", "值2", "收入项3", "值3", "今日收益", "小时收益", "采集时间"])
    for row in rows:
        sheet.append([
            row.account_user_id,
            row.source_label,
            row.income_1_name,
            float(row.income_1_value),
            row.income_2_name,
            float(row.income_2_value),
            row.income_3_name,
            float(row.income_3_value),
            float(row.today_income),
            float(row.hourly_income),
            row.captured_at.isoformat() if row.captured_at else "",
        ])
    estimate_sheet = workbook.create_sheet("做题收入估算")
    estimate_sheet.append(["账号", "显示名", "交付题量", "单题价格", "预估做题收入", "币种", "计费单位"])
    for item in summary.task_income_items:
        estimate_sheet.append([item.account_user_id, item.display_name, item.delivered_total, item.unit_price, item.estimated_income, summary.price_config.currency, summary.price_config.billable_unit])
    workbook.save(path)
    return path


def _build_task_income_items(db: Session, price_config: EarningsPriceConfigRead) -> list[EarningsTaskIncomeItem]:
    accounts = _production_accounts(db)
    account_ids = {account.user_id for account in accounts}
    delivered_by_account: dict[str, int] = {account.user_id: 0 for account in accounts}
    rows = list(db.scalars(select(TaskCatalogItem)))
    for row in rows:
        if str(row.source_account_user_id) not in account_ids:
            continue
        if row.visibility == TaskVisibility.HIDDEN:
            continue
        if _is_delivered(row):
            delivered_by_account[row.source_account_user_id] = delivered_by_account.get(row.source_account_user_id, 0) + 1
    return [
        EarningsTaskIncomeItem(
            account_user_id=account.user_id,
            display_name=account.display_name,
            delivered_total=delivered_by_account.get(account.user_id, 0),
            unit_price=price_config.unit_price,
            estimated_income=round(delivered_by_account.get(account.user_id, 0) * price_config.unit_price, 2),
        )
        for account in accounts
    ]


def _is_delivered(row: TaskCatalogItem) -> bool:
    status = str(row.task_status_raw or "")
    color = row.task_status_color.value if hasattr(row.task_status_color, "value") else str(row.task_status_color or "")
    return "交付" in status or color == "green"


def _production_accounts(db: Session) -> list[AidpAccount]:
    return [
        account
        for account in db.scalars(select(AidpAccount).order_by(AidpAccount.user_id.asc()))
        if _is_real_user_id(account.user_id)
        and account.status != AccountStatus.DISABLED
        and account.auth_mode != "local-profile-pending"
    ]


def _is_real_user_id(value: str) -> bool:
    return value.isdigit() and 12 <= len(value) <= 24


def _config_path() -> Path:
    value = get_settings().earnings_config_path
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


def _load_json(path: Path) -> Any:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
