import hashlib
import json
import re
from time import perf_counter
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditSeverity
from app.models.score_loop import ScoreLoopCase, ScoreLoopCaseStatus
from app.models.task import RuntimeConfig
from app.schemas.ai import AiIncidentAction
from app.schemas.score_loop import (
    ScoreLoopAutoSubmitRequest,
    ScoreLoopCaptureRequest,
    ScoreLoopCaseListResponse,
    ScoreLoopCaseRead,
    ScoreLoopDraftRequest,
    ScoreLoopGate,
    ScoreLoopLogContract,
    ScoreLoopManualStableRequest,
    ScoreLoopReadinessCheck,
    ScoreLoopReviewRequest,
    ScoreLoopStep,
    ScoreLoopSummaryResponse,
)
from app.schemas.worker import WorkerEventReportRequest
from app.services.ai_confirmation_service import create_confirmation_requests
from app.services.ai_service import draft_task_ai_answer
from app.services.api_paths import api_path
from app.services.audit_service import write_audit
from app.services.task_rules import utc_now
from app.services.worker_service import report_worker_event

SUPPORTED_TASK_TYPE_KEY = "rft_aesthetic_v1"
SUPPORTED_TASK_TYPE_NAME = "RFT人标_美观度"
PLUGIN_VERSION = "0.5.10"
REQUIRED_STABLE_COUNT = 3
MANUAL_STABLE_COUNT_KEY = "score_loop.manual_stable_count"
AUTO_SUBMIT_ENABLED_KEY = "score_loop.auto_submit_enabled"
AUTO_SUBMIT_FORCE_KEY = "score_loop.auto_submit_force_enabled"
AUTO_SUBMIT_UPDATED_AT_KEY = "score_loop.auto_submit_updated_at"
AUTO_SUBMIT_LAST_ENABLED_AT_KEY = "score_loop.auto_submit_last_enabled_at"
AUTO_SUBMIT_LAST_DISABLED_AT_KEY = "score_loop.auto_submit_last_disabled_at"
SCORE_LOOP_WORKER_ID = "score-loop-api"

