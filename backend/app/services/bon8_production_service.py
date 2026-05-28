import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Optional
from uuid import uuid4

import requests
from sqlalchemy.orm import Session

from app.models.ai import AiActionConfirmation, AiActionConfirmationStatus
from app.models.audit import AuditSeverity
from app.schemas.ai import AiIncidentAction
from app.schemas.ai_timer import AiTimerEventCreate, AiTimerStageDuration
from app.schemas.bon8_production import (
    Bon8FirstConfirmationSheet,
    Bon8ProductionAccountRunState,
    Bon8ProductionItemResult,
    Bon8ProductionItemAttemptState,
    Bon8ProductionRunResponse,
    Bon8ProductionStartRequest,
    Bon8ProductionStatusResponse,
)
from app.services.ai_confirmation_service import create_confirmation_requests
from app.services.audit_service import write_audit
from app.services.bon8_payload_service import build_bon8_submit_temp_payload
from app.services.runtime_account_service import load_runtime_account
from app.services.task_rules import utc_now

BON8_TASK_ID = "7637771731901861641"
BON8_NODE_ID = "1"
CONFIRMATION_PREFIX = "bon8_submit_"
RUN_PREFIX = "bon8-"
TIMER_STAGE_NAMES = {
    "claim": "领题",
    "read": "读题",
    "render": "截图和渲染",
    "upstreamAiElapsedMs": "上游 AI 往返",
    "provider_elapsed_ms": "上游 AI 往返",
    "payloadBuild": "整理答案",
    "categoryBefore": "读提交前状态",
    "submitTemp": "暂存答案",
    "verifySubmit": "提交前检查",
    "submitItem": "正式提交",
    "categoryAfter": "提交后回读",
}
TIMER_STAGE_ORDER = (
    "claim",
    "read",
    "render",
    "upstreamAiElapsedMs",
    "provider_elapsed_ms",
    "payloadBuild",
    "categoryBefore",
    "submitTemp",
    "verifySubmit",
    "submitItem",
    "categoryAfter",
)

AccountLoader = Callable[[str], Optional[dict[str, Any]]]
RemoteTransport = Callable[[dict[str, Any], str, str, dict[str, Any]], dict[str, Any]]


def build_bon8_production_timer_event(
    *,
    account_user_id: str,
    account_name: str = "",
    task_id: str = BON8_TASK_ID,
    item_id: str,
    status: str,
    timings_ms: dict[str, Any],
    source: str = "bon8_production",
) -> AiTimerEventCreate:
    total_ms = _timer_int(timings_ms.get("total"))
    if total_ms <= 0:
        raise ValueError("bon8 每题计时事件必须包含 total 总耗时。")
    stages = [
        AiTimerStageDuration(stage=TIMER_STAGE_NAMES[key], duration_ms=_timer_int(timings_ms.get(key)))
        for key in TIMER_STAGE_ORDER
        if _timer_int(timings_ms.get(key)) > 0
    ]
    if not stages:
        raise ValueError("bon8 每题计时事件必须至少包含一个阶段耗时。")
    return AiTimerEventCreate(
        account_user_id=str(account_user_id),
        account_name=str(account_name or ""),
        task_id=str(task_id),
        task_name="bon8",
        item_id=str(item_id),
        status=str(status),
        source=source,
        total_ms=total_ms,
        stages=stages,
        finished_at=utc_now(),
    )


def build_bon8_production_status(db: Session, manual_first_count: int = 1) -> Bon8ProductionStatusResponse:
    confirmed = min(manual_first_count, _approved_manual_confirmation_count(db))
    remaining = max(0, manual_first_count - confirmed)
    auto_allowed = remaining == 0
    return Bon8ProductionStatusResponse(
        generated_at=utc_now(),
        manual_first_count=manual_first_count,
        manual_confirmed_count=confirmed,
        remaining_manual_confirmations=remaining,
        auto_submit_allowed=auto_allowed,
        next_mode="first_item_review",
        guardrails=_guardrails(),
        message=(
            "bon8 首题审核已通过；下一次启动可进入允许正式启动后的全账号并行生产。"
            if auto_allowed
            else "bon8 一键生产将先生成 1 道首题审核单；人工允许前不会正式提交。"
        ),
    )


