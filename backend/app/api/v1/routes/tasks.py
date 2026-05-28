import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.session import get_db
from app.models.task import TaskCatalogItem
from app.schemas.task import (
    TaskCatalogDetailResponse,
    TaskCatalogEventRead,
    TaskCatalogItemRead,
    TaskCatalogListResponse,
    TaskCatalogRefreshJobResponse,
    TaskCatalogRefreshJobStep,
    TaskCatalogRefreshRequest,
    TaskCatalogRefreshResponse,
    TaskCatalogRefreshStepRequest,
    TaskCatalogSeedRequest,
    TaskCatalogSeedResponse,
    TaskRuleConfigResponse,
    TaskRuleConfigUpdateRequest,
    TaskSampleCaptureRequest,
    TaskSampleCaptureResponse,
)
from app.schemas.task_capability import TaskAiDraftBuildRequest, TaskCapabilityCardResponse, TaskDraftBuildRequest, TaskDraftBuildResponse, TaskDraftReviewApprovalRequest, TaskDraftReviewApprovalResponse, TaskHttpQuestionContextResponse, TaskMediaInspectionDraftRequest, TaskMediaInspectionExecutionRequest, TaskMediaInspectionExecutionResponse, TaskMediaInspectionPlanRequest, TaskMediaInspectionPlanResponse, TaskMediaInspectionProviderRequest, TaskMediaInspectionProviderResponse, TaskOperationProcessPlanResponse, TaskProviderDraftRequest, TaskSandboxClickDraftRequest, TaskSandboxClickExecutionRequest, TaskSandboxClickExecutionResponse, TaskSandboxClickPlanRequest, TaskSandboxClickPlanResponse, TaskVideoKeyframeExtractionRequest, TaskVideoKeyframeExtractionResponse
from app.schemas.worker import WorkerEventReportRequest
from app.services.aidp_readonly_client import capture_search_task_readonly
from app.services.audit_service import write_audit
from app.services.task_sample_service import save_redacted_task_sample
from app.services.task_service import (
    get_task_detail,
    get_task_source_account_user_id,
    get_task_rule_config,
    list_task_catalog,
    mark_task_catalog_pending_unverified,
    read_manual_short_names,
    read_prefix_rules,
    seed_task_catalog_item,
    seed_tasks_from_sample_summary,
    update_task_rule_config,
)
from app.services.task_capability_service import TaskCapabilityError, approve_provider_draft_review, build_http_question_context, build_media_inspection_draft, build_media_inspection_execution, build_media_inspection_plan, build_media_inspection_provider, build_operation_process_plan, build_or_execute_ai_temp_draft, build_or_execute_provider_temp_draft, build_or_execute_temp_draft, build_sandbox_click_draft, build_sandbox_click_execution, build_sandbox_click_plan, build_task_capability_card, build_video_keyframe_extraction, summarize_task_capability
from app.services.worker_service import report_worker_event

router = APIRouter(prefix="/tasks", tags=["tasks"])
_REFRESH_JOBS: dict[str, dict[str, Any]] = {}
TASK_REFRESH_WORKER_ID = "task-refresh-api"


def _task_catalog_item_read(item: TaskCatalogItem) -> TaskCatalogItemRead:
    return TaskCatalogItemRead.model_validate(item).model_copy(update=summarize_task_capability(item.task_id))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _refresh_steps() -> list[dict[str, Any]]:
    return [
        {"key": "prepare", "title": "准备刷新上下文", "status": "pending", "message": "确认任务源账号与刷新模式。", "started_at": None, "finished_at": None},
        {"key": "refresh", "title": "采集并导入待处理", "status": "pending", "message": "读取 SearchTask 或最近脱敏摘要并写入任务目录。", "started_at": None, "finished_at": None},
        {"key": "complete", "title": "写入日志并完成", "status": "pending", "message": "输出刷新结果，供前端继续展示。", "started_at": None, "finished_at": None},
    ]


