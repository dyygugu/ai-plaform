import json
import re
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Optional
from uuid import uuid4

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.account import AidpAccount
from app.models.ai import AiJob, AiJobStatus
from app.models.audit import AuditLog, AuditSeverity
from app.models.worker import Worker
from app.schemas.ai import (
    AiConfigCheckItem,
    AiConfigCheckResponse,
    AiChatRequest,
    AiChatResponse,
    AiIncidentAction,
    AiIncidentReviewRequest,
    AiIncidentReviewResponse,
    AiProviderConfigRead,
    AiRuntimeConfigRead,
    AiRuntimeConfigUpdate,
)
from app.schemas.alerting import AlertIncident
from app.services.ai_confirmation_service import create_confirmation_requests
from app.services.audit_service import write_audit
from app.services.incident_service import build_incident_summary
from app.services.task_rules import utc_now

_SECRET_PATTERNS = [
    re.compile(r"(cookie|api[_-]?key|token|secret|password|主密钥|恢复码)\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
]

HIGH_RISK_KEYS = {"real_submit", "delete_data", "modify_secret", "switch_domain", "clear_logs", "bulk_disable"}
OPERATOR_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "incident_ai_operator.md"
OPERATOR_PROMPT_FALLBACK = """你是 AIDP Monitor 内置事故处理 AI。
权限模型是最高权限 + 护栏：先读取项目上下文，定位原因，提出最小可回滚动作。
高危动作必须二次确认或明确授权；敏感明文不得进入提示词、飞书、前端、普通日志或报告。"""


def ensure_mock_jobs(db: Session) -> list[AiJob]:
    existing = list(db.scalars(select(AiJob).order_by(AiJob.created_at.desc()).limit(20)))
    if existing:
        return existing
    jobs = [
        AiJob(
            status=AiJobStatus.MOCK_COMPLETED,
            prompt_summary="任务页字段识别 mock",
            result_summary="已识别任务名称ID、任务状态、待处理",
            queue_wait_ms=12,
            upstream_ms=0,
            total_ms=18,
            trace_id=uuid4().hex,
        ),
        AiJob(
            status=AiJobStatus.PROVIDER_GATED,
            prompt_summary="真实 provider 闸门检查",
            result_summary="等待 AllowNetwork 与 provider 配置同时开启",
            queue_wait_ms=0,
            upstream_ms=0,
            total_ms=0,
            trace_id=uuid4().hex,
        ),
    ]
    db.add_all(jobs)
    db.flush()
    return jobs


def get_ai_queue_summary(db: Session) -> tuple[list[AiJob], dict[str, int]]:
    jobs = ensure_mock_jobs(db)
    counts = {"planned": 0, "mock_completed": 0, "provider_gated": 0, "failed": 0}
    for job in jobs:
        counts[job.status.value] = counts.get(job.status.value, 0) + 1
    return jobs, counts


def review_incidents_with_ai(db: Session, request: AiIncidentReviewRequest) -> AiIncidentReviewResponse:
    settings = get_settings()
    provider_config = _provider_config(settings, "system")
    trace_id = uuid4().hex
    started = perf_counter()
    incident_summary = build_incident_summary(db)
    context = _build_incident_context(db, incident_summary)
    local_actions = _build_guarded_actions(incident_summary.incidents, request)
    provider_status = "local_policy"
    provider_text = "未配置 provider 或未请求 provider，使用本地护栏策略完成事故评估。"
    if request.use_provider and provider_config.incident_ai_base_url and provider_config.incident_ai_api_key:
        provider_status, provider_text = _call_incident_provider(provider_config, context)
    elif request.use_provider:
        provider_status = "provider_gated"
        provider_text = "未检测到系统 AI Base URL/API Key，请在 AI 页面配置后再调用 provider。"
    actions = _merge_provider_note(local_actions, provider_text)
    status = _rollup_review_status(incident_summary.status, actions)
    notification_preview = _build_feishu_preview(status, context, actions)
    report_path = _write_incident_ai_report(trace_id, context, actions, provider_status, provider_text) if request.generate_report else None
    elapsed_ms = int((perf_counter() - started) * 1000)
    ai_job = AiJob(
        status=AiJobStatus.MOCK_COMPLETED if provider_status in {"local_policy", "provider_ok", "provider_error"} else AiJobStatus.PROVIDER_GATED,
        prompt_summary=f"事故 AI 处置评估 incidents={incident_summary.total_open}",
        result_summary=_redact(f"status={status}; provider={provider_status}; actions={len(actions)}; {provider_text[:160]}"),
        queue_wait_ms=0,
        upstream_ms=elapsed_ms if provider_status == "provider_ok" else 0,
        total_ms=elapsed_ms,
        trace_id=trace_id,
    )
    db.add(ai_job)
    db.flush()
    confirmation_items = create_confirmation_requests(db, trace_id, ai_job.id, actions, context, write_audit_enabled=request.write_audit)
    audit_trace_id = None
    if request.write_audit:
        audit = write_audit(
            db,
            event_type="incident_ai_review",
            severity=_audit_severity(status),
            actor="system-ai",
            target_type="incident_ai",
            target_id=trace_id,
            message=_redact(f"system AI incident review status={status}, incidents={incident_summary.total_open}, actions={len(actions)}, provider={provider_status}, report={report_path}"),
        )
        audit_trace_id = audit.trace_id
    db.commit()
    return AiIncidentReviewResponse(
        trace_id=trace_id,
        generated_at=utc_now(),
        status=status,
        provider_status=provider_status,
        permission_model="系统 AI：最高权限 + 前置上下文 + 护栏；做题 AI：仅做题调用",
        guardrail_summary="系统 AI 每次先加载项目功能地图和执行规则，可管理做题 AI 前置提示词/skills/md 文件；高危动作需二次确认或明确授权开关，敏感明文不得进入提示词/飞书/前端/普通日志/报告。",
        incident_count=incident_summary.total_open,
        action_count=len(actions),
        auto_executed_count=sum(1 for action in actions if action.status == "auto_executed"),
        confirmation_required_count=sum(1 for action in actions if action.requires_confirmation),
        confirmation_request_count=len(confirmation_items),
        confirmation_ids=[item.id for item in confirmation_items],
        feishu_notification_preview=notification_preview,
        report_path=report_path,
        audit_trace_id=audit_trace_id,
        ai_job_id=ai_job.id,
        context_summary=context,
        actions=actions,
        message="系统 AI 事故处置评估完成；高危动作已进入确认队列，只记录授权与审计，不自动执行破坏性动作。",
    )


def get_ai_runtime_config() -> AiRuntimeConfigRead:
    settings = get_settings()
    runtime = _load_ai_runtime_config()
    system_ai = _provider_read(settings, runtime, "system")
    task_ai = _provider_read(settings, runtime, "task")
    source = "page" if runtime else "env"
    return AiRuntimeConfigRead(
        system_ai=system_ai,
        task_ai=task_ai,
        task_ai_managed_by_system_ai=bool(runtime.get("task_ai_managed_by_system_ai", True)),
        source=source,
        message=_ai_config_message(system_ai, task_ai),
    )


def update_ai_runtime_config(payload: AiRuntimeConfigUpdate) -> AiRuntimeConfigRead:
    current = _load_ai_runtime_config()
    data = {
        "schema_version": 2,
        "system_ai": _provider_update_section(current, "system", payload.system_ai),
        "task_ai": _provider_update_section(current, "task", payload.task_ai),
        "task_ai_managed_by_system_ai": payload.task_ai_managed_by_system_ai,
        "updated_at": utc_now().isoformat(),
    }
    _write_ai_runtime_config(data)
    return get_ai_runtime_config()


def check_ai_runtime_config() -> AiConfigCheckResponse:
    config = get_ai_runtime_config()
    system_ready = bool(config.system_ai.base_url and config.system_ai.api_key_configured and config.system_ai.model)
    task_ready = bool(config.task_ai.base_url and config.task_ai.api_key_configured and config.task_ai.model)
    checks = [
        _config_check_item(
            key="system_ai_provider",
            title="系统 AI Provider",
            passed=system_ready,
            detail=f"base_url={'已填' if config.system_ai.base_url else '未填'}，api_key={'已配置' if config.system_ai.api_key_configured else '未配置'}，model={config.system_ai.model or '未填'}。",
            next_step="填写系统 AI Base URL、API Key 和模型名；用于聊天、运维评估和配置管理。",
        ),
        _config_check_item(
            key="task_ai_provider",
            title="做题 AI Provider",
            passed=task_ready,
            detail=f"base_url={'已填' if config.task_ai.base_url else '未填'}，api_key={'已配置' if config.task_ai.api_key_configured else '未配置'}，model={config.task_ai.model or '未填'}。",
            next_step="填写做题 AI Base URL、API Key 和模型名；仅用于做题/评分草稿链路。",
        ),
        _config_check_item(
            key="task_ai_guardrail",
            title="做题 AI 权限边界",
            passed=config.task_ai_managed_by_system_ai,
            detail="做题 AI 由系统 AI 管理前置提示词、skills 和 md 文件。" if config.task_ai_managed_by_system_ai else "做题 AI 未标记为系统 AI 托管。",
            next_step="保持做题 AI 由系统 AI 托管，避免做题模型越权处理系统动作。",
        ),
    ]
    status = "passed" if all(item.status == "passed" for item in checks) else "warning" if system_ready or task_ready else "blocked"
    return AiConfigCheckResponse(
        status=status,
        ready_for_system_chat=system_ready,
        ready_for_task_draft=task_ready and config.task_ai_managed_by_system_ai,
        source=config.source,
        system_model=config.system_ai.model,
        task_model=config.task_ai.model,
        checks=checks,
        message="AI 配置已满足系统聊天和做题草稿调用条件。" if status == "passed" else "AI 配置未完整；未配置的角色会继续走本地护栏/草稿策略。",
    )


def chat_with_ai(db: Session, request: AiChatRequest) -> AiChatResponse:
    trace_id = uuid4().hex
    context = _build_chat_context(db, request)
    provider_config = _provider_config(get_settings(), "system")
    if request.use_provider and provider_config.incident_ai_base_url and provider_config.incident_ai_api_key:
        provider_status, answer = _call_chat_provider(provider_config, request, context)
    elif request.use_provider:
        provider_status = "provider_gated"
        answer = _local_chat_answer(request.message, context)
    else:
        provider_status = "local_policy"
        answer = _local_chat_answer(request.message, context)
    job = AiJob(
        status=AiJobStatus.MOCK_COMPLETED if provider_status in {"provider_ok", "local_policy"} else AiJobStatus.PROVIDER_GATED,
        prompt_summary=_redact(f"AI 聊天：{request.message[:80]}"),
        result_summary=_redact(answer[:180]),
        queue_wait_ms=0,
        upstream_ms=0,
        total_ms=0,
        trace_id=trace_id,
    )
    db.add(job)
    write_audit(db, event_type="ai_chat", actor="system-ai", message=_redact(f"system AI chat provider={provider_status}, account={request.account_user_id}, task={request.task_id}"), target_type="ai_chat", target_id=trace_id)
    db.commit()
    return AiChatResponse(trace_id=trace_id, provider_status=provider_status, answer=_redact(answer), context_summary=context, message="系统 AI 聊天已完成；高危动作仍需单独确认。")


def _config_check_item(key: str, title: str, passed: bool, detail: str, next_step: str) -> AiConfigCheckItem:
    return AiConfigCheckItem(
        key=key,
        title=title,
        status="passed" if passed else "warning",
        detail=_redact(detail),
        next_step=next_step,
    )


def _build_incident_context(db: Session, incident_summary) -> dict[str, object]:
    accounts = list(db.scalars(select(AidpAccount).order_by(AidpAccount.id.asc()).limit(50)))
    workers = list(db.scalars(select(Worker).order_by(Worker.id.asc()).limit(50)))
    audits = list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(20)))
    severe_audits = [item for item in audits if item.severity in {AuditSeverity.ERROR, AuditSeverity.CRITICAL}]
    task_ai = _provider_config(get_settings(), "task")
    return {
        "operator_context_file": "app/prompts/incident_ai_operator.md",
        "operator_context_loaded": OPERATOR_PROMPT_PATH.exists(),
        "permission_model": "系统 AI 最高权限 + 护栏 + 前置项目上下文；做题 AI 仅做题调用",
        "task_ai_managed_by_system_ai": True,
        "task_ai_pre_prompt": _redact(task_ai.pre_prompt[:1000]),
        "task_ai_skills": task_ai.skills,
        "task_ai_md_files": task_ai.md_files,
        "incident_status": incident_summary.status,
        "open_incidents": incident_summary.total_open,
        "critical_count": incident_summary.critical_count,
        "warning_count": incident_summary.warning_count,
        "account_count": len(accounts),
        "active_account_count": sum(1 for account in accounts if str(account.status.value if hasattr(account.status, "value") else account.status) == "active"),
        "worker_count": len(workers),
        "online_worker_count": sum(1 for worker in workers if str(worker.status.value if hasattr(worker.status, "value") else worker.status) == "online"),
        "recent_audit_count": len(audits),
        "recent_severe_audit_count": len(severe_audits),
        "recent_severe_trace_ids": [audit.trace_id for audit in severe_audits[:5]],
        "incident_keys": [item.key for item in incident_summary.incidents],
        "next_actions": incident_summary.next_actions,
        "risk_notes": incident_summary.risk_notes,
    }


