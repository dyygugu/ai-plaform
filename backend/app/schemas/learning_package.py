from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LearningPackageCandidate(BaseModel):
    value: str
    source: str = ""
    confidence: str = ""


class LearningPackageItem(BaseModel):
    learning_package_id: str
    recording_id: str
    task_id: str
    display_name: str
    source: str
    uploaded_at: datetime
    status: str
    completeness: str
    selected: bool = False


class TaskLearningPackageListResponse(BaseModel):
    task_id: str
    selected_learning_package_id: str = ""
    items: list[LearningPackageItem] = Field(default_factory=list)


class SelectLearningPackageRequest(BaseModel):
    selected_learning_package_id: str = ""
    learning_package_id: str = ""
    recording_id: str = ""


class SelectLearningPackageResponse(BaseModel):
    task_id: str
    selected_learning_package_id: str
    message: str


class LearningPackageSummary(BaseModel):
    learning_package_id: str = ""
    source: str = ""
    status: str = ""
    completeness: str = ""
    uploaded_at: str = ""
    detected_actions: list[str] = Field(default_factory=list)
    page_url: str = ""
    task_id_candidates: list[LearningPackageCandidate] = Field(default_factory=list)
    summary_text: str = ""
