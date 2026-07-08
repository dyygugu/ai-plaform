from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.schemas.learning_package import SelectLearningPackageRequest
from app.schemas.task_auto_runs import TaskAutoRunPreflightResponse, TaskAutoRunResponse, TaskAutoRunStartRequest
from app.schemas.task_ability import TaskAbilityDraftCreateRequest, TaskAbilityDraftListResponse, TaskAbilityDraftRead
from app.services.task_auto_run_service import check_task_auto_run_preflight, default_task_auto_run_adapters, get_task_auto_run, start_task_auto_run, tick_task_auto_run
from app.services.task_auto_run_worker_service import GenericTaskAutoRunWorkerRegistry
from app.services.task_ability_service import (
    approve_task_ability_version as approve_task_ability_version_by_task,
    build_task_ability_payload_debug,
    chat_task_ability,
    create_task_ability_replay_report,
    create_prompt_snapshot,
    get_latest_task_ability_live_http_test_report,
    get_task_ability_draft_by_task,
    get_task_ability_live_http_test_report,
    get_task_ability_replay_report,
    get_task_ability_run_config,
    get_task_ability_run_gate,
    list_prompt_snapshots,
    replay_task_ability_testset,
    record_task_ability_run,
    TaskAbilityFlowError,
    approve_task_ability_draft,
    approve_task_ability_real_no_submit,
    create_task_ability_draft,
    is_task_ability_3d_rubric_draft,
    list_task_ability_drafts,
    restore_prompt_snapshot,
    run_task_ability_live_http_test,
    run_task_ability_dry_run,
    run_task_ability_real_no_submit,
    update_task_ability_prompt_by_task,
    update_task_ability_run_config,
    update_task_ability_draft,
)
from app.services.learning_package_service import list_task_learning_packages, save_selected_learning_package

router = APIRouter(prefix="/task-abilities", tags=["task-abilities"])


class TaskAbilityRealNoSubmitRequest(BaseModel):
    account_user_id: str = ""
    use_system_ai_for_vision: bool = False


class TaskAbilityDraftUpdateRequest(BaseModel):
    task_name: Optional[str] = None
    task_id: Optional[str] = None
    task_type: Optional[str] = None
    ability_source: Optional[str] = None
    source_config: Optional[dict] = Field(default=None)
    field_mapping: Optional[dict] = Field(default=None)
    validation_rules: Optional[dict] = Field(default=None)
    specific_rules: Optional[str] = None
    sample_data: Optional[str] = None
    related_content: Optional[str] = None
    system_ai_draft: Optional[str] = None
    system_ai_trace_id: Optional[str] = None
    provider_status: Optional[str] = None


class TaskAbilityPromptSnapshotRequest(BaseModel):
    note: str = ""


class TaskAbilityPromptRestoreRequest(BaseModel):
    snapshot_id: str


class TaskAbilityRunRequest(BaseModel):
    account_user_ids: list[str] = []
    node_id: str = "1"
    run_config: dict[str, object] = {}


class TaskAbilityChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []
    use_provider: bool = True
    selected_learning_package_id: str = ""
    learning_package_id: str = ""
    recording_id: str = ""
    selected_recording_id: str = ""


class TaskAbilityPayloadPreviewRequest(BaseModel):
    uid: str


class TaskAbilityReplayRequest(BaseModel):
    testset_id: str = ""
    prompt_version_id: str = ""
    prompt_content: str = ""
    sample_limit: int = 10
    view: str = "compact_cards"
    include_debug: bool = False


@router.get("/drafts", response_model=TaskAbilityDraftListResponse)
def read_task_ability_drafts() -> TaskAbilityDraftListResponse:
    return list_task_ability_drafts()


@router.post("/drafts", response_model=TaskAbilityDraftRead)
def create_draft(payload: TaskAbilityDraftCreateRequest) -> TaskAbilityDraftRead:
    return create_task_ability_draft(payload)


@router.post("/drafts/{draft_id}/dry-run")
def run_draft_dry_run(draft_id: str) -> dict:
    try:
        return run_task_ability_dry_run(draft_id)
    except TaskAbilityFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/approve-draft")
def approve_draft(draft_id: str) -> dict:
    try:
        return approve_task_ability_draft(draft_id)
    except TaskAbilityFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/real-no-submit")
def run_real_no_submit(draft_id: str, payload: TaskAbilityRealNoSubmitRequest = TaskAbilityRealNoSubmitRequest(), db: Session = Depends(get_db)) -> dict:
    try:
        is_3d_preview_only = is_task_ability_3d_rubric_draft(draft_id)
        return run_task_ability_real_no_submit(
            draft_id,
            db=db,
            allow_temp_save=not is_3d_preview_only,
            target_account_user_id=payload.account_user_id,
            use_system_ai_for_vision=payload.use_system_ai_for_vision,
        )
    except TaskAbilityFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/approve-real-no-submit")
def approve_real_no_submit(draft_id: str) -> dict:
    try:
        return approve_task_ability_real_no_submit(draft_id)
    except TaskAbilityFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/drafts/{draft_id}")
