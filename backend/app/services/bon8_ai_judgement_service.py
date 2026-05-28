import json
import re
from time import perf_counter
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from app.services.ai_service import get_task_ai_runtime_prompt
from app.services.ai_timer_service import record_ai_timer_event
from app.services.bon8_payload_service import MODEL_KEYS
from app.services.bon8_production_service import (
    AccountLoader,
    RemoteTransport,
    _base_status_code,
    _category_body,
    _elapsed_ms_from_result,
    _parse_item_content,
    _post_aidp,
    _read_run_state,
    _readback_confirms_submitted,
    _write_run_state,
    build_bon8_production_timer_event,
    mark_bon8_account_operation_needed,
    plan_bon8_parallel_account_ticks,
    prepare_bon8_first_item_review,
)
from app.schemas.bon8_production import Bon8ProductionItemResult
from app.services.bon8_payload_service import build_bon8_submit_temp_payload
from app.services.runtime_account_service import load_runtime_account
from app.services.task_rules import utc_now

ProviderClient = Callable[[dict[str, Any], dict[str, object]], dict[str, Any]]


def parse_bon8_ai_judgement(content: str, upstream_ai_elapsed_ms: int = 0) -> dict[str, Any]:
    parsed = _parse_json_object(content)
    scores_raw = parsed.get("scores") if isinstance(parsed.get("scores"), dict) else {}
    reasons_raw = parsed.get("scoreReasons") if isinstance(parsed.get("scoreReasons"), dict) else {}
    scores: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for key in MODEL_KEYS:
        value = str(scores_raw.get(key) or "").strip()
        if value not in {"0", "1", "2"}:
            raise ValueError(f"做题 AI 返回的 {key} 分数无效：{value}")
        scores[key] = value
        reason = str(reasons_raw.get(key) or "").strip()
        if not reason:
            raise ValueError(f"做题 AI 未返回 {key} 的评分原因。")
        reasons[key] = reason[:500]
    if sum(1 for score in scores.values() if score == "2") != 1:
        raise ValueError("做题 AI 必须且只能返回一个 2 分最佳模型。")
    best_model = next(key for key, score in scores.items() if score == "2")
    sort_models_raw = parsed.get("sortModels") if isinstance(parsed.get("sortModels"), list) else []
    sort_models = [str(item).strip() for item in sort_models_raw if str(item).strip() in MODEL_KEYS]
    if set(sort_models) != set(MODEL_KEYS):
        sort_models = sorted(MODEL_KEYS, key=lambda key: (-int(scores[key]), key))
    if sort_models[0] != best_model:
        sort_models = [best_model] + [key for key in sort_models if key != best_model]
    summary = str(parsed.get("summary") or f"真实调用做题 AI 完成，AI 往返 {upstream_ai_elapsed_ms} 毫秒。").strip()
    return {
        "scores": scores,
        "scoreReasons": reasons,
        "sortModels": sort_models,
        "bestModel": best_model,
        "summary": summary[:1000],
        "provider_elapsed_ms": int(upstream_ai_elapsed_ms or 0),
    }


def prepare_bon8_first_item_review_with_ai(
    run_id: str,
    *,
    provider_client: Optional[ProviderClient] = None,
    account_loader: AccountLoader = load_runtime_account,
    transport: Optional[RemoteTransport] = None,
    state_dir=None,
) -> Any:
    run = _read_run_state(run_id, state_dir=state_dir)
    if run.status != "waiting_first_confirm" or not run.confirmation_sheet:
        raise ValueError("当前 run 不在首题审核生成状态。")
    sheet = run.confirmation_sheet
    attempt = next((item for item in run.attempts if item.attempt_id == sheet.attempt_id), None)
    if not attempt:
        raise ValueError("首题 attempt 不存在。")
    item_content = attempt.ai_result_summary.get("item_content") if isinstance(attempt.ai_result_summary, dict) else None
    if not isinstance(item_content, dict) or not item_content:
        raise ValueError("首题缺少可供做题 AI 判题的题目内容。")

    runtime = get_task_ai_runtime_prompt()
    provider_result = provider_client(item_content, runtime) if provider_client else _call_bon8_task_ai_provider(item_content, runtime)
    provider_status = str(provider_result.get("provider_status") or "provider_ok")
    judgement = parse_bon8_ai_judgement(str(provider_result.get("content") or ""), int(provider_result.get("elapsed_ms") or 0))
    prepared = prepare_bon8_first_item_review(
        run_id,
        scores=judgement["scores"],
        sort_models=judgement["sortModels"],
        score_reasons=judgement["scoreReasons"],
        account_loader=account_loader,
        transport=transport,
        state_dir=state_dir,
    )
    if prepared.confirmation_sheet:
        prepared.confirmation_sheet.issue_options = {
            **(prepared.confirmation_sheet.issue_options or {}),
            "providerStatus": provider_status,
            "bestModel": judgement["bestModel"],
            "summary": judgement["summary"],
        }
        prepared.confirmation_sheet.timings = {
            **(prepared.confirmation_sheet.timings or {}),
            "provider_elapsed_ms": judgement["provider_elapsed_ms"],
        }
    for item in prepared.attempts:
        if prepared.confirmation_sheet and item.attempt_id == prepared.confirmation_sheet.attempt_id:
            item.ai_result_summary = {
                **(item.ai_result_summary or {}),
                "provider_status": provider_status,
                "bestModel": judgement["bestModel"],
                "summary": judgement["summary"],
                "scores": judgement["scores"],
                "sortModels": judgement["sortModels"],
            }
    _write_run_state(prepared, state_dir=state_dir)
    return prepared