def _build_guarded_actions(incidents: list[AlertIncident], request: AiIncidentReviewRequest) -> list[AiIncidentAction]:
    if not incidents:
        return [
            AiIncidentAction(
                key="no_open_incident",
                title="无开放事故",
                risk_level="low",
                status="auto_executed" if not request.dry_run else "dry_run",
                requires_confirmation=False,
                allowed_by_policy=True,
                message="AI 已完成巡检归因，当前无需修复动作。",
                rollback_hint="无需回滚。",
            )
        ]
    actions: list[AiIncidentAction] = []
    for incident in incidents:
        risk_level = "medium"
        requires_confirmation = False
        action_key = f"review_{incident.key}"
        message = f"AI 评估 {incident.title}：{incident.reason}；建议：{incident.recommended_action}"
        rollback = "该动作只写审计和建议，无需回滚。"
        if incident.key == "collector_guard_not_passed":
            message += "；允许自动建议或触发只读刷新，不允许绕过 Cookie 护栏。"
            rollback = "如刷新结果异常，保留旧任务目录并查看审计 trace。"
        elif incident.key == "account_needs_login":
            message += "；可暂停该账号自动做题并提示重登。"
            rollback = "账号重新登录并通过健康复核后恢复。"
        elif incident.key == "worker_offline":
            message += "；可暂停该 Worker 租约分配并提示检查主机。"
            rollback = "Worker 心跳恢复后重新分配租约。"
        elif incident.key == "release_gate_blocked":
            risk_level = "high"
            requires_confirmation = True
            action_key = "switch_domain"
            message += "；正式域名切换属于高危动作，AI 只能阻止/提示，不能自动切换。"
            rollback = "保持当前反代不变；人工确认 8789 验收通过后再执行。"
        elif incident.key == "backup_missing":
            message += "；可建议执行手动备份，但不得删除旧备份。"
            rollback = "保留备份任务日志，可重新执行备份。"
        elif incident.key == "audit_errors_present":
            risk_level = "medium"
            message += "；按 trace_id 聚合上下文，生成修复建议。"
            rollback = "若建议误判，只撤销对应修复提交，审计日志保留。"
        status = "requires_confirmation" if requires_confirmation else "auto_executed" if not request.dry_run else "dry_run"
        if risk_level == "high" and not request.allow_high_risk:
            status = "requires_confirmation"
        actions.append(
            AiIncidentAction(
                key=action_key,
                title=incident.title,
                risk_level=risk_level,
                status=status,
                requires_confirmation=status == "requires_confirmation",
                allowed_by_policy=risk_level != "high" or request.allow_high_risk,
                message=_redact(message),
                rollback_hint=rollback,
            )
        )
    return actions


