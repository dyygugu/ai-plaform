import base64
import json
import os
import re
from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import uuid4

import requests
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.task import TaskCatalogItem
from app.schemas.task_capability import (
    TaskAiDraftBuildRequest,
    TaskCapabilityCardResponse,
    TaskCapabilityFieldMapping,
    TaskCapabilityIdentity,
    TaskCapabilityInputSpec,
    TaskCapabilityOutputField,
    TaskCapabilityRule,
    TaskCapabilityValidation,
    TaskHttpQuestionContextResponse,
    TaskMediaInspectionPlanRequest,
    TaskMediaInspectionPlanResponse,
    TaskMediaInspectionStep,
    TaskMediaInspectionExecutionRequest,
    TaskMediaInspectionExecutionResponse,
    TaskMediaInspectionDraftRequest,
    TaskMediaInspectionProviderRequest,
    TaskMediaInspectionProviderResponse,
    TaskMediaImageJudgement,
    TaskMediaProbeResult,
    TaskMediaResource,
    TaskOperationProcessPlanResponse,
    TaskVideoKeyframe,
    TaskVideoKeyframeExtractionRequest,
    TaskVideoKeyframeExtractionResponse,
    TaskVideoKeyframeExtractionResult,
    TaskVideoKeyframeJudgement,
    TaskDraftBuildRequest,
    TaskDraftBuildResponse,
    TaskDraftConfirmationFieldDiff,
    TaskDraftConfirmationGateStatus,
    TaskDraftConfirmationSheet,
    TaskDraftRehearsalChecklistItem,
    TaskDraftReviewApprovalRequest,
    TaskDraftReviewApprovalResponse,
    TaskDraftReviewItem,
    TaskDraftReviewPreview,
    TaskQuestionDecisionStep,
    TaskQuestionIterationCandidate,
    TaskQuestionMaterialResource,
    TaskSandboxClickExecutionRequest,
    TaskSandboxClickExecutionResponse,
    TaskSandboxClickExecutionResult,
    TaskSandboxClickDraftRequest,
    TaskSandboxClickInteractionSummary,
    TaskProviderDraftRequest,
    TaskSandboxClickCandidate,
    TaskSandboxClickPlanRequest,
    TaskSandboxClickPlanResponse,
    TaskSandboxClickPlanStep,
)
from app.services.ai_service import get_task_ai_runtime_prompt
from app.services.audit_service import write_audit
from app.services.worker_service import report_worker_event
from app.schemas.worker import WorkerEventReportRequest


SUBMIT_TEMP_ENDPOINT = "/api/dispatch/SubmitTempItemAnswer"
MGET_ANSWER_LIST_ENDPOINT = "/api/dispatch/MGetAnswerList"
ALLOWED_DRAFT_FIELDS = [
    "beauty_score",
    "motion_richness_score",
    "high_richness_reason",
    "richness_reason",
    "sceneConsistencyScore",
    "sceneConsistencyRemarks",
    "discard",
    "discard_type",
    "discard_remark",
    "checkRemark",
    "screen_record",
    "__internalData__",
]
FIELD_ROLES = {
    "beauty_score": "截图美观分",
    "motion_richness_score": "视频/动效丰富度分",
    "high_richness_reason": "高丰富度原因",
    "richness_reason": "评分原因",
    "sceneConsistencyScore": "前后场景一致性分",
    "sceneConsistencyRemarks": "前后场景一致性原因",
    "discard": "是否废弃",
    "discard_type": "废弃类型",
    "discard_remark": "草稿标记",
    "checkRemark": "复核备注",
    "screen_record": "录屏辅助字段",
    "__internalData__": "平台内部字段",
}


class TaskCapabilityError(ValueError):
    def __init__(self, message: str, blockers: Optional[list[str]] = None) -> None:
        super().__init__(message)
        self.blockers = blockers or []


def build_task_capability_card(db: Session, item_id: int) -> TaskCapabilityCardResponse:
    item = _get_item(db, item_id)
    recordings = _find_recordings(item.task_id)
    if not recordings:
        raise TaskCapabilityError("该任务还没有可用操作录制包。", ["missing-recording"])
    learned = _learn_from_recording(recordings[-1])
    content = learned["content"]
    identity = _identity_from_payload(learned["payload"])
    field_mappings = _build_field_mappings(content)
    state = "http_draft_verified" if learned["success_response_count"] > 0 else "analyzed"
    return TaskCapabilityCardResponse(
        task_catalog_item_id=item.id,
        task_id=item.task_id,
        task_type_key=_task_type_key(content),
        task_type_name=item.task_short_name or "返修评分",
        state=state,
        capability_level="HTTP-only",
        endpoint=SUBMIT_TEMP_ENDPOINT,
        recording_count=len(recordings),
        recording_paths=[str(path) for path in recordings],
        identity=identity,
        field_mappings=field_mappings,
        latest_validation=TaskCapabilityValidation(
            mode="operation_recording_analysis",
            endpoint=SUBMIT_TEMP_ENDPOINT,
            request_count=learned["request_count"],
            success_response_count=learned["success_response_count"],
            evidence_path=str(recordings[-1]),
        ),
        supported_actions=["temp_draft_dry_run", "temp_draft_gated_write"],
        ai_input_requirements=_build_ai_input_requirements(content),
        ai_input_materials=_build_ai_input_materials(content),
        ai_input_spec=_build_ai_input_spec(content),
        scoring_rules=_build_scoring_rules(content),
        reason_rules=_build_reason_rules(),
        ai_output_schema=_build_ai_output_schema(),
        guardrails=[
            "默认只生成草稿 payload，不触网。",
            "真实写草稿只允许 SubmitTempItemAnswer。",
            "执行写草稿必须 allow_draft_write=true 且 AIDP_TEMP_DRAFT_ALLOW_WRITE=1。",
            "不提交、不继续下一题、不放弃、不领取。",
        ],
        next_steps=[
            "补齐本题型 AI 输入材料和评分原因规则。",
            "用 dry-run diff 检查 AI 输出只覆盖允许字段。",
            "草稿暂存后仍由人工打开页面复核并决定是否提交。",
        ],
    )


def summarize_task_capability(task_id: str) -> dict[str, object]:
    recordings = _find_recordings(task_id)
    return {
        "capability_available": bool(recordings),
        "capability_recording_count": len(recordings),
    }


def build_http_question_context(
    db: Session,
    item_id: int,
    *,
    prefer_live: bool = False,
    allow_remote_fetch: bool = False,
    account_user_id: str = "",
) -> TaskHttpQuestionContextResponse:
    item = _get_item(db, item_id)
    recordings = _find_recordings(item.task_id)
    if not recordings:
        raise TaskCapabilityError("该任务还没有可用操作录制包。", ["missing-recording"])
    learned = _learn_from_recording(recordings[-1])
    source_mode = "recorded_submit_temp_payload"
    sends_network = False
    evidence_path = str(recordings[-1])
    content = learned["content"]
    identity = _identity_from_payload(learned["payload"])
    if prefer_live and allow_remote_fetch:
        live = _fetch_live_question_context_from_mget_answer_list(item, learned, account_user_id)
        content = live["content"]
        identity = live["identity"]
        source_mode = "live_mget_answer_list"
        sends_network = True
        evidence_path = live["evidence_path"]
    data = content.get("data") if isinstance(content.get("data"), dict) else {}
    return TaskHttpQuestionContextResponse(
        ok=True,
        mode="http_question_context",
        source_mode=source_mode,
        sends_network=sends_network,
        writes_remote=False,
        task_catalog_item_id=item.id,
        task_id=item.task_id,
        task_type_name=item.task_short_name or "返修评分",
        identity=identity,
        payload_identity={
            "TaskID": identity.TaskID,
            "NodeID": identity.NodeID,
            "ItemID": identity.ItemID,
            "StagingTime": identity.StagingTime,
            "allowed_save_endpoint": SUBMIT_TEMP_ENDPOINT,
            "requires_current_item_match": True,
        },
        material_resources=_build_question_material_resources(content),
        current_answer_data=deepcopy(data),
        scoring_rules=_build_scoring_rules(content),
        reason_rules=_build_reason_rules(),
        guardrails=[
            "禁止打开 AIDP 做题 UI",
            "禁止提交、继续下一题、放弃、领取新题。",
            "禁止调用 Receive/PreReceive",
            "Receive/PreReceive 可能有领取或切题副作用，必须保持禁用。",
            "本接口只整理题面资源和暂存 payload 身份，不触网、不写远端。",
            "prefer_live=true 也只允许调用 MGetAnswerList 读取当前答案列表。",
            "结构化答案必须先经过 dry-run diff 和人工/操作者闸门。",
        ],
        decision_pipeline=_build_question_decision_pipeline(content, include_live_read=sends_network),
        iteration_candidates=_build_question_iteration_candidates(),
        blockers=[
            "visual-judge-requires-sandbox-or-multimodal",
            "web-interaction-requires-isolated-browser",
            "video-judge-requires-frame-sampling-or-multimodal",
        ],
        evidence_path=evidence_path,
        message=(
            "已通过 MGetAnswerList 读取题面上下文；未打开 AIDP UI、未写远端。"
            if sends_network
            else "已生成不打开 AIDP UI 的题面上下文；下一步应由沙箱浏览器和视觉/视频判断层产出结构化评分，再走 HTTP dry-run/受控暂存。"
        ),
    )


def build_operation_process_plan(db: Session, item_id: int) -> TaskOperationProcessPlanResponse:
    item = _get_item(db, item_id)
    operation_url = "https://aidp.juejin.cn/operation"
    return TaskOperationProcessPlanResponse(
        ok=True,
        mode="operation_process_plan",
        operation_url=operation_url,
        task_catalog_item_id=item.id,
        task_id=item.task_id,
        task_type_name=item.task_short_name or "待处理任务",
        source_account_user_id=item.source_account_user_id,
        claims_task=True,
        sends_network=True,
        writes_remote=True,
        submits_answer=False,
        post_claim_read_step="处理跳转到题目后，再用 MGetAnswerList 读取当前 ItemID 和题面 Content。",
        answer_write_step="结构化答案通过 SubmitTempItemAnswer 写草稿；最终提交必须作为单独动作审计。",
        guardrails=[
            "点击“处理”会领题并改变账号任务状态",
            "领题、读题、写草稿、最终提交必须分成独立步骤",
            "只允许授权账号执行处理入口，禁止使用用户正在操作的账号",
            "未捕获跳转后的 TaskID/NodeID/ItemID 时禁止写草稿或提交",
            "最终提交不能复用草稿暂存闸门，必须单独确认和记录",
        ],
        steps=[
            TaskQuestionDecisionStep(
                key="operation-click-process",
                title="打开 operation 页点击处理",
                executor="operator-or-isolated-browser",
                input_keys=["operation_url", "authorized_account_cookie"],
                output_keys=["task_page_url", "task_id", "node_id", "item_id"],
                can_run_without_aidp_ui=False,
                status="required",
            ),
            TaskQuestionDecisionStep(
                key="post-claim-read-current-question",
                title="读取处理后当前题面",
                executor="http-mget-answer-list",
                input_keys=["task_id", "node_id", "item_id"],
                output_keys=["material_resources", "current_answer_data"],
                can_run_without_aidp_ui=True,
                status="next",
            ),
            TaskQuestionDecisionStep(
                key="ai-answer-and-controlled-write",
                title="AI 判题并受控写入",
                executor="task-ai-provider-and-http",
                input_keys=["material_resources", "sandbox_trace", "keyframes"],
                output_keys=["draft_payload", "write_result", "submit_result"],
                can_run_without_aidp_ui=True,
                status="planned",
            ),
        ],
        message="已按真实入口建模：在 operation 页点击处理后自动分配题目；该步骤会领题但不会提交答案。",
    )


def build_sandbox_click_plan(db: Session, item_id: int, request: TaskSandboxClickPlanRequest) -> TaskSandboxClickPlanResponse:
    _get_item(db, item_id)
    html_url = str(request.html_url or "").strip()
    html_snapshot = str(request.html_snapshot or "")
    source_mode = "provided_html_snapshot"
    sends_network = False
    blockers: list[str] = []
    if not html_snapshot and request.allow_remote_fetch:
        if not _is_safe_question_resource_url(html_url):
            raise TaskCapabilityError("沙箱只允许加载非 AIDP 的题目网页 URL。", ["unsafe-sandbox-url"])
        fetched = _fetch_question_html_snapshot(html_url)
        html_snapshot = fetched["html"]
        source_mode = "remote_question_html"
        sends_network = True
    elif not html_snapshot:
        blockers.append("missing-html-snapshot")
    candidates = _extract_click_candidates(html_snapshot, max_candidates=max(1, min(int(request.max_candidates or 20), 50))) if html_snapshot else []
    return TaskSandboxClickPlanResponse(
        ok=not blockers,
        mode="sandbox_click_plan",
        sends_network=sends_network,
        writes_remote=False,
        executes_clicks=False,
        html_url=html_url,
        source_mode=source_mode,
        click_candidates=candidates,
        guardrails=[
            "只允许加载题目网页 URL，不允许打开 AIDP UI",
            "首版只提取点击候选，不执行点击。",
            "后续真实点击必须在独立浏览器上下文中限制域名、点击次数和超时。",
            "禁止提交、继续下一题、放弃、领取新题。",
        ],
        next_steps=[
            TaskSandboxClickPlanStep(
                key="sandbox-browser-click-execution",
                title="独立浏览器点击执行",
                status="planned",
                detail="按候选选择少量元素点击，记录 URL 变化、DOM 变化、弹窗/菜单和动画信号。",
            ),
            TaskSandboxClickPlanStep(
                key="interaction-signal-summary",
                title="交互信号汇总",
                status="planned",
                detail="把跳转、交互、动效映射为 high_richness_reason 和 richness_reason 的结构化输入。",
            ),
            TaskSandboxClickPlanStep(
                key="http-draft-mapping",
                title="HTTP 草稿映射",
                status="planned",
                detail="将沙箱判断结果交给既有 dry-run diff 和三重闸门暂存链路。",
            ),
        ],
        blockers=blockers,
        message="已生成沙箱点击候选计划；未执行点击、未打开 AIDP UI、未写远端。" if not blockers else "缺少 HTML 快照；未执行点击、未触网。",
    )