def execute_bon8_account_tick_with_ai(
    run_id: str,
    account_user_id: str,
    *,
    provider_client: Optional[ProviderClient] = None,
    account_loader: AccountLoader = load_runtime_account,
    transport: Optional[RemoteTransport] = None,
    state_dir=None,
    timer_event_log_path: Optional[Path] = None,
) -> Any:
    run = _read_run_state(run_id, state_dir=state_dir)
    if run.mode != "auto_parallel" or run.status != "running_auto":
        raise ValueError("当前 run 尚未进入全账号并行自动提交状态。")
    account_state = next((item for item in run.accounts if item.account_user_id == account_user_id), None)
    if not account_state:
        raise ValueError(f"账号不属于该 run：{account_user_id}")
    if account_state.status != "running_auto":
        raise ValueError(f"账号当前不能执行自动 tick：{account_state.status}")
    account = account_loader(account_user_id)
    if not account or not account.get("cookie"):
        account_state.status = "isolated_failed"
        account_state.current_stage = "账号 Cookie 不可用"
        account_state.last_error = "账号未找到或没有 Cookie，不能执行自动提交。"
        run.updated_at = utc_now()
        _write_run_state(run, state_dir=state_dir)
        return run

    remote = transport or _post_aidp
    queued_attempt = next(
        (
            attempt
            for attempt in run.attempts
            if attempt.account_user_id == account_user_id and attempt.stage == "queued_account_tick" and attempt.finished_at is None
        ),
        None,
    )
    now = utc_now()
    if queued_attempt is None:
        from app.schemas.bon8_production import Bon8ProductionItemAttemptState

        queued_attempt = Bon8ProductionItemAttemptState(
            attempt_id=f"attempt-auto-{int(perf_counter() * 1000000)}",
            run_id=run.run_id,
            account_user_id=account_user_id,
            task_id=run.task_id,
            item_id="",
            stage="queued_account_tick",
            started_at=now,
            updated_at=now,
        )
        run.attempts.append(queued_attempt)

    read_started = perf_counter()
    category_before = remote(account, "agw", "/dispatcher/search_item/category", _category_body(run.task_id, run.node_id))
    read_ms = round((perf_counter() - read_started) * 1000) or _elapsed_ms_from_result(category_before)
    items = _category_items(category_before)
    if not items:
        return mark_bon8_account_operation_needed(run_id, account_user_id, state_dir=state_dir)

    current_item = items[0]
    item_id = str(current_item.get("ItemID") or "")
    item_content = _parse_item_content(current_item.get("Content"))
    if not item_id or not item_content:
        queued_attempt.stage = "failed"
        queued_attempt.error_code = "missing-current-item-content"
        queued_attempt.error_message = "当前题缺少 ItemID 或 Content，已隔离该账号。"
        queued_attempt.finished_at = utc_now()
        queued_attempt.updated_at = queued_attempt.finished_at
        account_state.status = "isolated_failed"
        account_state.current_stage = "当前题内容缺失"
        account_state.last_error = queued_attempt.error_message
        run.updated_at = queued_attempt.finished_at
        _write_run_state(run, state_dir=state_dir)
        return run

    queued_attempt.item_id = item_id
    queued_attempt.stage = "running_auto_ai"
    queued_attempt.ai_result_summary = {"item_content": item_content}
    account_state.current_item_id = item_id
    account_state.current_stage = "自动 AI 判题提交中"

    runtime = get_task_ai_runtime_prompt()
    provider_result = provider_client(item_content, runtime) if provider_client else _call_bon8_task_ai_provider(item_content, runtime)
    provider_status = str(provider_result.get("provider_status") or "provider_ok")
    judgement = parse_bon8_ai_judgement(str(provider_result.get("content") or ""), int(provider_result.get("elapsed_ms") or 0))

    payload_started = perf_counter()
    payload = build_bon8_submit_temp_payload(
        task_id=run.task_id,
        node_id=run.node_id,
        item_id=item_id,
        item_content=item_content,
        scores=judgement["scores"],
        sort_models=judgement["sortModels"],
        score_reasons=judgement["scoreReasons"],
    )
    payload_ms = round((perf_counter() - payload_started) * 1000)
    temp_result = remote(account, "api", "/api/dispatch/SubmitTempItemAnswer", payload)
    submit_request = {"TaskID": str(run.task_id), "NodeID": int(run.node_id), "Status": 4, "Answers": payload["AuditAnswers"]}
    verify_result = remote(account, "agw", "/dispatcher/verify/submit", {"SubmitItemRequest": submit_request, "Verifiers": ["ItemRepeatVerifier"]})
    submit_result = remote(account, "api", "/api/dispatch/SubmitItem", submit_request)
    category_after = remote(account, "agw", "/dispatcher/search_item/category", _category_body(run.task_id, run.node_id))

    timings = {
        "read": read_ms,
        "provider_elapsed_ms": judgement["provider_elapsed_ms"],
        "payloadBuild": payload_ms,
        "submitTemp": _elapsed_ms_from_result(temp_result),
        "verifySubmit": _elapsed_ms_from_result(verify_result),
        "submitItem": _elapsed_ms_from_result(submit_result),
        "categoryAfter": _elapsed_ms_from_result(category_after),
    }
    timings["total"] = sum(value for value in timings.values() if isinstance(value, int) and value > 0)
    submit_ok = _base_status_code(temp_result) == 0 and _base_status_code(verify_result) == 0 and _base_status_code(submit_result) == 0
    readback_ok = _readback_confirms_submitted(category_after, item_id)
    success = submit_ok and readback_ok
    finished_at = utc_now()

    queued_attempt.ai_result_summary = {
        "provider_status": provider_status,
        "bestModel": judgement["bestModel"],
        "summary": judgement["summary"],
        "scores": judgement["scores"],
        "sortModels": judgement["sortModels"],
    }
    queued_attempt.payload_check_status = "passed"
    queued_attempt.temp_save_status = "saved" if _base_status_code(temp_result) == 0 else "failed"
    queued_attempt.verify_submit_status = "verified" if _base_status_code(verify_result) == 0 else "failed"
    queued_attempt.submit_status = "submitted" if _base_status_code(submit_result) == 0 else "failed"
    queued_attempt.readback_status = "readback_ok" if readback_ok else "readback_failed"
    queued_attempt.stage = "submitted" if success else "failed"
    queued_attempt.updated_at = finished_at
    queued_attempt.finished_at = finished_at
    if not success:
        queued_attempt.error_code = "auto-submit-or-readback-failed"
        queued_attempt.error_message = "自动提交或回读失败。"

    run.items.append(
        Bon8ProductionItemResult(
            account_user_id=account_user_id,
            account_name=account_state.account_name,
            task_id=run.task_id,
            node_id=run.node_id,
            item_id=item_id,
            status="submitted" if success else "failed",
            mode="auto_parallel",
            writes_remote=True,
            base_resp_status_code=_base_status_code(submit_result),
            elapsed_ms=timings["total"],
            message="自动提交并回读成功。" if success else "自动提交或回读失败。",
        )
    )
    if success:
        account_state.success_count += 1
        account_state.status = "running_auto"
        account_state.current_stage = "自动提交成功，等待下一轮 tick"
        account_state.last_submit_at = finished_at
        run.submit_count += 1
    else:
        account_state.failed_count += 1
        account_state.status = "isolated_failed"
        account_state.current_stage = "自动提交失败"
        account_state.last_error = queued_attempt.error_message
        run.status = "blocked"
        run.gate_status = "auto_submit_failed"
        run.auto_submit_allowed = False
        run.last_error = queued_attempt.error_message
    event = build_bon8_production_timer_event(
        account_user_id=account_user_id,
        account_name=account_state.account_name,
        task_id=run.task_id,
        item_id=item_id,
        status="submitted" if success else "failed",
        timings_ms=timings,
    )
    record_ai_timer_event(event, event_log_path=timer_event_log_path)
    queued_attempt.timer_status = "recorded"
    run.updated_at = finished_at
    run.message = "账号自动 tick 已提交并回读成功。" if success else "账号自动 tick 失败，已阻断 run。"
    run.next_step = "继续调度该账号下一轮 tick；处理中为 0 时再触发 operation 补领。" if success else "查看该账号提交/回读证据，修复后重新启动。"
    _write_run_state(run, state_dir=state_dir)
    return run