def _call_incident_provider(settings, context: dict[str, object]) -> tuple[str, str]:
    endpoint = settings.incident_ai_base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"
    operator_prompt = _system_prompt(settings)
    prompt = _redact(json.dumps(context, ensure_ascii=False))
    payload = {
        "model": settings.incident_ai_model,
        "messages": [
            {"role": "system", "content": operator_prompt},
            {"role": "user", "content": f"本次运行时脱敏上下文如下。请先恢复项目职责和护栏，再评估事故、输出简短原因、最小修复动作、确认项与回滚建议：{prompt}"},
        ],
        "temperature": 0.1,
        "max_tokens": 700,
    }
    headers = {"Authorization": f"Bearer {settings.incident_ai_api_key}", "Content-Type": "application/json"}
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=settings.incident_ai_timeout_seconds)
        response.raise_for_status()
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return "provider_ok", _redact(str(content)[:2000] or "provider 返回为空")
    except Exception as exc:  # noqa: BLE001 - provider failure must not block local incident handling.
        return "provider_error", _redact(f"provider 调用失败，已回退本地策略：{exc}")


def _call_chat_provider(settings, request: AiChatRequest, context: dict[str, object]) -> tuple[str, str]:
    endpoint = settings.incident_ai_base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"
    messages = [{"role": "system", "content": _system_prompt(settings) + "\n你现在是系统处理 AI 聊天助手，负责运维、排障、配置和系统处置；做题答案只解释流程，不直接替代做题 AI。"}]
    for item in request.history[-8:]:
        if item.role in {"user", "assistant"} and item.content.strip():
            messages.append({"role": item.role, "content": _redact(item.content[:1200])})
    messages.append({"role": "user", "content": _redact(f"当前做题生产上下文：{json.dumps(context, ensure_ascii=False)}\n\n用户问题：{request.message}")})
    payload = {"model": settings.incident_ai_model, "messages": messages, "temperature": 0.2, "max_tokens": 900}
    headers = {"Authorization": f"Bearer {settings.incident_ai_api_key}", "Content-Type": "application/json"}
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=settings.incident_ai_timeout_seconds)
        response.raise_for_status()
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return "provider_ok", _redact(str(content)[:3000] or "provider 返回为空")
    except Exception as exc:  # noqa: BLE001 - chat must keep local fallback visible.
        return "provider_error", _local_chat_answer(request.message, context) + f"\n\nProvider 调用失败，已回退本地策略：{_redact(str(exc))}"