def build_sandbox_click_execution(db: Session, item_id: int, request: TaskSandboxClickExecutionRequest) -> TaskSandboxClickExecutionResponse:
    _get_item(db, item_id)
    html_url = str(request.html_url or "").strip()
    selectors = _normalize_click_selectors(request.selectors, request.max_clicks)
    allowed_domains = _normalize_allowed_domains(html_url, request.allowed_domains)
    helper_endpoint = _sandbox_helper_endpoint()
    guardrails = [
        "只允许加载题目网页 URL，不打开 AIDP UI。",
        "默认不执行点击；必须 allow_execute=true 才会调用本机 helper。",
        "执行层只记录 URL/DOM/弹窗/动画信号，不提交表单、不写 AIDP。",
        "禁止提交、继续下一题、放弃、领取新题。",
    ]
    blockers: list[str] = []
    if not request.allow_execute:
        blockers.append("missing-allow-execute")
    if not html_url:
        blockers.append("missing-html-url")
    elif not _is_safe_question_resource_url(html_url):
        blockers.append("unsafe-sandbox-url")
    if not selectors:
        blockers.append("missing-selectors")
    if blockers:
        return TaskSandboxClickExecutionResponse(
            ok=False,
            mode="sandbox_click_execution",
            sends_network=False,
            writes_remote=False,
            executes_clicks=False,
            html_url=html_url,
            allowed_domains=allowed_domains,
            guardrails=guardrails,
            blockers=blockers,
            helper_endpoint=helper_endpoint,
            message="沙箱点击执行未开始；需要显式允许、题目网页 URL 和点击 selector。",
        )
    try:
        helper_result = _execute_sandbox_clicks_via_helper(
            html_url=html_url,
            selectors=selectors,
            allowed_domains=allowed_domains,
            max_clicks=max(1, min(int(request.max_clicks or 3), 10)),
            timeout_ms=max(500, min(int(request.timeout_ms or 5000), 15000)),
            helper_endpoint=helper_endpoint,
        )
    except Exception as exc:  # noqa: BLE001 - surface helper failures without falling back to AIDP UI.
        return TaskSandboxClickExecutionResponse(
            ok=False,
            mode="sandbox_click_execution",
            sends_network=True,
            writes_remote=False,
            executes_clicks=False,
            html_url=html_url,
            allowed_domains=allowed_domains,
            guardrails=guardrails,
            blockers=["sandbox-helper-unavailable"],
            helper_endpoint=helper_endpoint,
            message=f"本机 helper 沙箱点击执行失败：{exc}",
        )
    click_results = [_sandbox_execution_result_from_helper(item) for item in helper_result.get("results", []) if isinstance(item, dict)]
    summary = _sandbox_interaction_summary_from_helper(helper_result.get("summary"), click_results)
    helper_ok = bool(helper_result.get("ok", bool(click_results)))
    return TaskSandboxClickExecutionResponse(
        ok=helper_ok,
        mode="sandbox_click_execution",
        sends_network=True,
        writes_remote=False,
        executes_clicks=True,
        html_url=html_url,
        allowed_domains=allowed_domains,
        click_results=click_results,
        interaction_summary=summary,
        guardrails=guardrails,
        blockers=[] if helper_ok else ["sandbox-helper-execution-failed"],
        helper_endpoint=helper_endpoint,
        elapsed_ms=_safe_int(helper_result.get("elapsedMs") or helper_result.get("elapsed_ms")),
        message="已通过独立本机 helper 执行沙箱点击；未打开 AIDP UI、未写远端。" if helper_ok else "本机 helper 已返回，但沙箱点击未成功。",
    )


def build_sandbox_click_draft(db: Session, item_id: int, request: TaskSandboxClickDraftRequest) -> TaskDraftBuildResponse:
    if not request.web_accessible:
        answer_data = {
            "motion_richness_score": "0",
            "richness_reason": "沙箱检测网页不可访问或白屏，未观察到可评分的跳转、交互或动效。",
            "high_richness_reason": [],
            "checkRemark": "沙箱点击结果 dry-run：网页不可访问，需人工复核。",
        }
    else:
        answer_data = _map_sandbox_clicks_to_answer_data(request.click_results, request.interaction_summary)
    draft_response = build_or_execute_temp_draft(
        db,
        item_id,
        TaskDraftBuildRequest(
            answer_data=answer_data,
            remark_marker=request.remark_marker or "SANDBOX_CLICK_DRY_RUN",
            execute=False,
            allow_draft_write=False,
            write_audit=request.write_audit,
        ),
    )
    draft_response.mode = "sandbox_click_draft_plan"
    draft_response.message = "已把沙箱点击信号映射为评分 dry-run 草稿；未触网、未写远端、未提交。"
    return draft_response


def build_media_inspection_plan(db: Session, item_id: int, request: TaskMediaInspectionPlanRequest) -> TaskMediaInspectionPlanResponse:
    _get_item(db, item_id)
    media_resources = _build_media_resources_from_request(request)
    blockers = [] if media_resources else ["missing-media-resources"]
    return TaskMediaInspectionPlanResponse(
        ok=not blockers,
        mode="media_inspection_plan",
        sends_network=False,
        writes_remote=False,
        claims_visual_judgement=False,
        media_resources=media_resources,
        inspection_steps=_build_media_inspection_steps(media_resources),
        guardrails=[
            "无多模态执行结果前不能声明图片/视频已判分",
            "图片基础探测只能发现白屏、不可访问、尺寸异常等低层信号。",
            "视频是否复现同样操作必须由关键帧/多模态模型或人工复核确认。",
            "不得把媒体计划直接映射为提交；只能进入 dry-run 草稿和人工复核。",
        ],
        blockers=blockers,
        message="已生成媒体检查计划；未下载媒体、未做视觉判分、未写远端。" if not blockers else "缺少图片或视频资源，无法生成媒体检查计划。",
    )


def build_media_inspection_execution(db: Session, item_id: int, request: TaskMediaInspectionExecutionRequest) -> TaskMediaInspectionExecutionResponse:
    _get_item(db, item_id)
    guardrails = [
        "默认不下载媒体；必须 allow_remote_probe=true 才执行基础探测。",
        "基础探测只判断可访问性、类型、大小和图片尺寸，不声明视觉判分。",
        "视频操作复现仍必须由关键帧/多模态模型或人工复核确认。",
        "不得把媒体探测结果直接提交，只能进入 dry-run 草稿和人工复核。",
    ]
    resources = [item for item in request.media_resources if str(item.url or "").strip()]
    if not request.allow_remote_probe:
        return TaskMediaInspectionExecutionResponse(
            ok=False,
            mode="media_inspection_execution",
            sends_network=False,
            writes_remote=False,
            claims_visual_judgement=False,
            guardrails=guardrails,
            blockers=["missing-allow-remote-probe"],
            message="媒体基础探测未执行；需要显式 allow_remote_probe=true。",
        )
    max_bytes = max(1024, min(int(request.max_bytes or 65536), 1024 * 1024))
    results = [_probe_media_resource(item, max_bytes=max_bytes) for item in resources]
    blockers = []
    if not results:
        blockers.append("missing-media-resources")
    if any(not item.ok for item in results):
        blockers.append("media-probe-failed")
    blockers.append("multimodal-still-required")
    return TaskMediaInspectionExecutionResponse(
        ok=bool(results) and all(item.ok for item in results),
        mode="media_inspection_execution",
        sends_network=True,
        writes_remote=False,
        claims_visual_judgement=False,
        probe_results=results,
        guardrails=guardrails,
        blockers=blockers,
        message="已完成媒体基础探测；仍未做视觉/视频判分，后续需要多模态或人工复核。",
    )


def build_video_keyframe_extraction(db: Session, item_id: int, request: TaskVideoKeyframeExtractionRequest) -> TaskVideoKeyframeExtractionResponse:
    _get_item(db, item_id)
    resources = [resource for resource in request.media_resources if resource.material_type == "video" and str(resource.url or "").strip()]
    helper_endpoint = _video_keyframe_helper_endpoint()
    guardrails = [
        "默认不抽取视频关键帧；必须 allow_extract=true 才调用本机 helper。",
        "只允许加载题目视频资源，不打开 AIDP UI。",
        "关键帧只是多模态输入材料，不直接等于视频判分。",
        "禁止提交、继续下一题、放弃、领取新题。",
    ]
    blockers: list[str] = []
    if not request.allow_extract:
        blockers.append("missing-allow-extract")
    if not resources:
        blockers.append("missing-video-resources")
    unsafe = [resource.key for resource in resources if not _is_safe_question_resource_url(resource.url)]
    if unsafe:
        blockers.append("unsafe-video-url")
    if blockers:
        return TaskVideoKeyframeExtractionResponse(
            ok=False,
            mode="media_keyframe_extraction",
            sends_network=False,
            writes_remote=False,
            claims_visual_judgement=False,
            helper_endpoint=helper_endpoint,
            guardrails=guardrails,
            blockers=blockers,
            message="视频关键帧抽取未执行；需要显式允许、视频资源和安全 URL。",
        )
    max_frames = max(1, min(int(request.max_frames_per_video or 3), 5))
    timeout_ms = max(3000, min(int(request.timeout_ms or 12000), 30000))
    if request.reuse_cached_frames:
        cached = _load_cached_video_keyframes(
            item_id=item_id,
            resources=resources,
            min_frames_per_video=max_frames,
            cache_manifest_path=request.cache_manifest_path,
        )
        if cached is not None:
            cached_results, cached_manifest_path, cached_frame_count = cached
            return TaskVideoKeyframeExtractionResponse(
                ok=True,
                mode="media_keyframe_extraction",
                sends_network=False,
                writes_remote=False,
                claims_visual_judgement=False,
                helper_endpoint=helper_endpoint,
                helper_mode="cached_keyframe_archive",
                keyframe_results=cached_results,
                artifact_path=cached_manifest_path,
                archived_frame_count=cached_frame_count,
                cache_hit=True,
                guardrails=[*guardrails, "cached-keyframes-reused"],
                blockers=["multimodal-still-required"],
                elapsed_ms=0,
                message="已复用本地多帧关键帧 manifest；未调用 helper、未触网、未写远端。低置信时可提高 max_frames_per_video 补抽更多帧。",
            )
    try:
        helper_result = _extract_video_keyframes_via_helper(
            resources=resources,
            max_frames_per_video=max_frames,
            timeout_ms=timeout_ms,
            helper_endpoint=helper_endpoint,
        )
    except Exception as exc:  # noqa: BLE001 - helper failure must not create fake frames.
        return TaskVideoKeyframeExtractionResponse(
            ok=False,
            mode="media_keyframe_extraction",
            sends_network=True,
            writes_remote=False,
            claims_visual_judgement=False,
            helper_endpoint=helper_endpoint,
            guardrails=guardrails,
            blockers=["video-keyframe-helper-unavailable"],
            message=f"本机 helper 视频关键帧抽取失败：{exc}",
        )
    results = [_video_keyframe_result_from_helper(item) for item in helper_result.get("results", []) if isinstance(item, dict)]
    ok = bool(helper_result.get("ok", bool(results))) and bool(results) and all(result.status == "ok" and result.keyframes for result in results)
    artifact_path = ""
    archived_frame_count = 0
    if request.archive_frames and results:
        artifact_path, archived_frame_count = _archive_video_keyframes(item_id=item_id, results=results)
    blockers = [] if ok else ["video-keyframe-extraction-incomplete"]
    blockers.append("multimodal-still-required")
    return TaskVideoKeyframeExtractionResponse(
        ok=ok,
        mode="media_keyframe_extraction",
        sends_network=True,
        writes_remote=False,
        claims_visual_judgement=False,
        helper_endpoint=helper_endpoint,
        helper_mode=str(helper_result.get("mode") or ""),
        keyframe_results=results,
        artifact_path=artifact_path,
        archived_frame_count=archived_frame_count,
        cache_hit=False,
        guardrails=guardrails,
        blockers=blockers,
        elapsed_ms=_safe_int(helper_result.get("elapsedMs") or helper_result.get("elapsed_ms")),
        message="已通过本机 helper 抽取视频关键帧；未打开 AIDP UI、未写远端，后续仍需多模态判断。" if ok else "本机 helper 返回了结果，但关键帧抽取不完整。",
    )


def build_media_inspection_draft(db: Session, item_id: int, request: TaskMediaInspectionDraftRequest) -> TaskDraftBuildResponse:
    answer_data = _map_media_judgements_to_answer_data(request)
    draft_response = build_or_execute_temp_draft(
        db,
        item_id,
        TaskDraftBuildRequest(
            answer_data=answer_data,
            remark_marker=request.remark_marker or "MEDIA_INSPECTION_DRY_RUN",
            execute=False,
            allow_draft_write=False,
            write_audit=request.write_audit,
        ),
    )
    draft_response.mode = "media_inspection_draft_plan"
    draft_response.message = "已把多模态图片判断和视频关键帧判断映射为 dry-run 草稿；未触 AIDP、未写远端、未提交。"
    return draft_response