def start_bon8_production(
    db: Session,
    request: Bon8ProductionStartRequest,
    *,
    account_loader: AccountLoader = load_runtime_account,
    transport: Optional[RemoteTransport] = None,
    state_dir: Optional[Path] = None,
) -> Bon8ProductionRunResponse:
    remote = transport or _post_aidp
    account_ids = _normalize_account_ids(request.account_user_ids)
    if not account_ids:
        raise ValueError("请至少选择一个生产账号。")
    run_id = f"{RUN_PREFIX}{uuid4().hex[:12]}"
    now = utc_now()
    accounts: list[Bon8ProductionAccountRunState] = []
    items: list[Bon8ProductionItemResult] = []
    attempts: list[Bon8ProductionItemAttemptState] = []
    confirmation_sheet: Optional[Bon8FirstConfirmationSheet] = None
    seed_account_id = ""
    last_error = ""
    loaded_accounts: dict[str, dict[str, Any]] = {}
    for user_id in account_ids:
        account = account_loader(user_id)
        if not account or not account.get("cookie"):
            accounts.append(
                Bon8ProductionAccountRunState(
                    account_user_id=user_id,
                    account_name="",
                    status="isolated_failed",
                    current_stage="account_cookie_missing",
                    isolated_reason="account_cookie_missing",
                    last_error="账号未找到或没有 Cookie，不能参与 bon8 连续生产。",
                )
            )
            items.append(
                _blocked_item(
                    request,
                    user_id,
                    "",
                    "account_cookie_missing",
                    "账号未找到或没有 Cookie，不能参与 bon8 连续生产。",
                )
            )
            continue
        loaded_accounts[user_id] = account
        accounts.append(
            Bon8ProductionAccountRunState(
                account_user_id=user_id,
                account_name=_account_name(account),
                status="waiting_first_gate",
                current_stage="等待首题审核",
            )
        )
    for account_state in accounts:
        account = loaded_accounts.get(account_state.account_user_id)
        if not account:
            continue
        current_items = _fetch_current_items(account, request, remote)
        if not current_items:
            account_state.no_item_count += 1
            account_state.status = "backoff_no_item"
            account_state.current_stage = "未发现当前处理中题"
            account_state.last_error = "当前账号没有处理中 bon8 题。"
            items.append(
                _blocked_item(
                    request,
                    account_state.account_user_id,
                    _account_name(account),
                    "no_current_item",
                    "当前账号没有处理中 bon8 题；自动 operation 领题接口尚未捕获，第一版先处理已领取题。",
                )
            )
            continue
        current_item = current_items[0]
        item_content = _parse_item_content(current_item.get("Content"))
        seed_account_id = account_state.account_user_id
        item_id = str(current_item.get("ItemID") or "")
        attempt_id = f"attempt-{uuid4().hex[:12]}"
        confirmation_id = f"confirm-{uuid4().hex[:12]}"
        account_state.status = "waiting_first_confirm"
        account_state.current_item_id = item_id
        account_state.current_stage = "首题审核单待生成执行器"
        attempts.append(
            Bon8ProductionItemAttemptState(
                attempt_id=attempt_id,
                run_id=run_id,
                account_user_id=seed_account_id,
                task_id=str(request.task_id),
                item_id=item_id,
                stage="waiting_review_executor",
                is_first_review_item=True,
                ai_result_summary={"item_content": item_content},
                payload_check_status="pending",
                temp_save_status="pending",
                verify_submit_status="pending",
                timer_status="pending",
                started_at=now,
                updated_at=now,
            )
        )
        confirmation_sheet = Bon8FirstConfirmationSheet(
            confirmation_id=confirmation_id,
            run_id=run_id,
            attempt_id=attempt_id,
            account_user_id=seed_account_id,
            item_id=item_id,
            status="waiting_review",
            payload_check={"status": "pending", "message": "真实 AI 判题提交执行器待接入。"},
            temp_save_result={"status": "pending", "endpoint": "SubmitTempItemAnswer"},
            verify_submit_result={"status": "pending", "endpoint": "/dispatcher/verify/submit"},
            timings={},
        )
        items.append(
            Bon8ProductionItemResult(
                account_user_id=seed_account_id,
                account_name=_account_name(account),
                task_id=str(request.task_id),
                node_id=str(request.node_id),
                item_id=item_id,
                status="waiting_first_confirm",
                mode="first_item_review",
                confirmation_id=None,
                writes_remote=False,
                message="已创建首题审核运行态；正式提交执行器接入前不会调用 SubmitItem。",
            )
        )
        break
    if not seed_account_id:
        last_error = "所有选中账号都没有可用于首题审核的当前处理中题。"
    if request.write_audit:
        write_audit(
            db,
            event_type="bon8_production_start",
            severity=AuditSeverity.INFO,
            actor="operator",
            target_type="bon8_production",
            target_id=str(request.task_id),
            message=f"bon8 production run start run_id={run_id}, mode=first_item_review, accounts={len(account_ids)}, seed={seed_account_id}",
        )
    db.commit()
    confirmation_count = 1 if confirmation_sheet else 0
    submit_count = 0
    blocked_count = sum(1 for item in items if item.status.startswith("blocked") or item.status in {"account_cookie_missing", "no_current_item"})
    response = Bon8ProductionRunResponse(
        generated_at=utc_now(),
        run_id=run_id,
        status="waiting_first_confirm" if confirmation_sheet else "completed_no_item",
        gate_status="waiting_review" if confirmation_sheet else "no_review_item",
        seed_account_id=seed_account_id,
        confirmation_id=confirmation_sheet.confirmation_id if confirmation_sheet else "",
        started_at=now,
        updated_at=utc_now(),
        mode="first_item_review",
        task_id=str(request.task_id),
        node_id=str(request.node_id),
        selected_account_count=len(account_ids),
        manual_first_count=1,
        manual_confirmed_count=0,
        remaining_manual_confirmations=1 if confirmation_sheet else 0,
        auto_submit_allowed=False,
        confirmation_count=confirmation_count,
        submit_count=submit_count,
        blocked_count=blocked_count,
        items=items,
        accounts=accounts,
        attempts=attempts,
        confirmation_sheet=confirmation_sheet,
        last_error=last_error,
        next_step="审核首题确认单并点击允许正式启动。" if confirmation_sheet else "检查账号是否有 bon8 当前处理中题，或等待 operation 补领接口接入。",
        guardrails=_guardrails(),
        message="已进入首题审核启动；人工允许前不会正式提交。" if confirmation_sheet else last_error,
    )
    _write_run_state(response, state_dir=state_dir)
    return response