def _build_chat_context(db: Session, request: AiChatRequest) -> dict[str, object]:
    accounts = list(db.scalars(select(AidpAccount).order_by(AidpAccount.is_task_source.desc(), AidpAccount.user_id.asc()).limit(20)))
    workers = list(db.scalars(select(Worker).order_by(Worker.updated_at.desc()).limit(10)))
    recent_errors = list(db.scalars(select(AuditLog).where(AuditLog.severity.in_([AuditSeverity.ERROR, AuditSeverity.CRITICAL])).order_by(AuditLog.created_at.desc()).limit(5)))
    selected = next((account for account in accounts if account.user_id == request.account_user_id), None)
    return {
        "product_goal": "AIDP 多账号做题生产控制台，P0-P20 和运维报告只是支撑模块。",
        "account_user_id": request.account_user_id,
        "task_id": request.task_id,
        "selected_account": _account_context(selected) if selected else None,
        "account_count": len(accounts),
        "active_accounts": sum(1 for item in accounts if str(item.status.value if hasattr(item.status, "value") else item.status) == "active"),
        "worker_count": len(workers),
        "recent_errors": [{"event_type": item.event_type, "message": _redact(item.message), "trace_id": item.trace_id} for item in recent_errors],
        "guardrails": ["真实提交必须人工确认", "不泄露 Cookie/API Key/token", "没有真实评分题时不能声明提交回读完成"],
    }