def build_media_inspection_provider(db: Session, item_id: int, request: TaskMediaInspectionProviderRequest) -> TaskMediaInspectionProviderResponse:
    started = datetime.now(timezone.utc)
    item = _get_item(db, item_id)
    resources = [resource for resource in request.media_resources if str(resource.url or "").strip()]
    guardrails = [
        "只允许调用已配置的做题 AI provider，不打开 AIDP UI。",
        "provider 结果只生成结构化媒体判断和 dry-run 草稿，不写 AIDP。",
        "视频 URL 交给 provider 时不要声称已经解码视频；没有关键帧抽取器时仍需人工或后续抽帧复核。",
        "禁止提交、继续下一题、放弃、领取新题。",
    ]
    if not request.use_provider:
        return TaskMediaInspectionProviderResponse(
            ok=False,
            mode="media_inspection_provider",
            sends_network=False,
            writes_remote=False,
            claims_visual_judgement=False,
            provider_status="provider_disabled",
            media_resources=resources,
            guardrails=guardrails,
            blockers=["provider-disabled"],
            message="媒体 provider 判断未执行；需要 use_provider=true。",
        )
    if not resources:
        return TaskMediaInspectionProviderResponse(
            ok=False,
            mode="media_inspection_provider",
            sends_network=False,
            writes_remote=False,
            claims_visual_judgement=False,
            provider_status="missing_media_resources",
            media_resources=resources,
            guardrails=guardrails,
            blockers=["missing-media-resources"],
            message="缺少媒体资源，无法调用做题 AI 生成媒体判断。",
        )
    runtime = get_task_ai_runtime_prompt()
    if not runtime.get("provider_configured"):
        return TaskMediaInspectionProviderResponse(
            ok=False,
            mode="media_inspection_provider",
            sends_network=False,
            writes_remote=False,
            claims_visual_judgement=False,
            provider_status="provider_not_configured",
            media_resources=resources,
            guardrails=guardrails,
            blockers=["task-ai-provider-not-configured"],
            message="做题 AI provider 未配置，不能生成真实多模态媒体判断。",
        )
    initial_video_judgements: list[TaskVideoKeyframeJudgement] = []
    supplement_attempted = False
    supplement_status = ""
    supplement_keyframes: Optional[TaskVideoKeyframeExtractionResponse] = None
    provider_call_count = 0
    provider_elapsed_ms = 0
    provider_input_text_chars = 0
    provider_input_image_count = 0
    provider_input_keyframe_count = 0
    provider_request = request
    supplement_max_frames = max(4, min(int(request.supplement_max_frames_per_video or 5), 5))
    if request.auto_supplement_low_confidence:
        cached_supplement = _load_cached_video_keyframes(
            item_id=item_id,
            resources=[resource for resource in resources if resource.material_type == "video"],
            min_frames_per_video=supplement_max_frames,
        )
        if cached_supplement is not None:
            cached_results, cached_manifest_path, cached_frame_count = cached_supplement
            supplement_attempted = True
            supplement_status = "cached_supplement_used"
            supplement_keyframes = TaskVideoKeyframeExtractionResponse(
                ok=True,
                mode="media_keyframe_extraction",
                sends_network=False,
                writes_remote=False,
                claims_visual_judgement=False,
                helper_endpoint=_video_keyframe_helper_endpoint(),
                helper_mode="cached_keyframe_archive",
                keyframe_results=cached_results,
                artifact_path=cached_manifest_path,
                archived_frame_count=cached_frame_count,
                cache_hit=True,
                guardrails=["cached-keyframes-reused"],
                blockers=["multimodal-still-required"],
                elapsed_ms=0,
                message="已预加载本地 5 帧关键帧缓存，跳过首轮低帧 provider 探测。",
            )
            provider_request = request.model_copy(
                update={
                    "video_keyframes": cached_results,
                    "operator_prompt": (request.operator_prompt + "\n已复用本地更多关键帧缓存；请直接基于这些多帧证据判断，避免重复低帧探测。")[:1200],
                    "auto_supplement_low_confidence": False,
                }
            )
    provider_started = datetime.now(timezone.utc)
    image_judgement, video_judgements, provider_input_stats = _call_task_ai_provider_for_media_inspection(item, resources, provider_request, runtime)
    provider_call_count += 1
    provider_elapsed_ms += int((datetime.now(timezone.utc) - provider_started).total_seconds() * 1000)
    provider_input_text_chars += int(provider_input_stats.get("text_chars", 0))
    provider_input_image_count += int(provider_input_stats.get("image_count", 0))
    provider_input_keyframe_count += int(provider_input_stats.get("keyframe_count", 0))
    if request.auto_supplement_low_confidence and supplement_status != "cached_supplement_used" and any(item.review_required for item in video_judgements):
        supplement_attempted = True
        supplement_keyframes = build_video_keyframe_extraction(
            db,
            item_id,
            TaskVideoKeyframeExtractionRequest(
                media_resources=resources,
                allow_extract=True,
                archive_frames=True,
                reuse_cached_frames=True,
                max_frames_per_video=supplement_max_frames,
                timeout_ms=max(int(request.supplement_max_frames_per_video or 5) * 4000, 20000),
            ),
        )
        if supplement_keyframes.ok and supplement_keyframes.keyframe_results:
            initial_video_judgements = video_judgements
            supplement_request = request.model_copy(
                update={
                    "video_keyframes": supplement_keyframes.keyframe_results,
                    "operator_prompt": (request.operator_prompt + "\n低置信已补抽更多关键帧；请基于补抽后的多帧证据重新判断。")[:1200],
                    "auto_supplement_low_confidence": False,
                }
            )
            provider_started = datetime.now(timezone.utc)
            image_judgement, video_judgements, provider_input_stats = _call_task_ai_provider_for_media_inspection(item, resources, supplement_request, runtime)
            provider_call_count += 1
            provider_elapsed_ms += int((datetime.now(timezone.utc) - provider_started).total_seconds() * 1000)
            provider_input_text_chars += int(provider_input_stats.get("text_chars", 0))
            provider_input_image_count += int(provider_input_stats.get("image_count", 0))
            provider_input_keyframe_count += int(provider_input_stats.get("keyframe_count", 0))
            supplement_status = "supplemented_and_rejudged"
        else:
            supplement_status = "supplement_failed"
    review_blockers = ["low-confidence-media-review-required"] if any(item.review_required for item in video_judgements) else []
    draft_preview = build_media_inspection_draft(
        db,
        item_id,
        TaskMediaInspectionDraftRequest(
            image_judgement=image_judgement,
            video_keyframe_judgements=video_judgements,
            remark_marker="MEDIA_PROVIDER_DRY_RUN",
            write_audit=request.write_audit,
        ),
    )
    total_elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    return TaskMediaInspectionProviderResponse(
        ok=True,
        mode="media_inspection_provider",
        sends_network=True,
        writes_remote=False,
        claims_visual_judgement=True,
        provider_status="provider_ok",
        media_resources=resources,
        image_judgement=image_judgement,
        video_keyframe_judgements=video_judgements,
        draft_preview=draft_preview,
        provider_call_count=provider_call_count,
        provider_elapsed_ms=provider_elapsed_ms,
        total_elapsed_ms=total_elapsed_ms,
        provider_input_text_chars=provider_input_text_chars,
        provider_input_image_count=provider_input_image_count,
        provider_input_keyframe_count=provider_input_keyframe_count,
        provider_diagnostics=_build_media_provider_diagnostics(
            provider_call_count=provider_call_count,
            provider_elapsed_ms=provider_elapsed_ms,
            total_elapsed_ms=total_elapsed_ms,
            provider_input_text_chars=provider_input_text_chars,
            provider_input_image_count=provider_input_image_count,
            provider_input_keyframe_count=provider_input_keyframe_count,
            supplement_status=supplement_status,
        ),
        supplement_attempted=supplement_attempted,
        supplement_status=supplement_status,
        supplement_keyframes=supplement_keyframes,
        initial_video_keyframe_judgements=initial_video_judgements,
        guardrails=guardrails,
        blockers=review_blockers,
        message="已调用做题 AI provider 生成结构化媒体判断，并映射为 dry-run 草稿；未打开 AIDP UI、未写远端。",
    )


def build_or_execute_ai_temp_draft(db: Session, item_id: int, request: TaskAiDraftBuildRequest) -> TaskDraftBuildResponse:
    answer_data = _map_ai_output_to_answer_data(request.ai_output)
    draft_request = TaskDraftBuildRequest(
        answer_data=answer_data,
        remark_marker=request.remark_marker or "AI_OUTPUT_DRY_RUN",
        execute=request.execute,
        allow_draft_write=request.allow_draft_write,
        account_user_id=request.account_user_id,
        write_audit=request.write_audit,
    )
    response = build_or_execute_temp_draft(db, item_id, draft_request)
    response.mode = "ai_temp_draft_execute" if request.execute else "ai_temp_draft_plan"
    response.message = "已将 AI 结构化输出映射为草稿暂存 dry-run；未触网、未写远端。" if not request.execute else response.message
    return response


def build_or_execute_provider_temp_draft(db: Session, item_id: int, request: TaskProviderDraftRequest) -> TaskDraftBuildResponse:
    item = _get_item(db, item_id)
    recordings = _find_recordings(item.task_id)
    if not recordings:
        raise TaskCapabilityError("该任务还没有可用操作录制包。", ["missing-recording"])
    learned = _learn_from_recording(recordings[-1])
    content = learned["content"]
    blockers: list[str] = []
    provider_status = "local_policy"
    if request.use_provider:
        runtime = get_task_ai_runtime_prompt()
        if runtime.get("provider_configured"):
            ai_output, provider_status = _call_task_ai_provider_for_capability(item, content, request, runtime)
        else:
            blockers.append("task-ai-provider-not-configured")
            ai_output = _build_local_capability_ai_output(content, request.operator_prompt)
            provider_status = "provider_not_configured_local_fallback"
    else:
        ai_output = _build_local_capability_ai_output(content, request.operator_prompt)
    mapped_answer_data = _map_ai_output_to_answer_data(ai_output)
    ai_request = TaskAiDraftBuildRequest(
        ai_output=ai_output,
        remark_marker="AI_PROVIDER_DRY_RUN" if provider_status == "provider_ok" else "AI_PROVIDER_LOCAL_DRY_RUN",
        execute=request.execute,
        allow_draft_write=request.allow_draft_write,
        account_user_id=request.account_user_id,
        write_audit=request.write_audit,
    )
    response = build_or_execute_ai_temp_draft(db, item_id, ai_request)
    response.mode = _provider_draft_mode(provider_status, request.execute)
    response.blockers = blockers
    response.ai_review_preview = _build_ai_review_preview(ai_output, mapped_answer_data, provider_status)
    response.message = _provider_draft_message(provider_status, request.execute, blockers)
    return response


def approve_provider_draft_review(db: Session, item_id: int, request: TaskDraftReviewApprovalRequest) -> TaskDraftReviewApprovalResponse:
    item = _get_item(db, item_id)
    recordings = _find_recordings(item.task_id)
    if not recordings:
        raise TaskCapabilityError("该任务还没有可用操作录制包。", ["missing-recording"])
    learned = _learn_from_recording(recordings[-1])
    mapped_answer_data = _map_ai_output_to_answer_data(request.ai_output)
    draft_response = build_or_execute_ai_temp_draft(
        db,
        item_id,
        TaskAiDraftBuildRequest(
            ai_output=request.ai_output,
            remark_marker="AI_REVIEW_APPROVED_DRY_RUN",
            execute=False,
            allow_draft_write=False,
            write_audit=request.write_audit,
        ),
    )
    if request.write_audit:
        write_audit(
            db,
            event_type="task_capability_ai_review_approved",
            message=f"AI 草稿人工复核通过 task={item.task_id} reviewer={request.reviewer}; only generated controlled draft confirmation sheet",
            target_type="task",
            target_id=item.task_id,
        )
        db.commit()
    gate_statuses = _build_confirmation_gate_statuses()
    ready_for_gated_write = all(gate.passed for gate in gate_statuses if gate.required)
    field_diff = _build_confirmation_field_diff(learned["content"], mapped_answer_data)
    required_gates = ["execute=true", "allow_draft_write=true", "AIDP_TEMP_DRAFT_ALLOW_WRITE=1"]
    forbidden_actions = ["提交", "继续下一题", "放弃", "领取新题", "修改账号密钥"]
    sheet = TaskDraftConfirmationSheet(
        title="受控草稿暂存确认单",
        status="review_approved",
        reviewer=(request.reviewer or "operator")[:80],
        review_note=(request.review_note or "")[:1000],
        mapped_answer_data=mapped_answer_data,
        field_diff=field_diff,
        gate_statuses=gate_statuses,
        ready_for_gated_write=ready_for_gated_write,
        rehearsal_checklist=_build_rehearsal_checklist(
            identity=_identity_from_payload(learned["payload"]),
            field_diff=field_diff,
            gate_statuses=gate_statuses,
            ready_for_gated_write=ready_for_gated_write,
            required_gates=required_gates,
            allowed_endpoint=SUBMIT_TEMP_ENDPOINT,
            forbidden_actions=forbidden_actions,
            draft_evidence_path=draft_response.evidence_path or "",
        ),
        required_gates=required_gates,
        allowed_endpoint=SUBMIT_TEMP_ENDPOINT,
        forbidden_actions=forbidden_actions,
        draft_evidence_path=draft_response.evidence_path or "",
        confirm_text="我确认只执行草稿暂存，不提交",
        next_step="如需真实暂存，必须再次点击受控执行草稿暂存，并满足后端三重闸门；暂存后仍需人工打开页面复核。",
    )
    return TaskDraftReviewApprovalResponse(
        ok=True,
        status="review_approved",
        sends_network=False,
        writes_remote=False,
        confirmation_sheet=sheet,
        message="已生成受控草稿暂存确认单；未触网、未写远端、未提交。",
    )


def build_or_execute_temp_draft(db: Session, item_id: int, request: TaskDraftBuildRequest) -> TaskDraftBuildResponse:
    item = _get_item(db, item_id)
    recordings = _find_recordings(item.task_id)
    if not recordings:
        raise TaskCapabilityError("该任务还没有可用操作录制包。", ["missing-recording"])
    learned = _learn_from_recording(recordings[-1])
    payload = deepcopy(learned["payload"])
    answer = payload["AuditAnswers"][0]
    content = json.loads(answer["Content"])
    _apply_answer_data(content, request.answer_data)
    if request.remark_marker:
        content.setdefault("data", {})["discard_remark"] = request.remark_marker
        content.setdefault("dataMap", {})["discard_remark"] = request.remark_marker
    answer["Content"] = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    identity = _identity_from_payload(payload)
    evidence_path = _write_draft_evidence(item, payload, request, sends_network=False, response=None)
    if not request.execute:
        return TaskDraftBuildResponse(
            ok=True,
            mode="temp_draft_plan",
            sends_network=False,
            writes_remote=False,
            endpoint=SUBMIT_TEMP_ENDPOINT,
            payload_identity=identity,
            payload_preview=payload,
            allowed_fields=ALLOWED_DRAFT_FIELDS,
            blockers=[],
            evidence_path=str(evidence_path),
            message="已生成草稿暂存 dry-run payload；未触网、未写远端。",
        )
    blockers = _execute_blockers(request)
    if blockers:
        raise TaskCapabilityError("草稿暂存执行被安全闸门拦截。", blockers)
    account_user_id = request.account_user_id or learned["account_user_id"] or item.source_account_user_id or get_settings().task_source_account_user_id
    account = _select_account(account_user_id)
    cookie = str(account.get("cookie") or "") if account else ""
    if not account:
        raise TaskCapabilityError("找不到可用于写草稿的账号。", ["missing-account"])
    if not cookie:
        raise TaskCapabilityError("账号缺少 Cookie，不能写草稿。", ["missing-cookie"])
    response = _post_temp_draft(payload, cookie, learned["referer"])
    evidence_path = _write_draft_evidence(item, payload, request, sends_network=True, response=response)
    base_resp = _base_resp_status_code(response.get("data"))
    ok = bool(response.get("ok") and base_resp == 0)
    if request.write_audit:
        write_audit(
            db,
            event_type="task_capability_temp_draft_write",
            message=f"HTTP-only 草稿暂存 {'成功' if ok else '失败'} task={item.task_id} status={response.get('status_code')} baseResp={base_resp}",
            target_type="task",
            target_id=item.task_id,
        )
        _record_worker_event(db, item.task_id, "info" if ok else "warning", "save_draft", f"HTTP-only 草稿暂存 {'成功' if ok else '失败'}")
        db.commit()
    return TaskDraftBuildResponse(
        ok=ok,
        mode="temp_draft_execute",
        sends_network=True,
        writes_remote=True,
        endpoint=SUBMIT_TEMP_ENDPOINT,
        payload_identity=identity,
        payload_preview=payload,
        allowed_fields=ALLOWED_DRAFT_FIELDS,
        blockers=[],
        evidence_path=str(evidence_path),
        status_code=response.get("status_code"),
        base_resp_status_code=base_resp,
        message="纯 HTTP 草稿暂存已执行；仍未提交，需人工复核。" if ok else "纯 HTTP 草稿暂存执行失败，禁止继续提交。",
    )


def _get_item(db: Session, item_id: int) -> TaskCatalogItem:
    item = db.get(TaskCatalogItem, item_id)
    if not item:
        raise TaskCapabilityError("Task catalog item not found", ["missing-task"])
    return item


def _find_recordings(task_id: str) -> list[Path]:
    root = Path(get_settings().operation_recording_root)
    if not root.exists():
        return []
    candidates: list[Path] = []
    for path in sorted(root.glob("opr-*.json"), key=lambda item: item.stat().st_mtime):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8")
        if SUBMIT_TEMP_ENDPOINT not in text:
            continue
        if task_id and task_id in text:
            candidates.append(path)
    return candidates