def get_bon8_production_run(run_id: str, *, state_dir: Optional[Path] = None) -> Bon8ProductionRunResponse:
    return _read_run_state(run_id, state_dir=state_dir)


def approve_bon8_run_confirmation(run_id: str, confirmation_id: str, *, state_dir: Optional[Path] = None, approved_by: str = "operator") -> Bon8ProductionRunResponse:
    run = _read_run_state(run_id, state_dir=state_dir)
    if not run.confirmation_sheet or run.confirmation_sheet.confirmation_id != confirmation_id:
        raise ValueError("首题确认单不存在或不属于该 run。")
    if run.status != "waiting_first_confirm":
        raise ValueError("当前 run 不在等待首题审核状态。")
    now = utc_now()
    run.mode = "first_item_approved"
    run.status = "waiting_first_submit"
    run.gate_status = "approved_pending_submit"
    run.auto_submit_allowed = False
    run.manual_confirmed_count = 1
    run.remaining_manual_confirmations = 0
    run.updated_at = now
    run.confirmation_sheet.status = "approved"
    run.confirmation_sheet.approved_by = approved_by
    run.confirmation_sheet.approved_at = now
    run.next_step = "首题已允许；下一步必须正式提交首题并回读成功，随后才能切换全账号并行自动提交。"
    run.message = "首题审核已允许，等待正式提交首题和回读。"
    for account in run.accounts:
        if account.status in {"waiting_first_confirm", "waiting_first_gate"}:
            account.status = "waiting_first_submit"
            account.current_stage = "等待首题正式提交和回读"
    _write_run_state(run, state_dir=state_dir)
    return run


def reject_bon8_run_confirmation(run_id: str, confirmation_id: str, *, rejected_reason: str = "", state_dir: Optional[Path] = None) -> Bon8ProductionRunResponse:
    run = _read_run_state(run_id, state_dir=state_dir)
    if not run.confirmation_sheet or run.confirmation_sheet.confirmation_id != confirmation_id:
        raise ValueError("首题确认单不存在或不属于该 run。")
    now = utc_now()
    run.status = "blocked"
    run.gate_status = "rejected"
    run.auto_submit_allowed = False
    run.updated_at = now
    run.last_error = rejected_reason or "首题审核被驳回。"
    run.next_step = "修正 AI 判题或 payload 规则后重新启动首题审核。"
    run.message = "首题审核已驳回，已阻断全账号自动提交。"
    run.confirmation_sheet.status = "rejected"
    run.confirmation_sheet.rejected_reason = rejected_reason
    for account in run.accounts:
        if account.status not in {"isolated_failed", "stopped"}:
            account.status = "stopped"
            account.current_stage = "首题审核驳回"
    _write_run_state(run, state_dir=state_dir)
    return run