def _account_context(account: Optional[AidpAccount]) -> Optional[dict[str, object]]:
    if account is None:
        return None
    return {
        "user_id": account.user_id,
        "display_name": account.display_name,
        "status": account.status.value if hasattr(account.status, "value") else str(account.status),
        "is_task_source": account.is_task_source,
        "last_error": _redact(account.last_error or ""),
    }


def _local_chat_answer(message: str, context: dict[str, object]) -> str:
    return (
        "我已按本地护栏读取当前生产上下文。\n"
        f"- 当前主线：{context.get('product_goal')}\n"
        f"- 账号：{context.get('account_user_id') or '未指定'}，任务：{context.get('task_id') or '未指定'}\n"
        f"- 最近错误数：{len(context.get('recent_errors') or [])}\n"
        "- 建议：先确认账号登录态和真实待处理数，再处理题面/评分草稿；真实提交必须进入确认队列。\n"
        f"你的问题：{_redact(message)}"
    )


def _provider_config(settings, role: str):
    runtime = _load_ai_runtime_config()
    section = _provider_section(runtime, role)
    fallback_base_url = settings.incident_ai_base_url if role == "system" else ""
    fallback_api_key = settings.incident_ai_api_key if role == "system" else ""
    fallback_model = settings.incident_ai_model if role == "system" else "gpt-4.1-mini"
    return SimpleNamespace(
        role=role,
        incident_ai_base_url=str(section.get("base_url") or fallback_base_url or ""),
        incident_ai_api_key=str(section.get("api_key") or fallback_api_key or ""),
        incident_ai_model=str(section.get("model") or fallback_model or "gpt-4.1-mini"),
        incident_ai_timeout_seconds=_int(section.get("timeout_seconds"), settings.incident_ai_timeout_seconds),
        pre_prompt=str(section.get("pre_prompt") or ""),
        skills=_string_list(section.get("skills")),
        md_files=_string_list(section.get("md_files")),
    )


