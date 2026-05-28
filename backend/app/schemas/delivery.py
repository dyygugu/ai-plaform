from datetime import datetime

from pydantic import BaseModel, Field


class DeliveryArtifact(BaseModel):
    key: str
    title: str
    path: str
    exists: bool
    size_bytes: int = 0
    updated_at: str = ""


class DeliveryChecklistItem(BaseModel):
    key: str
    title: str
    status: str
    description: str
    evidence_path: str = ""


class DeliveryChecklistResponse(BaseModel):
    generated_at: datetime
    base_url: str
    production_domain: str
    manual_domain_switch_required: bool
    items: list[DeliveryChecklistItem]
    risk_notes: list[str]
    rollback_notes: list[str]


class DeliverySummaryResponse(BaseModel):
    generated_at: datetime
    status: str
    base_url: str
    production_domain: str
    manual_domain_switch_required: bool
    latest_report: DeliveryArtifact
    screenshots: list[DeliveryArtifact]
    todo_unchecked_count: int
    api_groups: list[str]
    checklist: DeliveryChecklistResponse
    message: str


class DeliveryBundleResponse(BaseModel):
    generated_at: datetime
    status: str
    bundle_path: str
    bundle_markdown: str
    artifacts: list[DeliveryArtifact] = Field(default_factory=list)
    message: str