def prepare_bon8_first_item_review(
    run_id: str,
    *,
    scores: dict[str, Any],
    sort_models: list[str],
    score_reasons: dict[str, Any],
    account_loader: AccountLoader = load_runtime_account,
    transport: Optional[RemoteTransport] = None,
    state_dir: Optional[Path] = None,
) -> Bon8ProductionRunResponse:
    run = _read_run_state(run_id, state_dir=state_dir)
    if run.status != "waiting_first_confirm" or not run.confirmation_sheet:
        raise ValueError("当前 run 不在首题审核生成状态。")
    sheet = run.confirmation_sheet
    attempt = next((item for item in run.attempts if item.attempt_id == sheet.attempt_id), None)
    if not attempt:
        raise ValueError("首题 attempt 不存在。")
    item_content = attempt.ai_result_summary.get("item_content") if isinstance(attempt.ai_result_summary, dict) else None
    if not isinstance(item_content, dict) or not item_content:
        raise ValueError("首题缺少可生成 payload 的题目内容。")
    account = account_loader(sheet.account_user_id)
    if not account or not account.get("cookie"):
        raise ValueError("首题账号未找到或 Cookie 不可用，不能暂存审核单。")
    started = perf_counter()
    payload_started = perf_counter()
    payload = build_bon8_submit_temp_payload(
        task_id=run.task_id,
        node_id=run.node_id,
        item_id=sheet.item_id,
        item_content=item_content,
        scores=scores,
        sort_models=sort_models,
        score_reasons=score_reasons,
    )
    payload_ms = round((perf_counter() - payload_started) * 1000)
    remote = transport or _post_aidp
    temp_result = remote(account, "api", "/api/dispatch/SubmitTempItemAnswer", payload)
    submit_request = {"TaskID": str(run.task_id), "NodeID": int(run.node_id), "Status": 4, "Answers": payload["AuditAnswers"]}
    verify_result = remote(account, "agw", "/dispatcher/verify/submit", {"SubmitItemRequest": submit_request, "Verifiers": ["ItemRepeatVerifier"]})
    payload_path = _first_review_payload_path(run.run_id, sheet.item_id, state_dir=state_dir)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    temp_ok = _base_status_code(temp_result) == 0
    verify_ok = _base_status_code(verify_result) == 0
    now = utc_now()
    sheet.review_payload_path = str(payload_path)
    sheet.ai_scores = dict(scores)
    sheet.model_order = list(sort_models)
    sheet.reasons = dict(score_reasons)
    sheet.payload_check = {
        "status": "passed",
        "answersCount": len(payload["AuditAnswers"]),
        "forbiddenSubmitItem": True,
        "message": "首题 payload 已生成；review_only 阶段禁止 SubmitItem。",
    }
    sheet.temp_save_result = _compact_remote_result(temp_result)
    sheet.verify_submit_result = _compact_remote_result(verify_result)
    sheet.timings = {
        "payloadBuild": payload_ms,
        "submitTemp": _elapsed_ms_from_result(temp_result),
        "verifySubmit": _elapsed_ms_from_result(verify_result),
        "total": round((perf_counter() - started) * 1000),
    }
    sheet.evidence_path = str(payload_path)
    attempt.payload_check_status = "passed"
    attempt.temp_save_status = "saved" if temp_ok else "failed"
    attempt.verify_submit_status = "verified" if verify_ok else "failed"
    attempt.stage = "waiting_human_review" if temp_ok and verify_ok else "review_prepare_failed"
    attempt.evidence_path = str(payload_path)
    attempt.updated_at = now
    for account_state in run.accounts:
        if account_state.account_user_id == sheet.account_user_id:
            account_state.current_stage = "首题审核单待人工确认" if temp_ok and verify_ok else "首题审核单生成失败"
            if not temp_ok or not verify_ok:
                account_state.status = "isolated_failed"
                account_state.last_error = "首题暂存或提交前校验失败。"
    if not temp_ok or not verify_ok:
        run.status = "blocked"
        run.gate_status = "first_review_prepare_failed"
        run.last_error = "首题暂存或提交前校验失败，已阻断自动提交。"
        run.message = run.last_error
        run.next_step = "查看首题审核证据，修复 payload 或接口问题后重新启动。"
    else:
        run.message = "首题审核单已生成，已完成暂存和提交前校验；等待人工允许。"
        run.next_step = "人工审核评分、勾选、理由、payload、暂存和提交前校验结果后，点击允许首题正式提交。"
    run.updated_at = now
    _write_run_state(run, state_dir=state_dir)
    return run