def _learn_from_recording(path: Path) -> dict[str, Any]:
    wrapper = json.loads(path.read_text(encoding="utf-8-sig"))
    recording = wrapper.get("recording") if isinstance(wrapper.get("recording"), dict) else wrapper
    requests_found = []
    success_response_count = 0
    for entry in recording.get("network", []):
        if not isinstance(entry, dict) or SUBMIT_TEMP_ENDPOINT not in str(entry.get("url") or ""):
            continue
        body = str(entry.get("request_body") or entry.get("post_data") or "")
        if body:
            try:
                requests_found.append({"entry": entry, "payload": json.loads(body)})
            except json.JSONDecodeError:
                pass
        response_body = str(entry.get("response_body") or "")
        if response_body:
            try:
                response = json.loads(response_body)
                if _base_resp_status_code(response) == 0:
                    success_response_count += 1
            except json.JSONDecodeError:
                pass
    if not requests_found:
        raise TaskCapabilityError("录制包中没有可解析的 SubmitTempItemAnswer 请求。", ["missing-submit-temp-request"])
    latest = requests_found[-1]
    payload = latest["payload"]
    answer = payload["AuditAnswers"][0]
    content = json.loads(answer["Content"])
    return {
        "path": path,
        "payload": payload,
        "content": content,
        "request_count": len(requests_found),
        "success_response_count": success_response_count,
        "account_user_id": _find_account_user_id(recording, payload),
        "referer": str(latest["entry"].get("request_headers", {}).get("Referer") or recording.get("page_url") or "https://aidp.juejin.cn/operation/task-v2?page=1"),
    }


def _fetch_live_question_context_from_mget_answer_list(item: TaskCatalogItem, learned: dict[str, Any], account_user_id: str) -> dict[str, Any]:
    account_id = account_user_id or learned["account_user_id"] or item.source_account_user_id or get_settings().task_source_account_user_id
    account = _select_account(account_id)
    cookie = str(account.get("cookie") or "") if account else ""
    if not account:
        raise TaskCapabilityError("找不到可用于只读取题的账号。", ["missing-account"])
    if not cookie:
        raise TaskCapabilityError("账号缺少 Cookie，不能只读取题。", ["missing-cookie"])
    identity = _identity_from_payload(learned["payload"])
    referer = learned["referer"]
    response = _post_mget_answer_list(item.task_id, identity.NodeID, identity.ItemID, cookie, referer)
    if not response.get("ok"):
        raise TaskCapabilityError("MGetAnswerList 只读取题失败。", ["mget-answer-list-failed"])
    data = response.get("data")
    if _base_resp_status_code(data) not in {None, 0}:
        raise TaskCapabilityError("MGetAnswerList 返回非成功 BaseResp。", ["mget-answer-list-base-resp-failed"])
    answer = _find_answer_content(data, preferred_item_id=identity.ItemID)
    if not answer:
        raise TaskCapabilityError("MGetAnswerList 响应中没有可解析题面 Content。", ["missing-live-answer-content"])
    content = answer["content"]
    payload_identity = TaskCapabilityIdentity(
        TaskID=item.task_id,
        NodeID=identity.NodeID,
        ItemID=str(answer.get("ItemID") or content.get("itemID") or identity.ItemID),
        StagingTime=identity.StagingTime,
    )
    return {
        "content": content,
        "identity": payload_identity,
        "evidence_path": f"{MGET_ANSWER_LIST_ENDPOINT} status={response.get('status_code')} item={payload_identity.ItemID}",
    }


def _post_mget_answer_list(task_id: str, node_id: str, item_id: str, cookie: str, referer: str) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    payload = {
        "TaskID": str(task_id or ""),
        "NodeID": str(node_id or ""),
        "ItemIDs": [str(item_id or "")] if item_id else [],
    }
    try:
        response = requests.post(
            f"https://aidp.juejin.cn{MGET_ANSWER_LIST_ENDPOINT}",
            json=payload,
            headers={
                "Cookie": cookie,
                "Referer": referer,
                "Origin": "https://aidp.juejin.cn",
                "Content-Type": "application/json",
                "User-Agent": "aidp-monitor-next/8789-http-question-context",
            },
            timeout=20,
        )
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:1000]}
        return {
            "ok": response.ok,
            "status_code": response.status_code,
            "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "data": data,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status_code": None,
            "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "error": str(exc),
            "data": None,
        }