def _job_response(job: dict[str, Any]) -> TaskCatalogRefreshJobResponse:
    steps = [TaskCatalogRefreshJobStep(**step) for step in job["steps"]]
    current = next((index for index, step in enumerate(job["steps"]) if step["status"] in {"pending", "running"}), len(job["steps"]) - 1)
    result = job.get("result")
    return TaskCatalogRefreshJobResponse(
        job_id=job["job_id"],
        status=job["status"],
        source_account_user_id=job["source_account_user_id"],
        use_live_readonly=job["payload"].use_live_readonly,
        current_step_index=current,
        steps=steps,
        result=result,
        error=job.get("error"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        message=job["message"],
    )


def _run_task_catalog_refresh(payload: Optional[TaskCatalogRefreshRequest], db: Session) -> TaskCatalogRefreshResponse:
    settings = get_settings()
    request = payload or TaskCatalogRefreshRequest()
    started_at = _utc_now()
    source = request.source_account_user_id or get_task_source_account_user_id(db)
    sample_payload = request.sample_payload
    live_requested = bool(request.use_live_readonly)
    live_ok = False
    refresh_mode = "cached_summary"
    error = None
    pending_verified = False
    unverified_reason = "脱敏样本只作为任务目录参考；待处理数未经过真实刷新确认，已隐藏。"
    message = unverified_reason
    if live_requested:
        refresh_mode = "live_readonly"
        try:
            sample_payload = capture_search_task_readonly(source)
            live_ok = True
            pending_verified = True
            unverified_reason = ""
            message = "已通过主账号 Cookie 只读调用 SearchTask，保存脱敏样本并刷新任务目录。"
        except Exception as exc:  # noqa: BLE001 - API must preserve old data and return actionable warning.
            error = str(exc)
            sample_payload = None
            refresh_mode = "live_readonly_fallback"
            pending_verified = False
            unverified_reason = f"实时只读刷新失败，旧摘要待处理数已隐藏，避免把 1495/4450 当作当前真实剩余题量：{error}"
            message = unverified_reason
    if sample_payload is None:
        summary_path = settings.task_sample_root.rstrip("/\\") + "/task-page-latest-summary.json"
        try:
            sample_payload = json.loads(Path(summary_path).read_text(encoding="utf-8-sig"))
            if not live_requested:
                unverified_reason = "最近脱敏摘要只作为任务目录参考；待处理数未经过真实刷新确认，已隐藏。"
                message = unverified_reason
        except FileNotFoundError:
            sample_payload = {"tasks": []}
    sample_path = save_redacted_task_sample(sample_payload, source)
    items = seed_tasks_from_sample_summary(
        db,
        sample_payload if isinstance(sample_payload, dict) else {"tasks": []},
        source,
        pending_verified=pending_verified,
        unverified_reason=unverified_reason,
    )
    if not pending_verified:
        mark_task_catalog_pending_unverified(db, source, unverified_reason)
    audit_message = f"Refreshed task catalog from source {source}, mode {refresh_mode}, imported {len(items)} tasks"
    if error:
        audit_message += f", warning {error}"
    _record_task_refresh_worker_event(
        db,
        source=source,
        imported_count=len(items),
        refresh_mode=refresh_mode,
        error=error,
        duration_ms=int((_utc_now() - started_at).total_seconds() * 1000),
    )
    write_audit(db, event_type="task_catalog_refresh", message=audit_message, target_type="account", target_id=source)
    db.commit()
    return TaskCatalogRefreshResponse(
        source_account_user_id=source,
        sample_saved=True,
        redacted_sample_path=str(sample_path),
        imported_count=len(items),
        message=message,
        refresh_mode=refresh_mode,
        live_readonly_requested=live_requested,
        live_readonly_ok=live_ok,
        started_at=started_at,
        finished_at=_utc_now(),
        error=error,
    )


def _record_task_refresh_worker_event(
    db: Session,
    source: str,
    imported_count: int,
    refresh_mode: str,
    error: Optional[str],
    duration_ms: int,
) -> None:
    has_error = bool(error)
    report_worker_event(
        db,
        WorkerEventReportRequest(
            worker_id=TASK_REFRESH_WORKER_ID,
            event_type="event_report",
            account_user_id=source,
            task_id="task-catalog-refresh",
            severity="warning" if has_error else "info",
            stage="task_refresh",
            step="fetch_task_page" if has_error else "finish",
            error_code=_task_refresh_error_code(error) if has_error else "",
            error_detail=error or "",
            retryable=True if has_error else None,
            duration_ms=duration_ms,
            message=f"任务目录刷新 mode={refresh_mode}，导入 {imported_count} 个任务" + (f"，错误={error}" if has_error else ""),
        ),
    )


def _task_refresh_error_code(error: Optional[str]) -> str:
    text = (error or "").lower()
    if "401" in text or "403" in text or "auth" in text or "login" in text or "cookie" in text or "登录" in (error or ""):
        return "TASK_PAGE_AUTH_EXPIRED"
    if "timeout" in text or "timed out" in text or "超时" in (error or ""):
        return "TASK_PAGE_TIMEOUT"
    return "TASK_PARSE_FAILED"


@router.get("/catalog", response_model=TaskCatalogListResponse)
def read_task_catalog(source_account_user_id: Optional[str] = None, db: Session = Depends(get_db)) -> TaskCatalogListResponse:
    source = source_account_user_id or get_task_source_account_user_id(db)
    items = list_task_catalog(db, source)
    stale = any(item.last_task_page_error for item in items)
    last_error = next((item.last_task_page_error for item in items if item.last_task_page_error), None)
    return TaskCatalogListResponse(
        source_account_user_id=source,
        items=[_task_catalog_item_read(item) for item in items],
        stale=stale,
        last_error=last_error,
    )


@router.get("/catalog/{item_id}", response_model=TaskCatalogDetailResponse)
def read_task_detail(item_id: int, db: Session = Depends(get_db)) -> TaskCatalogDetailResponse:
    detail = get_task_detail(db, item_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Task catalog item not found")
    return TaskCatalogDetailResponse(
        item=_task_catalog_item_read(detail["item"]),
        source_account_user_id=detail["source_account_user_id"],
        covered_account_count=detail["covered_account_count"],
        latest_failure=detail["latest_failure"],
        status_history=[TaskCatalogEventRead.model_validate(event) for event in detail["status_history"]],
        pending_history=[TaskCatalogEventRead.model_validate(event) for event in detail["pending_history"]],
        timeline=[TaskCatalogEventRead.model_validate(event) for event in detail["timeline"]],
    )


@router.get("/catalog/{item_id}/capability", response_model=TaskCapabilityCardResponse)
def read_task_capability(item_id: int, db: Session = Depends(get_db)) -> TaskCapabilityCardResponse:
    try:
        return build_task_capability_card(db, item_id)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.get("/catalog/{item_id}/capability/http-question-context", response_model=TaskHttpQuestionContextResponse)
def read_task_http_question_context(
    item_id: int,
    prefer_live: bool = False,
    allow_remote_fetch: bool = False,
    account_user_id: str = "",
    db: Session = Depends(get_db),
) -> TaskHttpQuestionContextResponse:
    try:
        return build_http_question_context(db, item_id, prefer_live=prefer_live, allow_remote_fetch=allow_remote_fetch, account_user_id=account_user_id)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.get("/catalog/{item_id}/capability/operation-process-plan", response_model=TaskOperationProcessPlanResponse)
def read_task_operation_process_plan(item_id: int, db: Session = Depends(get_db)) -> TaskOperationProcessPlanResponse:
    try:
        return build_operation_process_plan(db, item_id)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/sandbox-click-plan", response_model=TaskSandboxClickPlanResponse)
def create_task_sandbox_click_plan(item_id: int, payload: TaskSandboxClickPlanRequest, db: Session = Depends(get_db)) -> TaskSandboxClickPlanResponse:
    try:
        return build_sandbox_click_plan(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/sandbox-click-execution", response_model=TaskSandboxClickExecutionResponse)
def create_task_sandbox_click_execution(item_id: int, payload: TaskSandboxClickExecutionRequest, db: Session = Depends(get_db)) -> TaskSandboxClickExecutionResponse:
    try:
        return build_sandbox_click_execution(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/sandbox-click-draft", response_model=TaskDraftBuildResponse)
def create_task_sandbox_click_draft(item_id: int, payload: TaskSandboxClickDraftRequest, db: Session = Depends(get_db)) -> TaskDraftBuildResponse:
    try:
        return build_sandbox_click_draft(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/media-inspection-plan", response_model=TaskMediaInspectionPlanResponse)
def create_task_media_inspection_plan(item_id: int, payload: TaskMediaInspectionPlanRequest, db: Session = Depends(get_db)) -> TaskMediaInspectionPlanResponse:
    try:
        return build_media_inspection_plan(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/media-inspection-execution", response_model=TaskMediaInspectionExecutionResponse)
def create_task_media_inspection_execution(item_id: int, payload: TaskMediaInspectionExecutionRequest, db: Session = Depends(get_db)) -> TaskMediaInspectionExecutionResponse:
    try:
        return build_media_inspection_execution(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/media-keyframe-extraction", response_model=TaskVideoKeyframeExtractionResponse)
def create_task_media_keyframe_extraction(item_id: int, payload: TaskVideoKeyframeExtractionRequest, db: Session = Depends(get_db)) -> TaskVideoKeyframeExtractionResponse:
    try:
        return build_video_keyframe_extraction(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/media-inspection-draft", response_model=TaskDraftBuildResponse)
def create_task_media_inspection_draft(item_id: int, payload: TaskMediaInspectionDraftRequest, db: Session = Depends(get_db)) -> TaskDraftBuildResponse:
    try:
        return build_media_inspection_draft(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/media-inspection-provider", response_model=TaskMediaInspectionProviderResponse)
def create_task_media_inspection_provider(item_id: int, payload: TaskMediaInspectionProviderRequest, db: Session = Depends(get_db)) -> TaskMediaInspectionProviderResponse:
    try:
        return build_media_inspection_provider(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/draft", response_model=TaskDraftBuildResponse)
def create_task_capability_draft(item_id: int, payload: TaskDraftBuildRequest, db: Session = Depends(get_db)) -> TaskDraftBuildResponse:
    try:
        return build_or_execute_temp_draft(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/ai-draft", response_model=TaskDraftBuildResponse)
def create_task_capability_ai_draft(item_id: int, payload: TaskAiDraftBuildRequest, db: Session = Depends(get_db)) -> TaskDraftBuildResponse:
    try:
        return build_or_execute_ai_temp_draft(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/provider-draft", response_model=TaskDraftBuildResponse)
def create_task_capability_provider_draft(item_id: int, payload: TaskProviderDraftRequest, db: Session = Depends(get_db)) -> TaskDraftBuildResponse:
    try:
        return build_or_execute_provider_temp_draft(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/{item_id}/capability/review-approval", response_model=TaskDraftReviewApprovalResponse)
def approve_task_capability_review(item_id: int, payload: TaskDraftReviewApprovalRequest, db: Session = Depends(get_db)) -> TaskDraftReviewApprovalResponse:
    try:
        return approve_provider_draft_review(db, item_id, payload)
    except TaskCapabilityError as exc:
        status = 404 if "missing-task" in exc.blockers or "missing-recording" in exc.blockers else 400
        raise HTTPException(status_code=status, detail={"message": str(exc), "blockers": exc.blockers}) from exc


@router.post("/catalog/seed", response_model=TaskCatalogSeedResponse)
def seed_task_catalog(payload: TaskCatalogSeedRequest, db: Session = Depends(get_db)) -> TaskCatalogSeedResponse:
    item, created = seed_task_catalog_item(db, payload)
    write_audit(db, event_type="task_catalog_seed", message=f"Seeded task catalog item {item.task_name_id}", target_type="task", target_id=item.task_id)
    db.commit()
    db.refresh(item)
    return TaskCatalogSeedResponse(item=_task_catalog_item_read(item), created=created)


@router.post("/catalog/refresh", response_model=TaskCatalogRefreshResponse)
def refresh_task_catalog(payload: Optional[TaskCatalogRefreshRequest] = None, db: Session = Depends(get_db)) -> TaskCatalogRefreshResponse:
    return _run_task_catalog_refresh(payload, db)


@router.post("/catalog/refresh-job", response_model=TaskCatalogRefreshJobResponse)
def create_task_refresh_job(payload: Optional[TaskCatalogRefreshRequest] = None, db: Session = Depends(get_db)) -> TaskCatalogRefreshJobResponse:
    request = payload or TaskCatalogRefreshRequest()
    source = request.source_account_user_id or get_task_source_account_user_id(db)
    now = _utc_now()
    job = {
        "job_id": uuid4().hex,
        "status": "pending",
        "source_account_user_id": source,
        "payload": request,
        "steps": _refresh_steps(),
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "message": "刷新 job 已创建，前端可逐步执行，避免长请求阻塞页面。",
    }
    _REFRESH_JOBS[job["job_id"]] = job
    write_audit(db, event_type="task_catalog_refresh_job_create", message=f"Created task refresh job {job['job_id']} for source {source}", target_type="account", target_id=source)
    db.commit()
    return _job_response(job)


@router.get("/catalog/refresh-job/{job_id}", response_model=TaskCatalogRefreshJobResponse)
def read_task_refresh_job(job_id: str) -> TaskCatalogRefreshJobResponse:
    job = _REFRESH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Task refresh job not found")
    return _job_response(job)


@router.post("/catalog/refresh-step", response_model=TaskCatalogRefreshJobResponse)
def run_task_refresh_step(payload: TaskCatalogRefreshStepRequest, db: Session = Depends(get_db)) -> TaskCatalogRefreshJobResponse:
    job = _REFRESH_JOBS.get(payload.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Task refresh job not found")
    if job["status"] in {"completed", "failed"}:
        return _job_response(job)
    step = next((item for item in job["steps"] if item["status"] == "pending"), None)
    if not step:
        job["status"] = "completed"
        job["updated_at"] = _utc_now()
        job["message"] = "刷新 job 已完成。"
        return _job_response(job)
    now = _utc_now()
    step["status"] = "running"
    step["started_at"] = now
    job["status"] = "running"
    job["updated_at"] = now
    try:
        if step["key"] == "prepare":
            step["message"] = f"任务源账号 {job['source_account_user_id']}，实时只读={job['payload'].use_live_readonly}。"
        elif step["key"] == "refresh":
            result = _run_task_catalog_refresh(job["payload"], db)
            job["result"] = result
            step["message"] = result.message
        elif step["key"] == "complete":
            imported_count = job["result"].imported_count if job.get("result") else 0
            step["message"] = f"刷新完成，导入 {imported_count} 个任务。"
            job["status"] = "completed"
            job["message"] = step["message"]
        step["status"] = "completed"
        step["finished_at"] = _utc_now()
        if job["status"] != "completed":
            job["message"] = step["message"]
        write_audit(db, event_type="task_catalog_refresh_job_step", message=f"Refresh job {job['job_id']} step {step['key']} completed", target_type="account", target_id=job["source_account_user_id"])
        db.commit()
    except Exception as exc:  # noqa: BLE001 - step API must surface actionable failure state.
        step["status"] = "failed"
        step["finished_at"] = _utc_now()
        step["message"] = str(exc)
        job["status"] = "failed"
        job["error"] = str(exc)
        job["message"] = f"刷新 job 失败：{exc}"
    job["updated_at"] = _utc_now()
    return _job_response(job)


@router.get("/rules", response_model=TaskRuleConfigResponse)
def read_task_rules(db: Session = Depends(get_db)) -> TaskRuleConfigResponse:
    get_task_rule_config(db)
    db.commit()
    return TaskRuleConfigResponse(prefix_rules=read_prefix_rules(db), manual_short_names=read_manual_short_names(db))


@router.put("/rules", response_model=TaskRuleConfigResponse)
def update_task_rules(payload: TaskRuleConfigUpdateRequest, db: Session = Depends(get_db)) -> TaskRuleConfigResponse:
    update_task_rule_config(db, payload, updated_by="operator")
    write_audit(db, event_type="task_rule_config_update", message="Updated task prefix/manual short-name rules", target_type="task_rule", target_id="default")
    db.commit()
    return TaskRuleConfigResponse(prefix_rules=read_prefix_rules(db), manual_short_names=read_manual_short_names(db))


@router.post("/task-page/sample-capture", response_model=TaskSampleCaptureResponse)
def capture_task_page_sample(payload: Optional[TaskSampleCaptureRequest] = None) -> TaskSampleCaptureResponse:
    settings = get_settings()
    source = payload.source_account_user_id if payload and payload.source_account_user_id else settings.task_source_account_user_id
    sample_payload = payload.sample_payload if payload else None
    message = "已保存任务页脱敏样本；真实只读采集接入后会用真实响应覆盖该文件。"
    if payload and payload.use_live_readonly:
        sample_payload = capture_search_task_readonly(source)
        message = "已通过主账号 Cookie 只读调用 SearchTask，并保存脱敏样本。"
    sample_path = save_redacted_task_sample(sample_payload, source)
    return TaskSampleCaptureResponse(source_account_user_id=source, sample_saved=True, message=message, redacted_sample_path=str(sample_path))
