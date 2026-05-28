from typing import Literal, Optional

from pydantic import BaseModel, Field


class AutoProductionAccountScope(BaseModel):
    mode: Literal["all_available", "specified"] = "all_available"
    account_user_ids: list[str] = Field(default_factory=list)


class AutoProductionQuestionScope(BaseModel):
    mode: Literal["pending", "repair", "pending_repair"] = "pending"


class AutoProductionDeviceScope(BaseModel):
    mode: Literal["auto", "specified"] = "auto"
    worker_ids: list[str] = Field(default_factory=list)


class AutoProductionLimits(BaseModel):
    max_items_total: Optional[int] = None
    failure_threshold: int = Field(default=3, ge=1)


class StartAutoProductionRequest(BaseModel):
    account_scope: AutoProductionAccountScope = Field(default_factory=AutoProductionAccountScope)
    question_scope: AutoProductionQuestionScope = Field(default_factory=AutoProductionQuestionScope)
    execution_mode: Literal["platform", "platform_plus_devices", "devices"] = "platform_plus_devices"
    device_scope: AutoProductionDeviceScope = Field(default_factory=AutoProductionDeviceScope)
    limits: AutoProductionLimits = Field(default_factory=AutoProductionLimits)


class AutoProductionStatusResponse(BaseModel):
    production_allowed: bool
    ability_version: str = ""
    prompt_version: str = ""
    selected_learning_package_id: str = ""
    missing_requirements: list[str] = Field(default_factory=list)
    current_run: Optional[dict] = None
    available_account_count: int = 0
    available_device_count: int = 0
    message: str = ""