def update_draft(draft_id: str, payload: TaskAbilityDraftUpdateRequest) -> dict:
    try:
        updates = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
        return update_task_ability_draft(draft_id, updates)
    except TaskAbilityFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/prompt/snapshot")
def create_task_prompt_snapshot(task_id: str, payload: TaskAbilityPromptSnapshotRequest = TaskAbilityPromptSnapshotRequest()) -> dict:
    try:
        return create_prompt_snapshot(task_id, note=payload.note)
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}/prompt/snapshots")
def read_task_prompt_snapshots(task_id: str) -> dict:
    items = list_prompt_snapshots(task_id)
    return {"task_id": str(task_id), "total": len(items), "items": items}


@router.get("/{task_id}")
def read_task_ability_draft_by_task(task_id: str) -> dict:
    try:
        return get_task_ability_draft_by_task(task_id)
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{task_id}/learning-packages")
def read_task_learning_packages(task_id: str) -> dict:
    return list_task_learning_packages(task_id).model_dump(mode="json")


@router.post("/{task_id}/selected-learning-package")
def save_task_learning_package_selection(task_id: str, payload: SelectLearningPackageRequest) -> dict:
    package_id = str(payload.selected_learning_package_id or payload.learning_package_id or payload.recording_id or "").strip()
    if not package_id:
        raise HTTPException(status_code=400, detail="selected_learning_package_id / learning_package_id 不能为空。")
    try:
        return save_selected_learning_package(task_id, package_id).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/chat")