def execute_bon8_run_tick_with_ai(
    run_id: str,
    *,
    provider_client: Optional[ProviderClient] = None,
    account_loader: AccountLoader = load_runtime_account,
    transport: Optional[RemoteTransport] = None,
    state_dir=None,
    timer_event_log_path: Optional[Path] = None,
    max_accounts: int = 20,
) -> Any:
    planned = plan_bon8_parallel_account_ticks(run_id, state_dir=state_dir)
    if planned.mode != "auto_parallel" or planned.status != "running_auto":
        raise ValueError("当前 run 尚未进入全账号并行自动提交状态。")
    account_ids: list[str] = []
    for attempt in planned.attempts:
        if attempt.stage != "queued_account_tick" or attempt.finished_at is not None:
            continue
        if attempt.account_user_id in account_ids:
            continue
        account = next((item for item in planned.accounts if item.account_user_id == attempt.account_user_id), None)
        if account and account.status == "running_auto":
            account_ids.append(attempt.account_user_id)
        if len(account_ids) >= max(1, int(max_accounts or 1)):
            break
    latest = planned
    for account_user_id in account_ids:
        latest = execute_bon8_account_tick_with_ai(
            run_id,
            account_user_id,
            provider_client=provider_client,
            account_loader=account_loader,
            transport=transport,
            state_dir=state_dir,
            timer_event_log_path=timer_event_log_path,
        )
        if latest.status != "running_auto":
            break
    if not account_ids:
        latest.message = "本轮 run tick 没有可执行账号；可能正在等待 operation 补领或账号已隔离。"
        latest.next_step = "检查账号状态；需要补领的账号等待 operation 处理接口。"
        latest.updated_at = utc_now()
        _write_run_state(latest, state_dir=state_dir)
    return latest