def _find_answer_content(payload: Any, preferred_item_id: str = "") -> Optional[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            content_text = value.get("Content") or value.get("content")
            if isinstance(content_text, str) and content_text.strip():
                try:
                    content = json.loads(content_text)
                except json.JSONDecodeError:
                    content = None
                if isinstance(content, dict) and isinstance(content.get("item"), dict):
                    candidates.append({"ItemID": value.get("ItemID") or value.get("itemID") or content.get("itemID"), "content": content})
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    if preferred_item_id:
        match = next((item for item in candidates if str(item.get("ItemID") or "") == str(preferred_item_id)), None)
        if match:
            return match
    return candidates[-1] if candidates else None


def _identity_from_payload(payload: dict[str, Any]) -> TaskCapabilityIdentity:
    answer = payload["AuditAnswers"][0]
    return TaskCapabilityIdentity(
        TaskID=str(payload.get("TaskID") or ""),
        NodeID=str(payload.get("NodeID") or ""),
        ItemID=str(answer.get("ItemID") or ""),
        StagingTime=str(payload.get("StagingTime") or ""),
    )


def _build_field_mappings(content: dict[str, Any]) -> list[TaskCapabilityFieldMapping]:
    data = content.get("data") if isinstance(content.get("data"), dict) else {}
    data_map = content.get("dataMap") if isinstance(content.get("dataMap"), dict) else {}
    mappings = []
    for field in ALLOWED_DRAFT_FIELDS:
        if field in data or field in data_map:
            mappings.append(
                TaskCapabilityFieldMapping(
                    field=field,
                    path=f"Content.data.{field}",
                    role=FIELD_ROLES.get(field, "草稿字段"),
                    current_value=data.get(field),
                    mirrored_in_data_map=field in data_map,
                )
            )
    return mappings


def _build_ai_input_requirements(content: dict[str, Any]) -> list[str]:
    item = content.get("item") if isinstance(content.get("item"), dict) else {}
    requirements = []
    if item.get("html"):
        requirements.append("网页 HTML/在线预览 URL")
    if item.get("image"):
        requirements.append("最终截图")
    if item.get("mediaUrls"):
        requirements.append("前后视频或录屏素材")
    if item.get("imageScoreGuide"):
        requirements.append("截图评分规则")
    if item.get("videoGuideline") or item.get("scoringGuidelines"):
        requirements.append("视频/动效评分规则")
    return requirements or ["待补充 AI 输入材料"]


def _build_ai_input_materials(content: dict[str, Any]) -> list[str]:
    item = content.get("item") if isinstance(content.get("item"), dict) else {}
    materials = []
    if item.get("html"):
        materials.append(f"网页URL：{item['html']}")
    if item.get("image"):
        materials.append(f"最终截图：{item['image']}")
    media_urls = item.get("mediaUrls") if isinstance(item.get("mediaUrls"), list) else []
    for index, url in enumerate(media_urls, start=1):
        materials.append(f"视频/录屏{index}：{url}")
    if item.get("imageScoreGuide"):
        materials.append(f"截图评分说明：{item['imageScoreGuide']}")
    if item.get("videoGuideline"):
        materials.append(f"视频评分说明：{item['videoGuideline']}")
    if item.get("scoringGuidelines"):
        materials.append(f"综合评分说明：{item['scoringGuidelines']}")
    return materials or ["待从题面/录制包补充 AI 输入材料"]


def _build_question_material_resources(content: dict[str, Any]) -> list[TaskQuestionMaterialResource]:
    item = content.get("item") if isinstance(content.get("item"), dict) else {}
    resources: list[TaskQuestionMaterialResource] = []
    html_url = str(item.get("html") or "").strip()
    if html_url:
        resources.append(
            TaskQuestionMaterialResource(
                key="web_page",
                title="题目网页",
                material_type="url",
                url=html_url,
                purpose="沙箱浏览器打开该 URL，枚举并点击可交互元素，判断跳转、交互和动效。",
            )
        )
    image_url = str(item.get("image") or "").strip()
    if image_url:
        resources.append(
            TaskQuestionMaterialResource(
                key="final_screenshot",
                title="左侧截图/最终截图",
                material_type="image",
                url=image_url,
                purpose="视觉判断图片是否完整、排版正常、无乱码乱版，映射第一项评分。",
            )
        )
    media_urls = item.get("mediaUrls") if isinstance(item.get("mediaUrls"), list) else []
    for index, url in enumerate(media_urls, start=1):
        text = str(url or "").strip()
        if not text:
            continue
        resources.append(
            TaskQuestionMaterialResource(
                key=f"motion_media_{index}",
                title=f"产物视频 {index}",
                material_type="video",
                url=text,
                purpose="视频采样或多模态判断是否出现与沙箱网页操作一致的跳转、交互或动效。",
            )
        )
    return resources


def _build_question_decision_pipeline(content: dict[str, Any], include_live_read: bool = False) -> list[TaskQuestionDecisionStep]:
    resources = {resource.key for resource in _build_question_material_resources(content)}
    steps = [
        TaskQuestionDecisionStep(
            key="http_material_context",
            title="HTTP 题面上下文",
            executor="backend-http",
            input_keys=["recorded_submit_temp_payload"],
            output_keys=["identity", "material_resources", "current_answer_data"],
            status="ready",
        ),
        TaskQuestionDecisionStep(
            key="image_quality_check",
            title="图片完整性和排版判断",
            executor="vision",
            input_keys=["final_screenshot"] if "final_screenshot" in resources else [],
            output_keys=["beauty_score", "beauty_reason"],
            status="blocked" if "final_screenshot" not in resources else "planned",
        ),
        TaskQuestionDecisionStep(
            key="sandbox_web_interaction",
            title="沙箱网页交互判断",
            executor="isolated-browser-cdp",
            input_keys=["web_page"] if "web_page" in resources else [],
            output_keys=["motion_richness_score", "high_richness_reason", "richness_reason", "interaction_trace"],
            status="blocked" if "web_page" not in resources else "planned",
        ),
        TaskQuestionDecisionStep(
            key="video_consistency_check",
            title="产物视频一致性判断",
            executor="video-frame-sampler-or-multimodal",
            input_keys=sorted(key for key in resources if key.startswith("motion_media_")),
            output_keys=["sceneConsistencyScore", "sceneConsistencyRemarks"],
            status="blocked" if not any(key.startswith("motion_media_") for key in resources) else "planned",
        ),
        TaskQuestionDecisionStep(
            key="http_draft_dry_run",
            title="HTTP 草稿 dry-run",
            executor="backend-http",
            input_keys=["structured_scores", "payload_identity"],
            output_keys=["payload_preview", "field_diff"],
            status="planned",
        ),
    ]
    if include_live_read:
        steps.insert(
            0,
            TaskQuestionDecisionStep(
                key="readonly-live-mget-answer-list",
                title="MGetAnswerList 实时只读取题",
                executor="backend-http",
                input_keys=["TaskID", "NodeID", "ItemID", "account_cookie"],
                output_keys=["live_answer_content", "material_resources", "current_answer_data"],
                status="ready",
            ),
        )
    return steps


def _build_question_iteration_candidates() -> list[TaskQuestionIterationCandidate]:
    return [
        TaskQuestionIterationCandidate(
            key="http-receive-live-fetch",
            title="接入 Receive/MGetAnswerList 实时取题",
            value="把当前录制 payload 来源替换为只读 HTTP 拉取题面和当前 ItemID，彻底摆脱 AIDP UI 打开动作。",
            risk="需要确认接口入参、Cookie/CSRF 头和响应字段，必须禁止 SubmitTempItemAnswer 以外的写接口。",
        ),
        TaskQuestionIterationCandidate(
            key="sandbox-click-classifier",
            title="沙箱点击分类器",
            value="只加载题目网页 URL，枚举 button/link/input/role 元素，记录 URL 变化、DOM 变化和动画信号。",
            risk="题目网页可能有外链、下载、弹窗或无限动画，需要限制点击数量、超时和网络域名。",
        ),
        TaskQuestionIterationCandidate(
            key="media-vision-sampler",
            title="图片/视频采样判断",
            value="图片先做可访问、尺寸、白屏基础检测；视频抽关键帧和时序摘要，再交给多模态模型评分。",
            risk="仅规则检测不能可靠识别乱码、排版乱和视频中是否复现同一操作。",
        ),
        TaskQuestionIterationCandidate(
            key="controlled-http-draft-write",
            title="结构化结果受控暂存",
            value="沙箱/视觉输出映射到 Content.data/dataMap，继续沿用三重闸门保存草稿。",
            risk="ItemID 不匹配或用户已切题时必须阻断，仍禁止提交、继续、放弃、领取。",
        ),
    ]


class _ClickableHtmlParser(HTMLParser):
    def __init__(self, max_candidates: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_candidates = max_candidates
        self.candidates: list[dict[str, Any]] = []
        self._stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        candidate = self._candidate_from_attrs(tag.lower(), attr_map)
        self._stack.append(candidate or {"candidate": False})

    def handle_data(self, data: str) -> None:
        text = " ".join(str(data or "").split())
        if not text:
            return
        for item in reversed(self._stack):
            if item.get("candidate"):
                item["text"] = str(item.get("text") or "") + text[:120]
                break

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        item = self._stack.pop()
        if item.get("candidate") and len(self.candidates) < self.max_candidates:
            self.candidates.append(item)

    def _candidate_from_attrs(self, tag: str, attrs: dict[str, str]) -> Optional[dict[str, Any]]:
        reason = _clickable_reason(tag, attrs)
        if not reason:
            return None
        return {
            "candidate": True,
            "selector": _selector_for_candidate(tag, attrs),
            "tag": tag,
            "text": attrs.get("aria-label") or attrs.get("title") or attrs.get("value") or "",
            "reason": reason,
            "href": attrs.get("href", ""),
            "risk": _candidate_risk(tag, attrs),
        }


def _extract_click_candidates(html_snapshot: str, max_candidates: int = 20) -> list[TaskSandboxClickCandidate]:
    parser = _ClickableHtmlParser(max_candidates=max_candidates)
    parser.feed(html_snapshot[:500_000])
    seen: set[str] = set()
    result: list[TaskSandboxClickCandidate] = []
    for item in parser.candidates:
        selector = str(item.get("selector") or "")
        if not selector or selector in seen:
            continue
        seen.add(selector)
        result.append(
            TaskSandboxClickCandidate(
                selector=selector,
                tag=str(item.get("tag") or ""),
                text=str(item.get("text") or "")[:160],
                reason=str(item.get("reason") or ""),
                href=str(item.get("href") or ""),
                risk=str(item.get("risk") or "low"),
            )
        )
    return result


def _clickable_reason(tag: str, attrs: dict[str, str]) -> str:
    role = attrs.get("role", "").lower()
    input_type = attrs.get("type", "").lower()
    if tag == "a" and attrs.get("href"):
        return "href"
    if tag == "button":
        return "button"
    if tag == "input" and input_type in {"button", "submit", "reset", "image"}:
        return f"input:{input_type}"
    if role in {"button", "link", "menuitem", "tab", "switch"}:
        return f"role={role}"
    if attrs.get("onclick"):
        return "onclick"
    if attrs.get("tabindex") in {"0", "1"}:
        return "tabindex"
    return ""


def _selector_for_candidate(tag: str, attrs: dict[str, str]) -> str:
    element_id = attrs.get("id", "").strip()
    if element_id:
        return "#" + element_id.replace("'", "\\'")
    test_id = attrs.get("data-testid") or attrs.get("data-test-id")
    if test_id:
        return f"[data-testid='{test_id}']"
    aria = attrs.get("aria-label", "").strip()
    if aria:
        return f"{tag}[aria-label='{aria[:80]}']"
    href = attrs.get("href", "").strip()
    if tag == "a" and href:
        return f"a[href='{href[:120]}']"
    role = attrs.get("role", "").strip()
    if role:
        return f"{tag}[role='{role}']"
    return tag


def _candidate_risk(tag: str, attrs: dict[str, str]) -> str:
    href = attrs.get("href", "").lower()
    if href.startswith(("http://", "https://")):
        return "external-navigation"
    if tag == "input" and attrs.get("type", "").lower() == "submit":
        return "form-submit"
    return "low"


def _normalize_click_selectors(selectors: list[str], max_clicks: int) -> list[str]:
    limit = max(1, min(int(max_clicks or 3), 10))
    normalized: list[str] = []
    seen: set[str] = set()
    for selector in selectors or []:
        text = str(selector or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text[:200])
        if len(normalized) >= limit:
            break
    return normalized


def _normalize_allowed_domains(html_url: str, allowed_domains: list[str]) -> list[str]:
    domains = [str(item or "").strip().lower() for item in allowed_domains or [] if str(item or "").strip()]
    if not domains:
        parsed = urlparse(html_url)
        if parsed.hostname:
            domains.append(parsed.hostname.lower())
    seen: set[str] = set()
    result: list[str] = []
    for domain in domains:
        safe = domain.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
        if safe and safe not in seen:
            seen.add(safe)
            result.append(safe)
    return result[:5]


def _sandbox_helper_endpoint() -> str:
    settings = get_settings()
    base = (settings.host_launcher_internal_url or settings.host_launcher_url).rstrip("/")
    return f"{base}/api/sandbox-click-execute"


def _video_keyframe_helper_endpoint() -> str:
    settings = get_settings()
    base = (settings.host_launcher_internal_url or settings.host_launcher_url).rstrip("/")
    return f"{base}/api/video-keyframe-extract"


def _execute_sandbox_clicks_via_helper(
    *,
    html_url: str,
    selectors: list[str],
    allowed_domains: list[str],
    max_clicks: int,
    timeout_ms: int,
    helper_endpoint: str,
) -> dict[str, Any]:
    response = requests.post(
        helper_endpoint,
        json={
            "html_url": html_url,
            "selectors": selectors,
            "allowed_domains": allowed_domains,
            "max_clicks": max_clicks,
            "timeout_ms": timeout_ms,
        },
        timeout=max(10, int(timeout_ms / 1000) + 10),
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise TaskCapabilityError("本机 helper 返回格式不是 JSON 对象。", ["invalid-sandbox-helper-response"])
    return data


def _extract_video_keyframes_via_helper(
    *,
    resources: list[TaskMediaResource],
    max_frames_per_video: int,
    timeout_ms: int,
    helper_endpoint: str,
) -> dict[str, Any]:
    response = requests.post(
        helper_endpoint,
        json={
            "video_resources": [resource.model_dump() for resource in resources],
            "max_frames_per_video": max_frames_per_video,
            "timeout_ms": timeout_ms,
        },
        timeout=max(15, int(timeout_ms / 1000) * len(resources) + 15),
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise TaskCapabilityError("本机 helper 返回格式不是 JSON 对象。", ["invalid-video-keyframe-helper-response"])
    return data


def _video_keyframe_result_from_helper(item: dict[str, Any]) -> TaskVideoKeyframeExtractionResult:
    frames = []
    raw_frames = item.get("keyframes") if isinstance(item.get("keyframes"), list) else []
    for frame in raw_frames:
        if not isinstance(frame, dict):
            continue
        frames.append(
            TaskVideoKeyframe(
                index=_safe_int(frame.get("index")),
                timestamp_sec=float(frame.get("timestampSec") or frame.get("timestamp_sec") or 0),
                data_url=str(frame.get("dataUrl") or frame.get("data_url") or ""),
                width=_safe_int_or_none(frame.get("width")),
                height=_safe_int_or_none(frame.get("height")),
                mime_type=str(frame.get("mimeType") or frame.get("mime_type") or "image/jpeg"),
            )
        )
    return TaskVideoKeyframeExtractionResult(
        resource_key=str(item.get("resourceKey") or item.get("resource_key") or ""),
        url=str(item.get("url") or ""),
        status=str(item.get("status") or ""),
        keyframes=frames,
        error=str(item.get("error") or ""),
    )


def _archive_video_keyframes(*, item_id: int, results: list[TaskVideoKeyframeExtractionResult]) -> tuple[str, int]:
    root = Path(get_settings().operation_recording_root).parent / "task-capabilities" / "keyframes"
    archive_dir = root / f"task-{item_id}-{uuid4().hex[:8]}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = 0
    manifest_results: list[dict[str, Any]] = []
    for result in results:
        frame_entries: list[dict[str, Any]] = []
        safe_key = _safe_artifact_name(result.resource_key or "video")
        for frame in result.keyframes:
            payload = _decode_data_url(frame.data_url)
            if payload is None:
                frame_entries.append(
                    {
                        "index": frame.index,
                        "timestamp_sec": frame.timestamp_sec,
                        "status": "skipped-invalid-data-url",
                    }
                )
                continue
            suffix = ".jpg" if "jpeg" in frame.mime_type.lower() or "jpg" in frame.mime_type.lower() else ".bin"
            frame_path = archive_dir / f"{safe_key}-frame-{frame.index:03d}{suffix}"
            frame_path.write_bytes(payload)
            frame.artifact_path = str(frame_path)
            frame.preview_url = frame.data_url
            archived += 1
            frame_entries.append(
                {
                    "index": frame.index,
                    "timestamp_sec": frame.timestamp_sec,
                    "artifact_path": frame.artifact_path,
                    "width": frame.width,
                    "height": frame.height,
                    "mime_type": frame.mime_type,
                }
            )
        manifest_results.append(
            {
                "resource_key": result.resource_key,
                "url": result.url,
                "status": result.status,
                "frames": frame_entries,
                "error": result.error,
            }
        )
    manifest_path = archive_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "mode": "video_keyframe_archive",
                "task_catalog_item_id": item_id,
                "writes_remote": False,
                "claims_visual_judgement": False,
                "archived_frame_count": archived,
                "results": manifest_results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return str(manifest_path), archived


def _load_cached_video_keyframes(
    *,
    item_id: int,
    resources: list[TaskMediaResource],
    min_frames_per_video: int,
    cache_manifest_path: str = "",
) -> Optional[tuple[list[TaskVideoKeyframeExtractionResult], str, int]]:
    manifest_path = _resolve_keyframe_manifest_path(item_id=item_id, cache_manifest_path=cache_manifest_path)
    if manifest_path is None or not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict) or int(manifest.get("task_catalog_item_id") or 0) != item_id:
        return None
    results_raw = manifest.get("results") if isinstance(manifest.get("results"), list) else []
    by_key = {str(item.get("resource_key") or ""): item for item in results_raw if isinstance(item, dict)}
    cached_results: list[TaskVideoKeyframeExtractionResult] = []
    total_frames = 0
    for resource in resources:
        raw = by_key.get(resource.key)
        if not isinstance(raw, dict) or str(raw.get("url") or "") != str(resource.url or ""):
            return None
        frame_entries = raw.get("frames") if isinstance(raw.get("frames"), list) else []
        frames: list[TaskVideoKeyframe] = []
        for frame in frame_entries:
            if not isinstance(frame, dict) or not str(frame.get("artifact_path") or "").strip():
                continue
            artifact_path = _resolve_artifact_path(str(frame.get("artifact_path") or ""))
            if not artifact_path.exists():
                continue
            mime_type = str(frame.get("mime_type") or "image/jpeg")
            data_url = _artifact_to_data_url(artifact_path, mime_type)
            frames.append(
                TaskVideoKeyframe(
                    index=_safe_int(frame.get("index")),
                    timestamp_sec=float(frame.get("timestamp_sec") or 0),
                    data_url=data_url,
                    width=_safe_int_or_none(frame.get("width")),
                    height=_safe_int_or_none(frame.get("height")),
                    mime_type=mime_type,
                    artifact_path=str(artifact_path),
                    preview_url=data_url,
                )
            )
        frames.sort(key=lambda item: item.index)
        if len(frames) < min_frames_per_video:
            return None
        selected = frames[:min_frames_per_video]
        total_frames += len(selected)
        cached_results.append(
            TaskVideoKeyframeExtractionResult(
                resource_key=resource.key,
                url=resource.url,
                status=str(raw.get("status") or "ok"),
                keyframes=selected,
                error=str(raw.get("error") or ""),
            )
        )
    return cached_results, str(manifest_path), total_frames


def _resolve_keyframe_manifest_path(*, item_id: int, cache_manifest_path: str = "") -> Optional[Path]:
    if cache_manifest_path:
        path = _resolve_artifact_path(cache_manifest_path)
        return path if path.name == "manifest.json" else None
    root = Path(get_settings().operation_recording_root).parent / "task-capabilities" / "keyframes"
    if not root.exists():
        return None
    candidates = sorted(root.glob(f"task-{item_id}-*/manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _resolve_artifact_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path(get_settings().operation_recording_root).parent.parent / path


def _artifact_to_data_url(path: Path, mime_type: str) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type or 'image/jpeg'};base64,{encoded}"


def _decode_data_url(value: str) -> Optional[bytes]:
    if not value.startswith("data:") or "," not in value:
        return None
    header, encoded = value.split(",", 1)
    if ";base64" not in header:
        return None
    try:
        padded = encoded + ("=" * ((4 - len(encoded) % 4) % 4))
        return base64.b64decode(padded, validate=False)
    except (ValueError, TypeError):
        return None


def _safe_artifact_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in value.strip())
    return cleaned.strip("-") or "video"


def _sandbox_execution_result_from_helper(item: dict[str, Any]) -> TaskSandboxClickExecutionResult:
    return TaskSandboxClickExecutionResult(
        selector=str(item.get("selector") or ""),
        status=str(item.get("status") or ""),
        before_url=str(item.get("beforeUrl") or item.get("before_url") or ""),
        after_url=str(item.get("afterUrl") or item.get("after_url") or ""),
        url_changed=bool(item.get("urlChanged") if "urlChanged" in item else item.get("url_changed")),
        dom_changed=bool(item.get("domChanged") if "domChanged" in item else item.get("dom_changed")),
        popup_detected=bool(item.get("popupDetected") if "popupDetected" in item else item.get("popup_detected")),
        animation_detected=bool(item.get("animationDetected") if "animationDetected" in item else item.get("animation_detected")),
        interaction_detected=bool(item.get("interactionDetected") if "interactionDetected" in item else item.get("interaction_detected")),
        evidence=str(item.get("evidence") or ""),
        error=str(item.get("error") or ""),
    )


def _sandbox_interaction_summary_from_helper(summary: Any, results: list[TaskSandboxClickExecutionResult]) -> TaskSandboxClickInteractionSummary:
    if isinstance(summary, dict):
        return TaskSandboxClickInteractionSummary(
            has_navigation=bool(summary.get("hasNavigation") if "hasNavigation" in summary else summary.get("has_navigation")),
            has_dom_interaction=bool(summary.get("hasDomInteraction") if "hasDomInteraction" in summary else summary.get("has_dom_interaction")),
            has_popup=bool(summary.get("hasPopup") if "hasPopup" in summary else summary.get("has_popup")),
            has_animation=bool(summary.get("hasAnimation") if "hasAnimation" in summary else summary.get("has_animation")),
            clicked_count=_safe_int(summary.get("clickedCount") or summary.get("clicked_count")),
        )
    return TaskSandboxClickInteractionSummary(
        has_navigation=any(item.url_changed for item in results),
        has_dom_interaction=any(item.dom_changed for item in results),
        has_popup=any(item.popup_detected for item in results),
        has_animation=any(item.animation_detected for item in results),
        clicked_count=sum(1 for item in results if item.status == "clicked"),
    )


def _map_sandbox_clicks_to_answer_data(
    click_results: list[TaskSandboxClickExecutionResult],
    summary: TaskSandboxClickInteractionSummary,
) -> dict[str, Any]:
    has_navigation = bool(summary.has_navigation or any(item.url_changed for item in click_results))
    has_dom_interaction = bool(summary.has_dom_interaction or any(item.dom_changed or item.popup_detected or item.interaction_detected for item in click_results))
    has_animation = bool(summary.has_animation or any(item.animation_detected for item in click_results))
    high_reasons: list[str] = []
    evidence_parts: list[str] = []
    if has_navigation:
        high_reasons.append("有明显的交互跳转")
        evidence_parts.append("点击候选元素后观察到 URL 跳转")
    if has_dom_interaction:
        evidence_parts.append("点击后页面 DOM、弹窗或状态发生变化")
    if has_animation:
        high_reasons.append("有明显视觉动效")
        evidence_parts.append("点击后观察到动画或 transition 信号")
    if has_navigation or has_dom_interaction or has_animation:
        score = "2"
        reason = "；".join(evidence_parts) + "，符合网页存在跳转、交互或动效的 2 分条件。"
    else:
        score = "1"
        reason = "沙箱已加载真实网页并点击候选元素，但未观察到跳转、交互或动效，按真实网页但无明显变化给 1 分。"
    return {
        "motion_richness_score": score,
        "richness_reason": reason,
        "high_richness_reason": high_reasons,
        "checkRemark": "沙箱点击结果 dry-run：仅用于草稿，仍需人工复核图片和视频。",
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_safe_question_resource_url(url: str) -> bool:
    text = str(url or "").lower()
    return bool(text.startswith(("http://", "https://")) and "aidp.juejin.cn" not in text)


def _fetch_question_html_snapshot(url: str) -> dict[str, str]:
    response = requests.get(url, timeout=15, headers={"User-Agent": "aidp-monitor-next/8789-sandbox-click-plan"})
    response.raise_for_status()
    return {"html": response.text[:500_000]}


def _build_media_resources_from_request(request: TaskMediaInspectionPlanRequest) -> list[TaskMediaResource]:
    resources: list[TaskMediaResource] = []
    image_url = str(request.image_url or "").strip()
    if image_url:
        resources.append(
            TaskMediaResource(
                key="final_screenshot",
                title="左图/最终截图",
                material_type="image",
                url=image_url,
                expected_output=["image_accessible", "layout_normal", "mojibake_or_broken_layout", "beauty_score_signal"],
            )
        )
    for index, url in enumerate(request.video_urls or [], start=1):
        text = str(url or "").strip()
        if not text:
            continue
        resources.append(
            TaskMediaResource(
                key=f"motion_media_{index}",
                title=f"产物视频 {index}",
                material_type="video",
                url=text,
                expected_output=["video_accessible", "keyframe_summary", "action_matches_sandbox_trace", "scene_consistency_signal"],
            )
        )
    return resources


def _build_media_inspection_steps(resources: list[TaskMediaResource]) -> list[TaskMediaInspectionStep]:
    resource_keys = {item.key for item in resources}
    video_keys = sorted(key for key in resource_keys if key.startswith("motion_media_"))
    return [
        TaskMediaInspectionStep(
            key="basic-media-access-probe",
            title="媒体可访问性基础探测",
            executor="http-head-or-range",
            input_keys=sorted(resource_keys),
            output_keys=["http_status", "content_type", "content_length"],
            status="planned" if resources else "blocked",
            detail="只用于发现无法访问、白屏候选或明显资源异常；不能替代视觉判断。",
        ),
        TaskMediaInspectionStep(
            key="multimodal-image-layout-check",
            title="图片排版/乱码多模态判断",
            executor="multimodal-vision",
            input_keys=["final_screenshot"] if "final_screenshot" in resource_keys else [],
            output_keys=["beauty_score", "image_reason"],
            status="planned" if "final_screenshot" in resource_keys else "blocked",
            detail="判断左图是否完好、排版正常、是否乱码或乱版；输出 0/2 分信号和理由。",
        ),
        TaskMediaInspectionStep(
            key="video-keyframe-sampling",
            title="视频关键帧采样",
            executor="video-frame-sampler",
            input_keys=video_keys,
            output_keys=["keyframes", "timeline_summary"],
            status="planned" if video_keys else "blocked",
            detail="抽取产物视频关键帧，供多模态模型判断是否复现网页点击操作。",
        ),
        TaskMediaInspectionStep(
            key="multimodal-video-action-match",
            title="视频操作复现判断",
            executor="multimodal-video",
            input_keys=["interaction_trace", *video_keys],
            output_keys=["sceneConsistencyScore", "sceneConsistencyRemarks"],
            status="planned" if video_keys else "blocked",
            detail="比较沙箱点击轨迹与视频内容；两个视频都匹配时仍需应用不允许两个都给 2 分的业务规则。",
        ),
    ]


def _probe_media_resource(resource: TaskMediaResource, *, max_bytes: int) -> TaskMediaProbeResult:
    url = str(resource.url or "").strip()
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "aidp-monitor-next/8789-media-probe", "Range": f"bytes=0-{max_bytes - 1}"},
            timeout=20,
        )
        response.raise_for_status()
        content = bytes(response.content or b"")[:max_bytes]
        content_type = str(response.headers.get("content-type") or "").split(";")[0].strip().lower()
        content_length = _safe_int_or_none(response.headers.get("content-length"))
        width, height = _detect_image_dimensions(content, content_type)
        return TaskMediaProbeResult(
            key=resource.key,
            title=resource.title,
            material_type=resource.material_type,
            url=url,
            ok=True,
            status_code=int(response.status_code),
            content_type=content_type,
            content_length=content_length,
            fetched_bytes=len(content),
            width=width,
            height=height,
        )
    except Exception as exc:  # noqa: BLE001 - probe errors should be explicit and non-fatal.
        return TaskMediaProbeResult(
            key=resource.key,
            title=resource.title,
            material_type=resource.material_type,
            url=url,
            ok=False,
            error=str(exc),
        )


def _map_media_judgements_to_answer_data(request: TaskMediaInspectionDraftRequest) -> dict[str, Any]:
    image = request.image_judgement
    image_reason = str(image.reason or "").strip()
    if image.mojibake_or_broken_layout:
        beauty_score = "0"
        image_summary = image_reason or "左图存在乱码、破损或排版异常，按规则给 0 分。"
    elif image.layout_normal:
        beauty_score = "2"
        image_summary = image_reason or "左图完好且排版正常，按规则给 2 分。"
    else:
        beauty_score = "1"
        image_summary = image_reason or "左图未确认破损，但也缺少完好排版的明确证据，保守给 1 分并待复核。"
    video_scores, video_remarks = _map_video_keyframes_to_scene_scores(request.video_keyframe_judgements)
    video_summaries = [remark for remark in video_remarks.values() if remark]
    richness_reason = image_summary
    if video_summaries:
        richness_reason += "；" + "；".join(video_summaries)
    return {
        "beauty_score": beauty_score,
        "richness_reason": richness_reason,
        "sceneConsistencyScore": video_scores,
        "sceneConsistencyRemarks": video_remarks,
        "checkRemark": "媒体多模态/关键帧 dry-run：仅用于草稿，仍需人工复核后再决定。",
    }


def _map_video_keyframes_to_scene_scores(judgements: list[Any]) -> tuple[dict[str, str], dict[str, str]]:
    normalized = list(judgements or [])[:2]
    matched = [item for item in normalized if _video_judgement_is_high_confidence_match(item)]
    scores = {"product1": "0", "product2": "0"}
    remarks = {"product1": "未提供产物一关键帧判断。", "product2": "未提供产物二关键帧判断。"}
    for index, item in enumerate(normalized, start=1):
        key = f"product{index}"
        matched_this = _video_judgement_is_high_confidence_match(item)
        low_confidence_match = bool(item.action_visible and item.matches_sandbox_trace and not matched_this)
        scores[key] = "2" if matched_this else ("1" if low_confidence_match else "0")
        reason = str(item.reason or item.keyframe_summary or "").strip()
        if matched_this:
            remarks[key] = _append_video_vote_review_note(item, reason or f"产物{index}关键帧可见并复现沙箱操作。")
        elif low_confidence_match:
            remarks[key] = _append_video_vote_review_note(item, reason or f"产物{index}部分关键帧显示操作反馈，但支持帧不足，保守给 1 分并待复核。")
        elif item.action_visible:
            remarks[key] = _append_video_vote_review_note(item, reason or f"产物{index}关键帧可见操作，但未确认与沙箱轨迹一致。")
        else:
            remarks[key] = _append_video_vote_review_note(item, reason or f"产物{index}关键帧未看到与操作一致的过程。")
    if len(matched) >= 2:
        scores["product1"] = "2"
        scores["product2"] = "1"
        remarks["product2"] = remarks["product2"] + "；两个产物都匹配时按业务规则不允许两个都给 2 分，本次 dry-run 将产物二降为 1 分。"
    return scores, remarks


def _video_judgement_is_high_confidence_match(item: Any) -> bool:
    if not bool(item.action_visible and item.matches_sandbox_trace):
        return False
    confidence = str(getattr(item, "confidence", "") or "").strip().lower()
    if bool(getattr(item, "review_required", False)) or confidence == "low":
        return False
    total = _safe_int(getattr(item, "total_frame_count", 0))
    supporting = _safe_int(getattr(item, "supporting_frame_count", 0))
    if total > 0 and supporting > 0:
        return supporting * 2 > total
    return True


def _append_video_vote_review_note(item: Any, reason: str) -> str:
    total = _safe_int(getattr(item, "total_frame_count", 0))
    supporting = _safe_int(getattr(item, "supporting_frame_count", 0))
    confidence = str(getattr(item, "confidence", "") or "unknown").strip() or "unknown"
    review_hint = str(getattr(item, "review_hint", "") or "").strip()
    parts = [reason]
    if total:
        parts.append(f"多帧投票：{supporting}/{total} 帧支持，置信度 {confidence}。")
    if bool(getattr(item, "review_required", False)):
        parts.append(review_hint or "低置信结果需要人工复核。")
    return "；".join(part for part in parts if part)


def _detect_image_dimensions(content: bytes, content_type: str) -> tuple[Optional[int], Optional[int]]:
    if content_type == "image/png" or content.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(content) >= 24 and content[12:16] == b"IHDR":
            return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    if content_type in {"image/jpeg", "image/jpg"} or content.startswith(b"\xff\xd8"):
        return _detect_jpeg_dimensions(content)
    return None, None


def _detect_jpeg_dimensions(content: bytes) -> tuple[Optional[int], Optional[int]]:
    index = 2
    while index + 9 < len(content):
        if content[index] != 0xFF:
            index += 1
            continue
        marker = content[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(content):
            break
        segment_length = int.from_bytes(content[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(content):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and segment_length >= 7:
            height = int.from_bytes(content[index + 3:index + 5], "big")
            width = int.from_bytes(content[index + 5:index + 7], "big")
            return width, height
        index += segment_length
    return None, None


def _safe_int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_ai_input_spec(content: dict[str, Any]) -> list[TaskCapabilityInputSpec]:
    item = content.get("item") if isinstance(content.get("item"), dict) else {}
    specs = [
        TaskCapabilityInputSpec(
            key="web_page",
            title="网页/在线预览",
            material_type="url",
            source=str(item.get("html") or ""),
            required=bool(item.get("html")),
            usage="用于判断页面主体、布局完整性、最终状态是否与截图一致。",
            review_check="链接可打开，页面主体与题面任务一致；打不开时必须转人工复核。",
        ),
        TaskCapabilityInputSpec(
            key="final_screenshot",
            title="最终截图",
            material_type="image",
            source=str(item.get("image") or ""),
            required=bool(item.get("image")),
            usage="用于美观度评分，重点看主体完整、布局清晰、无明显错位或空白。",
            review_check="截图应能独立支撑 beauty_score；若模糊、缺失或与网页不一致，不能自动采信。",
        ),
        TaskCapabilityInputSpec(
            key="motion_media",
            title="视频/录屏素材",
            material_type="video",
            source="；".join(str(url) for url in (item.get("mediaUrls") if isinstance(item.get("mediaUrls"), list) else [])),
            required=bool(item.get("mediaUrls")),
            usage="用于动效丰富度评分，重点看是否有明确交互、跳转、动效或状态变化。",
            review_check="至少能看到有效前后状态；素材缺失时 motion_richness_score 只能保守给分。",
        ),
        TaskCapabilityInputSpec(
            key="score_guideline",
            title="评分规则",
            material_type="text",
            source=str(item.get("imageScoreGuide") or item.get("videoGuideline") or item.get("scoringGuidelines") or ""),
            required=True,
            usage="用于约束美观度、动效丰富度和废弃判断，AI 必须按 0/1/2 档位输出。",
            review_check="规则文本必须与当前任务类型匹配；规则缺失时只允许生成待复核草稿。",
        ),
    ]
    return specs


def _build_scoring_rules(content: dict[str, Any]) -> list[TaskCapabilityRule]:
    item = content.get("item") if isinstance(content.get("item"), dict) else {}
    image_rule = str(item.get("imageScoreGuide") or "截图是否美观：2 分完美符合，1 分普通，0 分不相干。")
    video_rule = str(item.get("videoGuideline") or item.get("scoringGuidelines") or "视频是否有完整动效或跳转，且美观、流畅：2 分完美符合，1 分普通，0 分不相干。")
    return [
        TaskCapabilityRule(key="beauty_score", title="美观度评分", description=image_rule, values=["0", "1", "2"]),
        TaskCapabilityRule(key="motion_richness_score", title="动效丰富度评分", description=video_rule, values=["0", "1", "2"]),
        TaskCapabilityRule(key="scene_consistency_score", title="前后场景一致性", description="product1/product2 分别给 0/1/2；判断前后素材主体、操作目标和视觉状态是否一致。", values=["0", "1", "2"]),
        TaskCapabilityRule(key="discard", title="废弃判断", description="只有素材明显无关、无法访问、内容缺失或无法评分时才废弃；默认不废弃。", values=["true", "false"]),
    ]


def _build_reason_rules() -> list[TaskCapabilityRule]:
    return [
        TaskCapabilityRule(key="richness_reason", title="评分原因", description="用一句中文说明截图美观度和视频动效判断依据；必须可供人工复核，不能只写分数。", values=[]),
        TaskCapabilityRule(key="scene_consistency_reason", title="一致性原因", description="说明前后素材是否为同一主体/同一网页状态；会同步到 product1/product2 的一致性原因。", values=[]),
        TaskCapabilityRule(key="check_remark", title="人工复核提示", description="可选字段；用于标记 AI 草稿来源和提醒人工复核，不得写提交、通过等最终结论。", values=[]),
    ]


def _build_ai_output_schema() -> list[TaskCapabilityOutputField]:
    return [
        TaskCapabilityOutputField(field="beauty_score", type="integer-string", allowed_values=["0", "1", "2"], maps_to=["Content.data.beauty_score", "Content.dataMap.beauty_score"], description="截图美观度评分。"),
        TaskCapabilityOutputField(field="motion_richness_score", type="integer-string", allowed_values=["0", "1", "2"], maps_to=["Content.data.motion_richness_score", "Content.dataMap.motion_richness_score"], description="视频/动效丰富度评分。"),
        TaskCapabilityOutputField(field="richness_reason", type="string", maps_to=["Content.data.richness_reason", "Content.dataMap.richness_reason"], description="评分原因。"),
        TaskCapabilityOutputField(field="scene_consistency_score", type="object", maps_to=["Content.data.sceneConsistencyScore", "Content.dataMap.sceneConsistencyScore"], description="包含 product1/product2，值为 0/1/2。"),
        TaskCapabilityOutputField(field="scene_consistency_reason", type="string", maps_to=["Content.data.sceneConsistencyRemarks", "Content.dataMap.sceneConsistencyRemarks"], description="同步到 product1/product2 的一致性原因。"),
        TaskCapabilityOutputField(field="discard", type="boolean", required=False, allowed_values=["true", "false"], maps_to=["Content.data.discard", "Content.dataMap.discard"], description="是否废弃；默认 false。"),
        TaskCapabilityOutputField(field="discard_type", type="string-list", required=False, maps_to=["Content.data.discard_type", "Content.dataMap.discard_type"], description="废弃类型列表。"),
        TaskCapabilityOutputField(field="check_remark", type="string", required=False, maps_to=["Content.data.checkRemark", "Content.dataMap.checkRemark"], description="人工复核备注。"),
    ]


def _task_type_key(content: dict[str, Any]) -> str:
    template_id = str(content.get("templateID") or "unknown")
    type_name = str(content.get("type") or "unknown")
    return f"{type_name}_{template_id}_http_temp_draft"


def _apply_answer_data(content: dict[str, Any], answer_data: dict[str, Any]) -> None:
    data = content.setdefault("data", {})
    data_map = content.setdefault("dataMap", {})
    unknown = [field for field in answer_data if field not in ALLOWED_DRAFT_FIELDS]
    if unknown:
        raise TaskCapabilityError(f"草稿字段未在能力卡允许列表中：{', '.join(unknown)}", [f"unsupported-field:{field}" for field in unknown])
    for field, value in answer_data.items():
        data[field] = value
        data_map[field] = value


def _map_ai_output_to_answer_data(ai_output: dict[str, Any]) -> dict[str, Any]:
    required = ["beauty_score", "motion_richness_score", "richness_reason"]
    missing = [field for field in required if field not in ai_output or ai_output.get(field) is None or str(ai_output.get(field)).strip() == ""]
    if missing:
        raise TaskCapabilityError(f"AI 输出缺少必填字段：{', '.join(missing)}", ["invalid-ai-output", *[f"missing:{field}" for field in missing]])
    beauty_score = _coerce_score(ai_output.get("beauty_score"), "beauty_score")
    motion_score = _coerce_score(ai_output.get("motion_richness_score"), "motion_richness_score")
    scene_scores = _coerce_scene_scores(ai_output.get("scene_consistency_score"))
    richness_reason = str(ai_output.get("richness_reason") or "").strip()
    scene_reason = str(ai_output.get("scene_consistency_reason") or richness_reason).strip()
    check_remark = str(ai_output.get("check_remark") or "AI草稿：仅暂存，人工复核后再决定。").strip()
    discard = bool(ai_output.get("discard", False))
    discard_type = ai_output.get("discard_type") if isinstance(ai_output.get("discard_type"), list) else []
    answer_data: dict[str, Any] = {
        "beauty_score": beauty_score,
        "motion_richness_score": motion_score,
        "richness_reason": richness_reason,
        "sceneConsistencyScore": scene_scores,
        "sceneConsistencyRemarks": {"product1": scene_reason, "product2": scene_reason},
        "discard": "Yes" if discard else "No",
        "discard_type": [str(item) for item in discard_type],
        "checkRemark": check_remark,
    }
    if motion_score == "2":
        answer_data["high_richness_reason"] = ["有明显视觉动效"]
    return answer_data


def _build_local_capability_ai_output(content: dict[str, Any], operator_prompt: str) -> dict[str, Any]:
    data = content.get("data") if isinstance(content.get("data"), dict) else {}
    beauty_score = _safe_score(data.get("beauty_score"), "1")
    motion_score = _safe_score(data.get("motion_richness_score"), "1")
    scene_scores = data.get("sceneConsistencyScore") if isinstance(data.get("sceneConsistencyScore"), dict) else {}
    scene_product1 = _safe_score(scene_scores.get("product1") if isinstance(scene_scores, dict) else None, "1")
    scene_product2 = _safe_score(scene_scores.get("product2") if isinstance(scene_scores, dict) else None, "1")
    prompt_note = str(operator_prompt or "").strip()
    reason = str(data.get("richness_reason") or "").strip() or "本地做题 AI 草稿：已按能力卡字段生成评分起点，需人工复核。"
    if prompt_note:
        reason = f"{reason}；操作提示：{prompt_note[:120]}"
    return {
        "beauty_score": beauty_score,
        "motion_richness_score": motion_score,
        "richness_reason": reason,
        "scene_consistency_score": {"product1": scene_product1, "product2": scene_product2},
        "scene_consistency_reason": _local_scene_reason(data, reason),
        "discard": False,
        "check_remark": "本地AI草稿：仅暂存，人工复核后再决定。",
    }


def _call_task_ai_provider_for_media_inspection(
    item: TaskCatalogItem,
    resources: list[TaskMediaResource],
    request: TaskMediaInspectionProviderRequest,
    runtime: dict[str, object],
) -> tuple[TaskMediaImageJudgement, list[TaskVideoKeyframeJudgement], dict[str, int]]:
    endpoint = str(runtime.get("base_url") or "").rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"
    messages = _build_media_inspection_provider_prompt(item, resources, request, runtime)
    input_stats = _measure_media_provider_input(messages)
    payload = {
        "model": runtime.get("model") or "gpt-4.1-mini",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {runtime.get('api_key')}", "Content-Type": "application/json"}
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=int(runtime.get("timeout_seconds") or 30))
        response.raise_for_status()
        data = response.json()
        content_text = str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))
        image_judgement, video_judgements = _parse_media_provider_output(content_text, request.video_keyframes)
        return image_judgement, video_judgements, input_stats
    except TaskCapabilityError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider failures must not fall back to fake visual judgement.
        raise TaskCapabilityError(f"做题 AI 媒体判断 provider 调用失败：{exc}", ["task-ai-media-provider-error"]) from exc


def _build_media_inspection_provider_prompt(
    item: TaskCatalogItem,
    resources: list[TaskMediaResource],
    request: TaskMediaInspectionProviderRequest,
    runtime: dict[str, object],
) -> list[dict[str, Any]]:
    resource_summary = [_media_resource_provider_summary(resource) for resource in resources]
    keyframed_video_keys = {result.resource_key for result in request.video_keyframes if result.keyframes}
    video_urls_without_keyframes = [
        resource.url
        for resource in resources
        if resource.material_type == "video" and resource.key not in keyframed_video_keys
    ]
    instruction = {
        "task_id": item.task_id,
        "task_type_name": item.task_short_name or "返修评分",
        "media_resources": resource_summary,
        "sandbox_trace": request.sandbox_trace,
        "operator_prompt": request.operator_prompt[:1000],
        "required_output": {
            "image_judgement": {
                "layout_normal": "boolean，左侧截图是否完好且排版正常",
                "mojibake_or_broken_layout": "boolean，是否乱码、白屏、排版乱掉或无法判断",
                "reason": "中文原因，必须说明视觉依据",
            },
            "video_keyframe_judgements": [
                {
                    "resource_key": "必须对应 media_resources 里的 video key",
                    "action_visible": "boolean，视频/关键帧是否能看到操作反馈",
                    "matches_sandbox_trace": "boolean，是否复现沙箱点击观察到的跳转/交互/动效",
                    "total_frame_count": "integer，本次用于判断的该视频关键帧总数",
                    "supporting_frame_count": "integer，其中明确支持 matches_sandbox_trace 的关键帧数量",
                    "confidence": "high/medium/low。多帧多数支持且画面清晰为 high；证据不足或帧间冲突为 low",
                    "review_required": "boolean；低置信、支持帧未过半、画面不清或关键动作不可见时必须 true",
                    "review_hint": "中文复核提示；review_required=true 时必须说明需要复核什么",
                    "keyframe_summary": "中文概括看到的关键帧、多帧投票和可见动作",
                    "reason": "中文原因",
                }
            ],
        },
    }
    system_parts = [
        "你是 AIDP 做题 AI 的媒体判断执行器。",
        "只输出 JSON 对象，禁止输出 Markdown。",
        "不要调用 AIDP，不要提交、继续下一题、放弃或领取。",
        "不打开 AIDP UI；只根据提供的媒体资源、图片输入和沙箱轨迹生成结构化判断。",
        "视频 URL 如果没有可访问关键帧或模型不支持视频读取，不要声称已经解码视频；应在 reason 中说明只能依据可见关键帧/可访问素材判断。",
        "对视频必须做多帧投票：逐个视频统计 total_frame_count 和 supporting_frame_count；支持帧未过半或证据冲突时标记低置信 review_required=true。",
    ]
    if runtime.get("pre_prompt"):
        system_parts.append("系统 AI 注入的做题前置提示词：" + str(runtime["pre_prompt"])[:4000])
    if runtime.get("skills"):
        system_parts.append("可用 skills：" + "；".join(str(skill) for skill in runtime["skills"]))
    if runtime.get("md_files"):
        system_parts.append("可参考 md 文件：" + "；".join(str(path) for path in runtime["md_files"]))
    prompt_text = (
        "请按以下媒体判断契约输出 JSON：\n"
        + json.dumps(instruction, ensure_ascii=False)
        + "\n如提供了视频关键帧，请优先依据已抽取关键帧判断视频是否复现沙箱点击；只有缺少关键帧时才参考视频 URL，并明确不确定性。"
        + "\n输出必须包含 supporting_frame_count、total_frame_count、confidence、review_required；低置信时必须给 review_hint。"
    )
    if video_urls_without_keyframes:
        prompt_text += "\n缺少关键帧的视频资源 URL：" + json.dumps(video_urls_without_keyframes, ensure_ascii=False)
    user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
    for resource in resources:
        if resource.material_type == "image":
            user_content.append({"type": "image_url", "image_url": {"url": resource.url}})
    for keyframe_result in request.video_keyframes:
        for frame in keyframe_result.keyframes:
            if frame.data_url:
                user_content.append(
                    {
                        "type": "text",
                        "text": f"视频关键帧：resource_key={keyframe_result.resource_key}, timestamp={frame.timestamp_sec}s, size={frame.width or 0}x{frame.height or 0}",
                    }
                )
                user_content.append({"type": "image_url", "image_url": {"url": frame.data_url}})
    return [
        {"role": "system", "content": "\n".join(system_parts)},
        {"role": "user", "content": user_content},
    ]


def _media_resource_provider_summary(resource: TaskMediaResource) -> dict[str, Any]:
    return {
        "key": resource.key,
        "title": resource.title,
        "material_type": resource.material_type,
        "url": resource.url,
        "required": resource.required,
    }


def _measure_media_provider_input(messages: list[dict[str, Any]]) -> dict[str, int]:
    text_chars = 0
    image_count = 0
    keyframe_count = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            text_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    text_chars += len(str(part.get("text") or ""))
                elif part.get("type") == "image_url":
                    image_count += 1
                    image_url = part.get("image_url") if isinstance(part.get("image_url"), dict) else {}
                    url = str(image_url.get("url") or "")
                    if url.startswith("data:image/"):
                        keyframe_count += 1
    return {
        "text_chars": text_chars,
        "image_count": image_count,
        "keyframe_count": keyframe_count,
    }


def _build_media_provider_diagnostics(
    *,
    provider_call_count: int,
    provider_elapsed_ms: int,
    total_elapsed_ms: int,
    provider_input_text_chars: int,
    provider_input_image_count: int,
    provider_input_keyframe_count: int,
    supplement_status: str,
) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    if provider_call_count >= 2:
        diagnostics.append(
            {
                "key": "provider-called-twice",
                "level": "warning",
                "title": "二次 provider 调用",
                "detail": f"本次累计调用 provider {provider_call_count} 次，通常来自低置信补帧后复判。",
                "suggestion": "优先复用已有 5 帧 manifest；若仍低置信，保留人工复核 blocker，不继续自动追加调用。",
            }
        )
    elif supplement_status == "cached_supplement_used":
        diagnostics.append(
            {
                "key": "cached-supplement-single-provider",
                "level": "ok",
                "title": "缓存补帧单次调用",
                "detail": "已命中本地 5 帧关键帧缓存，并跳过首轮低帧 provider 探测。",
                "suggestion": "这是当前低置信链路的优先快路径；后续同题优先复用该缓存。",
            }
        )
    if provider_input_keyframe_count >= 10:
        diagnostics.append(
            {
                "key": "provider-keyframe-input-high",
                "level": "warning",
                "title": "关键帧输入偏多",
                "detail": f"本次 provider 输入累计 {provider_input_keyframe_count} 张关键帧、{provider_input_image_count} 张图片。",
                "suggestion": "优先保留 5 帧缓存；若 provider 仍慢，下一步应按置信度减少低价值帧或分段复核。",
            }
        )
    elif provider_input_keyframe_count > 0:
        diagnostics.append(
            {
                "key": "provider-keyframes-present",
                "level": "info",
                "title": "关键帧输入已启用",
                "detail": f"本次 provider 输入包含 {provider_input_keyframe_count} 张关键帧。",
                "suggestion": "继续使用多帧证据判断跳转/动效，低置信时再补到 5 帧。",
            }
        )
    if provider_input_text_chars >= 8000:
        diagnostics.append(
            {
                "key": "provider-text-input-high",
                "level": "warning",
                "title": "文本输入偏长",
                "detail": f"本次 provider 文本输入约 {provider_input_text_chars} 字。",
                "suggestion": "下一步优先压缩 operator prompt、资源摘要和系统注入提示词。",
            }
        )
    if provider_call_count > 0:
        diagnostics.append(
            {
                "key": "provider-elapsed-observed",
                "level": "info",
                "title": "耗时已记录",
                "detail": f"provider 累计 {provider_elapsed_ms} ms，总耗时 {total_elapsed_ms} ms。",
                "suggestion": "结合输入规模判断慢点；真实复测会消耗额度，默认只用已保存证据观察。",
            }
        )
    return diagnostics


def _parse_media_provider_output(content: str, video_keyframes: list[TaskVideoKeyframeExtractionResult]) -> tuple[TaskMediaImageJudgement, list[TaskVideoKeyframeJudgement]]:
    parsed = _parse_provider_ai_output(content)
    image_raw = parsed.get("image_judgement")
    if not isinstance(image_raw, dict):
        raise TaskCapabilityError("做题 AI 媒体判断缺少 image_judgement。", ["invalid-media-provider-output"])
    videos_raw = parsed.get("video_keyframe_judgements")
    if videos_raw is None:
        videos_raw = []
    if not isinstance(videos_raw, list):
        raise TaskCapabilityError("做题 AI 媒体判断 video_keyframe_judgements 必须是数组。", ["invalid-media-provider-output"])
    try:
        image_judgement = TaskMediaImageJudgement(**image_raw)
        frame_counts = {item.resource_key: len(item.keyframes) for item in video_keyframes}
        video_judgements = [_normalize_video_provider_judgement(item, frame_counts) for item in videos_raw if isinstance(item, dict)]
    except Exception as exc:  # noqa: BLE001 - convert provider shape issues to capability blockers.
        raise TaskCapabilityError("做题 AI 媒体判断字段格式无效。", ["invalid-media-provider-output"]) from exc
    if len(video_judgements) != len(videos_raw):
        raise TaskCapabilityError("做题 AI 媒体判断包含非对象视频判断。", ["invalid-media-provider-output"])
    return image_judgement, video_judgements


def _normalize_video_provider_judgement(item: dict[str, Any], frame_counts: dict[str, int]) -> TaskVideoKeyframeJudgement:
    resource_key = str(item.get("resource_key") or "")
    total = _safe_int(item.get("total_frame_count"))
    if total <= 0:
        total = frame_counts.get(resource_key, 0)
    supporting = _safe_int(item.get("supporting_frame_count"))
    if supporting <= 0 and bool(item.get("action_visible") and item.get("matches_sandbox_trace")) and total <= 1:
        supporting = total
    confidence = str(item.get("confidence") or "unknown").strip().lower() or "unknown"
    if confidence not in {"high", "medium", "low", "unknown"}:
        confidence = "unknown"
    review_required = bool(item.get("review_required"))
    if total > 1 and supporting * 2 <= total and bool(item.get("matches_sandbox_trace")):
        review_required = True
        if confidence == "unknown":
            confidence = "low"
    payload = dict(item)
    payload["resource_key"] = resource_key
    payload["total_frame_count"] = total
    payload["supporting_frame_count"] = supporting
    payload["confidence"] = confidence
    payload["review_required"] = review_required
    if review_required and not str(payload.get("review_hint") or "").strip():
        payload["review_hint"] = f"多帧投票只有 {supporting}/{total} 帧支持，需要人工复核。"
    return TaskVideoKeyframeJudgement(**payload)


def _call_task_ai_provider_for_capability(item: TaskCatalogItem, content: dict[str, Any], request: TaskProviderDraftRequest, runtime: dict[str, object]) -> tuple[dict[str, Any], str]:
    endpoint = str(runtime.get("base_url") or "").rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"
    prompt = _build_task_ai_provider_prompt(item, content, request, runtime)
    payload = {
        "model": runtime.get("model") or "gpt-4.1-mini",
        "messages": prompt,
        "temperature": 0.1,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {runtime.get('api_key')}", "Content-Type": "application/json"}
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=int(runtime.get("timeout_seconds") or 30))
        response.raise_for_status()
        data = response.json()
        content_text = str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))
        return _parse_provider_ai_output(content_text), "provider_ok"
    except TaskCapabilityError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider failure must be explicit and never write remote.
        raise TaskCapabilityError(f"做题 AI provider 调用失败：{exc}", ["task-ai-provider-error"]) from exc


def _build_task_ai_provider_prompt(item: TaskCatalogItem, content: dict[str, Any], request: TaskProviderDraftRequest, runtime: dict[str, object]) -> list[dict[str, str]]:
    capability_context = {
        "task_id": item.task_id,
        "task_type_name": item.task_short_name or "返修评分",
        "ai_input_materials": _build_ai_input_materials(content),
        "ai_input_spec": [spec.model_dump() for spec in _build_ai_input_spec(content)],
        "scoring_rules": [rule.model_dump() for rule in _build_scoring_rules(content)],
        "reason_rules": [rule.model_dump() for rule in _build_reason_rules()],
        "ai_output_schema": [field.model_dump() for field in _build_ai_output_schema()],
        "operator_prompt": request.operator_prompt[:1000],
    }
    system_parts = [
        "你是 AIDP 做题 AI，只能生成返修评分草稿 JSON。",
        "禁止提交、继续下一题、放弃、领取、改密钥、切域名或执行任何系统动作。",
        "必须严格输出 JSON 对象，字段只允许来自 ai_output_schema。",
    ]
    if runtime.get("pre_prompt"):
        system_parts.append("系统 AI 注入的做题前置提示词：" + str(runtime["pre_prompt"])[:4000])
    if runtime.get("skills"):
        system_parts.append("可用 skills：" + "；".join(str(skill) for skill in runtime["skills"]))
    if runtime.get("md_files"):
        system_parts.append("可参考 md 文件：" + "；".join(str(path) for path in runtime["md_files"]))
    return [
        {"role": "system", "content": "\n".join(system_parts)},
        {"role": "user", "content": "请按以下脱敏能力卡上下文输出评分草稿 JSON：\n" + json.dumps(capability_context, ensure_ascii=False)},
    ]


def _parse_provider_ai_output(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TaskCapabilityError("做题 AI provider 未返回有效 JSON。", ["invalid-provider-ai-output"]) from exc
    if not isinstance(parsed, dict):
        raise TaskCapabilityError("做题 AI provider 输出必须是 JSON 对象。", ["invalid-provider-ai-output"])
    return parsed


def _build_ai_review_preview(ai_output: dict[str, Any], mapped_answer_data: dict[str, Any], provider_status: str) -> TaskDraftReviewPreview:
    warnings = []
    if not str(ai_output.get("richness_reason") or "").strip():
        warnings.append("AI 未提供评分原因，必须人工补充后再暂存。")
    if bool(ai_output.get("discard", False)) and not ai_output.get("discard_type"):
        warnings.append("AI 标记废弃但未提供废弃类型。")
    scene_score = ai_output.get("scene_consistency_score")
    review_items = [
        TaskDraftReviewItem(key="beauty_score", title="美观度评分", value=str(mapped_answer_data.get("beauty_score", "")), review_hint="核对最终截图是否支撑该分数。"),
        TaskDraftReviewItem(key="motion_richness_score", title="动效丰富度评分", value=str(mapped_answer_data.get("motion_richness_score", "")), review_hint="核对视频/录屏是否有明确动效或状态变化。"),
        TaskDraftReviewItem(key="richness_reason", title="评分原因", value=str(ai_output.get("richness_reason") or ""), review_hint="原因必须能解释分数，不能只复述规则。"),
        TaskDraftReviewItem(key="scene_consistency_score", title="前后场景一致性", value=scene_score if isinstance(scene_score, dict) else mapped_answer_data.get("sceneConsistencyScore"), review_hint="核对 product1/product2 是否与素材主体一致。"),
        TaskDraftReviewItem(key="discard", title="废弃判断", value="是" if bool(ai_output.get("discard", False)) else "否", review_hint="只有素材无关、缺失、无法访问或无法评分时才废弃。"),
    ]
    return TaskDraftReviewPreview(
        provider_status=provider_status,
        ai_output=ai_output,
        mapped_answer_data=mapped_answer_data,
        review_items=review_items,
        warnings=warnings,
    )


def _build_confirmation_gate_statuses() -> list[TaskDraftConfirmationGateStatus]:
    env_ready = os.environ.get("AIDP_TEMP_DRAFT_ALLOW_WRITE") == "1"
    return [
        TaskDraftConfirmationGateStatus(
            key="execute",
            title="执行请求闸门",
            passed=True,
            status="prepared",
            detail="点击确认单执行按钮时，平台会以 execute=true 发起请求。",
            next_step="继续保留二次确认弹窗。",
        ),
        TaskDraftConfirmationGateStatus(
            key="allow_draft_write",
            title="草稿写入许可",
            passed=True,
            status="prepared",
            detail="点击确认单执行按钮时，平台会以 allow_draft_write=true 发起请求。",
            next_step="仅允许写草稿，不允许提交或继续下一题。",
        ),
        TaskDraftConfirmationGateStatus(
            key="env_allow_write",
            title="后端环境闸门",
            passed=env_ready,
            status="ready" if env_ready else "blocked",
            detail="AIDP_TEMP_DRAFT_ALLOW_WRITE=1 已开启。" if env_ready else "AIDP_TEMP_DRAFT_ALLOW_WRITE 未设置为 1，真实草稿暂存会被后端拦截。",
            next_step="可执行真实草稿暂存。" if env_ready else "如需真实暂存，先由操作者显式开启后端环境闸门。",
        ),
    ]


def _build_confirmation_field_diff(content: dict[str, Any], mapped_answer_data: dict[str, Any]) -> list[TaskDraftConfirmationFieldDiff]:
    data = content.get("data") if isinstance(content.get("data"), dict) else {}
    diffs = []
    for field, next_value in mapped_answer_data.items():
        current_value = data.get(field)
        diffs.append(
            TaskDraftConfirmationFieldDiff(
                field=field,
                role=FIELD_ROLES.get(field, "草稿字段"),
                current_value=current_value,
                next_value=next_value,
                changed=_json_compare_key(current_value) != _json_compare_key(next_value),
                source_path=f"Content.data.{field}",
            )
        )
    return diffs


def _build_rehearsal_checklist(
    *,
    identity: TaskCapabilityIdentity,
    field_diff: list[TaskDraftConfirmationFieldDiff],
    gate_statuses: list[TaskDraftConfirmationGateStatus],
    ready_for_gated_write: bool,
    required_gates: list[str],
    allowed_endpoint: str,
    forbidden_actions: list[str],
    draft_evidence_path: str,
) -> list[TaskDraftRehearsalChecklistItem]:
    blocked_gates = [gate.title for gate in gate_statuses if gate.required and not gate.passed]
    changed_fields = [item.field for item in field_diff if item.changed]
    return [
        TaskDraftRehearsalChecklistItem(
            key="field_diff_review",
            title="字段 diff 已生成",
            status="ready" if field_diff else "blocked",
            detail=f"共 {len(field_diff)} 个字段待核对；变化字段：{', '.join(changed_fields) if changed_fields else '无'}。",
            next_step="逐项确认 next_value 可写入草稿，尤其是分数、原因和废弃判断。",
        ),
        TaskDraftRehearsalChecklistItem(
            key="gate_status_review",
            title="环境闸门状态可见",
            status="ready" if ready_for_gated_write else "blocked",
            detail="全部必需闸门已通过。" if ready_for_gated_write else f"仍阻塞：{', '.join(blocked_gates) if blocked_gates else '未知闸门'}。",
            next_step="未全部通过时只能演练和复核，不能真实写草稿。",
        ),
        TaskDraftRehearsalChecklistItem(
            key="identity_confirmed",
            title="任务身份已确认",
            status="ready" if identity.TaskID and identity.ItemID else "blocked",
            detail=f"TaskID={identity.TaskID}；NodeID={identity.NodeID}；ItemID={identity.ItemID}；StagingTime={identity.StagingTime}。",
            next_step="确认页面上正在处理的题与该身份一致后再考虑真实暂存。",
        ),
        TaskDraftRehearsalChecklistItem(
            key="allowed_endpoint_only",
            title="仅允许草稿暂存接口",
            status="ready" if allowed_endpoint.endswith("SubmitTempItemAnswer") else "blocked",
            detail=f"允许接口：{allowed_endpoint}。",
            next_step="不得切换到提交、继续、放弃、领取等接口。",
        ),
        TaskDraftRehearsalChecklistItem(
            key="forbidden_actions_review",
            title="禁止动作已列明",
            status="ready" if forbidden_actions else "blocked",
            detail="禁止：" + "；".join(forbidden_actions),
            next_step="演练和真实暂存都必须保持这些动作不可执行。",
        ),
        TaskDraftRehearsalChecklistItem(
            key="dry_run_evidence",
            title="dry-run 证据已生成",
            status="ready" if draft_evidence_path else "blocked",
            detail=draft_evidence_path or "尚未生成 dry-run evidence。",
            next_step="真实暂存前先保留该证据，失败时用它回放字段映射。",
        ),
        TaskDraftRehearsalChecklistItem(
            key="manual_page_review",
            title="人工页面复核要求",
            status="needs_operator",
            detail="真实暂存后必须由操作者打开 AIDP 页面，核对草稿值和最近暂存时间。",
            next_step="页面确认无误前禁止提交或继续下一题。",
        ),
        TaskDraftRehearsalChecklistItem(
            key="explicit_write_permission",
            title="明确允许真实暂存",
            status="needs_operator" if ready_for_gated_write else "blocked",
            detail="；".join(required_gates),
            next_step="只有操作者明确允许并开启后端环境闸门后，才能点击受控执行草稿暂存。",
        ),
    ]


def _json_compare_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_score(value: Any, fallback: str) -> str:
    text = str(value).strip()
    return text if text in {"0", "1", "2"} else fallback


def _local_scene_reason(data: dict[str, Any], fallback: str) -> str:
    remarks = data.get("sceneConsistencyRemarks")
    if isinstance(remarks, dict):
        first = str(remarks.get("product1") or remarks.get("product2") or "").strip()
        if first:
            return first
    return fallback


def _provider_draft_mode(provider_status: str, execute: bool) -> str:
    action = "execute" if execute else "plan"
    if provider_status == "provider_ok":
        return f"provider_ai_temp_draft_{action}"
    return f"local_ai_temp_draft_{action}"


def _provider_draft_message(provider_status: str, execute: bool, blockers: list[str]) -> str:
    if provider_status == "provider_ok":
        return "做题 AI provider 已生成结构化输出，并已映射为草稿暂存 dry-run；未触 AIDP。" if not execute else "做题 AI provider 输出已进入受控草稿暂存；仍未提交。"
    if blockers:
        return "做题 AI provider 未配置，已回退本地确定性草稿；未触网、未写远端。"
    return "本地做题 AI 草稿已按能力卡映射为 dry-run；未触网、未写远端。"


def _coerce_score(value: Any, field: str) -> str:
    text = str(value).strip()
    if text not in {"0", "1", "2"}:
        raise TaskCapabilityError(f"{field} 只能是 0/1/2。", ["invalid-ai-output", f"invalid-score:{field}"])
    return text


def _coerce_scene_scores(value: Any) -> dict[str, str]:
    if value is None:
        return {"product1": "1", "product2": "1"}
    if not isinstance(value, dict):
        raise TaskCapabilityError("scene_consistency_score 必须是对象。", ["invalid-ai-output", "invalid-scene-consistency-score"])
    return {
        "product1": _coerce_score(value.get("product1", "1"), "scene_consistency_score.product1"),
        "product2": _coerce_score(value.get("product2", "1"), "scene_consistency_score.product2"),
    }


def _execute_blockers(request: TaskDraftBuildRequest) -> list[str]:
    blockers = []
    if not request.allow_draft_write:
        blockers.append("missing-AllowDraftWrite")
    if os.environ.get("AIDP_TEMP_DRAFT_ALLOW_WRITE") != "1":
        blockers.append("missing-env-AIDP_TEMP_DRAFT_ALLOW_WRITE")
    return blockers


def _select_account(account_user_id: str) -> Optional[dict[str, Any]]:
    state_path = Path(get_settings().production_state_path)
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    accounts = [item for item in state.get("accounts", []) if isinstance(item, dict) and item.get("enabled") is not False]
    if account_user_id:
        match = next((item for item in accounts if _account_user_id(item) == account_user_id), None)
        if match:
            return match
    return accounts[0] if len(accounts) == 1 else None


def _account_user_id(account: dict[str, Any]) -> str:
    return str(account.get("userId") or account.get("user_id") or "")


def _post_temp_draft(payload: dict[str, Any], cookie: str, referer: str) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        response = requests.post(
            f"https://aidp.juejin.cn{SUBMIT_TEMP_ENDPOINT}",
            json=payload,
            headers={
                "Cookie": cookie,
                "Referer": referer,
                "Origin": "https://aidp.juejin.cn",
                "Content-Type": "application/json",
                "User-Agent": "aidp-monitor-next/8789-http-draft",
            },
            timeout=20,
        )
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:1000]}
        return {
            "ok": response.ok,
            "status_code": response.status_code,
            "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "data": data,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status_code": None,
            "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "error": str(exc),
            "data": None,
        }