def submit_approved_bon8_first_item(
    run_id: str,
    *,
    account_loader: AccountLoader = load_runtime_account,
    transport: Optional[RemoteTransport] = None,
    state_dir: Optional[Path] = None,
) -> Bon8ProductionRunResponse:
    run = _read_run_state(run_id, state_dir=state_dir)
    if run.status != "waiting_first_submit" or run.gate_status != "approved_pending_submit":
        raise ValueError("当前 run 不在等待首题正式提交状态。")
    if not run.confirmation_sheet:
        raise ValueError("首题确认单不存在。")
    sheet = run.confirmation_sheet
    payload = _load_first_item_payload(sheet.review_payload_path)
    answers = payload.get("AuditAnswers")
    if not isinstance(answers, list) or not answers:
        raise ValueError("首题确认单 payload 缺少 AuditAnswers，不能正式提交。")
    account = account_loader(sheet.account_user_id)
    if not account or not account.get("cookie"):
        run.status = "blocked"
        run.gate_status = "first_submit_failed"
        run.last_error = "首题账号未找到或 Cookie 不可用，不能正式提交。"
        run.message = run.last_error
        run.next_step = "修复账号 Cookie 后重新启动首题审核。"
        _write_run_state(run, state_dir=state_dir)
        return run

    remote = transport or _post_aidp
    started = perf_counter()
    submit_request = {"TaskID": str(run.task_id), "NodeID": int(run.node_id), "Status": 4, "Answers": answers}
    submit_result = remote(account, "api", "/api/dispatch/SubmitItem", submit_request)
    category_after = remote(account, "agw", "/dispatcher/search_item/category", _category_body(run.task_id, run.node_id))
    total_ms = round((perf_counter() - started) * 1000)
    sheet.submit_result = _compact_remote_result(submit_result)
    sheet.readback_result = _compact_remote_result(category_after)
    sheet.timings = {**(sheet.timings or {}), "submitItem": _elapsed_ms_from_result(submit_result), "categoryAfter": _elapsed_ms_from_result(category_after), "total": total_ms}

    submit_ok = _base_status_code(submit_result) == 0
    readback_ok = _readback_confirms_submitted(category_after, sheet.item_id)
    now = utc_now()
    if not submit_ok or not readback_ok:
        run.status = "blocked"
        run.gate_status = "first_submit_failed"
        run.auto_submit_allowed = False
        run.last_error = "首题正式提交或回读失败，已阻断全账号自动提交。"
        run.message = run.last_error
        run.next_step = "查看首题提交和回读证据，修复后重新启动首题审核。"
        for account_state in run.accounts:
            if account_state.account_user_id == sheet.account_user_id:
                account_state.status = "isolated_failed"
                account_state.current_stage = "首题正式提交失败"
                account_state.last_error = run.last_error
        for attempt in run.attempts:
            if attempt.attempt_id == sheet.attempt_id:
                attempt.stage = "first_submit_failed"
                attempt.submit_status = "submitted" if submit_ok else "failed"
                attempt.readback_status = "readback_ok" if readback_ok else "readback_failed"
                attempt.error_code = "first-submit-or-readback-failed"
                attempt.error_message = run.last_error
                attempt.updated_at = now
                attempt.finished_at = now
        _write_run_state(run, state_dir=state_dir)
        return run

    run.mode = "auto_parallel"
    run.status = "running_auto"
    run.gate_status = "approved"
    run.auto_submit_allowed = True
    run.submit_count = max(1, run.submit_count)
    run.updated_at = now
    run.message = "首题已正式提交并回读成功，已放开全账号并行自动提交。"
    run.next_step = "继续执行全账号并行生产；每个账号内部保持串行。"
    sheet.status = "submitted"
    for account_state in run.accounts:
        if account_state.status in {"waiting_first_submit", "waiting_first_gate"}:
            account_state.status = "running_auto"
            account_state.current_stage = "全账号并行生产中"
        if account_state.account_user_id == sheet.account_user_id:
            account_state.success_count += 1
            account_state.last_submit_at = now
    for attempt in run.attempts:
        if attempt.attempt_id == sheet.attempt_id:
            attempt.stage = "submitted"
            attempt.submit_status = "submitted"
            attempt.readback_status = "readback_ok"
            attempt.timer_status = "pending"
            attempt.updated_at = now
            attempt.finished_at = now
    for item in run.items:
        if item.account_user_id == sheet.account_user_id and item.item_id == sheet.item_id:
            item.status = "submitted"
            item.mode = "first_item_submit"
            item.writes_remote = True
            item.base_resp_status_code = 0
            item.elapsed_ms = total_ms
            item.message = "首题已正式提交并回读成功。"
    _write_run_state(run, state_dir=state_dir)
    return run