def _provider_section(runtime: dict[str, Any], role: str) -> dict[str, Any]:
    key = f"{role}_ai"
    section = runtime.get(key)
    if isinstance(section, dict):
        return section
    if role == "system" and any(item in runtime for item in ["base_url", "api_key", "model", "timeout_seconds"]):
        return runtime
    return {}


def _provider_read(settings, runtime: dict[str, Any], role: str) -> AiProviderConfigRead:
    config = _provider_config(settings, role)
    source = "page" if _provider_section(runtime, role) else "env" if role == "system" else "runtime"
    configured = bool(config.incident_ai_base_url and config.incident_ai_api_key)
    is_system = role == "system"
    return AiProviderConfigRead(
        role=role,
        title="系统处理 AI" if is_system else "做题 AI",
        base_url=config.incident_ai_base_url,
        model=config.incident_ai_model,
        api_key_configured=bool(config.incident_ai_api_key),
        timeout_seconds=config.incident_ai_timeout_seconds,
        permission_scope="最高权限，可处理运维、配置、前置上下文、skills 和 md 文件" if is_system else "受限权限，仅在做题/评分草稿链路调用",
        call_scope="内置聊天、事故评估、运维处置和系统配置" if is_system else "做题时生成答案/理由草稿，不处理运维和系统动作",
        pre_prompt=config.pre_prompt,
        skills=config.skills,
        md_files=config.md_files,
        source=source,
        message=("已配置 provider。" if configured else "未完整配置 provider，将使用本地护栏/草稿策略。"),
    )


def _provider_update_section(current: dict[str, Any], role: str, payload) -> dict[str, Any]:
    previous = _provider_section(current, role)
    api_key = payload.api_key.strip() or str(previous.get("api_key") or "")
    return {
        "base_url": payload.base_url.strip(),
        "api_key": api_key,
        "model": payload.model.strip() or str(previous.get("model") or "gpt-4.1-mini"),
        "timeout_seconds": max(5, min(120, payload.timeout_seconds or 30)),
        "pre_prompt": _redact(payload.pre_prompt.strip())[:4000],
        "skills": _string_list(payload.skills)[:20],
        "md_files": _string_list(payload.md_files)[:20],
    }


def _ai_config_message(system_ai: AiProviderConfigRead, task_ai: AiProviderConfigRead) -> str:
    parts = []
    parts.append("系统 AI 已配置" if system_ai.api_key_configured and system_ai.base_url else "系统 AI 未完整配置")
    parts.append("做题 AI 已配置" if task_ai.api_key_configured and task_ai.base_url else "做题 AI 未完整配置")
    return "；".join(parts) + "。系统 AI 可管理做题 AI 前置提示词、skills、md 文件；做题 AI 只在做题链路调用。"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_redact(str(item).strip()) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [_redact(item.strip()) for item in value.replace("\r", "\n").replace(",", "\n").split("\n") if item.strip()]
    return []