def _base_resp_status_code(response: Any) -> Optional[int]:
    if isinstance(response, dict) and isinstance(response.get("BaseResp"), dict):
        value = response["BaseResp"].get("StatusCode")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _find_account_user_id(recording: dict[str, Any], payload: dict[str, Any]) -> str:
    task_id = str(payload.get("TaskID") or "")
    network_user_id = _extract_network_user_id(recording, task_id)
    if network_user_id:
        return network_user_id
    recorded = str(recording.get("account_user_id") or "")
    if recorded and recorded != task_id:
        return recorded
    return ""


def _extract_network_user_id(recording: dict[str, Any], task_id: str) -> str:
    candidates: Counter[str] = Counter()
    pattern = re.compile(r'"(?:user_id|userId|account_user_id)"\s*:\s*"([^"]+)"')
    for entry in recording.get("network", []):
        if not isinstance(entry, dict):
            continue
        for field in ("request_body", "response_body"):
            text = str(entry.get(field) or "")
            if not text:
                continue
            for match in pattern.finditer(text):
                value = str(match.group(1) or "").strip()
                if value and value != task_id:
                    candidates[value] += 1
    if candidates:
        return candidates.most_common(1)[0][0]
    return ""


def _write_draft_evidence(item: TaskCatalogItem, payload: dict[str, Any], request: TaskDraftBuildRequest, sends_network: bool, response: Optional[dict[str, Any]]) -> Path:
    root = Path(get_settings().operation_recording_root).parent / "task-capabilities"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"temp-draft-{item.task_id}-{uuid4().hex[:8]}.json"
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_catalog_item_id": item.id,
        "task_id": item.task_id,
        "mode": "execute" if request.execute else "dry-run",
        "sends_network": sends_network,
        "writes_remote": sends_network,
        "endpoint": SUBMIT_TEMP_ENDPOINT,
        "payload": payload,
        "response": response,
        "guardrails": {
            "allow_draft_write": request.allow_draft_write,
            "env_gate": os.environ.get("AIDP_TEMP_DRAFT_ALLOW_WRITE", ""),
            "no_submit": True,
        },
    }
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _record_worker_event(db: Session, task_id: str, severity: str, step: str, message: str) -> None:
    report_worker_event(
        db,
        WorkerEventReportRequest(
            worker_id="task-capability-api",
            event_type="event_report",
            task_id=task_id,
            severity=severity,
            stage="ai_draft",
            step=step,
            message=message,
        ),
    )