def plan_bon8_parallel_account_ticks(run_id: str, *, state_dir: Optional[Path] = None) -> Bon8ProductionRunResponse:
    run = _read_run_state(run_id, state_dir=state_dir)
    if run.mode != "auto_parallel" or run.status != "running_auto":
        raise ValueError("当前 run 尚未进入全账号并行生产状态。")
    now = utc_now()
    changed = False
    for account in run.accounts:
        if account.status != "running_auto":
            continue
        if _has_active_attempt(run, account.account_user_id):
            continue
        attempt_id = f"attempt-{uuid4().hex[:12]}"
        run.attempts.append(
            Bon8ProductionItemAttemptState(
                attempt_id=attempt_id,
                run_id=run.run_id,
                account_user_id=account.account_user_id,
                task_id=run.task_id,
                item_id="",
                stage="queued_account_tick",
                started_at=now,
                updated_at=now,
            )
        )
        account.current_stage = "等待账号生产循环执行"
        changed = True
    if changed:
        run.updated_at = now
        run.next_step = "账号并行 tick 已排队；执行器需逐账号串行处理当前题、提交、回读和计时。"
        run.message = "已为可运行账号排队生产 tick，同账号不会重复并发。"
        _write_run_state(run, state_dir=state_dir)
    return run


def mark_bon8_account_operation_needed(run_id: str, account_user_id: str, *, state_dir: Optional[Path] = None) -> Bon8ProductionRunResponse:
    run = _read_run_state(run_id, state_dir=state_dir)
    account = next((item for item in run.accounts if item.account_user_id == account_user_id), None)
    if not account:
        raise ValueError(f"账号不属于该 run：{account_user_id}")
    if account.status == "waiting_operation_claim":
        return run
    now = utc_now()
    account.status = "waiting_operation_claim"
    account.current_stage = "等待 operation 处理领题接口"
    account.no_item_count += 1
    account.last_error = "operation 处理领题接口尚未捕获；当前账号不会伪造领题成功。"
    target_attempt = next(
        (
            attempt
            for attempt in run.attempts
            if attempt.account_user_id == account_user_id and attempt.stage == "queued_account_tick" and attempt.finished_at is None
        ),
        None,
    )
    if target_attempt is None:
        target_attempt = Bon8ProductionItemAttemptState(
            attempt_id=f"attempt-{uuid4().hex[:12]}",
            run_id=run.run_id,
            account_user_id=account_user_id,
            task_id=run.task_id,
            item_id="",
            stage="operation_claim_needed",
            started_at=now,
            updated_at=now,
            finished_at=now,
            error_code="operation-claim-not-ready",
            error_message="operation 处理领题接口尚未捕获，不会伪造领题成功。",
        )
        run.attempts.append(target_attempt)
    else:
        target_attempt.stage = "operation_claim_needed"
        target_attempt.updated_at = now
        target_attempt.finished_at = now
        target_attempt.error_code = "operation-claim-not-ready"
        target_attempt.error_message = "operation 处理领题接口尚未捕获，不会伪造领题成功。"
    run.updated_at = now
    run.next_step = "账号当前处理中为 0，但 operation 处理领题接口尚未捕获；补领前不反复触发，也不会伪造成功。"
    run.message = "已标记账号等待 operation 处理领题接口。"
    _write_run_state(run, state_dir=state_dir)
    return run


def stop_bon8_production_run(run_id: str, *, state_dir: Optional[Path] = None) -> Bon8ProductionRunResponse:
    run = _read_run_state(run_id, state_dir=state_dir)
    run.status = "stopped"
    run.stop_requested = True
    run.updated_at = utc_now()
    run.next_step = "run 已停止；如需继续请重新启动首题审核。"
    run.message = "bon8 run 已停止。"
    for account in run.accounts:
        if account.status not in {"isolated_failed"}:
            account.status = "stopped"
            account.current_stage = "已停止"
    _write_run_state(run, state_dir=state_dir)
    return run


