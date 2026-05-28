from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


OPERATION_RECORDING_MODES = ["full", "safe", "network-only"]


class OperationRecordingRequest(BaseModel):
    recording_id: str = ""
    mode: str = "safe"
    source: str = "aidp-score-helper-extension"
    account_user_id: str = ""
    task_id: str = ""
    task_id_candidates: list[dict[str, Any]] = Field(default_factory=list)
    page_url: str = ""
    recorded_at: str = ""
    started_at: str = ""
    ended_at: str = ""
    detected_actions: list[str] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    network: list[dict[str, Any]] = Field(default_factory=list)
    dom_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    screenshots: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_collections(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        for key in ("events", "network", "dom_snapshots", "screenshots", "detected_actions"):
            value = normalized.get(key)
            if value is None:
                normalized[key] = []
            elif not isinstance(value, list):
                normalized[key] = [value]
        candidates = normalized.get("task_id_candidates")
        if candidates is None:
            normalized["task_id_candidates"] = []
        elif not isinstance(candidates, list):
            normalized["task_id_candidates"] = [candidates]
        return normalized

    @model_validator(mode="after")
    def validate_mode(self) -> "OperationRecordingRequest":
        if self.mode not in OPERATION_RECORDING_MODES:
            raise ValueError(f"mode 必须是 {', '.join(OPERATION_RECORDING_MODES)}")
        return self


class OperationRecordingResponse(BaseModel):
    ok: bool
    recording_id: str
    mode: str
    artifact_path: str
    event_count: int
    network_count: int
    screenshot_count: int
    received_at: datetime
    operation_claim_analysis: dict[str, Any] = Field(default_factory=dict)
    task_id: str = ""
    learning_package: dict[str, Any] = Field(default_factory=dict)
    message: str