def create_task_ability_chat(task_id: str, payload: TaskAbilityChatRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return chat_task_ability(db, task_id, payload.model_dump())
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/prompt/restore")
def restore_task_prompt_snapshot(task_id: str, payload: TaskAbilityPromptRestoreRequest) -> dict:
    try:
        restored = restore_prompt_snapshot(task_id, payload.snapshot_id)
        return {
            "ok": True,
            "task_id": str(task_id),
            "draft_id": str(restored.get("id") or ""),
            "status": str(restored.get("status") or ""),
            "flow_stage": str(restored.get("flow_stage") or ""),
            "message": "已恢复 Prompt 快照，并重置为待重新验证。",
        }
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{task_id}/prompt")
def update_task_prompt(task_id: str, payload: TaskAbilityDraftUpdateRequest) -> dict:
    try:
        updates = {key: value for key, value in payload.model_dump().items() if value is not None}
        return update_task_ability_prompt_by_task(task_id, updates)
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}/live-http-test/latest")
def read_latest_task_live_http_test(task_id: str) -> dict:
    try:
        return get_latest_task_ability_live_http_test_report(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/live-http-test")
def create_task_live_http_test(task_id: str, payload: TaskAbilityRealNoSubmitRequest = TaskAbilityRealNoSubmitRequest(), db: Session = Depends(get_db)) -> dict:
    try:
        return run_task_ability_live_http_test(
            task_id,
            db=db,
            account_user_id=payload.account_user_id,
            use_system_ai_for_vision=payload.use_system_ai_for_vision,
        )
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}/live-http-test/{report_id}")
def read_task_live_http_test(task_id: str, report_id: str) -> dict:
    try:
        return get_task_ability_live_http_test_report(task_id, report_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{task_id}/run-gate")
def read_task_run_gate(task_id: str) -> dict:
    return get_task_ability_run_gate(task_id)


@router.get("/{task_id}/run-config")
def read_task_run_config(task_id: str) -> dict:
    return {"task_id": str(task_id), "run_config": get_task_ability_run_config(task_id)}


@router.put("/{task_id}/run-config")
def save_task_run_config(task_id: str, payload: dict) -> dict:
    return {"task_id": str(task_id), "run_config": update_task_ability_run_config(task_id, payload), "message": "运行配置已保存。"}


@router.get("/{task_id}/replay")
def read_task_ability_replay(task_id: str) -> dict:
    try:
        return replay_task_ability_testset(task_id)
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/replay")
def create_task_replay_report(task_id: str, payload: TaskAbilityReplayRequest = TaskAbilityReplayRequest()) -> dict:
    try:
        return create_task_ability_replay_report(task_id, prompt_content=payload.prompt_content, sample_limit=payload.sample_limit)
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}/replay/{report_id}")
def read_task_replay_report(task_id: str, report_id: str) -> dict:
    try:
        return get_task_ability_replay_report(task_id, report_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{task_id}/payload-preview/{uid}")
def read_task_ability_payload_preview(task_id: str, uid: str) -> dict:
    try:
        return build_task_ability_payload_debug(task_id, uid)
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/payload/preview")
def create_task_payload_preview(task_id: str, payload: TaskAbilityPayloadPreviewRequest) -> dict:
    try:
        return build_task_ability_payload_debug(task_id, payload.uid)
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/approve")
def approve_task_ability_version(task_id: str) -> dict:
    try:
        result = approve_task_ability_version_by_task(task_id)
        return {**result, "task_id": str(task_id)}
    except (TaskAbilityFlowError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/trial-run", response_model=TaskAutoRunResponse)
def start_task_trial_run(task_id: str, payload: TaskAbilityRunRequest, request: Request, db: Session = Depends(get_db)) -> TaskAutoRunResponse:
    try:
        gate = get_task_ability_run_gate(task_id)
        if not gate.get("can_start_trial"):
            raise TaskAbilityFlowError(str(gate.get("next_step") or "请先通过 Step3 审核并启用做题能力，再启动试运行。"))
        run_config = update_task_ability_run_config(task_id, dict(payload.run_config or {}))
        run_config = {**run_config, "ability_run_mode": "trial"}
        run_request = TaskAutoRunStartRequest(task_id=str(task_id), node_id=payload.node_id, account_user_ids=payload.account_user_ids, run_config=run_config)
        run = start_task_auto_run(db, run_request, adapters=getattr(request.app.state, "task_auto_run_adapters", default_task_auto_run_adapters()), state_dir=getattr(request.app.state, "task_auto_run_state_dir", None))
        ticked = tick_task_auto_run(
            _run_id(run),
            adapters=getattr(request.app.state, "task_auto_run_adapters", default_task_auto_run_adapters()),
            state_dir=getattr(request.app.state, "task_auto_run_state_dir", None),
            db=db,
        )
        db.commit()
        if _run_status(ticked) == "blocked":
            raise ValueError(_run_last_error(ticked) or "试运行首轮被执行器阻断。")
        record_task_ability_run(task_id, "trial", ticked)
        return ticked
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TaskAbilityFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _run_id(run: object) -> str:
    if isinstance(run, dict):
        return str(run.get("run_id") or "")
    return str(getattr(run, "run_id", "") or "")


@router.post("/{task_id}/production-run", response_model=TaskAutoRunResponse)
async def start_task_production_run(task_id: str, payload: TaskAbilityRunRequest, request: Request, db: Session = Depends(get_db)) -> TaskAutoRunResponse:
    try:
        gate = get_task_ability_run_gate(task_id)
        if not gate.get("can_start_production"):
            raise TaskAbilityFlowError(str(gate.get("next_step") or "请先完成试运行，再人工确认后启动生产运行。"))
        run_config = update_task_ability_run_config(task_id, dict(payload.run_config or {}))
        run_config = {**run_config, "ability_run_mode": "production"}
        run_request = TaskAutoRunStartRequest(task_id=str(task_id), node_id=payload.node_id, account_user_ids=payload.account_user_ids, run_config=run_config)
        run = start_task_auto_run(db, run_request, adapters=getattr(request.app.state, "task_auto_run_adapters", default_task_auto_run_adapters()), state_dir=getattr(request.app.state, "task_auto_run_state_dir", None))
        ticked = await _start_step4_production_worker(request, _run_id(run), run_config)
        record_task_ability_run(task_id, "production", ticked)
        return ticked
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TaskAbilityFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _start_step4_production_worker(request: Request, run_id: str, run_config: dict) -> TaskAutoRunResponse:
    registry = _generic_worker_registry(request)
    worker = registry.ensure(
        run_id,
        tick_func=lambda: _tick_task_auto_run_with_db(
            request,
            run_id,
        ),
        interval_seconds=_worker_interval_seconds(run_config),
    )
    first_tick = await worker.run_once()
    if not worker.status.last_ok:
        raise ValueError(worker.status.last_error or "生产运行首轮执行失败。")
    latest = first_tick if first_tick is not None else get_task_auto_run(
        run_id,
        adapters=getattr(request.app.state, "task_auto_run_adapters", default_task_auto_run_adapters()),
        state_dir=getattr(request.app.state, "task_auto_run_state_dir", None),
    )
    if _run_status(latest) == "blocked":
        raise ValueError(_run_last_error(latest) or "生产运行首轮被执行器阻断。")
    if _run_status(latest) in {"stopped", "completed", "completed_no_item", "failed", "executor_pending"}:
        return latest
    worker.start()
    return latest


def _tick_task_auto_run_with_db(request: Request, run_id: str) -> TaskAutoRunResponse:
    db = SessionLocal()
    try:
        result = tick_task_auto_run(
            run_id,
            adapters=getattr(request.app.state, "task_auto_run_adapters", default_task_auto_run_adapters()),
            state_dir=getattr(request.app.state, "task_auto_run_state_dir", None),
            db=db,
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _generic_worker_registry(request: Request) -> GenericTaskAutoRunWorkerRegistry:
    registry = getattr(request.app.state, "generic_task_auto_run_worker_registry", None)
    if registry is None:
        registry = GenericTaskAutoRunWorkerRegistry()
        request.app.state.generic_task_auto_run_worker_registry = registry
    return registry


def _worker_interval_seconds(run_config: dict) -> int:
    try:
        return max(1, int(run_config.get("worker_interval_seconds") or 5))
    except (TypeError, ValueError):
        return 5


def _run_status(run: object) -> str:
    if isinstance(run, dict):
        return str(run.get("status") or "").lower()
    return str(getattr(run, "status", "") or "").lower()


def _run_last_error(run: object) -> str:
    if isinstance(run, dict):
        return str(run.get("last_error") or "")
    return str(getattr(run, "last_error", "") or "")