def _queue_manual_confirmation(
    db: Session,
    request: Bon8ProductionStartRequest,
    account: dict[str, Any],
    current_item: dict[str, Any],
) -> Bon8ProductionItemResult:
    user_id = str(account.get("userId") or account.get("user_id") or "")
    item_id = str(current_item.get("ItemID") or "")
    trace_id = uuid4().hex
    action = AiIncidentAction(
        key=f"{CONFIRMATION_PREFIX}{user_id}_{item_id}",
        title=f"bon8 提交前人工确认 {item_id}",
        risk_level="high",
        status="requires_confirmation",
        requires_confirmation=True,
        allowed_by_policy=False,
        message=f"bon8 连续生产首题必须先审核。账号={user_id}，ItemID={item_id}，确认后才允许进入后续自动提交。",
        rollback_hint="确认前不会提交；如发现 AI 判题有误，驳回确认并重新生成草稿。",
    )
    confirmations = create_confirmation_requests(
        db,
        source_trace_id=trace_id,
        source_ai_job_id=None,
        actions=[action],
        context={"permission_model": "bon8 首题提交前人工确认", "task_id": request.task_id, "item_id": item_id},
        write_audit_enabled=request.write_audit,
    )
    confirmation = confirmations[0]
    return Bon8ProductionItemResult(
        account_user_id=user_id,
        account_name=_account_name(account),
        task_id=str(request.task_id),
        node_id=str(request.node_id),
        item_id=item_id,
        status="confirmation_queued",
        mode="manual_confirmation",
        confirmation_id=confirmation.id,
        writes_remote=False,
        message="已进入提交前确认队列；确认项批准前不会写草稿或提交。",
    )


def _fetch_current_items(
    account: dict[str, Any],
    request: Bon8ProductionStartRequest,
    transport: RemoteTransport,
) -> list[dict[str, Any]]:
    body = {
        "TaskID": str(request.task_id),
        "NodeID": int(request.node_id),
        "ItemCategoryType": 0,
        "Filter": {},
        "PageRequest": {"PageNo": 0, "PageSize": max(1, request.max_items_per_account)},
    }
    result = transport(account, "agw", "/dispatcher/search_item/category", body)
    payload = result.get("body") if isinstance(result, dict) else {}
    data = payload.get("Data") if isinstance(payload, dict) else []
    return [item for item in data if isinstance(item, dict)]


def _category_body(task_id: str, node_id: str) -> dict[str, Any]:
    return {
        "TaskID": str(task_id),
        "NodeID": int(node_id),
        "ItemCategoryType": 0,
        "Filter": {},
        "PageRequest": {"PageNo": 0, "PageSize": 20},
    }


