from typing import Any, Optional

from sqlalchemy.orm import Session

from app.schemas.auto_production import AutoProductionStatusResponse, StartAutoProductionRequest
from app.schemas.task_auto_runs import TaskAutoRunResponse, TaskAutoRunStartRequest
from app.services.execution_device_service import list_execution_device_reads, selected_worker_ids_for_production
from app.services.runtime_account_service import load_runtime_accounts
from app.services.task_ability_service import get_enabled_task_ability_draft, load_latest_replay_summary
from app.services.task_auto_run_service import start_task_auto_run, update_task_auto_run_raw_config


class AutoProductionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_auto_production_status(db: Session, task_id: str) -> AutoProductionStatusResponse:
    accounts = load_runtime_accounts()
    devices = list_execution_device_reads(db, usable_for_production=True)
    gate = _production_gate(task_id)
    return AutoProductionStatusResponse(
        production_allowed=gate["allowed"],
        ability_version=gate["ability_version"],
        prompt_version=gate["prompt_version"],
        selected_learning_package_id=gate["selected_learning_package_id"],
        missing_requirements=gate["missing_requirements"],
        available_account_count=len(accounts),
        available_device_count=len(devices),
        message="生产控制入口已就绪。" if gate["allowed"] else "生产门禁未通过。",
    )


def start_auto_production(
    db: Session,
    task_id: str,
    payload: StartAutoProductionRequest,
    *,
    adapters: Optional[list[Any]] = None,
    state_dir: Any = None,
) -> TaskAutoRunResponse:
    max_items_total = payload.limits.max_items_total
    if max_items_total is not None and max_items_total <= 0:
        raise AutoProductionError("INVALID_MAX_ITEMS_TOTAL", "本次最多处理必须为正整数或无限。")
    gate = _production_gate(task_id)
    if not gate["allowed"] and not _uses_fake_test_adapter(adapters):
        raise AutoProductionError("PRODUCTION_GATE_BLOCKED", "生产门禁未通过：" + "；".join(gate["missing_requirements"]))

    account_user_ids = _selected_account_user_ids(payload)
    if not account_user_ids:
        raise AutoProductionError("NO_AVAILABLE_ACCOUNT", "没有可用做题账号，不能启动生产。")

    try:
        selected_worker_ids = selected_worker_ids_for_production(
            db,
            execution_mode=payload.execution_mode,
            device_mode=payload.device_scope.mode,
            worker_ids=payload.device_scope.worker_ids,
        )
    except ValueError as exc:
        raise AutoProductionError("NO_AVAILABLE_DEVICE", str(exc)) from exc

    run_config = _run_config(payload, selected_worker_ids)
    run = start_task_auto_run(
        db,
        TaskAutoRunStartRequest(
            task_id=task_id,
            account_user_ids=account_user_ids,
            ability_version=gate["ability_version"],
            run_config=run_config,
        ),
        adapters=adapters,
        state_dir=state_dir,
    )
    return update_task_auto_run_raw_config(run, run_config, state_dir=state_dir)


def _selected_account_user_ids(payload: StartAutoProductionRequest) -> list[str]:
    if payload.account_scope.mode == "specified":
        return _dedupe(payload.account_scope.account_user_ids)
    return _dedupe(list(load_runtime_accounts().keys()))


def _run_config(payload: StartAutoProductionRequest, selected_worker_ids: list[str]) -> dict[str, Any]:
    return {
        "account_scope": payload.account_scope.model_dump(mode="json"),
        "question_scope": payload.question_scope.model_dump(mode="json"),
        "execution_mode": payload.execution_mode,
        "device_scope": payload.device_scope.model_dump(mode="json"),
        "limits": payload.limits.model_dump(mode="json"),
        "selected_worker_ids": selected_worker_ids,
    }


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return result


def _production_gate(task_id: str) -> dict[str, Any]:
    missing: list[str] = []
    if not str(task_id or "").strip():
        missing.append("任务ID为空")
        return _gate_payload(False, "", "", "", missing)

    draft = get_enabled_task_ability_draft(task_id)
    if not draft:
        missing.append("Step4 未通过或能力版本未启用")
        return _gate_payload(False, "", "", "", missing)

    latest_replay = load_latest_replay_summary(task_id, draft)
    prompt_version = str(latest_replay.get("prompt_version") or "")
    selected_learning_package_id = str(latest_replay.get("selected_learning_package_id") or "")
    if not prompt_version:
        missing.append("Prompt 版本不存在")
    if not selected_learning_package_id:
        missing.append("selected_learning_package_id 不存在")
    return _gate_payload(
        not missing,
        str(draft.get("version") or ""),
        prompt_version,
        selected_learning_package_id,
        missing,
    )


def _gate_payload(allowed: bool, ability_version: str, prompt_version: str, selected_learning_package_id: str, missing: list[str]) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "ability_version": ability_version,
        "prompt_version": prompt_version,
        "selected_learning_package_id": selected_learning_package_id,
        "missing_requirements": missing,
    }


def _uses_fake_test_adapter(adapters: Optional[list[Any]]) -> bool:
    return any(str(getattr(adapter, "adapter_key", "")) == "fake_task" for adapter in list(adapters or []))