def _load_ai_runtime_config() -> dict[str, Any]:
    path = _ai_runtime_config_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _write_ai_runtime_config(data: dict[str, Any]) -> None:
    path = _ai_runtime_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ai_runtime_config_path() -> Path:
    value = get_settings().ai_runtime_config_path
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


def _int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _load_operator_prompt() -> str:
    try:
        content = OPERATOR_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return OPERATOR_PROMPT_FALLBACK
    return content or OPERATOR_PROMPT_FALLBACK


def _system_prompt(settings) -> str:
    base = _load_operator_prompt()
    extra = str(getattr(settings, "pre_prompt", "") or "").strip()
    if not extra:
        return base
    return _redact(base + "\n\n## 页面配置的系统 AI 前置提示词\n" + extra[:4000])


def get_task_ai_runtime_prompt() -> dict[str, object]:
    settings = get_settings()
    task_ai = _provider_config(settings, "task")
    return {
        "provider_configured": bool(task_ai.incident_ai_base_url and task_ai.incident_ai_api_key),
        "base_url": task_ai.incident_ai_base_url,
        "api_key": task_ai.incident_ai_api_key,
        "model": task_ai.incident_ai_model,
        "timeout_seconds": task_ai.incident_ai_timeout_seconds,
        "pre_prompt": task_ai.pre_prompt,
        "skills": task_ai.skills,
        "md_files": task_ai.md_files,
    }


def get_system_ai_runtime_prompt() -> dict[str, object]:
    settings = get_settings()
    system_ai = _provider_config(settings, "system")
    return {
        "provider_configured": bool(system_ai.incident_ai_base_url and system_ai.incident_ai_api_key),
        "base_url": system_ai.incident_ai_base_url,
        "api_key": system_ai.incident_ai_api_key,
        "model": system_ai.incident_ai_model,
        "timeout_seconds": system_ai.incident_ai_timeout_seconds,
        "pre_prompt": system_ai.pre_prompt,
        "skills": system_ai.skills,
        "md_files": system_ai.md_files,
    }