_SECRET_PATTERNS = [
    re.compile(r"(cookie|api[_-]?key|token|secret|password|主密钥|恢复码)\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
]


def build_score_loop_summary(db: Session) -> ScoreLoopSummaryResponse:
    gate = build_gate(db)
    return ScoreLoopSummaryResponse(
        generated_at=utc_now(),
        task_type_key=SUPPORTED_TASK_TYPE_KEY,
        task_type_name=SUPPORTED_TASK_TYPE_NAME,
        plugin_version=PLUGIN_VERSION,
        supported=True,
        mode="manual_confirmed_dry_run_submit_gate",
        gate=gate,
        plugin_workflow=[
            ScoreLoopStep(key="capture", title="采集题面", status="ready", detail="从插件/Worker 上传脱敏题面、题型和候选项。", source="browser-extension/worker"),
            ScoreLoopStep(key="draft", title="AI 草稿", status="ready", detail="内置 AI 生成建议答案和理由，但不触碰继续下一题 checkbox。", source="score-loop-api"),
            ScoreLoopStep(key="manual_review", title="人工确认", status="ready", detail="人工确认最终答案，稳定样本计数递增。", source="operator"),
            ScoreLoopStep(key="submit_gate", title="提交确认", status="guarded", detail="真实提交属于高危动作，必须进入 AI 确认队列。", source="ai-confirmation-flow"),
        ],
        http_dry_run_plan=[
            ScoreLoopStep(key="dry_payload", title="生成 dry-run payload", status="ready", detail="只生成脱敏提交载荷摘要，不访问 AIDP 写接口。", source="backend"),
            ScoreLoopStep(key="readback", title="回读校验", status="planned", detail="真实提交后必须回读状态；首版仅保留计划与审计。", source="backend"),
            ScoreLoopStep(key="next_question", title="下一题循环", status="blocked", detail="未获得提交确认前不进入下一题。", source="guardrail"),
        ],
        guardrails=[
            "首版只支持 RFT人标_美观度；未知题型默认暂停。",
            "AI 草稿必须人工确认后才能进入提交确认队列。",
            f"真实提交属于高危动作，复用 {api_path('/ai/confirmations')}；批准只授权，不自动绕过执行闸门。",
            "继续下一题 checkbox 不得被评分插件或内置 AI 自动触碰。",
            "Cookie、API key、token 和账号密码不得进入题面、日志、前端或报告。",
        ],
        log_contract=ScoreLoopLogContract(
            source="score_loop_cases + audit_logs",
            storage_key="score-loop.case.*",
            max_entries=200,
            required_events=["score_loop_case_captured", "score_loop_draft_created", "score_loop_manual_review", "ai_action_confirmation_requested"],
            ingestion_status="active",
            note="结构化日志保留 90 天；debug 截图/大响应摘要 30 天；关键事故永久归档。",
        ),
        readiness_checks=_readiness_checks(db, gate),
        source_files=[
            "backend/app/services/score_loop_service.py",
            "backend/app/api/v1/routes/score_loop.py",
            "frontend/src/pages/AiPage.tsx",
            "backend/app/services/ai_confirmation_service.py",
        ],
        case_counts=_case_counts(db),
        message="评分题生产闭环首版已就绪：先采集、再草稿、人工确认，真实提交进入高危确认队列。",
    )


def build_gate(db: Session, audit_trace_id: Optional[str] = None) -> ScoreLoopGate:
    stable_count = _get_int_config(db, MANUAL_STABLE_COUNT_KEY, 0)
    auto_enabled = _get_bool_config(db, AUTO_SUBMIT_ENABLED_KEY, False)
    force_enabled = _get_bool_config(db, AUTO_SUBMIT_FORCE_KEY, False)
    ready = stable_count >= REQUIRED_STABLE_COUNT
    blocked_reason = "" if ready else f"需要至少 {REQUIRED_STABLE_COUNT} 个人工确认稳定样本，当前 {stable_count} 个。"
    if auto_enabled and not ready and not force_enabled:
        blocked_reason = "自动提交开关已被护栏关闭：稳定样本不足。"
    return ScoreLoopGate(
        required_stable_count=REQUIRED_STABLE_COUNT,
        manual_stable_count=stable_count,
        auto_submit_enabled=auto_enabled and (ready or force_enabled),
        force_enabled=force_enabled,
        ready_for_auto_submit=ready,
        blocked_reason=blocked_reason,
        last_enabled_at=_parse_datetime(_get_config(db, AUTO_SUBMIT_LAST_ENABLED_AT_KEY, "")),
        last_disabled_at=_parse_datetime(_get_config(db, AUTO_SUBMIT_LAST_DISABLED_AT_KEY, "")),
        updated_at=_parse_datetime(_get_config(db, AUTO_SUBMIT_UPDATED_AT_KEY, "")),
        audit_trace_id=audit_trace_id,
    )


def list_score_loop_cases(db: Session, limit: int = 50) -> ScoreLoopCaseListResponse:
    bounded_limit = max(1, min(limit, 200))
    items = list(db.scalars(select(ScoreLoopCase).order_by(ScoreLoopCase.created_at.desc(), ScoreLoopCase.id.desc()).limit(bounded_limit)))
    return ScoreLoopCaseListResponse(total=len(items), items=[case_to_read(item) for item in items])


def capture_score_case(db: Session, request: ScoreLoopCaptureRequest) -> tuple[ScoreLoopCase, Optional[str]]:
    question = _redact(request.question_text.strip())
    if not question:
        raise ValueError("题面不能为空。")
    supported = request.task_type_key == SUPPORTED_TASK_TYPE_KEY
    case = ScoreLoopCase(
        status=ScoreLoopCaseStatus.CAPTURED if supported else ScoreLoopCaseStatus.UNSUPPORTED_PAUSED,
        task_type_key=request.task_type_key,
        task_type_name=request.task_type_name or request.task_type_key,
        task_catalog_item_id=request.task_catalog_item_id,
        account_user_id=_redact(request.account_user_id),
        question_hash=_hash_question(question, request.choices),
        question_text=question,
        choices_json=json.dumps([_redact(item) for item in request.choices], ensure_ascii=False),
        trace_id=uuid4().hex,
    )
    db.add(case)
    db.flush()
    _record_score_worker_event(
        db,
        account_user_id=case.account_user_id,
        task_id=f"score-case-{case.id}",
        severity="info",
        stage="task_refresh",
        step="parse_task_catalog",
        message=f"评分题题面已采集 id={case.id}, supported={supported}",
    )
    audit_trace_id = None
    if request.write_audit:
        audit = write_audit(
            db,
            event_type="score_loop_case_captured",
            severity=AuditSeverity.INFO if supported else AuditSeverity.WARNING,
            actor="score-loop",
            target_type="score_loop_case",
            target_id=str(case.id),
            message=_redact(f"score loop case captured id={case.id}, type={case.task_type_key}, supported={supported}, hash={case.question_hash}"),
        )
        audit_trace_id = audit.trace_id
    db.commit()
    return case, audit_trace_id


def create_ai_draft(db: Session, case_id: int, request: ScoreLoopDraftRequest) -> tuple[ScoreLoopCase, Optional[str]]:
    case = _get_case(db, case_id)
    if case.task_type_key != SUPPORTED_TASK_TYPE_KEY:
        case.status = ScoreLoopCaseStatus.UNSUPPORTED_PAUSED
        case.updated_at = utc_now()
        db.commit()
        raise ValueError("未知题型已暂停，不生成 AI 草稿。")
    choices = _load_choices(case)
    started = perf_counter()
    if request.use_provider:
        answer, reason, provider_status = draft_task_ai_answer(
            question_text=case.question_text,
            choices=choices,
            task_type_name=case.task_type_name,
            context={"case_id": case.id, "task_type_key": case.task_type_key, "account_user_id": case.account_user_id},
        )
    else:
        answer = _draft_answer(choices)
        reason = _draft_reason(case, choices)
        provider_status = "local_policy"
    case.ai_answer = answer
    case.ai_reason = reason
    case.status = ScoreLoopCaseStatus.DRAFT_READY
    case.updated_at = utc_now()
    duration_ms = int((perf_counter() - started) * 1000)
    provider_failed = provider_status == "provider_error"
    _record_score_worker_event(
        db,
        account_user_id=case.account_user_id,
        task_id=f"score-case-{case.id}",
        severity="error" if provider_failed else "info",
        stage="ai_draft",
        step="call_provider" if provider_failed else "save_draft",
        error_code=_task_ai_error_code(reason) if provider_failed else "",
        error_detail=reason if provider_failed else "",
        retryable=True if provider_failed else None,
        duration_ms=duration_ms,
        message=f"评分题 AI 草稿 provider={provider_status}, case_id={case.id}",
    )
    audit_trace_id = None
    if request.write_audit:
        audit = write_audit(
            db,
            event_type="score_loop_draft_created",
            severity=AuditSeverity.INFO,
            actor="score-loop-ai",
            target_type="score_loop_case",
            target_id=str(case.id),
            message=_redact(f"score loop task AI draft created id={case.id}, provider={provider_status}, answer={case.ai_answer[:80]}"),
        )
        audit_trace_id = audit.trace_id
    db.commit()
    return case, audit_trace_id


def review_score_case(db: Session, case_id: int, request: ScoreLoopReviewRequest) -> tuple[ScoreLoopCase, Optional[str]]:
    case = _get_case(db, case_id)
    decision = request.decision.strip().lower()
    previous_decision = case.manual_decision
    if decision not in {"approve", "reject"}:
        raise ValueError("decision 只能是 approve 或 reject。")
    final_answer = _redact((request.final_answer or case.ai_answer).strip())
    if decision == "approve" and not final_answer:
        raise ValueError("批准时必须提供 final_answer 或先生成 AI 草稿。")
    case.manual_decision = decision
    case.manual_note = _redact(request.note)
    case.final_answer = final_answer
    case.reviewed_at = utc_now()
    if decision == "reject":
        case.status = ScoreLoopCaseStatus.MANUAL_REJECTED
    elif request.request_submit:
        confirmation = _request_submit_confirmation(db, case, request)
        case.submit_confirmation_id = confirmation.id
        case.status = ScoreLoopCaseStatus.SUBMIT_CONFIRMATION_REQUIRED
    else:
        case.status = ScoreLoopCaseStatus.MANUAL_APPROVED
    case.updated_at = utc_now()
    if decision == "approve" and previous_decision != "approve":
        _increment_stable_count(db, 1)
    elif decision == "reject" and previous_decision == "approve":
        _increment_stable_count(db, -1)
    _record_score_worker_event(
        db,
        account_user_id=case.account_user_id,
        task_id=f"score-case-{case.id}",
        severity="warning" if request.request_submit or decision == "reject" else "info",
        stage="manual_confirmation",
        step="queue_confirmation" if request.request_submit else decision,
        error_code="CONFIRMATION_PENDING" if request.request_submit else "CONFIRMATION_REJECTED" if decision == "reject" else "",
        error_detail="真实提交请求已进入高危确认队列" if request.request_submit else "人工驳回评分结果" if decision == "reject" else "",
        retryable=False if decision == "reject" else None,
        message=f"评分题人工复核 id={case.id}, decision={decision}, request_submit={request.request_submit}",
    )
    audit_trace_id = None
    if request.write_audit:
        audit = write_audit(
            db,
            event_type="score_loop_manual_review",
            severity=AuditSeverity.WARNING if request.request_submit else AuditSeverity.INFO,
            actor="operator",
            target_type="score_loop_case",
            target_id=str(case.id),
            message=_redact(f"score loop manual review id={case.id}, decision={decision}, request_submit={request.request_submit}, confirmation_id={case.submit_confirmation_id}"),
        )
        audit_trace_id = audit.trace_id
    db.commit()
    return case, audit_trace_id


def add_manual_stable_count(db: Session, request: ScoreLoopManualStableRequest) -> ScoreLoopGate:
    _increment_stable_count(db, request.count_delta)
    audit = write_audit(
        db,
        event_type="score_loop_manual_stable_count",
        severity=AuditSeverity.INFO,
        actor="operator",
        target_type="score_loop_gate",
        target_id=MANUAL_STABLE_COUNT_KEY,
        message=_redact(f"manual stable count +{request.count_delta}: {request.note}"),
    )
    db.commit()
    return build_gate(db, audit.trace_id)


def set_auto_submit_gate(db: Session, request: ScoreLoopAutoSubmitRequest) -> ScoreLoopGate:
    stable_count = _get_int_config(db, MANUAL_STABLE_COUNT_KEY, 0)
    ready = stable_count >= REQUIRED_STABLE_COUNT
    enabled = request.enabled and (ready or request.force_confirmed)
    now = utc_now().isoformat()
    _set_config(db, AUTO_SUBMIT_ENABLED_KEY, "true" if enabled else "false", "operator")
    _set_config(db, AUTO_SUBMIT_FORCE_KEY, "true" if request.force_confirmed else "false", "operator")
    _set_config(db, AUTO_SUBMIT_UPDATED_AT_KEY, now, "operator")
    _set_config(db, AUTO_SUBMIT_LAST_ENABLED_AT_KEY if enabled else AUTO_SUBMIT_LAST_DISABLED_AT_KEY, now, "operator")
    audit = write_audit(
        db,
        event_type="score_loop_auto_submit_gate",
        severity=AuditSeverity.WARNING if enabled else AuditSeverity.INFO,
        actor="operator",
        target_type="score_loop_gate",
        target_id=AUTO_SUBMIT_ENABLED_KEY,
        message=_redact(f"auto submit requested={request.enabled}, enabled={enabled}, force={request.force_confirmed}, stable={stable_count}, reason={request.reason}"),
    )
    db.commit()
    return build_gate(db, audit.trace_id)


def case_to_read(case: ScoreLoopCase) -> ScoreLoopCaseRead:
    return ScoreLoopCaseRead(
        id=case.id,
        status=case.status.value if hasattr(case.status, "value") else str(case.status),
        task_type_key=case.task_type_key,
        task_type_name=case.task_type_name,
        task_catalog_item_id=case.task_catalog_item_id,
        account_user_id=case.account_user_id,
        question_hash=case.question_hash,
        question_text=case.question_text,
        choices=_load_choices(case),
        ai_answer=case.ai_answer,
        ai_reason=case.ai_reason,
        final_answer=case.final_answer,
        manual_decision=case.manual_decision,
        manual_note=case.manual_note,
        submit_confirmation_id=case.submit_confirmation_id,
        trace_id=case.trace_id,
        created_at=case.created_at,
        updated_at=case.updated_at,
        reviewed_at=case.reviewed_at,
        next_step=_case_next_step(case),
    )


def _request_submit_confirmation(db: Session, case: ScoreLoopCase, request: ScoreLoopReviewRequest):
    action = AiIncidentAction(
        key=f"real_submit_score_case_{case.id}",
        title=f"真实提交评分题 #{case.id}",
        risk_level="high",
        status="requires_confirmation",
        requires_confirmation=True,
        allowed_by_policy=False,
        message=_redact(f"人工确认评分结果后请求真实提交。题型={case.task_type_key}，答案={case.final_answer or request.final_answer or case.ai_answer}。确认只授权，不自动绕过提交执行闸门。"),
        rollback_hint="若误确认，保持 AIDP 页面不提交；如已提交，必须回读任务状态并记录人工回滚说明。",
    )
    confirmations = create_confirmation_requests(
        db,
        source_trace_id=case.trace_id,
        source_ai_job_id=None,
        actions=[action],
        context={
            "permission_model": "评分题真实提交属于高危动作",
            "score_loop_case_id": case.id,
            "task_type_key": case.task_type_key,
            "question_hash": case.question_hash,
        },
        write_audit_enabled=request.write_audit,
    )
    return confirmations[0]


def _get_case(db: Session, case_id: int) -> ScoreLoopCase:
    case = db.get(ScoreLoopCase, case_id)
    if not case:
        raise ValueError(f"评分题样本不存在：{case_id}")
    return case


def _case_counts(db: Session) -> dict[str, int]:
    counts = {item.value: 0 for item in ScoreLoopCaseStatus}
    for case in db.scalars(select(ScoreLoopCase)):
        status = case.status.value if hasattr(case.status, "value") else str(case.status)
        counts[status] = counts.get(status, 0) + 1
    return counts


def _readiness_checks(db: Session, gate: ScoreLoopGate) -> list[ScoreLoopReadinessCheck]:
    counts = _case_counts(db)
    pending_submit_confirmations = _score_submit_confirmation_pending_count(db)
    unsupported_paused = counts.get(ScoreLoopCaseStatus.UNSUPPORTED_PAUSED.value, 0)
    review_backlog = counts.get(ScoreLoopCaseStatus.CAPTURED.value, 0) + counts.get(ScoreLoopCaseStatus.DRAFT_READY.value, 0)
    submit_confirmation_required = counts.get(ScoreLoopCaseStatus.SUBMIT_CONFIRMATION_REQUIRED.value, 0)
    return [
        ScoreLoopReadinessCheck(
            key="real_question_available",
            title="真题可见演示",
            status="blocked",
            required=True,
            detail="当前没有真实评分题，不能探测真实提交/回读，也不能把模拟结果当成验收。",
            next_step="等出现真实评分题后，在你可见的页面做提交与回读演示。",
        ),
        ScoreLoopReadinessCheck(
            key="safe_score_loop_scaffold",
            title="安全评分闭环骨架",
            status="passed",
            required=True,
            detail="已具备采集脱敏题面、AI 草稿、人工确认、真实提交进入高危确认队列的链路。",
            next_step="无真题阶段只维护骨架、护栏和可观测性，不访问 AIDP 写接口。",
        ),
        ScoreLoopReadinessCheck(
            key="score_alert_closure",
            title="评分告警闭环",
            status="warning" if pending_submit_confirmations or unsupported_paused or review_backlog else "passed",
            required=True,
            detail=f"pending 提交确认 {pending_submit_confirmations} 个，未知题型暂停 {unsupported_paused} 个，待复核样本 {review_backlog} 个。",
            next_step="打开告警/异常处置页处理对应项；批准确认也不会自动执行真实提交。",
        ),
        ScoreLoopReadinessCheck(
            key="manual_stable_gate",
            title="自动提交闸门",
            status="passed" if gate.ready_for_auto_submit else "blocked",
            required=False,
            detail=("稳定样本已满足阈值。" if gate.ready_for_auto_submit else gate.blocked_reason),
            next_step="即使闸门满足，真实提交仍需高危确认队列；无真题阶段保持关闭更安全。",
        ),
        ScoreLoopReadinessCheck(
            key="submit_confirmation_items",
            title="提交确认项处理",
            status="warning" if submit_confirmation_required or pending_submit_confirmations else "passed",
            required=False,
            detail=f"评分样本提交确认状态 {submit_confirmation_required} 个，待处理确认项 {pending_submit_confirmations} 个。",
            next_step="需要真实题演示时，再由人工逐项确认并做真实回读。",
        ),
    ]


def _score_submit_confirmation_pending_count(db: Session) -> int:
    from app.models.ai import AiActionConfirmation, AiActionConfirmationStatus

    return (
        db.query(AiActionConfirmation)
        .filter(AiActionConfirmation.status == AiActionConfirmationStatus.PENDING)
        .filter(AiActionConfirmation.action_key.like("real_submit_score_case_%"))
        .count()
    )


def _record_score_worker_event(
    db: Session,
    account_user_id: str,
    task_id: str,
    severity: str,
    stage: str,
    step: str,
    message: str,
    error_code: str = "",
    error_detail: str = "",
    retryable: Optional[bool] = None,
    duration_ms: Optional[int] = None,
) -> None:
    report_worker_event(
        db,
        WorkerEventReportRequest(
            worker_id=SCORE_LOOP_WORKER_ID,
            event_type="event_report",
            account_user_id=account_user_id,
            task_id=task_id,
            severity=severity,
            stage=stage,
            step=step,
            error_code=error_code,
            error_detail=error_detail,
            retryable=retryable,
            duration_ms=duration_ms,
            message=_redact(message),
        ),
    )


def _task_ai_error_code(reason: str) -> str:
    lowered = reason.lower()
    if "502" in lowered or "bad gateway" in lowered:
        return "AI_PROVIDER_502"
    if "timeout" in lowered or "timed out" in lowered or "超时" in reason:
        return "AI_PROVIDER_TIMEOUT"
    return "AI_RESPONSE_INVALID"


def _case_next_step(case: ScoreLoopCase) -> str:
    status = case.status.value if hasattr(case.status, "value") else str(case.status)
    if status == ScoreLoopCaseStatus.CAPTURED.value:
        return "生成 AI 草稿。"
    if status == ScoreLoopCaseStatus.UNSUPPORTED_PAUSED.value:
        return "未知题型暂停，需补题型规则。"
    if status == ScoreLoopCaseStatus.DRAFT_READY.value:
        return "人工确认最终答案。"
    if status == ScoreLoopCaseStatus.SUBMIT_CONFIRMATION_REQUIRED.value:
        return "到 AI 确认队列输入确认短语；确认也不会自动执行破坏性提交。"
    if status == ScoreLoopCaseStatus.MANUAL_APPROVED.value:
        return "已人工确认，可按需请求提交确认。"
    return "已驳回，重新采集或修改题面。"


def _draft_answer(choices: list[str]) -> str:
    if choices:
        return choices[len(choices) // 2]
    return "人工复核后填写最终答案"


def _draft_reason(case: ScoreLoopCase, choices: list[str]) -> str:
    if choices:
        return _redact(f"首版本地 AI 草稿：题型 {case.task_type_name} 已识别，建议先选择中间档候选 `{_draft_answer(choices)}` 作为人工复核起点；必须人工确认后才可进入提交确认队列。")
    return "首版本地 AI 草稿：未提供候选项，仅生成理由模板；必须人工补充最终答案并确认。"


def _load_choices(case: ScoreLoopCase) -> list[str]:
    try:
        value = json.loads(case.choices_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _hash_question(question: str, choices: list[str]) -> str:
    source = json.dumps({"question": question, "choices": choices}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _get_config(db: Session, key: str, default: str) -> str:
    config = db.get(RuntimeConfig, key)
    return config.value if config and config.value else default


def _set_config(db: Session, key: str, value: str, updated_by: str) -> RuntimeConfig:
    config = db.get(RuntimeConfig, key)
    if config:
        config.value = value
        config.updated_by = updated_by
    else:
        config = RuntimeConfig(key=key, value=value, updated_by=updated_by)
        db.add(config)
    db.flush()
    return config


def _get_int_config(db: Session, key: str, default: int) -> int:
    value = _get_config(db, key, str(default))
    try:
        return int(value)
    except ValueError:
        return default


def _get_bool_config(db: Session, key: str, default: bool) -> bool:
    value = _get_config(db, key, "true" if default else "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _increment_stable_count(db: Session, delta: int) -> int:
    next_value = max(0, _get_int_config(db, MANUAL_STABLE_COUNT_KEY, 0) + delta)
    _set_config(db, MANUAL_STABLE_COUNT_KEY, str(next_value), "score-loop")
    return next_value


def _parse_datetime(value: str):
    if not value:
        return None
    try:
        return datetime_from_iso(value)
    except ValueError:
        return None


def datetime_from_iso(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)


def _redact(value: str) -> str:
    result = value or ""
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda match: match.group(0).split("=")[0].split(":")[0] + "=<redacted>", result)
    return result