def _parse_item_content(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_review_payload_path(run_id: str, item_id: str, *, state_dir: Optional[Path] = None) -> Path:
    return _state_dir(state_dir) / f"{run_id}-{item_id}-first-review-payload.json"


def _load_first_item_payload(path_value: str) -> dict[str, Any]:
    if not path_value:
        raise ValueError("首题确认单缺少 review_payload_path，不能正式提交。")
    path = Path(path_value)
    if not path.exists():
        raise ValueError(f"首题确认单 payload 不存在：{path_value}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("首题确认单 payload 格式错误。")
    return payload


def _compact_remote_result(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body") if isinstance(result, dict) else {}
    return {
        "statusCode": result.get("statusCode") if isinstance(result, dict) else None,
        "baseRespStatusCode": _base_status_code(result),
        "elapsedMs": _elapsed_ms_from_result(result),
        "totalMap": body.get("TotalMap") if isinstance(body, dict) else None,
    }


def _elapsed_ms_from_result(result: dict[str, Any]) -> int:
    if not isinstance(result, dict):
        return 0
    return _timer_int(result.get("elapsedMs"))


def _base_status_code(result: dict[str, Any]) -> Optional[int]:
    if not isinstance(result, dict):
        return None
    body = result.get("body")
    if not isinstance(body, dict):
        return None
    base_resp = body.get("BaseResp")
    if not isinstance(base_resp, dict):
        return None
    try:
        return int(base_resp.get("StatusCode"))
    except (TypeError, ValueError):
        return None


def _readback_confirms_submitted(result: dict[str, Any], item_id: str) -> bool:
    if _base_status_code(result) != 0:
        return False
    body = result.get("body") if isinstance(result, dict) else {}
    data = body.get("Data") if isinstance(body, dict) else []
    if not isinstance(data, list):
        return False
    return all(str(item.get("ItemID") or "") != str(item_id) for item in data if isinstance(item, dict))


def _has_active_attempt(run: Bon8ProductionRunResponse, account_user_id: str) -> bool:
    final_stages = {
        "submitted",
        "failed",
        "first_submit_failed",
        "review_prepare_failed",
        "blocked",
        "stopped",
    }
    for attempt in run.attempts:
        if attempt.account_user_id != account_user_id:
            continue
        if attempt.finished_at is None and attempt.stage not in final_stages:
            return True
    return False


def _post_aidp(account: dict[str, Any], kind: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    started = perf_counter()
    response = requests.post(f"https://aidp.juejin.cn{path}", headers=_headers(account, kind), json=body, timeout=30)
    try:
        parsed = response.json()
    except Exception:
        parsed = {"parseError": "non-json-response"}
    return {
        "statusCode": response.status_code,
        "elapsedMs": round((perf_counter() - started) * 1000),
        "body": parsed,
        "text": response.text[:2000],
    }


def _headers(account: dict[str, Any], kind: str) -> dict[str, str]:
    referer = str(account.get("referer") or account.get("operationUrl") or "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1")
    result = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://aidp.juejin.cn",
        "Referer": referer,
        "Cookie": str(account.get("cookie") or ""),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
    }
    if kind == "api":
        result.update({"x-secsdk-csrf-token": "DOWNGRADE", "x-backend-org-id": "100", "x-web-org-id": "100"})
    else:
        result.update({"Agw-Js-Conv": "str", "X-JS-REQ": "1", "X-Backend-Side": "4", "X-Backend-Org-Id": "100"})
    return result


def _approved_manual_confirmation_count(db: Session) -> int:
    return (
        db.query(AiActionConfirmation)
        .filter(AiActionConfirmation.action_key.like(f"{CONFIRMATION_PREFIX}%"))
        .filter(AiActionConfirmation.status == AiActionConfirmationStatus.APPROVED)
        .count()
    )


def _normalize_account_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        user_id = str(value or "").strip()
        if user_id and user_id not in result:
            result.append(user_id)
    return result


def _account_name(account: dict[str, Any]) -> str:
    return str(account.get("displayName") or account.get("display_name") or account.get("name") or "")


def _blocked_item(
    request: Bon8ProductionStartRequest,
    user_id: str,
    account_name: str,
    status: str,
    message: str,
) -> Bon8ProductionItemResult:
    return Bon8ProductionItemResult(
        account_user_id=user_id,
        account_name=account_name,
        task_id=str(request.task_id),
        node_id=str(request.node_id),
        status=status,
        mode="blocked",
        writes_remote=False,
        message=message,
    )


def _guardrails() -> list[str]:
    return [
        "一键启动后只生成 1 道首题审核；人工允许前不调用 SubmitItem。",
        "首题允许真实暂存和提交前校验；允许后才进入全账号并行自动提交。",
        "bon8 必须写评分、勾选和理由；只禁写额外审核/废弃备注。",
        "第一版只处理账号当前处理中题；自动 operation 领题接口捕获后再接连续领题。",
        "每完成一道题都必须写入 AI 做题计时事件，记录领题、读题、截图/渲染、上游 AI 往返、暂存、校验、提交和回读耗时。",
        "Cookie/API Key 不进入前端、飞书或普通日志。",
    ]


def _state_dir(state_dir: Optional[Path] = None) -> Path:
    if state_dir:
        return Path(state_dir)
    configured = os.environ.get("AIDP_BON8_RUN_STATE_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "data" / "production-runs" / "bon8-runs"


def _run_state_path(run_id: str, *, state_dir: Optional[Path] = None) -> Path:
    safe_run_id = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in {"-", "_"})
    if not safe_run_id:
        raise ValueError("run_id 不能为空。")
    return _state_dir(state_dir) / f"{safe_run_id}.json"


def _write_run_state(run: Bon8ProductionRunResponse, *, state_dir: Optional[Path] = None) -> None:
    path = _run_state_path(run.run_id, state_dir=state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    if hasattr(run, "model_dump"):
        payload = run.model_dump(mode="json")
    else:
        payload = json.loads(run.json())
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _read_run_state(run_id: str, *, state_dir: Optional[Path] = None) -> Bon8ProductionRunResponse:
    path = _run_state_path(run_id, state_dir=state_dir)
    if not path.exists():
        raise ValueError(f"bon8 run 不存在：{run_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if hasattr(Bon8ProductionRunResponse, "model_validate"):
        return Bon8ProductionRunResponse.model_validate(payload)
    return Bon8ProductionRunResponse.parse_obj(payload)


def _timer_int(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0