def draft_task_ai_answer(question_text: str, choices: list[str], task_type_name: str, context: Optional[dict[str, object]] = None) -> tuple[str, str, str]:
    runtime = get_task_ai_runtime_prompt()
    if runtime["provider_configured"]:
        return _call_task_provider(runtime, question_text, choices, task_type_name, context or {})
    answer = choices[len(choices) // 2] if choices else "人工复核后填写最终答案"
    reason = _redact(
        f"本地做题 AI 草稿：题型 {task_type_name} 已识别，"
        f"{'建议先选择中间档候选 `' + answer + '` 作为人工复核起点' if choices else '未提供候选项，仅生成理由模板'}；"
        "必须人工确认后才可进入提交确认队列。"
    )
    return answer, reason, "local_policy"


def _call_task_provider(runtime: dict[str, object], question_text: str, choices: list[str], task_type_name: str, context: dict[str, object]) -> tuple[str, str, str]:
    endpoint = str(runtime["base_url"]).rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"
    prompt_parts = [
        "你是 AIDP 做题 AI，只能在做题/评分草稿链路使用，不处理运维、系统配置、密钥、删除、切域名或提交动作。",
        "你只能基于脱敏题面生成候选答案和简短理由；真实提交必须人工确认并进入高危确认队列。",
    ]
    if runtime.get("pre_prompt"):
        prompt_parts.append("系统 AI 注入的做题前置提示词：" + str(runtime["pre_prompt"])[:4000])
    if runtime.get("skills"):
        prompt_parts.append("可用 skills：" + "；".join(str(item) for item in runtime["skills"]))
    if runtime.get("md_files"):
        prompt_parts.append("可参考 md 文件：" + "；".join(str(item) for item in runtime["md_files"]))
    user_payload = _redact(json.dumps({"task_type_name": task_type_name, "question_text": question_text, "choices": choices, "context": context}, ensure_ascii=False))
    payload = {
        "model": runtime["model"],
        "messages": [
            {"role": "system", "content": _redact("\n".join(prompt_parts))},
            {"role": "user", "content": f"请输出 JSON：answer 为候选答案，reason 为一句中文理由。脱敏题面上下文：{user_payload}"},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }
    headers = {"Authorization": f"Bearer {runtime['api_key']}", "Content-Type": "application/json"}
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=int(runtime["timeout_seconds"]))
        response.raise_for_status()
        data = response.json()
        content = str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))
        answer, reason = _parse_task_provider_content(content, choices, task_type_name)
        return answer, reason, "provider_ok"
    except Exception as exc:  # noqa: BLE001 - 做题草稿必须可回退到本地策略。
        answer = choices[len(choices) // 2] if choices else "人工复核后填写最终答案"
        return answer, _redact(f"做题 AI provider 调用失败，已回退本地草稿：{exc}"), "provider_error"


def _parse_task_provider_content(content: str, choices: list[str], task_type_name: str) -> tuple[str, str]:
    text = _redact(content[:2000])
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            answer = str(parsed.get("answer") or "").strip()
            reason = str(parsed.get("reason") or "").strip()
            if answer:
                return answer[:300], _redact(reason[:1000] or f"做题 AI 已为 {task_type_name} 生成草稿。")
    except json.JSONDecodeError:
        pass
    fallback_answer = next((choice for choice in choices if choice and choice in text), choices[len(choices) // 2] if choices else "人工复核后填写最终答案")
    return fallback_answer, text or f"做题 AI 已为 {task_type_name} 生成草稿。"


def _merge_provider_note(actions: list[AiIncidentAction], provider_text: str) -> list[AiIncidentAction]:
    if not actions:
        return actions
    first = actions[0]
    first.message = _redact(first.message + f"；Provider/本地补充：{provider_text[:300]}")
    return actions


def _build_feishu_preview(status: str, context: dict[str, object], actions: list[AiIncidentAction]) -> str:
    severity = "critical" if status == "failed" else "warning" if status == "warning" else "info"
    lines = [
        f"AIDP Monitor 内置 AI 事故处置：{status}",
        f"级别：{severity}",
        f"开放事故：{context.get('open_incidents', 0)}，高危确认：{sum(1 for item in actions if item.requires_confirmation)}",
        "自动动作：" + "；".join(item.title for item in actions[:3]),
        "护栏：高危动作需确认，敏感明文不进入通知。",
    ]
    return _redact("\n".join(lines))


def _write_incident_ai_report(trace_id: str, context: dict[str, object], actions: list[AiIncidentAction], provider_status: str, provider_text: str) -> str:
    report_dir = Path("data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"incident-ai-review-{trace_id}.md"
    lines = [
        f"# 内置 AI 事故处置报告 {trace_id}",
        "",
        "## 权限模型",
        "- 最高权限 + 前置上下文 + 护栏。",
        "- Provider 调用前加载 `app/prompts/incident_ai_operator.md`，先恢复项目功能地图、职责边界和执行顺序。",
        "- 高危动作需二次确认或明确授权开关。",
        "- 敏感明文不得进入提示词、飞书、前端、普通日志或报告。",
        "",
        "## 上下文摘要",
        "```json",
        _redact(json.dumps(context, ensure_ascii=False, indent=2)),
        "```",
        "",
        "## Provider 状态",
        f"- {provider_status}: {_redact(provider_text)}",
        "",
        "## 动作清单",
    ]
    for action in actions:
        lines.extend([
            f"### {action.title}",
            f"- key: {action.key}",
            f"- risk: {action.risk_level}",
            f"- status: {action.status}",
            f"- confirmation: {action.requires_confirmation}",
            f"- message: {_redact(action.message)}",
            f"- rollback: {action.rollback_hint}",
            "",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _rollup_review_status(incident_status: str, actions: list[AiIncidentAction]) -> str:
    if any(action.requires_confirmation and action.risk_level == "high" for action in actions):
        return "blocked"
    if incident_status in {"failed", "warning"}:
        return "warning"
    return "passed"


def _audit_severity(status: str) -> AuditSeverity:
    if status == "blocked":
        return AuditSeverity.WARNING
    if status == "failed":
        return AuditSeverity.ERROR
    if status == "warning":
        return AuditSeverity.WARNING
    return AuditSeverity.INFO


def _redact(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda match: match.group(0).split("=")[0].split(":")[0] + "=<redacted>", result)
    return result
