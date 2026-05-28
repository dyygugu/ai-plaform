from datetime import datetime

from typing import Optional

from pydantic import BaseModel, Field


class EarningsSnapshotRead(BaseModel):
    id: int
    account_user_id: str
    source_label: str
    income_1_name: str
    income_1_value: float
    income_2_name: str
    income_2_value: float
    income_3_name: str
    income_3_value: float
    today_income: float
    hourly_income: float
    captured_at: datetime

    model_config = {"from_attributes": True}


class EarningsSummary(BaseModel):
    items: list[EarningsSnapshotRead]
    today_income_total: float
    hourly_income_total: float
    price_config: "EarningsPriceConfigRead" = Field(default_factory=lambda: EarningsPriceConfigRead())
    task_income_items: list["EarningsTaskIncomeItem"] = Field(default_factory=list)
    estimated_task_income_total: float = 0
    ledger_items: list["EarningsLedgerTaskItem"] = Field(default_factory=list)
    ledger_total_amount: float = 0
    export_path: Optional[str] = None


class EarningsPriceConfigUpdate(BaseModel):
    unit_price: float = Field(default=0, ge=0)
    currency: str = "CNY"
    billable_unit: str = "交付题"


class EarningsPriceConfigRead(EarningsPriceConfigUpdate):
    updated_at: Optional[str] = None


class EarningsTaskIncomeItem(BaseModel):
    account_user_id: str
    display_name: str = ""
    delivered_total: int = 0
    unit_price: float = 0
    estimated_income: float = 0


class EarningsLedgerAccountItem(BaseModel):
    account_run_id: str
    account_user_id: str
    display_name: str = ""
    completed_count: int = 0
    amount: float = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    status: str = "closed"


class EarningsLedgerRunItem(BaseModel):
    run_id: str
    task_id: str
    task_name: str = ""
    completed_count: int = 0
    amount: float = 0
    unit_price: float = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    status: str = "closed"
    accounts: list[EarningsLedgerAccountItem] = Field(default_factory=list)


class EarningsLedgerTaskItem(BaseModel):
    task_id: str
    task_name: str = ""
    completed_count: int = 0
    amount: float = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    runs: list[EarningsLedgerRunItem] = Field(default_factory=list)


class EarningsLedgerPriceUpdate(BaseModel):
    unit_price: float = Field(default=0, ge=0)