def _call_bon8_task_ai_provider(item_content: dict[str, Any], runtime: dict[str, object]) -> dict[str, Any]:
    if not runtime.get("provider_configured"):
        raise ValueError("做题 AI provider 未配置，不能生成 bon8 首题审核单。")
    endpoint = str(runtime.get("base_url") or "").rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    started = perf_counter()
    payload = {
        "model": runtime.get("model") or "gpt-4.1-mini",
        "messages": _build_bon8_provider_messages(item_content, runtime),
        "temperature": 0.1,
        "max_tokens": 1400,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {runtime.get('api_key')}", "Content-Type": "application/json"}
    response = requests.post(endpoint, headers=headers, json=payload, timeout=int(runtime.get("timeout_seconds") or 30))
    response.raise_for_status()
    data = response.json()
    content = str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))
    return {
        "content": content,
        "elapsed_ms": round((perf_counter() - started) * 1000),
        "provider_status": "provider_ok",
    }


def _build_bon8_provider_messages(item_content: dict[str, Any], runtime: dict[str, object]) -> list[dict[str, str]]:
    system_parts = [
        "你是 AIDP bon8 做题 AI，只能输出评分 JSON，不允许提交、领取、切换账号、改配置或处理系统动作。",
        "评分必须覆盖 model1 到 model8，每个模型分数只能是字符串 0、1、2，且必须且只能有一个 2 分最佳模型。",
        "必须给每个模型中文评分原因，并给 sortModels 排序，最佳模型排第一。",
    ]
    if runtime.get("pre_prompt"):
        system_parts.append("系统 AI 注入的做题前置提示词：" + str(runtime["pre_prompt"])[:4000])
    if runtime.get("skills"):
        system_parts.append("可用 skills：" + "；".join(str(item) for item in runtime["skills"]))
    if runtime.get("md_files"):
        system_parts.append("可参考 md 文件：" + "；".join(str(item) for item in runtime["md_files"]))
    user_payload = json.dumps({"task": "bon8", "item_content": item_content}, ensure_ascii=False)
    return [
        {"role": "system", "content": "\n".join(system_parts)},
        {
            "role": "user",
            "content": (
                "请严格输出 JSON 对象："
                "{\"scores\":{\"model1\":\"1\"},\"scoreReasons\":{\"model1\":\"中文理由\"},"
                "\"sortModels\":[\"model1\"],\"bestModel\":\"model1\",\"summary\":\"一句中文总结\"}。\n"
                "题面内容如下：\n" + user_payload[:12000]
            ),
        },
    ]


def _category_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    body = result.get("body") if isinstance(result, dict) else {}
    data = body.get("Data") if isinstance(body, dict) else []
    return [item for item in data if isinstance(item, dict)]


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("做题 AI provider 未返回有效 JSON。")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("做题 AI provider 输出必须是 JSON 对象。")
    return parsed
