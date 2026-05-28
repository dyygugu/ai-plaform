import json
import os
import re
import base64
import time
import hashlib
from contextlib import suppress
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse, urlunparse
from urllib.parse import quote
from uuid import uuid4

import requests
import websocket
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.schemas.submitted_history import SubmittedHistorySampleRead, TestsetRead
from app.models.task import TaskCatalogItem
from app.services.learning_package_service import get_selected_learning_package_summary, list_task_learning_packages, resolve_learning_package_id
from app.schemas.task_ability import TaskAbilityDraftCreateRequest, TaskAbilityDraftListResponse, TaskAbilityDraftRead
from app.services.ai_service import get_system_ai_runtime_prompt, get_task_ai_runtime_prompt
from app.services.production_dashboard_service import create_browser_open_session
from app.services.submitted_history_service import get_submitted_history_sample, read_testset
from app.services.task_capability_service import TaskCapabilityError, build_http_question_context


RESEARCH_CHART_TASK_ID = "7638992213846740763"
RESEARCH_CHART_FULL_DATASET_TASK_ID = "7639402643386830630"
RESEARCH_CHART_TASK_IDS = {RESEARCH_CHART_TASK_ID, RESEARCH_CHART_FULL_DATASET_TASK_ID}
BON8_TASK_ID = "7637771731901861641"
BON8_TASK_ABILITY_DRAFT_ID = "bon8-task-ability"
BON8_TASK_NAME = "RFT人标支持VLM Coding（bon8草图与流程图）-正式队列"
MGET_ANSWER_LIST_ENDPOINT = "/api/dispatch/MGetAnswerList"
SUBMIT_TEMP_ENDPOINT = "/api/dispatch/SubmitTempItemAnswer"
SEARCH_ITEM_CATEGORY_ENDPOINT = "/dispatcher/search_item/category"
PRE_RECEIVE_ENDPOINT = "/api/dispatch/PreReceive"
RECEIVE_ENDPOINT = "/api/dispatch/Receive"
REPLAY_MAX_PARALLEL_SAMPLES = 10
CDP_WAIT_TIMEOUT_SECONDS = 20
CDP_NAVIGATION_SETTLE_SECONDS = 12
UNEXPLAINED_RESEARCH_CHART_REASONS = {
    "没对上",
    "差一点",
    "可以",
    "细节差点",
    "差太多",
    "不像",
    "偏差大",
    "问题大",
    "基本像",
    "没问题",
    "很像",
    "还原好",
}


def list_task_ability_drafts() -> TaskAbilityDraftListResponse:
    items = _load_items()
    drafts = [TaskAbilityDraftRead(**item) for item in items]
    return TaskAbilityDraftListResponse(
        generated_at=_now(),
        total=len(drafts),
        latest_draft=drafts[0] if drafts else None,
        items=drafts,
        message="任务定制能力草稿来自用户提交材料和系统 AI 制作结果。",
    )


def get_task_ability_draft_by_task(task_id: str, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_latest_draft_by_task_id(items, task_id)
    packages = list_task_learning_packages(task_id, root_dir=path.parent)
    return {
        **draft,
        "learning_packages": packages.model_dump(mode="json"),
    }


def get_enabled_task_ability_draft(task_id: str, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    task_id_text = str(task_id)
    return next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("task_id") or "") == task_id_text
            and bool(item.get("capability_enabled"))
            and str(item.get("flow_stage") or "") == "capability_enabled"
        ),
        {},
    )


def create_task_ability_draft(payload: TaskAbilityDraftCreateRequest) -> TaskAbilityDraftRead:
    items = _load_items()
    now = _now()
    draft = TaskAbilityDraftRead(
        id=uuid4().hex,
        version=f"ability-{now.strftime('%Y%m%d')}-{len(items) + 1:03d}",
        status="草稿",
        task_name=payload.task_name.strip(),
        task_id=payload.task_id.strip(),
        specific_rules=payload.specific_rules.strip(),
        sample_data=payload.sample_data.strip(),
        related_content=payload.related_content.strip(),
        system_ai_draft=payload.system_ai_draft.strip(),
        system_ai_trace_id=payload.system_ai_trace_id,
        provider_status=payload.provider_status,
        next_step="人工审核草稿，确认后进入真实题不提交验证。",
        flow_stage="draft_ready",
        capability_enabled=False,
        real_no_submit_review={},
        task_queue_snapshot={},
        created_at=now,
        updated_at=now,
    )
    items.insert(0, draft.model_dump(mode="json"))
    _write_items(items)
    return draft


def update_task_ability_draft(draft_id: str, updates: dict[str, Any], *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_draft(items, draft_id)
    changed_prompt_fields = False
    for key in ("task_name", "task_id", "specific_rules", "sample_data", "related_content", "system_ai_draft", "system_ai_trace_id", "provider_status"):
        if key not in updates:
            continue
        value = updates[key]
        if value is None:
            continue
        text = str(value).strip()
        if key in {"task_name", "task_id", "specific_rules", "sample_data", "system_ai_draft"} and not text:
            continue
        if str(draft.get(key) or "") != text:
            draft[key] = text
            if key in {"specific_rules", "sample_data", "related_content", "system_ai_draft"}:
                changed_prompt_fields = True
    if changed_prompt_fields:
        draft["status"] = "草稿已确认"
        draft["flow_stage"] = "real_no_submit_ready"
        draft["capability_enabled"] = False
        draft["next_step"] = "Prompt 或规则已修改，需重新执行真实题不提交验证。"
        draft["real_no_submit_review"] = {
            "review_status": "待重新验证",
            "previous_review_status": (draft.get("real_no_submit_review") or {}).get("review_status") if isinstance(draft.get("real_no_submit_review"), dict) else "",
        }
    draft["updated_at"] = _now().isoformat()
    _write_items_to_path(path, items)
    return draft


def update_task_ability_prompt_by_task(task_id: str, updates: dict[str, Any], *, store_path: Optional[Path] = None) -> dict[str, Any]:
    draft = get_task_ability_draft_by_task(task_id, store_path=store_path)
    return update_task_ability_draft(str(draft.get("id") or ""), updates, store_path=store_path)


def create_prompt_snapshot(task_id: str, *, note: str = "", store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_latest_draft_by_task_id(items, task_id)
    snapshot_id = f"prompt-{uuid4().hex[:10]}"
    snapshot = {
        "snapshot_id": snapshot_id,
        "task_id": str(task_id),
        "draft_id": str(draft.get("id") or ""),
        "task_name": str(draft.get("task_name") or ""),
        "ability_version": str(draft.get("version") or ""),
        "system_ai_draft": str(draft.get("system_ai_draft") or ""),
        "specific_rules": str(draft.get("specific_rules") or ""),
        "sample_data": str(draft.get("sample_data") or ""),
        "related_content": str(draft.get("related_content") or ""),
        "note": str(note or ""),
        "created_at": _now().isoformat(),
    }
    snapshot_path = _prompt_snapshot_dir(path, task_id) / f"{snapshot_id}.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    snapshot["path"] = str(snapshot_path)
    return snapshot


def list_prompt_snapshots(task_id: str, *, store_path: Optional[Path] = None) -> list[dict[str, Any]]:
    path = store_path or _store_path()
    snapshot_dir = _prompt_snapshot_dir(path, task_id)
    snapshots: list[dict[str, Any]] = []
    if not snapshot_dir.exists():
        return snapshots
    for snapshot_path in snapshot_dir.glob("*.json"):
        snapshot = _load_json(snapshot_path)
        if not isinstance(snapshot, dict):
            continue
        snapshots.append(
            {
                **snapshot,
                "snapshot_id": str(snapshot.get("snapshot_id") or snapshot_path.stem),
                "task_id": str(snapshot.get("task_id") or task_id),
                "path": str(snapshot_path),
            }
        )
    snapshots.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("snapshot_id") or "")), reverse=True)
    return snapshots


def restore_prompt_snapshot(task_id: str, snapshot_id: str, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    snapshot_path = _prompt_snapshot_dir(path, task_id) / f"{snapshot_id}.json"
    if not snapshot_path.exists():
        raise FileNotFoundError(f"prompt snapshot not found: {snapshot_id}")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
    draft_id = str(snapshot.get("draft_id") or "")
    if not draft_id:
        raise TaskAbilityFlowError("Prompt 快照缺少 draft_id，不能恢复。")
    return update_task_ability_draft(
        draft_id,
        {
            "system_ai_draft": str(snapshot.get("system_ai_draft") or ""),
            "specific_rules": str(snapshot.get("specific_rules") or ""),
            "sample_data": str(snapshot.get("sample_data") or ""),
            "related_content": str(snapshot.get("related_content") or ""),
        },
        store_path=path,
    )


def approve_task_ability_version(task_id: str, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_latest_draft_by_task_id(items, task_id)
    if bool(draft.get("capability_enabled")) and str(draft.get("flow_stage") or "") == "capability_enabled":
        return {
            "ok": True,
            "draft_id": str(draft.get("id") or ""),
            "status": str(draft.get("status") or "有做题能力"),
            "flow_stage": "capability_enabled",
            "capability_enabled": True,
            "message": "当前能力版本已经处于可运行状态。",
        }
    return approve_task_ability_real_no_submit(str(draft.get("id") or ""), store_path=path)


def run_task_ability_live_http_test(
    task_id: str,
    *,
    store_path: Optional[Path] = None,
    review_root: Optional[Path] = None,
    account_user_id: str = "",
    use_system_ai_for_vision: bool = True,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_latest_draft_by_task_id(items, task_id)
    artifact = run_task_ability_real_no_submit(
        str(draft.get("id") or ""),
        store_path=path,
        review_root=review_root,
        db=db,
        allow_temp_save=True,
        target_account_user_id=account_user_id,
        use_system_ai_for_vision=use_system_ai_for_vision,
    )
    report_path = str(artifact.get("review_artifact_path") or "")
    return {
        **artifact,
        "task_id": str(task_id),
        "task_name": str(draft.get("task_name") or artifact.get("task_name") or ""),
        "draft_id": str(draft.get("id") or artifact.get("draft_id") or ""),
        "report_id": Path(report_path).stem if report_path else "",
    }


def get_task_ability_live_http_test_report(
    task_id: str,
    report_id: str,
    *,
    store_path: Optional[Path] = None,
    review_root: Optional[Path] = None,
) -> dict[str, Any]:
    path = store_path or _store_path()
    root = review_root or _default_review_root(path, {"task_id": task_id})
    report_path = root / f"{report_id}.json"
    if not report_path.exists():
        raise FileNotFoundError(f"live http test report not found: {report_id}")
    artifact = json.loads(report_path.read_text(encoding="utf-8-sig"))
    artifact["report_id"] = report_id
    artifact["task_id"] = str(task_id)
    return artifact


def get_latest_task_ability_live_http_test_report(
    task_id: str,
    *,
    store_path: Optional[Path] = None,
    review_root: Optional[Path] = None,
) -> dict[str, Any]:
    path = store_path or _store_path()
    root = review_root or _default_review_root(path, {"task_id": task_id})
    latest_path = _latest_json_artifact(root)
    if latest_path is None:
        raise FileNotFoundError(f"live http test report not found for task: {task_id}")
    artifact = json.loads(latest_path.read_text(encoding="utf-8-sig"))
    artifact["report_id"] = latest_path.stem
    artifact["task_id"] = str(task_id)
    return artifact


def get_task_ability_run_gate(task_id: str, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    try:
        draft = _find_latest_draft_by_task_id(items, task_id)
    except TaskAbilityFlowError:
        return {
            "ok": False,
            "task_id": str(task_id),
            "task_name": "",
            "draft_id": "",
            "ability_version": "",
            "flow_stage": "",
            "capability_enabled": False,
            "review_status": "",
            "approved_at": "",
            "live_test_report": {},
            "last_trial_run": {},
            "last_production_run": {},
            "can_approve": False,
            "can_start_trial": False,
            "can_start_production": False,
            "next_step": "当前任务还没有能力草稿，先完成 Step2 能力调教。",
            "message": "能力草稿不存在，Step4 门禁不可用。",
        }
    review = draft.get("real_no_submit_review") if isinstance(draft.get("real_no_submit_review"), dict) else {}
    live_test_report: dict[str, Any] = {}
    try:
        live_test_report = get_latest_task_ability_live_http_test_report(task_id, store_path=path)
    except FileNotFoundError:
        live_test_report = {}
    state = _load_task_ability_run_state(path, task_id)
    run_config = get_task_ability_run_config(task_id, store_path=path)
    capability_enabled = bool(draft.get("capability_enabled")) and str(draft.get("flow_stage") or "") == "capability_enabled"
    can_approve = bool(review.get("saved_to_task_ui")) and not capability_enabled
    can_start_trial = capability_enabled
    can_start_production = capability_enabled and bool((state.get("last_trial_run") or {}).get("run_id"))
    if can_approve:
        next_step = "Live 暂存验证已经写入真实做题界面，可以人工批准当前能力版本。"
    elif not capability_enabled:
        next_step = "先完成 Live 暂存验证并人工批准能力版本。"
    elif not can_start_production:
        next_step = "请先启动一次试运行，再由人工拍板是否进入生产运行。"
    else:
        next_step = "试运行记录已存在，可以人工决定是否启动生产运行。"
    return {
        "ok": True,
        "task_id": str(task_id),
        "task_name": str(draft.get("task_name") or ""),
        "draft_id": str(draft.get("id") or ""),
        "ability_version": str(draft.get("version") or ""),
        "flow_stage": str(draft.get("flow_stage") or ""),
        "capability_enabled": capability_enabled,
        "review_status": str(review.get("review_status") or ""),
        "approved_at": str(review.get("approved_at") or ""),
        "live_test_report": live_test_report,
        "last_trial_run": state.get("last_trial_run") if isinstance(state.get("last_trial_run"), dict) else {},
        "last_production_run": state.get("last_production_run") if isinstance(state.get("last_production_run"), dict) else {},
        "can_approve": can_approve,
        "can_start_trial": can_start_trial,
        "can_start_production": can_start_production,
        "run_config": run_config,
        "next_step": next_step,
        "message": "Step4 门禁状态已汇总，可据此执行批准、试运行和生产运行。",
    }


def chat_task_ability(
    db: Optional[Session],
    task_id: str,
    payload: dict[str, Any],
    *,
    store_path: Optional[Path] = None,
) -> dict[str, Any]:
    del db
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_latest_draft_by_task_id(items, task_id)
    testset_summary = _task_testset_summary(task_id, store_path=path)
    selected_learning_package_id = _chat_learning_package_id(payload)
    try:
        selected_learning_package_id = resolve_learning_package_id(task_id, selected_learning_package_id, root_dir=path.parent)
    except FileNotFoundError as exc:
        raise TaskAbilityFlowError(str(exc)) from exc
    learning_package_summary = get_selected_learning_package_summary(task_id, selected_learning_package_id, root_dir=path.parent)
    latest_replay_summary = load_latest_replay_summary(task_id, draft, store_path=path)
    user_message = str(payload.get("message") or "").strip()
    message = _build_task_ability_chat_message(
        task_id=task_id,
        task_name=str(draft.get("task_name") or ""),
        draft=draft,
        testset_summary=testset_summary,
        learning_package_summary=learning_package_summary.summary_text,
        latest_replay_summary=latest_replay_summary,
        user_message=user_message,
    )
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    use_provider = bool(payload.get("use_provider", True))
    runtime = get_system_ai_runtime_prompt()
    if use_provider and runtime.get("provider_configured"):
        try:
            answer = _call_task_workspace_chat_provider(runtime, message, history)
            provider_status = "provider_ok"
        except Exception as exc:  # noqa: BLE001 - Step2 chat should not hard-fail when provider is unavailable.
            provider_status = "provider_error_fallback"
            answer = _local_task_ability_chat_answer(user_message, draft, testset_summary, latest_replay_summary) + f"\n\n系统 AI 服务暂不可用：{_sanitize_provider_error(str(exc))}"
    else:
        answer = _local_task_ability_chat_answer(user_message, draft, testset_summary, latest_replay_summary)
        provider_status = "local_workspace_fallback"
    return {
        "trace_id": uuid4().hex,
        "provider_status": provider_status,
        "answer": answer,
        "context_summary": {
            "task_id": str(task_id),
            "task_name": str(draft.get("task_name") or ""),
            "ability_version": str(draft.get("version") or ""),
            "testset_summary": testset_summary,
            "selected_learning_package_id": learning_package_summary.learning_package_id,
            "learning_package_summary": learning_package_summary.summary_text,
            "latest_replay_summary": latest_replay_summary,
        },
        "message": "任务内 AI 助手已返回结果。",
    }


def replay_task_ability_testset(
    task_id: str,
    *,
    store_path: Optional[Path] = None,
    use_system_ai_for_vision: bool = False,
    prompt_content: str = "",
    sample_limit: int = 10,
) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_latest_draft_by_task_id(items, task_id)
    testset = _read_testset_for_store(path, task_id)
    if prompt_content.strip():
        draft = {**draft, "system_ai_draft": prompt_content.strip()}
    selected_learning_package_id = resolve_learning_package_id(task_id, "", root_dir=path.parent)
    sample_ids = list(testset.sample_ids[: max(1, int(sample_limit or 10))])
    sample_payloads = [(_get_submitted_history_sample_for_store(path, task_id, uid)).model_dump(mode="json") for uid in sample_ids]
    if sample_payloads:
        with ThreadPoolExecutor(max_workers=min(REPLAY_MAX_PARALLEL_SAMPLES, len(sample_payloads))) as executor:
            replay_items = list(executor.map(lambda payload: _build_replay_item(payload, draft, use_system_ai_for_vision=use_system_ai_for_vision), sample_payloads))
    else:
        replay_items = []
    return {
        "task_id": str(task_id),
        "task_name": str(draft.get("task_name") or testset.task_name or ""),
        "testset_id": testset.testset_id,
        "prompt": {
            "version_id": str(draft.get("id") or ""),
            "display_name": str(draft.get("version") or ""),
            "fingerprint": _prompt_fingerprint(draft),
        },
        "selected_learning_package_id": selected_learning_package_id,
        "sample_count": len(replay_items),
        "total": len(replay_items),
        "success_count": len([item for item in replay_items if item.get("compare_status") != "error"]),
        "error_count": len([item for item in replay_items if item.get("compare_status") == "error"]),
        "items": replay_items,
        "cards": [_replay_item_to_card(item) for item in replay_items],
        "message": "固定测试集回放已完成；若未配置做题 AI，则当前结果为本地保守预览。",
    }


def create_task_ability_replay_report(
    task_id: str,
    *,
    store_path: Optional[Path] = None,
    use_system_ai_for_vision: bool = False,
    prompt_content: str = "",
    sample_limit: int = 10,
) -> dict[str, Any]:
    path = store_path or _store_path()
    report = replay_task_ability_testset(
        task_id,
        store_path=path,
        use_system_ai_for_vision=use_system_ai_for_vision,
        prompt_content=prompt_content,
        sample_limit=sample_limit,
    )
    report_id = f"replay-{uuid4().hex[:10]}"
    report["report_id"] = report_id
    report["created_at"] = _now().isoformat()
    report_path = _task_replay_report_dir(path, task_id) / f"{report_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["path"] = str(report_path)
    _write_latest_replay_summary(task_id, report, store_path=path)
    return report


def get_task_ability_replay_report(task_id: str, report_id: str, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    report_path = _task_replay_report_dir(path, task_id) / f"{report_id}.json"
    if not report_path.exists():
        raise FileNotFoundError(f"replay report not found: {report_id}")
    return json.loads(report_path.read_text(encoding="utf-8-sig"))


def build_task_ability_payload_debug(
    task_id: str,
    uid: str,
    *,
    store_path: Optional[Path] = None,
    use_system_ai_for_vision: bool = False,
) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_latest_draft_by_task_id(items, task_id)
    sample = _get_submitted_history_sample_for_store(path, task_id, uid)
    sample_payload = sample.model_dump(mode="json")
    context = _submitted_history_sample_context(sample_payload)
    expected_answer_preview = _sample_expected_answer_preview(sample_payload)
    try:
        decision, provider_used = _replay_decision_for_sample(context, draft, use_system_ai_for_vision=use_system_ai_for_vision)
        generated_answer_preview = _build_answer_preview(decision)
        payload = _build_temp_draft_payload(path, draft, context, decision)
        error_message = ""
    except Exception as exc:  # noqa: BLE001 - payload debug should surface sample-level failure instead of 400.
        decision = {}
        provider_used = "error"
        generated_answer_preview = {}
        payload = {}
        error_message = str(exc)
    return {
        "task_id": str(task_id),
        "task_name": str(draft.get("task_name") or ""),
        "uid": str(uid),
        "item_id": str(sample.item_id),
        "expected_answer_preview": expected_answer_preview,
        "generated_answer_preview": generated_answer_preview,
        "payload_preview": _compact_payload_preview(payload),
        "payload": payload,
        "source_context": context,
        "provider_used": provider_used,
        "error_message": error_message,
        "message": "右侧 payload 调试区已生成；仅本地预览，不写远端。",
    }


def record_task_ability_run(task_id: str, mode: str, run: Any, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    mode_text = str(mode or "").strip().lower()
    if mode_text not in {"trial", "production"}:
        raise TaskAbilityFlowError(f"不支持的运行模式：{mode}")
    state = _load_task_ability_run_state(path, task_id)
    if mode_text == "production" and not bool((state.get("last_trial_run") or {}).get("run_id")):
        raise TaskAbilityFlowError("请先完成试运行并人工确认，再启动生产运行。")
    payload = _to_plain_dict(run)
    summary = {
        "mode": mode_text,
        "run_id": str(payload.get("run_id") or ""),
        "status": str(payload.get("status") or ""),
        "selected_account_count": _num(payload.get("selected_account_count")),
        "healthy_account_count": _num(payload.get("healthy_account_count")),
        "abnormal_account_count": _num(payload.get("abnormal_account_count")),
        "health_ok": bool(payload.get("health_ok")),
        "generated_at": str(payload.get("generated_at") or _now().isoformat()),
    }
    state[f"last_{mode_text}_run"] = summary
    state["updated_at"] = _now().isoformat()
    _write_task_ability_run_state(path, task_id, state)
    return summary


def get_task_ability_run_config(task_id: str, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    state = _load_task_ability_run_state(path, task_id)
    run_config = state.get("run_config") if isinstance(state.get("run_config"), dict) else {}
    return _normalize_run_config(run_config)


def update_task_ability_run_config(task_id: str, run_config: dict[str, Any], *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    state = _load_task_ability_run_state(path, task_id)
    normalized = _normalize_run_config(run_config)
    state["run_config"] = normalized
    state["updated_at"] = _now().isoformat()
    _write_task_ability_run_state(path, task_id, state)
    return normalized


class TaskAbilityFlowError(ValueError):
    pass


def approve_task_ability_draft(draft_id: str, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_draft(items, draft_id)
    now = _now().isoformat()
    draft["status"] = "草稿已确认"
    draft["flow_stage"] = "real_no_submit_ready"
    draft["capability_enabled"] = bool(draft.get("capability_enabled"))
    draft["next_step"] = "进入端到端做题不提交：真实读取一道可执行题，生成待人工审核答案，但不提交。"
    draft["updated_at"] = now
    _write_items_to_path(path, items)
    return {
        "ok": True,
        "draft_id": str(draft_id),
        "status": draft["status"],
        "flow_stage": draft["flow_stage"],
        "next_step": draft["next_step"],
    }


def run_task_ability_real_no_submit(
    draft_id: str,
    *,
    store_path: Optional[Path] = None,
    review_root: Optional[Path] = None,
    queue_snapshot: Optional[dict[str, Any]] = None,
    question_context: Optional[dict[str, Any]] = None,
    ai_decision: Optional[dict[str, Any]] = None,
    db: Optional[Session] = None,
    allow_temp_save: bool = False,
    allow_claim_receive: bool = False,
    temp_save_executor: Optional[Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = None,
    target_account_user_id: str = "",
    use_system_ai_for_vision: bool = False,
) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_draft(items, draft_id)
    if str(draft.get("task_id") or "") == BON8_TASK_ID:
        return _run_bon8_task_ability_real_no_submit(
            draft_id,
            store_path=path,
            review_root=review_root,
            queue_snapshot=queue_snapshot,
            question_context=question_context,
            ai_decision=ai_decision,
            db=db,
            allow_temp_save=allow_temp_save,
            allow_claim_receive=allow_claim_receive,
            temp_save_executor=temp_save_executor,
            target_account_user_id=target_account_user_id,
            use_system_ai_for_vision=use_system_ai_for_vision,
        )
    snapshot = _normalize_queue_snapshot(queue_snapshot or _build_queue_snapshot(str(draft.get("task_id") or ""), account_user_id=target_account_user_id))
    if target_account_user_id:
        snapshot = _with_target_account(snapshot, target_account_user_id)
    if not snapshot["has_executable_item"]:
        draft["task_queue_snapshot"] = snapshot
        draft["flow_stage"] = "real_no_submit_blocked"
        draft["status"] = "真实不提交阻塞"
        draft["next_step"] = "待处理、处理中、返修均为 0；刷新任务或等待有题后再执行。"
        draft["updated_at"] = _now().isoformat()
        _write_items_to_path(path, items)
        raise TaskAbilityFlowError("当前待处理、处理中、返修均为 0，没有可执行题。")
    if snapshot["claim_required"]:
        if allow_claim_receive:
            if not snapshot.get("claim_allowed"):
                blocker = str(snapshot.get("claim_block_reason") or "当前账号不具备自动领题资格。")
                draft["task_queue_snapshot"] = snapshot
                draft["flow_stage"] = "real_no_submit_claim_required"
                draft["status"] = "需要先获取一道题"
                draft["next_step"] = blocker
                draft["updated_at"] = _now().isoformat()
                _write_items_to_path(path, items)
                raise TaskAbilityFlowError(blocker)
            claimed_context = _claim_pending_item_and_read_context(draft, snapshot)
            if claimed_context is not None:
                snapshot = _normalize_queue_snapshot(
                    {
                        **snapshot,
                        "processing": 1,
                        "has_executable_item": True,
                        "claim_required": False,
                    }
                )
                question_context = question_context or claimed_context
            else:
                draft["task_queue_snapshot"] = snapshot
                draft["flow_stage"] = "real_no_submit_claim_required"
                draft["status"] = "需要先获取一道题"
                draft["next_step"] = "待处理有题但自动获取当前题失败；请检查账号 Cookie 和领题接口后重试。"
                draft["updated_at"] = _now().isoformat()
                _write_items_to_path(path, items)
                raise TaskAbilityFlowError("待处理有题，但自动获取当前题失败；请检查账号 Cookie 和领题链路。")
        else:
            draft["task_queue_snapshot"] = snapshot
            draft["flow_stage"] = "real_no_submit_claim_required"
            draft["status"] = "需要先获取一道题"
            draft["next_step"] = "待处理有题但处理中/返修为 0；需要显式执行获取一道题后再做不提交验证。"
            draft["updated_at"] = _now().isoformat()
            _write_items_to_path(path, items)
            raise TaskAbilityFlowError("待处理有题但当前没有处理中/返修题，需要先获取一道题；本流程不会静默领取。")

    context = (
        question_context
        or _build_live_question_context(db, draft, snapshot)
        or _build_live_question_context_from_category(draft, snapshot)
        or _build_live_question_context_from_evidence(path, draft, snapshot)
        or _build_question_context_from_local_evidence(path, draft)
    )
    decision = _normalize_ai_decision(ai_decision or _build_task_ai_decision_for_research_chart(context, draft, require_provider=allow_temp_save, prefer_system_ai=use_system_ai_for_vision))
    answer_preview = _build_answer_preview(decision)
    sends_network = bool(context.get("sends_network"))
    temp_draft_result: Optional[dict[str, Any]] = None
    saved_to_task_ui = False
    writes_remote = False
    saved_payload_preview: dict[str, Any] = {}
    saved_payload: dict[str, Any] = {}
    ui_review_hint = "请人工打开真实题页面核对该审核件；确认无误后再启用有做题能力。"
    if allow_temp_save:
        _ensure_live_question_context_for_temp_save(context)
        payload = _build_temp_draft_payload(path, draft, context, decision)
        saved_payload = payload
        saved_payload_preview = _compact_payload_preview(payload)
        executor = temp_save_executor or _execute_temp_save_with_guard
        account = _select_temp_save_account(snapshot)
        temp_draft_result = executor(payload, account)
        writes_remote = True
        saved_to_task_ui = _temp_save_succeeded(temp_draft_result)
        if not saved_to_task_ui:
            blocker = str(temp_draft_result.get("error") or temp_draft_result.get("message") or "SubmitTempItemAnswer 未返回成功状态")
            raise TaskAbilityFlowError(f"真实题暂存失败：{blocker}")
        ui_review_hint = "AI 答案已保存到真实做题界面但未正式提交；请去页面审核填写内容，确认无误后再点击下一步启用能力。"
    artifact = {
        "ok": True,
        "stage": "端到端做题不提交：已暂存待人工审核" if saved_to_task_ui else "端到端做题不提交：待人工审核",
        "draft_id": str(draft_id),
        "task_name": str(draft.get("task_name") or ""),
        "task_id": str(draft.get("task_id") or ""),
        "writes_remote": writes_remote,
        "submits_remote": False,
        "sends_network": sends_network,
        "queue_snapshot": snapshot,
        "question_context": context,
        "ai_decision": decision,
        "answer_preview": answer_preview,
        "saved_answer": answer_preview,
        "saved_to_task_ui": saved_to_task_ui,
        "temp_draft_result": temp_draft_result or {},
        "temp_draft_payload": saved_payload,
        "temp_draft_payload_preview": saved_payload_preview,
        "ui_review_hint": ui_review_hint,
        "review_status": "待人工审核",
        "created_at": _now().isoformat(),
        "message": ui_review_hint if saved_to_task_ui else "已生成真实题不提交审核件：只形成答案预览和证据文件，未暂存、未提交、未回读。",
    }
    artifact_path = _write_review_artifact(review_root or _default_review_root(path, draft), artifact)
    artifact["review_artifact_path"] = str(artifact_path)

    previous_review = draft.get("real_no_submit_review") if isinstance(draft.get("real_no_submit_review"), dict) else {}
    capability_already_enabled = bool(draft.get("capability_enabled")) and previous_review.get("review_status") == "人工已通过"
    draft["status"] = "有做题能力" if capability_already_enabled else "待审核真实不提交结果"
    draft["flow_stage"] = "capability_enabled" if capability_already_enabled else "real_no_submit_review"
    draft["capability_enabled"] = capability_already_enabled
    draft["task_queue_snapshot"] = snapshot
    draft["real_no_submit_review"] = {
        "review_status": "人工已通过" if capability_already_enabled else "待人工审核",
        "stage": artifact["stage"],
        "item_id": str(context.get("item_id") or ""),
        "score": decision["score"],
        "reason": decision["reason"],
        "confidence": decision["confidence"],
        "account_user_id": str(snapshot.get("account_user_id") or ""),
        "account_name": str(snapshot.get("account_name") or ""),
        "source_mode": str(context.get("source_mode") or ""),
        "evidence_path": str(context.get("evidence_path") or ""),
        "review_artifact_path": str(artifact_path),
        "writes_remote": writes_remote,
        "submits_remote": False,
        "sends_network": sends_network,
        "saved_to_task_ui": saved_to_task_ui,
        "temp_draft_result": temp_draft_result or {},
        "temp_draft_payload_preview": saved_payload_preview,
        "ui_review_hint": ui_review_hint,
    }
    if capability_already_enabled and previous_review.get("approved_at"):
        draft["real_no_submit_review"]["approved_at"] = previous_review.get("approved_at")
    draft["next_step"] = "已启用任务定制做题能力；正式提交仍走高风险确认和回读验证。" if capability_already_enabled else ui_review_hint
    draft["updated_at"] = _now().isoformat()
    _write_items_to_path(path, items)
    return artifact


def _run_bon8_task_ability_real_no_submit(
    draft_id: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    raise TaskAbilityFlowError("bon8 统一不提交入口尚未完全接入，请先通过任务操作台执行 bon8 端到端做题不提交。")


def approve_task_ability_real_no_submit(draft_id: str, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = _find_draft(items, draft_id)
    review = draft.get("real_no_submit_review") if isinstance(draft.get("real_no_submit_review"), dict) else {}
    if not review:
        raise TaskAbilityFlowError("还没有真实题不提交审核件，不能启用做题能力。")
    if not review.get("saved_to_task_ui"):
        raise TaskAbilityFlowError("端到端不提交还没有把 AI 答案保存到真实做题界面，不能启用做题能力。")
    draft["status"] = "有做题能力"
    draft["flow_stage"] = "capability_enabled"
    draft["capability_enabled"] = True
    draft["next_step"] = "已启用任务定制做题能力；正式提交仍走高风险确认和回读验证。"
    review["review_status"] = "人工已通过"
    review["approved_at"] = _now().isoformat()
    draft["real_no_submit_review"] = review
    draft["updated_at"] = _now().isoformat()
    _write_items_to_path(path, items)
    return {
        "ok": True,
        "draft_id": str(draft_id),
        "status": draft["status"],
        "flow_stage": draft["flow_stage"],
        "capability_enabled": True,
        "message": "真实题不提交审核已通过，任务进入有做题能力状态。",
    }


def run_task_ability_dry_run(draft_id: str, *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = store_path or _store_path()
    items = _load_items_from_path(path)
    draft = next((item for item in items if isinstance(item, dict) and str(item.get("id")) == str(draft_id)), None)
    if not draft:
        raise TaskAbilityFlowError("能力草稿不存在。")
    task_id = str(draft.get("task_id") or "")
    if task_id not in RESEARCH_CHART_TASK_IDS:
        raise TaskAbilityFlowError("该能力草稿还没有接入端到端不提交 dry-run。")
    dry_run_path = path.parent / f"research-chart-{task_id}" / "research-chart-dry-run-payload.json"
    if not dry_run_path.exists():
        raise TaskAbilityFlowError("科研图 dry-run 证据文件不存在。")
    dry_run = json.loads(dry_run_path.read_text(encoding="utf-8-sig"))
    payload = dry_run.get("payload") if isinstance(dry_run, dict) else {}
    return {
        "ok": True,
        "stage": "端到端做题不提交",
        "draft_id": str(draft_id),
        "task_name": str(draft.get("task_name") or ""),
        "task_id": task_id,
        "writes_remote": False,
        "submits_remote": False,
        "evidence_path": str(dry_run_path),
        "field_diff": dry_run.get("field_diff", {}) if isinstance(dry_run, dict) else {},
        "payload_preview": _compact_payload_preview(payload if isinstance(payload, dict) else {}),
        "message": "已完成本地端到端 dry-run：读取能力草稿、样例字段和 payload 映射，不写远端、不暂存、不提交。",
    }


def _store_path() -> Path:
    base = Path(get_settings().production_state_path)
    root = base.parent if base.parent != Path("") else Path("data")
    return root / "task-abilities" / "ability-drafts.json"


def _load_items() -> list[dict[str, Any]]:
    return _load_items_from_path(_store_path())


def _load_items_from_path(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        items: list[dict[str, Any]] = []
    else:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            loaded = data.get("items", [])
            items = loaded if isinstance(loaded, list) else []
        else:
            items = data if isinstance(data, list) else []
    normalized_items, changed = _ensure_builtin_task_ability_items(items)
    if changed:
        _write_items_to_path(path, normalized_items)
    return normalized_items


def _write_items(items: list[dict[str, Any]]) -> None:
    _write_items_to_path(_store_path(), items)


def _write_items_to_path(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": items, "updated_at": _now().isoformat()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_builtin_task_ability_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    normalized = [item for item in items if isinstance(item, dict)]
    changed = False
    if not any(str(item.get("task_id") or "") == BON8_TASK_ID for item in normalized):
        normalized.insert(0, _build_builtin_bon8_task_ability_draft())
        changed = True
    return normalized, changed


def _build_builtin_bon8_task_ability_draft() -> dict[str, Any]:
    now = _now().isoformat()
    return {
        "id": BON8_TASK_ABILITY_DRAFT_ID,
        "version": "ability-20260514-bon8",
        "status": "有做题能力",
        "task_name": BON8_TASK_NAME,
        "task_id": BON8_TASK_ID,
        "specific_rules": "bon8 严格执行 0/1/2 分打分、最佳产物唯一、必填评分理由与勾选项，不写额外审核/废弃备注。",
        "sample_data": "沿用 bon8 已跑通的评分与提交闭环；多模型 HTML 产物比较后输出 0/1/2 和理由。",
        "related_content": "该能力由既有 bon8 生产链迁入题型能力库主流程；任务控制台统一按题型能力已发布来启动自动做题。",
        "system_ai_draft": (
            "# bon8 做题能力草稿\n\n"
            "## 适用任务\n"
            f"- TaskID：{BON8_TASK_ID}\n"
            "- NodeID：1\n"
            "- 场景：草图与流程图多模型页面还原质量评分。\n\n"
            "## 读题材料\n"
            "- 输入图：`mediaUrls[0]`\n"
            "- 多模型产物：`model1..model8.html`\n"
            "- 结合题面里的 `scoringGuidelines/prompt` 做 0/1/2 评分。\n\n"
            "## 输出约束\n"
            "- 必须给每个模型输出 0/1/2 分。\n"
            "- 只能有一个最佳产物给 2 分。\n"
            "- 0/1 分必须给中文理由。\n"
            "- 只写评分、问题勾选和理由；禁止额外审核/废弃备注。\n\n"
            "## 主流程\n"
            "- 先走端到端做题不提交，人工核对结果。\n"
            "- 审核通过后，任务控制台才允许统一启动自动做题。"
        ),
        "system_ai_trace_id": "",
        "provider_status": "migrated_from_bon8_production",
        "next_step": "已迁入题型能力库主流程；任务控制台可统一执行端到端不提交和自动做题。",
        "created_at": now,
        "updated_at": now,
        "flow_stage": "capability_enabled",
        "capability_enabled": True,
        "real_no_submit_review": {
            "review_status": "人工已通过",
            "stage": "迁移自 bon8 既有首题审核链",
            "ui_review_hint": "bon8 已迁入题型能力库主流程；后续可在任务控制台先做端到端做题不提交，再决定是否正式启动自动做题。",
        },
        "task_queue_snapshot": {},
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _compact_payload_preview(payload: dict[str, Any]) -> dict[str, Any]:
    answers = payload.get("AuditAnswers") if isinstance(payload.get("AuditAnswers"), list) else []
    first = answers[0] if answers and isinstance(answers[0], dict) else {}
    return {
        "TaskID": payload.get("TaskID"),
        "NodeID": payload.get("NodeID"),
        "answers_count": len(answers),
        "first_item_id": first.get("ItemID"),
        "has_content": bool(first.get("Content")),
    }


def _find_draft(items: list[dict[str, Any]], draft_id: str) -> dict[str, Any]:
    draft = next((item for item in items if isinstance(item, dict) and str(item.get("id")) == str(draft_id)), None)
    if not draft:
        raise TaskAbilityFlowError("能力草稿不存在。")
    return draft


def _find_latest_draft_by_task_id(items: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    task_id_text = str(task_id or "")
    draft = next((item for item in items if isinstance(item, dict) and str(item.get("task_id") or "") == task_id_text), None)
    if not draft:
        raise TaskAbilityFlowError("当前任务还没有能力草稿。")
    return draft


def _prompt_snapshot_dir(store_path: Path, task_id: str) -> Path:
    return store_path.parent / str(task_id) / "prompt-history"


def _task_replay_report_dir(store_path: Path, task_id: str) -> Path:
    return store_path.parent / str(task_id) / "replay-reports"


def _latest_replay_summary_path(store_path: Path, task_id: str) -> Path:
    return store_path.parent / str(task_id) / "latest-replay-summary.json"


def _chat_learning_package_id(payload: dict[str, Any]) -> str:
    for key in ("selected_learning_package_id", "learning_package_id", "recording_id", "selected_recording_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _prompt_fingerprint(draft: dict[str, Any]) -> str:
    parts = [
        str(draft.get("system_ai_draft") or ""),
        str(draft.get("specific_rules") or ""),
        str(draft.get("sample_data") or ""),
        str(draft.get("related_content") or ""),
    ]
    return hashlib.sha256("\n---\n".join(parts).encode("utf-8")).hexdigest()


def _write_latest_replay_summary(task_id: str, report: dict[str, Any], *, store_path: Path) -> dict[str, Any]:
    prompt = report.get("prompt") if isinstance(report.get("prompt"), dict) else {}
    summary = {
        "replay_id": str(report.get("report_id") or ""),
        "task_id": str(task_id),
        "prompt_version": str(prompt.get("display_name") or ""),
        "prompt_snapshot_id": str(prompt.get("version_id") or ""),
        "prompt_fingerprint": str(prompt.get("fingerprint") or ""),
        "selected_learning_package_id": str(report.get("selected_learning_package_id") or ""),
        "fixed_testset_id": str(report.get("testset_id") or ""),
        "created_at": str(report.get("created_at") or _now().isoformat()),
        "is_stale_for_current_prompt": False,
        "items": [
            {
                "item_id": str(card.get("item_id") or ""),
                "score": str(card.get("score") or ""),
                "reason": str(card.get("reason") or ""),
                "filled_fields": card.get("filled_fields") if isinstance(card.get("filled_fields"), dict) else {},
                "status": str(card.get("status") or "success"),
                "error_message": card.get("error_message"),
            }
            for card in report.get("cards", [])
            if isinstance(card, dict)
        ],
    }
    path = _latest_replay_summary_path(store_path, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def load_latest_replay_summary(task_id: str, draft: dict[str, Any], *, store_path: Optional[Path] = None) -> dict[str, Any]:
    path = _latest_replay_summary_path(store_path or _store_path(), task_id)
    if not path.exists():
        return {}
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return {}
    expected = _prompt_fingerprint(draft)
    stored = str(payload.get("prompt_fingerprint") or "")
    payload["is_stale_for_current_prompt"] = bool(stored and stored != expected)
    return payload


def _task_testset_summary(task_id: str, *, store_path: Optional[Path] = None) -> str:
    path = store_path or _store_path()
    try:
        testset = _read_testset_for_store(path, task_id)
    except FileNotFoundError:
        return "固定测试集：尚未生成。"
    preview = "、".join(testset.sample_ids[:3])
    suffix = "…" if len(testset.sample_ids) > 3 else ""
    return f"固定测试集 {testset.sample_count} 条，示例样本：{preview}{suffix}"


def _build_task_ability_chat_message(*, task_id: str, task_name: str, draft: dict[str, Any], testset_summary: str, learning_package_summary: str, latest_replay_summary: dict[str, Any], user_message: str) -> str:
    replay_context = "无最新回放结果。"
    if latest_replay_summary:
        replay_context = json.dumps(
            {
                "replay_id": latest_replay_summary.get("replay_id"),
                "prompt_version": latest_replay_summary.get("prompt_version"),
                "selected_learning_package_id": latest_replay_summary.get("selected_learning_package_id"),
                "fixed_testset_id": latest_replay_summary.get("fixed_testset_id"),
                "is_stale_for_current_prompt": latest_replay_summary.get("is_stale_for_current_prompt"),
                "items": latest_replay_summary.get("items", []),
            },
            ensure_ascii=False,
        )
    return "\n\n".join(
        [
            "你现在在 AIDP 能力题库 Step2 集中式工作区中工作。",
            f"任务：{task_name or task_id} / {task_id}",
            f"当前能力版本：{draft.get('version') or '-'}；状态：{draft.get('status') or '-'}；阶段：{draft.get('flow_stage') or '-'}",
            testset_summary,
            "当前选定学习包摘要：\n" + str(learning_package_summary or "未选择学习包"),
            "最新 10 题回放摘要 latest_replay_summary：\n" + replay_context,
            "当前 Prompt 草稿：\n" + str(draft.get("system_ai_draft") or ""),
            "当前特定规则：\n" + str(draft.get("specific_rules") or ""),
            "请围绕提示词优化、样例补充、回放差异分析和 payload 字段调试来回答，不要偏到运维告警或其他系统管理话题。",
            "用户问题：\n" + user_message,
        ]
    )


def _call_task_workspace_chat_provider(runtime: dict[str, object], prompt: str, history: list[dict[str, Any]]) -> str:
    endpoint = str(runtime.get("base_url") or "").rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    messages = [{"role": "system", "content": "你是 AIDP 能力题库 Step2 工作区内的 AI 助手，只回答 Prompt、回放、payload 调试和样例优化。"}]
    for item in history[-6:]:
        role = str(item.get("role") or "user").strip() or "user"
        content = str(item.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content[:4000]})
    messages.append({"role": "user", "content": prompt[:12000]})
    payload = {
        "model": runtime.get("model") or "gpt-4.1-mini",
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 900,
    }
    headers = {"Authorization": f"Bearer {runtime.get('api_key')}", "Content-Type": "application/json"}
    response = requests.post(endpoint, headers=headers, json=payload, timeout=int(runtime.get("timeout_seconds") or 30))
    if not getattr(response, "ok", True):
        body = str(getattr(response, "text", ""))[:600]
        raise TaskAbilityFlowError(f"任务内 AI 助手 provider 返回 HTTP {getattr(response, 'status_code', '')}: {body}")
    data = response.json()
    return str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip() or "任务内 AI 助手没有返回内容。"


def _local_task_ability_chat_answer(user_message: str, draft: dict[str, Any], testset_summary: str, latest_replay_summary: Optional[dict[str, Any]] = None) -> str:
    suggestions: list[str] = []
    question = user_message.lower()
    if "prompt" in question or "提示词" in user_message:
        suggestions.append("先把 Prompt 拆成输入材料、评分标准、输出 JSON 字段和禁止动作四段，能最快减少回放抖动。")
    if "回放" in user_message or "对比" in user_message:
        suggestions.append("优先查看固定测试集里 `different` 的样本，逐项比较期望输出和当前回放输出，再决定是改 Prompt 还是补样例。")
        if latest_replay_summary and isinstance(latest_replay_summary.get("items"), list):
            weak_items = [
                item
                for item in latest_replay_summary["items"]
                if isinstance(item, dict) and (not str(item.get("reason") or "").strip() or len(str(item.get("reason") or "")) < 16)
            ]
            if weak_items:
                sample_ids = "、".join(str(item.get("item_id") or "") for item in weak_items[:3])
                suggestions.append(f"最新回放中 {sample_ids} 的理由偏短，应要求做题 AI 写出原图与 AI 图在文字、坐标、布局或数据表达上的具体差异。")
    if "payload" in question or "字段" in user_message:
        suggestions.append("右侧先核对 `TaskID/NodeID/first_item_id/has_content`，再检查 `data.label_sorce.*` 和 `data.label_remark.*` 是否完整。")
    if "样例" in user_message or "sample" in question:
        suggestions.append("样例数据优先补 0 分/1 分边界案例，而不是重复 2 分标准样本。")
    if not suggestions:
        suggestions.append("建议先看当前能力版本、固定测试集回放和 payload 调试三块，再决定改 Prompt、补样例还是收紧字段规则。")
    suggestions.append(f"当前能力版本：{draft.get('version') or '-'}。{testset_summary}")
    return "\n".join(f"{index + 1}. {item}" for index, item in enumerate(suggestions))


def _sanitize_provider_error(message: str) -> str:
    text = str(message or "")
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"((?:api[_-]?key|token|authorization|cookie)=)[^&\s\"']+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    return text[:600]


def _submitted_history_sample_context(sample: dict[str, Any]) -> dict[str, Any]:
    primary_output = sample.get("primary_output") if isinstance(sample.get("primary_output"), dict) else {}
    item = primary_output.get("item") if isinstance(primary_output.get("item"), dict) else {}
    data = primary_output.get("data") if isinstance(primary_output.get("data"), dict) else {}
    context = {
        "task_id": str(sample.get("task_id") or ""),
        "item_id": str(sample.get("item_id") or ""),
        "uid": str(sample.get("uid") or ""),
        "node_id": "1",
        "source_mode": "submitted_history_testset_sample",
        "sends_network": False,
        "writes_remote": False,
        "image_gt": str(item.get("image_gt") or ""),
        "model_image": str(item.get("model_image") or ""),
        "current_answer_data": data,
        "current_content": primary_output,
    }
    context.update(_model_images_from_item(item))
    return context


def _sample_expected_answer_preview(sample: dict[str, Any]) -> dict[str, Any]:
    primary_output = sample.get("primary_output") if isinstance(sample.get("primary_output"), dict) else {}
    data = primary_output.get("data") if isinstance(primary_output.get("data"), dict) else {}
    label_score = data.get("label_sorce") if isinstance(data.get("label_sorce"), dict) else {}
    label_reason = data.get("label_remark") if isinstance(data.get("label_remark"), dict) else {}
    result: dict[str, Any] = {"data.discard": str(data.get("discard") or "No")}
    for key, value in label_score.items():
        result[f"data.label_sorce.{key}"] = value
    for key, value in label_reason.items():
        result[f"data.label_remark.{key}"] = value
    return result


def _replay_decision_for_sample(context: dict[str, Any], draft: dict[str, Any], *, use_system_ai_for_vision: bool = False) -> tuple[dict[str, Any], str]:
    task_id = str(draft.get("task_id") or "")
    if task_id in RESEARCH_CHART_TASK_IDS:
        require_provider = bool(get_system_ai_runtime_prompt().get("provider_configured")) if use_system_ai_for_vision else bool(get_task_ai_runtime_prompt().get("provider_configured"))
        decision = _normalize_ai_decision(
            _build_task_ai_decision_for_research_chart(
                context,
                draft,
                require_provider=require_provider,
                prefer_system_ai=use_system_ai_for_vision,
            )
        )
        return decision, "provider" if require_provider else "local_fallback"
    return _normalize_ai_decision({"score": "0", "reason": "当前题型回放引擎尚未接入，先显示本地保守预览。", "confidence": "low"}), "unsupported"


def _task_ability_run_state_path(store_path: Path, task_id: str) -> Path:
    return store_path.parent / str(task_id) / "run-gate.json"


def _submitted_history_sample_path(store_path: Path, task_id: str, uid: str) -> Path:
    return store_path.parent / str(task_id) / "submitted-history" / "samples" / f"{_safe_uid(uid)}.json"


def _testset_file_path(store_path: Path, task_id: str) -> Path:
    return store_path.parent / str(task_id) / "testsets" / "current.json"


def _read_testset_for_store(store_path: Path, task_id: str):
    path = _testset_file_path(store_path, task_id)
    if not path.exists():
        raise FileNotFoundError(f"testset not found for task {task_id}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return TestsetRead(**payload)


def _get_submitted_history_sample_for_store(store_path: Path, task_id: str, uid: str):
    path = _submitted_history_sample_path(store_path, task_id, uid)
    if not path.exists():
        raise FileNotFoundError(f"submitted history sample not found: {uid}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return SubmittedHistorySampleRead(**payload)


def _load_task_ability_run_state(store_path: Path, task_id: str) -> dict[str, Any]:
    payload = _load_json(_task_ability_run_state_path(store_path, task_id))
    return payload if isinstance(payload, dict) else {}


def _write_task_ability_run_state(store_path: Path, task_id: str, payload: dict[str, Any]) -> None:
    state_path = _task_ability_run_state_path(store_path, task_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_run_config(run_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": str(run_config.get("mode") or "safe"),
        "rate_limit_per_minute": max(1, _num(run_config.get("rate_limit_per_minute")) or 5),
        "trial_max_items_per_account": max(1, _num(run_config.get("trial_max_items_per_account")) or 3),
        "production_max_items_per_account": max(1, _num(run_config.get("production_max_items_per_account")) or 50),
        "consecutive_fail_threshold": max(1, _num(run_config.get("consecutive_fail_threshold")) or 3),
    }


def _answer_preview_difference_fields(expected: dict[str, Any], generated: dict[str, Any]) -> list[str]:
    fields = sorted(set(expected.keys()) | set(generated.keys()))
    return [field for field in fields if expected.get(field) != generated.get(field)]


def _difference_summary(fields: list[str]) -> str:
    if not fields:
        return "当前回放输出与样本期望输出一致。"
    preview = "、".join(fields[:4])
    suffix = " 等字段" if len(fields) > 4 else ""
    return f"当前回放与期望输出在 {preview}{suffix} 上存在差异。"


def _build_replay_item(sample_payload: dict[str, Any], draft: dict[str, Any], *, use_system_ai_for_vision: bool = False) -> dict[str, Any]:
    sample = SubmittedHistorySampleRead(**sample_payload)
    expected_answer_preview = _sample_expected_answer_preview(sample_payload)
    context = _submitted_history_sample_context(sample_payload)
    try:
        decision, provider_used = _replay_decision_for_sample(context, draft, use_system_ai_for_vision=use_system_ai_for_vision)
        generated_answer_preview = _build_answer_preview(decision)
        difference_fields = _answer_preview_difference_fields(expected_answer_preview, generated_answer_preview)
        return {
            "uid": sample.uid,
            "item_id": sample.item_id,
            "expected_answer_preview": expected_answer_preview,
            "generated_answer_preview": generated_answer_preview,
            "compare_status": "matched" if not difference_fields else "different",
            "difference_fields": difference_fields,
            "difference_count": len(difference_fields),
            "difference_summary": _difference_summary(difference_fields),
            "provider_used": provider_used,
            "source_mode": str(context.get("source_mode") or ""),
            "error_message": "",
            "images": _sample_images(sample_payload),
        }
    except Exception as exc:  # noqa: BLE001 - replay report should keep per-sample failures visible.
        return {
            "uid": sample.uid,
            "item_id": sample.item_id,
            "expected_answer_preview": expected_answer_preview,
            "generated_answer_preview": {},
            "compare_status": "error",
            "difference_fields": [],
            "difference_count": 0,
            "difference_summary": "该样本回放失败，已保留错误信息。",
            "provider_used": "error",
            "source_mode": str(context.get("source_mode") or ""),
            "error_message": str(exc),
            "images": _sample_images(sample_payload),
        }


def _sample_images(sample_payload: dict[str, Any]) -> dict[str, Any]:
    primary_output = sample_payload.get("primary_output") if isinstance(sample_payload.get("primary_output"), dict) else {}
    item = primary_output.get("item") if isinstance(primary_output.get("item"), dict) else {}
    original = str(item.get("image_gt") or "").strip()
    ai_entries = _model_images_from_item(item)
    ai_url = ""
    for key in sorted(ai_entries.keys()):
        if key.endswith("_bon_id"):
            continue
        ai_url = str(ai_entries[key] or "").strip()
        if ai_url:
            break
    return {
        "original": {"label": "原图", "url": original or None, "available": bool(original)},
        "ai": {"label": "AI图", "url": ai_url or None, "available": bool(ai_url)},
    }


def _replay_item_to_card(item: dict[str, Any]) -> dict[str, Any]:
    score_text = _preview_score_text(item.get("generated_answer_preview") if isinstance(item.get("generated_answer_preview"), dict) else {})
    reason_text = _preview_reason_text(item.get("generated_answer_preview") if isinstance(item.get("generated_answer_preview"), dict) else {})
    return {
        "uid": str(item.get("uid") or ""),
        "item_id": str(item.get("item_id") or ""),
        "display_title": f"题目ID：{item.get('item_id') or item.get('uid') or ''}",
        "status": "success" if str(item.get("compare_status") or "") != "error" else "error",
        "images": item.get("images") if isinstance(item.get("images"), dict) else {},
        "score": score_text,
        "reason": reason_text,
        "filled_fields": {"score": score_text, "reason": reason_text},
        "error_message": str(item.get("error_message") or "") or None,
    }


def _preview_score_text(preview: dict[str, Any]) -> str:
    scores = [str(value) for key, value in preview.items() if "label_sorce" in key]
    return " / ".join(scores)


def _preview_reason_text(preview: dict[str, Any]) -> str:
    reasons = [str(value) for key, value in preview.items() if "label_remark" in key]
    return "\n".join(reasons)


def _latest_json_artifact(root: Path) -> Optional[Path]:
    if not root.exists():
        return None
    candidates = [path for path in root.glob("*.json") if path.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    return candidates[0]


def _to_plain_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _normalize_queue_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    pending = _num(snapshot.get("pending"))
    processing = _num(snapshot.get("processing"))
    repair = _num(snapshot.get("repair"))
    receive_enabled = _bool_value(snapshot.get("receive_enabled"), snapshot.get("receiveEnable"), snapshot.get("receive_enable"))
    operation_url_ok = _bool_value(snapshot.get("operation_url_ok"), snapshot.get("operationUrlOk"), snapshot.get("operation_url_valid"))
    return {
        "task_id": str(snapshot.get("task_id") or ""),
        "pending": pending,
        "processing": processing,
        "repair": repair,
        "account_user_id": str(snapshot.get("account_user_id") or snapshot.get("user_id") or ""),
        "account_name": str(snapshot.get("account_name") or snapshot.get("display_name") or ""),
        "has_executable_item": pending > 0 or processing > 0 or repair > 0,
        "claim_required": pending > 0 and processing == 0 and repair == 0,
        "receive_enabled": receive_enabled,
        "operation_url_ok": operation_url_ok,
        "claim_allowed": pending > 0,
        "claim_block_reason": str(snapshot.get("claim_block_reason") or _claim_block_reason(pending)).strip(),
        "source": str(snapshot.get("source") or "provided"),
    }


def _build_queue_snapshot(task_id: str, account_user_id: str = "") -> dict[str, Any]:
    state_path = _production_state_path()
    state = _load_json(state_path)
    candidates: list[dict[str, Any]] = []
    accounts = state.get("accounts", []) if isinstance(state, dict) else []
    target_account_user_id = str(account_user_id or "").strip()
    for account in accounts:
        if not isinstance(account, dict):
            continue
        account_id = str(account.get("userId") or account.get("user_id") or "")
        if target_account_user_id and account_id != target_account_user_id:
            continue
        tasks = account.get("tasks", []) if isinstance(account.get("tasks"), list) else []
        for task in tasks:
            if isinstance(task, dict) and str(task.get("id") or task.get("taskId") or "") == task_id:
                pending = _num(task.get("poolPendingSubmit"), task.get("pending"), task.get("todo"))
                total_map = task.get("frontendCategoryTotalMap") if isinstance(task.get("frontendCategoryTotalMap"), dict) else {}
                category = task.get("frontendSubmittedCategory") if isinstance(task.get("frontendSubmittedCategory"), dict) else {}
                status_counts = category.get("statusCounts") if isinstance(category.get("statusCounts"), dict) else {}
                processing = _num(task.get("frontendNotSubmitted"), total_map.get("0"), task.get("processing"), task.get("personalProcessing"))
                repair = _num(task.get("frontendRepairCount"), status_counts.get("9"), task.get("repair"), task.get("modify"))
                receive_enabled = _task_receive_enabled(task)
                operation_url_ok = _operation_url_is_task_page(account.get("operationUrl") or account.get("referer") or "")
                candidates.append(
                    {
                        "task_id": task_id,
                        "pending": pending,
                        "processing": processing,
                        "repair": repair,
                        "account_user_id": account_id,
                        "account_name": str(account.get("name") or account.get("displayName") or ""),
                        "receive_enabled": receive_enabled,
                        "operation_url_ok": operation_url_ok,
                        "claim_block_reason": _claim_block_reason(pending),
                        "source": "production-state",
                    }
                )
    if not candidates:
        account = _find_state_account(target_account_user_id) if target_account_user_id else {}
        return {
            "task_id": task_id,
            "pending": 0,
            "processing": 0,
            "repair": 0,
            "account_user_id": target_account_user_id,
            "account_name": str(account.get("name") or account.get("displayName") or ""),
            "source": "production-state-missing",
        }
    candidates.sort(key=lambda item: (_num(item.get("processing")) + _num(item.get("repair")), _num(item.get("pending"))), reverse=True)
    return candidates[0]


def _with_target_account(snapshot: dict[str, Any], account_user_id: str) -> dict[str, Any]:
    account = _find_state_account(account_user_id)
    next_snapshot = dict(snapshot)
    next_snapshot["account_user_id"] = str(account_user_id)
    next_snapshot["account_name"] = str(account.get("name") or account.get("displayName") or next_snapshot.get("account_name") or "")
    task = _find_state_task(account, str(next_snapshot.get("task_id") or ""))
    receive_enabled = _task_receive_enabled(task) if task else False
    operation_url_ok = _operation_url_is_task_page(account.get("operationUrl") or account.get("referer") or "")
    next_snapshot["receive_enabled"] = receive_enabled
    next_snapshot["operation_url_ok"] = operation_url_ok
    next_snapshot["claim_block_reason"] = _claim_block_reason(_num(next_snapshot.get("pending")))
    return _normalize_queue_snapshot(next_snapshot)


def _build_live_question_context(db: Optional[Session], draft: dict[str, Any], snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    if db is None:
        return None
    task_id = str(draft.get("task_id") or "")
    item = db.execute(select(TaskCatalogItem).where(TaskCatalogItem.task_id == task_id).order_by(TaskCatalogItem.updated_at.desc())).scalars().first()
    if item is None:
        return None
    try:
        live = build_http_question_context(
            db,
            item.id,
            prefer_live=True,
            allow_remote_fetch=True,
            account_user_id=str(snapshot.get("account_user_id") or ""),
        )
    except TaskCapabilityError as exc:
        return {
            "source_mode": "live-mget-answer-list-failed",
            "sends_network": False,
            "live_error": str(exc),
            "blockers": exc.blockers,
            "item_id": "",
            "current_answer_data": {},
        }
    material_resources = [resource.model_dump(mode="json") for resource in live.material_resources]
    return {
        "source_mode": live.source_mode,
        "sends_network": live.sends_network,
        "evidence_path": live.evidence_path,
        "item_id": live.identity.ItemID,
        "uid": _first_material_url(material_resources, "uid"),
        "image_gt": _first_material_url(material_resources, "原图"),
        "model_image": _first_material_url(material_resources, "AI"),
        "current_answer_data": live.current_answer_data,
        "material_resources": material_resources,
        **_model_images_from_material_resources(material_resources),
    }


def _build_live_question_context_from_category(draft: dict[str, Any], snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    account = _find_state_account(str(snapshot.get("account_user_id") or ""))
    cookie = str(account.get("cookie") or "") if account else ""
    if not cookie:
        return None
    task_id = str(snapshot.get("task_id") or draft.get("task_id") or "")
    if not task_id:
        return None
    node_id = _num(draft.get("node_id"), snapshot.get("node_id"), 1) or 1
    payload = {
        "TaskID": task_id,
        "NodeID": node_id,
        "ItemCategoryType": 0,
        "Filter": {},
        "PageRequest": {"PageNo": 0, "PageSize": 1},
    }
    referer = str(account.get("referer") or account.get("operationUrl") or "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1")
    try:
        response = requests.post(
            f"https://aidp.juejin.cn{SEARCH_ITEM_CATEGORY_ENDPOINT}",
            json=payload,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Cookie": cookie,
                "Referer": referer,
                "Origin": "https://aidp.juejin.cn",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
                "Agw-Js-Conv": "str",
                "X-JS-REQ": "1",
                "X-Backend-Side": "4",
                "X-Backend-Org-Id": "100",
            },
            timeout=20,
        )
        data = response.json()
    except (requests.RequestException, ValueError):
        return None
    current_item = _first_category_item(data)
    if not current_item:
        return None
    content = _parse_category_item_content(current_item)
    item = content.get("item") if isinstance(content.get("item"), dict) else content
    item_id = str(current_item.get("ItemID") or current_item.get("itemID") or content.get("itemID") or "")
    if not item_id:
        return None
    return {
        "source_mode": "live_search_item_category",
        "sends_network": True,
        "evidence_path": f"{SEARCH_ITEM_CATEGORY_ENDPOINT} status={response.status_code} item={item_id}",
        "item_id": item_id,
        "node_id": str(node_id),
        "uid": str(item.get("uid") or current_item.get("UID") or current_item.get("uid") or ""),
        "image_gt": str(item.get("image_gt") or current_item.get("image_gt") or ""),
        "model_image": str(item.get("model_image") or current_item.get("model_image") or ""),
        "current_answer_data": content.get("data") if isinstance(content.get("data"), dict) else {},
        "raw_status": current_item.get("Status") or current_item.get("status"),
        **_model_images_from_item(item),
    }


def _claim_pending_item_and_read_context(draft: dict[str, Any], snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    account = _find_state_account(str(snapshot.get("account_user_id") or ""))
    cookie = str(account.get("cookie") or "") if account else ""
    if not cookie:
        return None
    task_id = str(snapshot.get("task_id") or draft.get("task_id") or "")
    if not task_id:
        return None
    node_id = _num(draft.get("node_id"), snapshot.get("node_id"), 1) or 1
    browser_claim = _claim_pending_item_via_browser(account, task_id, node_id)
    if browser_claim:
        browser_context = browser_claim.get("question_context") if isinstance(browser_claim.get("question_context"), dict) else None
        if browser_context is None:
            browser_context = _build_live_question_context_from_category(draft, snapshot)
        if browser_context is not None:
            browser_context["claim_source"] = str(browser_claim.get("source") or "browser")
            return browser_context
    claim_source = _claim_pending_item_via_http(account, task_id, node_id)
    if not claim_source:
        if not _claim_pending_item_via_helper(account, task_id, node_id):
            return None
        claim_source = "helper"
    context = _build_live_question_context_from_category(draft, snapshot)
    if context is not None:
        context["claim_source"] = claim_source
    return context


def _claim_pending_item_via_browser(account: dict[str, Any], task_id: str, node_id: int) -> dict[str, Any]:
    cdp_port = _ensure_task_browser_cdp_port(account)
    if not cdp_port:
        return {}
    ws_url = _pick_task_page_websocket(cdp_port)
    if not ws_url:
        return {}
    metadata = _fetch_task_metadata_from_browser(ws_url, task_id)
    mark_v3_url = _build_mark_v3_url(task_id, node_id, metadata)
    if not mark_v3_url:
        return {}
    try:
        session = _CdpPageSession(ws_url)
    except Exception:
        return {}
    try:
        session.enable()
        auto_claim = _claim_pending_item_via_mark_v3_navigation(session, mark_v3_url, task_id)
        if auto_claim:
            return auto_claim
    finally:
        session.close()
    return {}


def _claim_pending_item_via_http(account: dict[str, Any], task_id: str, node_id: int) -> str:
    referer = _task_claim_referer(account, task_id, node_id)
    cookie = str(account.get("cookie") or "")
    attempts = [
        (
            PRE_RECEIVE_ENDPOINT,
            {"Filter": {"Type": 1, "TaskID": str(task_id), "NodeID": int(node_id), "Count": 1, "StatusList": []}},
            "aidp-monitor-next/task-ability-pre-receive",
            "pre_receive",
        ),
        (
            RECEIVE_ENDPOINT,
            {"TaskID": str(task_id), "NodeID": int(node_id)},
            "aidp-monitor-next/task-ability-receive",
            "receive",
        ),
    ]
    for endpoint, payload, user_agent, source in attempts:
        try:
            response = requests.post(
                f"https://aidp.juejin.cn{endpoint}",
                json=payload,
                headers=_task_claim_headers(cookie, referer, user_agent=user_agent),
                timeout=20,
            )
            data = response.json()
        except (requests.RequestException, ValueError):
            continue
        if _base_resp_status_code(data) == 0:
            return source
    return ""


def _build_live_question_context_from_evidence(store_path: Path, draft: dict[str, Any], snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    evidence = _load_local_evidence(store_path, draft)
    if not evidence:
        return None
    account = _find_state_account(str(snapshot.get("account_user_id") or ""))
    cookie = str(account.get("cookie") or "") if account else ""
    if not cookie:
        return None
    payload = {
        "TaskID": str(evidence.get("task_id") or draft.get("task_id") or ""),
        "NodeID": str(evidence.get("node_id") or "1"),
        "ItemIDs": [str(evidence.get("item_id") or "")],
    }
    try:
        response = requests.post(
            f"https://aidp.juejin.cn{MGET_ANSWER_LIST_ENDPOINT}",
            json=payload,
            headers={
                "Cookie": cookie,
                "Referer": str(account.get("operationUrl") or account.get("referer") or "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1"),
                "Origin": "https://aidp.juejin.cn",
                "Content-Type": "application/json",
                "User-Agent": "aidp-monitor-next/task-ability-real-no-submit",
            },
            timeout=20,
        )
        data = response.json()
    except (requests.RequestException, ValueError):
        return None
    answer = _find_mget_answer(data, str(evidence.get("item_id") or ""))
    if not answer:
        return None
    content = answer.get("content") if isinstance(answer.get("content"), dict) else {}
    item = content.get("item") if isinstance(content.get("item"), dict) else {}
    return {
        "source_mode": "live_mget_answer_list_from_evidence",
        "sends_network": True,
        "evidence_path": f"{MGET_ANSWER_LIST_ENDPOINT} status={response.status_code} item={answer.get('ItemID') or evidence.get('item_id')}",
        "item_id": str(answer.get("ItemID") or content.get("itemID") or evidence.get("item_id") or ""),
        "uid": str(item.get("uid") or evidence.get("uid") or ""),
        "image_gt": str(item.get("image_gt") or evidence.get("image_gt") or ""),
        "model_image": str(item.get("model_image") or evidence.get("model_image") or ""),
        "current_answer_data": content.get("data") if isinstance(content.get("data"), dict) else {},
        **_model_images_from_item(item),
    }


def _build_question_context_from_local_evidence(store_path: Path, draft: dict[str, Any]) -> dict[str, Any]:
    evidence = _load_local_evidence(store_path, draft)
    if evidence:
        return {
            "source_mode": "local-evidence-real-task-sample",
            "sends_network": False,
            "evidence_path": str(evidence.get("evidence_path") or ""),
            "item_id": str(evidence.get("item_id") or ""),
            "uid": str(evidence.get("uid") or ""),
            "image_gt": str(evidence.get("image_gt") or ""),
            "model_image": str(evidence.get("model_image") or ""),
            "current_answer_data": evidence.get("current_answer_data") if isinstance(evidence.get("current_answer_data"), dict) else {},
        }
    return {
        "source_mode": "draft-only",
        "sends_network": False,
        "item_id": "",
        "uid": "",
        "image_gt": "",
        "model_image": "",
        "current_answer_data": {},
    }


def _load_local_evidence(store_path: Path, draft: dict[str, Any]) -> dict[str, Any]:
    task_id = str(draft.get("task_id") or "")
    evidence_path = store_path.parent / f"research-chart-{task_id}" / "research-chart-dry-run-payload.json"
    if evidence_path.exists():
        data = _load_json(evidence_path)
        payload = data.get("payload") if isinstance(data, dict) else {}
        answers = payload.get("AuditAnswers") if isinstance(payload, dict) and isinstance(payload.get("AuditAnswers"), list) else []
        answer = answers[0] if answers and isinstance(answers[0], dict) else {}
        content = _load_json_text(str(answer.get("Content") or "{}"))
        item = content.get("item") if isinstance(content.get("item"), dict) else {}
        return {
            "evidence_path": str(evidence_path),
            "task_id": str(payload.get("TaskID") or task_id),
            "node_id": str(payload.get("NodeID") or "1"),
            "item_id": str(answer.get("ItemID") or content.get("itemID") or ""),
            "uid": str(item.get("uid") or ""),
            "image_gt": str(item.get("image_gt") or ""),
            "model_image": str(item.get("model_image") or ""),
            "current_answer_data": content.get("data") if isinstance(content.get("data"), dict) else {},
        }
    return {}


def _first_material_url(resources: list[dict[str, Any]], keyword: str) -> str:
    for resource in resources:
        title = str(resource.get("title") or resource.get("key") or "")
        if keyword.lower() in title.lower():
            return str(resource.get("url") or "")
    return ""


def _find_state_account(account_user_id: str) -> dict[str, Any]:
    state = _load_json(_production_state_path())
    accounts = state.get("accounts", []) if isinstance(state, dict) else []
    for account in accounts:
        if isinstance(account, dict) and str(account.get("userId") or account.get("user_id") or "") == account_user_id:
            return account
    return {}


def _find_state_task(account: dict[str, Any], task_id: str) -> dict[str, Any]:
    tasks = account.get("tasks", []) if isinstance(account.get("tasks"), list) else []
    for task in tasks:
        if isinstance(task, dict) and str(task.get("id") or task.get("taskId") or task.get("TaskID") or "") == str(task_id):
            return task
    return {}


def _task_receive_enabled(task: dict[str, Any]) -> bool:
    category = task.get("frontendSubmittedCategory") if isinstance(task.get("frontendSubmittedCategory"), dict) else {}
    if "receiveEnable" in category:
        return bool(category.get("receiveEnable"))
    return bool(task.get("receiveEnable"))


def _operation_url_is_task_page(operation_url: Any) -> bool:
    return "/operation/task-v2" in str(operation_url or "")


def _claim_block_reason(pending: int) -> str:
    if pending <= 0:
        return "当前没有待处理题，不能自动领题。"
    return ""


def _task_claim_headers(cookie: str, referer: str, *, user_agent: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
        "Referer": referer,
        "Origin": "https://aidp.juejin.cn",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "x-secsdk-csrf-token": "DOWNGRADE",
        "x-backend-org-id": "100",
        "x-web-org-id": "100",
    }


def _task_claim_referer(account: dict[str, Any], task_id: str, node_id: int) -> str:
    preferred = str(account.get("operationUrl") or account.get("referer") or "")
    if "/operation/task-v2/" in preferred:
        return preferred
    return f"https://aidp.juejin.cn/operation/task-v2/{task_id}/mark-v3/{node_id}"


def _claim_pending_item_via_helper(account: dict[str, Any], task_id: str, node_id: int) -> bool:
    user_id = str(account.get("userId") or account.get("user_id") or "").strip()
    if not user_id:
        return False
    try:
        session = create_browser_open_session(user_id, "task")
    except Exception:
        return False
    settings = get_settings()
    launcher = _resolve_host_launcher_base_url(settings)
    monitor_url = settings.public_base_url.rstrip("/")
    payload = {
        "monitorUrl": monitor_url,
        "token": str(session.get("token") or ""),
        "taskId": str(task_id),
        "targetUrl": "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1",
        "buttonText": "处理",
        "nodeId": int(node_id),
    }
    if not payload["token"]:
        return False
    try:
        response = requests.post(f"{launcher}/api/aidp-claim-task", json=payload, timeout=45)
        data = response.json()
    except Exception:
        return False
    return bool(response.ok and isinstance(data, dict) and data.get("ok"))


def _ensure_task_browser_cdp_port(account: dict[str, Any]) -> int:
    existing_port = _num(account.get("cdpPort"))
    if existing_port and _cdp_port_ready(existing_port):
        return existing_port
    user_id = str(account.get("userId") or account.get("user_id") or "").strip()
    if not user_id:
        return 0
    try:
        session = create_browser_open_session(user_id, "task")
    except Exception:
        return 0
    token = str(session.get("token") or "").strip()
    if not token:
        return 0
    settings = get_settings()
    launcher = _resolve_host_launcher_base_url(settings)
    monitor_url = _normalize_local_monitor_url(settings.public_base_url).rstrip("/")
    open_url = f"{launcher}/api/open-with-cookie?monitorUrl={quote(monitor_url, safe='')}&token={quote(token, safe='')}"
    try:
        response = requests.get(open_url, timeout=45)
        payload = response.json()
    except Exception:
        return 0
    for candidate in _flatten_open_with_cookie_payload(payload):
        port = _num(candidate.get("cdpPort"))
        if port and _wait_cdp_port_ready(port, timeout_seconds=15):
            return port
    return 0


def _flatten_open_with_cookie_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _normalize_local_monitor_url(raw_url: str) -> str:
    try:
        parsed = urlparse(str(raw_url or "").strip())
    except Exception:
        return str(raw_url or "").strip()
    if parsed.scheme in {"http", "https"} and parsed.hostname == "localhost":
        netloc = f"127.0.0.1:{parsed.port}" if parsed.port else "127.0.0.1"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return str(raw_url or "").strip()


def _resolve_host_launcher_base_url(settings: Any) -> str:
    explicit_internal = str(getattr(settings, "host_launcher_internal_url", "") or "").strip()
    if explicit_internal:
        return explicit_internal.rstrip("/")
    raw_url = str(getattr(settings, "host_launcher_url", "") or "").strip()
    if not raw_url:
        return "http://host.docker.internal:8790"
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return raw_url.rstrip("/")
    if parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost"}:
        netloc = f"host.docker.internal:{parsed.port}" if parsed.port else "host.docker.internal"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)).rstrip("/")
    return raw_url.rstrip("/")


def _cdp_port_ready(port: int) -> bool:
    try:
        response = requests.get(f"http://127.0.0.1:{int(port)}/json/version", timeout=5)
        response.raise_for_status()
        return True
    except Exception:
        return False


def _wait_cdp_port_ready(port: int, *, timeout_seconds: int) -> bool:
    end = _now().timestamp() + timeout_seconds
    while _now().timestamp() < end:
        if _cdp_port_ready(port):
            return True
        time.sleep(0.5)
    return False


def _pick_task_page_websocket(cdp_port: int) -> str:
    end = _now().timestamp() + 12
    while _now().timestamp() < end:
        try:
            response = requests.get(f"http://127.0.0.1:{int(cdp_port)}/json/list", timeout=5)
            targets = response.json()
        except Exception:
            time.sleep(0.5)
            continue
        if not isinstance(targets, list):
            time.sleep(0.5)
            continue
        pages = [item for item in targets if isinstance(item, dict) and str(item.get("type") or "") == "page"]
        preferred = next(
            (
                str(item.get("webSocketDebuggerUrl") or "")
                for item in pages
                if "/operation/task-v2" in str(item.get("url") or "")
            ),
            "",
        )
        if preferred:
            return preferred
        fallback = next((str(item.get("webSocketDebuggerUrl") or "") for item in pages if item.get("webSocketDebuggerUrl")), "")
        if fallback:
            return fallback
        time.sleep(0.5)
    return ""


def _fetch_task_metadata_from_browser(ws_url: str, task_id: str) -> dict[str, Any]:
    script = f"""
    (async () => {{
      const res = await fetch('/api/dispatch/SearchTask', {{
        method: 'POST',
        headers: {{
          'accept': 'application/json, text/plain, */*',
          'content-type': 'application/json;charset=UTF-8',
          'x-web-org-id': '100',
          'x-backend-org-id': '100',
          'x-secsdk-csrf-token': 'DOWNGRADE'
        }},
        body: JSON.stringify({{
          Filter: {{ Query: '', TaskIDs: [], BusinessLine: [], DccOption: {{ RelationType: 0 }} }},
          PageRequest: {{ PageNo: 0, PageSize: 50 }}
        }}),
        credentials: 'include'
      }});
      const payload = await res.json();
      const item = (payload.Tasks || []).find(x => String(((x || {{}}).Task || {{}}).TaskID || '') === {json.dumps(str(task_id))}) || null;
      return item || {{}};
    }})()
    """
    for _ in range(5):
        try:
            session = _CdpPageSession(ws_url)
        except Exception:
            time.sleep(0.5)
            continue
        try:
            session.enable()
            result = session.evaluate(script, await_promise=True, user_gesture=True)
            if isinstance(result, dict) and result.get("Task"):
                return result
        finally:
            session.close()
        time.sleep(0.8)
    return {}


def _build_mark_v3_url(task_id: str, node_id: int, metadata: dict[str, Any]) -> str:
    task = metadata.get("Task") if isinstance(metadata.get("Task"), dict) else {}
    template_id = str(task.get("TemplateID") or "").strip()
    if not template_id:
        return ""
    return (
        f"https://aidp.juejin.cn/operation/task-v2/{quote(str(task_id))}/mark-v3/{int(node_id)}"
        f"?from_pathname=%2Ftask-v2%3Fpage%3D1&org=AIDP%20Coding&templateID={quote(template_id)}&templateType=1000"
    )


def _claim_pending_item_via_mark_v3_navigation(session: "_CdpPageSession", mark_v3_url: str, task_id: str) -> dict[str, Any]:
    events = session.navigate_and_capture(mark_v3_url, timeout_seconds=CDP_NAVIGATION_SETTLE_SECONDS)
    receive_response = next(
        (
            event
            for event in events
            if event.get("stage") == "response"
            and "/api/dispatch/Receive" in str(event.get("url") or "")
        ),
        {},
    )
    payload = _load_json_text(str(receive_response.get("body_text") or "{}"))
    item = _extract_claimed_item_from_receive_payload(payload)
    if not item:
        return {"source": "browser_mark_v3_opened"} if events else {}
    question_context = _question_context_from_received_item(item)
    if question_context:
        return {"source": "browser_mark_v3_auto", "question_context": question_context}
    return {"source": "browser_mark_v3_opened"}


def _extract_claimed_item_from_receive_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if _base_resp_status_code(payload) != 0:
        return {}
    items = payload.get("Items")
    if not isinstance(items, list) or not items:
        return {}
    first = items[0]
    if not isinstance(first, dict):
        return {}
    item = first.get("Item")
    return item if isinstance(item, dict) else {}


def _question_context_from_received_item(item: dict[str, Any]) -> dict[str, Any]:
    content = _load_json_text(str(item.get("Content") or "{}"))
    payload_item = content.get("item") if isinstance(content.get("item"), dict) else content
    item_id = str(item.get("ItemID") or content.get("itemID") or "")
    if not item_id:
        return {}
    return {
        "source_mode": "browser_signed_receive",
        "sends_network": True,
        "evidence_path": f"browser-signed-receive item={item_id}",
        "item_id": item_id,
        "node_id": str(item.get("NodeID") or ""),
        "uid": str(payload_item.get("uid") or ""),
        "image_gt": str(payload_item.get("image_gt") or ""),
        "model_image": str(payload_item.get("model_image") or ""),
        "current_answer_data": content.get("data") if isinstance(content.get("data"), dict) else {},
        "raw_status": item.get("Status") or item.get("status"),
        **_model_images_from_item(payload_item),
    }


class _CdpPageSession:
    def __init__(self, ws_url: str) -> None:
        self._ws = websocket.create_connection(ws_url, timeout=CDP_WAIT_TIMEOUT_SECONDS, suppress_origin=True)
        self._ws.settimeout(1)
        self._next_id = 0
        self._responses: dict[int, dict[str, Any]] = {}
        self._network_events: list[dict[str, Any]] = []

    def close(self) -> None:
        with suppress(Exception):
            self._ws.close()

    def enable(self) -> None:
        self.command("Page.enable", timeout_seconds=5)
        self.command("Runtime.enable", timeout_seconds=5)
        self.command("Network.enable", timeout_seconds=5)
        self.command("Page.bringToFront", timeout_seconds=5)

    def evaluate(self, expression: str, *, await_promise: bool = False, user_gesture: bool = False) -> Any:
        response = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": user_gesture,
            },
        )
        return (((response.get("result") or {}).get("result") or {}).get("value"))

    def navigate_and_capture(self, url: str, *, timeout_seconds: int) -> list[dict[str, Any]]:
        self._network_events = []
        self.command("Page.navigate", {"url": url}, timeout_seconds=10)
        self._drain_events(timeout_seconds)
        return list(self._network_events)

    def command(self, method: str, params: Optional[dict[str, Any]] = None, *, timeout_seconds: int = CDP_WAIT_TIMEOUT_SECONDS) -> dict[str, Any]:
        command_id = self._send(method, params or {})
        result = self._wait_response(command_id, timeout_seconds)
        return result or {}

    def _send(self, method: str, params: dict[str, Any]) -> int:
        self._next_id += 1
        command_id = self._next_id
        self._ws.send(json.dumps({"id": command_id, "method": method, "params": params}))
        return command_id

    def _wait_response(self, command_id: int, timeout_seconds: int) -> dict[str, Any]:
        end = _now().timestamp() + timeout_seconds
        while _now().timestamp() < end:
            if command_id in self._responses:
                return self._responses.pop(command_id)
            self._pump_once()
        return {}

    def _drain_events(self, timeout_seconds: int) -> None:
        end = _now().timestamp() + timeout_seconds
        while _now().timestamp() < end:
            self._pump_once()

    def _pump_once(self) -> None:
        try:
            payload = json.loads(self._ws.recv())
        except Exception:
            return
        if "id" in payload:
            self._responses[int(payload["id"])] = payload
            return
        self._handle_event(payload)

    def _handle_event(self, payload: dict[str, Any]) -> None:
        method = str(payload.get("method") or "")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        if method == "Network.requestWillBeSent":
            request = params.get("request") if isinstance(params.get("request"), dict) else {}
            url = str(request.get("url") or "")
            if any(endpoint in url for endpoint in (PRE_RECEIVE_ENDPOINT, RECEIVE_ENDPOINT, MGET_ANSWER_LIST_ENDPOINT)):
                self._network_events.append(
                    {
                        "stage": "request",
                        "request_id": str(params.get("requestId") or ""),
                        "url": url,
                        "post_data": request.get("postData"),
                    }
                )
            return
        if method != "Network.responseReceived":
            return
        response = params.get("response") if isinstance(params.get("response"), dict) else {}
        url = str(response.get("url") or "")
        if not any(endpoint in url for endpoint in (PRE_RECEIVE_ENDPOINT, RECEIVE_ENDPOINT, MGET_ANSWER_LIST_ENDPOINT)):
            return
        body_text = ""
        request_id = str(params.get("requestId") or "")
        if request_id:
            response_id = self._send("Network.getResponseBody", {"requestId": request_id})
            body_response = self._wait_response(response_id, 5)
            result = body_response.get("result") if isinstance(body_response.get("result"), dict) else {}
            body_text = str(result.get("body") or "")
        self._network_events.append(
            {
                "stage": "response",
                "request_id": request_id,
                "url": url,
                "status": int(response.get("status") or 0),
                "body_text": body_text,
            }
        )


def _bool_value(*values: Any) -> bool:
    for value in values:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
        if value is not None:
            return bool(value)
    return False


def _find_mget_answer(data: Any, preferred_item_id: str) -> dict[str, Any]:
    candidates = []
    if isinstance(data, dict):
        for key in ("Data", "data", "AnswerList", "answerList"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(value)
            elif isinstance(value, dict):
                nested = value.get("AnswerList") or value.get("answerList") or value.get("Data") or value.get("data")
                if isinstance(nested, list):
                    candidates.extend(nested)
    parsed = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("Content") or candidate.get("content")
        parsed_content = _load_json_text(str(content or "{}"))
        parsed.append(candidate | {"content": parsed_content})
    for candidate in parsed:
        if str(candidate.get("ItemID") or candidate.get("itemID") or "") == preferred_item_id:
            return candidate
    return parsed[0] if parsed else {}


def _first_category_item(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    value = data.get("Data") or data.get("data")
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    if isinstance(value, dict):
        nested = value.get("Data") or value.get("data") or value.get("List") or value.get("list")
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    return item
    return {}


def _parse_category_item_content(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("Content") or item.get("content")
    if isinstance(content, dict):
        return content
    parsed = _load_json_text(str(content or "{}"))
    return parsed if isinstance(parsed, dict) else {}


def _build_conservative_ai_decision(context: dict[str, Any], draft: dict[str, Any]) -> dict[str, str]:
    reason = "科研图表还原任务采用严格保守规则；未进行人工视觉复核前默认给0分并说明需核对文字、图表、点位和网格。"
    if context.get("image_gt") and (context.get("model_image") or _extract_research_chart_model_entries(context)):
        reason = "已取得原图和AI生产图材料；按严格规则先保守判0分，需人工核对文字、图表、点位、网格是否完全一致后再放行。"
    return {"score": "0", "reason": reason, "confidence": "low", "rules_source": str(draft.get("version") or "")}


def _build_task_ai_decision_for_research_chart(context: dict[str, Any], draft: dict[str, Any], *, require_provider: bool, prefer_system_ai: bool = False) -> dict[str, Any]:
    image_gt = str(context.get("image_gt") or "").strip()
    if not require_provider:
        return _build_conservative_ai_decision(context, draft)
    if not image_gt:
        raise TaskAbilityFlowError("当前真实题缺少原图或AI生成图，不能执行最后闸门评分。")
    if prefer_system_ai:
        system_runtime = get_system_ai_runtime_prompt()
        if not system_runtime.get("provider_configured"):
            raise TaskAbilityFlowError("系统 AI provider 未配置，不能执行最后闸门的真实看图评分。")
        return _call_research_chart_ai_provider(context, draft, system_runtime, provider_role="system_ai_vision")
    runtime = get_task_ai_runtime_prompt()
    if not runtime.get("provider_configured"):
        raise TaskAbilityFlowError("做题 AI provider 未配置，不能执行最后闸门的真实看图评分。")
    model_entries = _extract_research_chart_model_entries(context)
    if not model_entries:
        raise TaskAbilityFlowError("当前真实题缺少原图或AI生成图，不能执行最后闸门评分。")
    if len(model_entries) == 1:
        model_key, model_image = model_entries[0]
        single_context = dict(context)
        single_context["model_image"] = model_image
        single_context["current_model_key"] = model_key
        try:
            return _call_research_chart_ai_provider(single_context, draft, runtime, provider_role="task_ai")
        except TaskAbilityFlowError as exc:
            if not _is_image_unsupported_provider_error(str(exc)):
                raise
            system_runtime = get_system_ai_runtime_prompt()
            if not system_runtime.get("provider_configured"):
                raise TaskAbilityFlowError(f"做题 AI 不支持图片输入，且系统 AI 未配置，不能执行最后闸门评分：{exc}") from exc
            decision = _call_research_chart_ai_provider(single_context, draft, system_runtime, provider_role="system_ai_vision_fallback")
            decision["fallback_reason"] = "task_ai_image_input_unsupported"
            return decision
    model_scores: dict[str, dict[str, Any]] = {}
    for model_key, model_image in model_entries:
        model_context = dict(context)
        model_context["model_image"] = model_image
        model_context["current_model_key"] = model_key
        model_scores[model_key] = _call_research_chart_ai_provider(model_context, draft, runtime, provider_role="task_ai")
    first_key = model_entries[0][0]
    first = model_scores[first_key]
    return {
        "score": str(first.get("score") or "0"),
        "reason": str(first.get("reason") or ""),
        "confidence": str(first.get("confidence") or "low"),
        "provider_status": "provider_ok",
        "provider_role": "task_ai_multi_model",
        "rules_source": str(draft.get("version") or ""),
        "model_scores": model_scores,
        "model_order": [item[0] for item in model_entries],
    }


def _call_research_chart_ai_provider(context: dict[str, Any], draft: dict[str, Any], runtime: dict[str, object], *, provider_role: str) -> dict[str, Any]:
    endpoint = str(runtime.get("base_url") or "").rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    headers = {"Authorization": f"Bearer {runtime.get('api_key')}", "Content-Type": "application/json"}
    started = datetime.now(timezone.utc)
    decision: dict[str, Any] = {}
    last_error: Optional[TaskAbilityFlowError] = None
    for attempt in range(2):
        messages = _build_research_chart_ai_messages(context, draft, runtime, format_retry=bool(attempt))
        payload = {
            "model": runtime.get("model") or "gpt-4.1-mini",
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 900,
            "response_format": {"type": "json_object"},
        }
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=int(runtime.get("timeout_seconds") or 30))
            if not getattr(response, "ok", True):
                body = str(getattr(response, "text", ""))[:600]
                raise TaskAbilityFlowError(f"{provider_role} 返回 HTTP {getattr(response, 'status_code', '')}: {body}")
            response.raise_for_status()
            data = response.json()
            content = str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))
            decision = _parse_research_chart_ai_decision(content)
            break
        except TaskAbilityFlowError as exc:
            last_error = exc
            if attempt == 0 and _should_retry_research_chart_provider_output(str(exc)):
                continue
            raise
        except Exception as exc:  # noqa: BLE001 - final gate must not silently fall back.
            raise TaskAbilityFlowError(f"{provider_role} 看图评分失败，不能进入端到端不提交最后闸门：{exc}") from exc
    if not decision and last_error:
        raise last_error
    decision["provider_status"] = "provider_ok"
    decision["provider_role"] = provider_role
    decision["provider_elapsed_ms"] = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    decision["rules_source"] = str(draft.get("version") or "")
    return decision


def _should_retry_research_chart_provider_output(message: str) -> bool:
    text = str(message or "")
    return any(
        marker in text
        for marker in (
            "复述了输入上下文",
            "未返回 JSON 对象",
            "无依据短理由",
            "未返回合法 score",
            "未返回评分原因",
        )
    )


def _is_image_unsupported_provider_error(message: str) -> bool:
    text = message.lower()
    return "image_url" in text and ("unknown variant" in text or "expected `text`" in text or "expected text" in text)


def _build_research_chart_ai_messages(
    context: dict[str, Any],
    draft: dict[str, Any],
    runtime: dict[str, object],
    *,
    format_retry: bool = False,
) -> list[dict[str, Any]]:
    system_parts = [
        "你是 AIDP 科研图表还原评分 AI，只能根据两张图片输出评分 JSON，不允许提交、领取、切换账号或改系统配置。",
        "不确定时给 0 分。必须输出 JSON 对象，禁止 Markdown。",
    ]
    model_key = str(context.get("current_model_key") or "model_image")
    page_fields = context.get("page_fields") if isinstance(context.get("page_fields"), dict) else {}
    extra_context = context.get("extra_context") if isinstance(context.get("extra_context"), dict) else {}
    images = context.get("images") if isinstance(context.get("images"), list) else []
    video = context.get("video") if isinstance(context.get("video"), dict) else {}
    audio = context.get("audio") if isinstance(context.get("audio"), dict) else {}
    task_ai_input = {
        "prompt_template": {
            "task_name": str(draft.get("task_name") or ""),
            "task_id": str(draft.get("task_id") or ""),
            "ability_version": str(draft.get("version") or ""),
            "rules": str(draft.get("system_ai_draft") or ""),
        },
        "current_item_input": {
            "task_id": str(draft.get("task_id") or context.get("task_id") or ""),
            "item_id": str(context.get("item_id") or ""),
            "uid": str(context.get("uid") or ""),
            "task_text": str(context.get("task_text") or ""),
            "web_url": str(context.get("web_url") or ""),
            "media": {
                "original_image": {"url": str(context.get("image_gt") or ""), "role": "reference_image"},
                "ai_image": {"url": str(context.get("model_image") or ""), "role": "candidate_image", "key": model_key},
                "images": images,
                "video": {
                    "url": str(video.get("url") or context.get("video") or "") or None,
                    "keyframes": video.get("keyframes") if isinstance(video.get("keyframes"), list) else [],
                    "duration_seconds": video.get("duration_seconds"),
                },
                "audio": {
                    "url": str(audio.get("url") or "") or None,
                    "duration_seconds": audio.get("duration_seconds"),
                },
            },
            "page_fields": {
                "score_field": str(page_fields.get("score_field") or "data.label_sorce.*"),
                "reason_field": str(page_fields.get("reason_field") or "data.label_remark.*"),
            },
            "extra_context": extra_context,
        },
        "output_schema": {
            "score": "字符串 0/1/2",
            "reason": "中文评分原因，必须说明图片对比依据",
            "confidence": "high/medium/low",
            "visual_findings": ["列出关键视觉发现，如文字、坐标、点位、网格、布局差异"],
        },
    }
    if format_retry:
        user_text = (
            "这是格式纠错重试。不要复述输入，不要输出表单字段，只根据当前两张图片评分。\n\n"
            "【第一层：完整 Prompt 草稿】\n"
            f"任务名称：{draft.get('task_name') or ''}\n"
            f"任务 ID：{draft.get('task_id') or context.get('task_id') or ''}\n"
            f"能力版本：{draft.get('version') or ''}\n"
            f"{draft.get('system_ai_draft') or ''}\n\n"
            "【第二层：当前题目信息】\n"
            f"item_id：{context.get('item_id') or ''}\n"
            f"uid：{context.get('uid') or ''}\n"
            f"task_text：{context.get('task_text') or ''}\n"
            f"web_url：{context.get('web_url') or ''}\n"
            f"原图 image_gt：{context.get('image_gt') or ''}\n"
            f"待评分 AI 图 {model_key}：{context.get('model_image') or ''}\n"
            f"extra_context：{json.dumps(extra_context, ensure_ascii=False)}\n\n"
            "【第三层：输出规则】\n"
            "只输出一个 JSON 对象，只允许四个键：score、reason、confidence、visual_findings。\n"
            "score 必须是字符串 0/1/2；reason 必须用中文具体说明当前两张图片的可见对比依据；confidence 必须是 high/medium/low；visual_findings 必须是字符串数组。\n"
            "禁止输出输入层级标题、表单字段、丢弃字段、Markdown 或解释文字。"
        )
    else:
        user_text = (
            f"请严格按当前能力版本完整 Prompt 草稿执行。请比较下面两张图：第一张是原图 image_gt，第二张是 AI 生成图 {model_key}。\n"
            "下面的 JSON 是输入上下文，不是输出模板；禁止复述 prompt_template、current_item_input 或 output_schema。\n"
            "你必须根据输入上下文和两张图片完成评分，最终只输出一个顶层 JSON 对象，且只能包含 score、reason、confidence、visual_findings 字段。\n"
            "系统会把 score/reason 映射到 data.label_sorce 与 data.label_remark，你不要输出任何 data.*、discard、checkRemark 或表单字段。\n"
            "score 必须是字符串 0/1/2；reason 必须用中文具体说明图片对比依据；confidence 必须是 high/medium/low；visual_findings 必须是字符串数组。\n"
            "输出 JSON 对象闭合后立即停止，不要补空白、不要继续生成任何字符。\n"
            "输入上下文 JSON：\n"
            + json.dumps(task_ai_input, ensure_ascii=False)
        )
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": user_text,
        },
        {"type": "image_url", "image_url": {"url": _prepare_research_chart_image_for_ai(str(context.get("image_gt") or ""))}},
        {"type": "image_url", "image_url": {"url": _prepare_research_chart_image_for_ai(str(context.get("model_image") or ""))}},
    ]
    return [
        {"role": "system", "content": "\n".join(system_parts)},
        {"role": "user", "content": content},
    ]


def _parse_research_chart_ai_decision(content: str) -> dict[str, Any]:
    raw_content = str(content or "")
    try:
        parsed = _parse_json_object(raw_content)
    except TaskAbilityFlowError as exc:
        if '"prompt_template"' in raw_content and '"current_item_input"' in raw_content:
            raise TaskAbilityFlowError("做题 AI 复述了输入上下文，未返回评分 JSON，不能暂存。") from exc
        parsed = _parse_partial_research_chart_decision(raw_content)
        if not parsed:
            raise
    if "prompt_template" in parsed or "current_item_input" in parsed or "output_schema" in parsed:
        raise TaskAbilityFlowError("做题 AI 复述了输入上下文，未返回评分 JSON，不能暂存。")
    nested_output = parsed.get("required_output")
    if isinstance(nested_output, dict):
        parsed = nested_output
    score_value = parsed.get("score") if "score" in parsed else ""
    score = str(score_value).strip()
    if score not in {"0", "1", "2"}:
        raise TaskAbilityFlowError("做题 AI 未返回合法 score，不能暂存。")
    reason = str(parsed.get("reason") or "").strip()
    if not reason:
        raise TaskAbilityFlowError("做题 AI 未返回评分原因，不能暂存。")
    if reason in UNEXPLAINED_RESEARCH_CHART_REASONS:
        raise TaskAbilityFlowError("做题 AI 返回无依据短理由，不能暂存。")
    confidence = str(parsed.get("confidence") or "low").strip()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    findings = parsed.get("visual_findings")
    if not isinstance(findings, list):
        findings = []
    return {
        "score": score,
        "reason": reason[:1200],
        "confidence": confidence,
        "visual_findings": [str(item)[:240] for item in findings if str(item).strip()][:8],
    }


def _parse_partial_research_chart_decision(text: str) -> dict[str, Any]:
    raw = str(text or "")
    score = _extract_json_string_field(raw, "score")
    reason = _extract_json_string_field(raw, "reason")
    if score not in {"0", "1", "2"} or not reason:
        return {}
    confidence = _extract_json_string_field(raw, "confidence") or "low"
    findings: list[Any] = []
    findings_fragment = _extract_json_array_field(raw, "visual_findings")
    if findings_fragment:
        try:
            loaded_findings = json.loads(findings_fragment)
            if isinstance(loaded_findings, list):
                findings = loaded_findings
        except json.JSONDecodeError:
            findings = []
    return {
        "score": score,
        "reason": reason,
        "confidence": confidence,
        "visual_findings": findings,
    }


def _extract_json_string_field(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', str(text or ""), flags=re.DOTALL)
    if not match:
        return ""
    try:
        value = json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return ""
    return str(value).strip()


def _extract_json_array_field(text: str, key: str) -> str:
    key_match = re.search(rf'"{re.escape(key)}"\s*:', str(text or ""))
    if not key_match:
        return ""
    start = key_match.end()
    source = str(text or "")
    while start < len(source) and source[start].isspace():
        start += 1
    if start >= len(source) or source[start] != "[":
        return ""
    stack: list[str] = []
    in_string = False
    escape = False
    for index in range(start, len(source)):
        char = source[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append("]" if char == "[" else "}")
        elif char in "]}":
            if not stack or stack[-1] != char:
                return ""
            stack.pop()
            if not stack:
                return source[start : index + 1]
    return ""


def _prepare_research_chart_image_for_ai(url: str) -> str:
    source = str(url or "").strip()
    if not source:
        return ""
    if source.startswith("data:"):
        return source
    try:
        response = requests.get(source, timeout=30)
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type") or "").strip() or "image/png"
        encoded = base64.b64encode(response.content).decode("ascii")
        return f"data:{content_type};base64,{encoded}"
    except Exception:
        return source


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        repaired = _repair_truncated_json_object(cleaned)
        if repaired:
            try:
                parsed = json.loads(repaired)
            except json.JSONDecodeError:
                repaired = ""
        if repaired:
            if not isinstance(parsed, dict):
                raise TaskAbilityFlowError("做题 AI 输出不是 JSON 对象，不能暂存。")
            return parsed
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise TaskAbilityFlowError("做题 AI 未返回 JSON 对象，不能暂存。")
        fragment = match.group(0)
        try:
            parsed = json.loads(fragment)
        except json.JSONDecodeError:
            repaired = _repair_truncated_json_object(fragment)
            if not repaired:
                raise
            parsed = json.loads(repaired)
    if not isinstance(parsed, dict):
        raise TaskAbilityFlowError("做题 AI 输出不是 JSON 对象，不能暂存。")
    return parsed


def _repair_truncated_json_object(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned.startswith("{"):
        return ""
    stack: list[str] = []
    in_string = False
    escape = False
    for char in cleaned:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack or stack[-1] != char:
                return ""
            stack.pop()
    if in_string or not stack:
        return ""
    return cleaned + "".join(reversed(stack))


def _normalize_ai_decision(decision: dict[str, Any]) -> dict[str, Any]:
    score = str(decision.get("score") or decision.get("data.label_sorce.model_image") or "0").strip()
    if score not in {"0", "1", "2"}:
        score = "0"
    reason = str(decision.get("reason") or "不确定时按规则给0分并要求人工复核。").strip()
    confidence = str(decision.get("confidence") or "low").strip()
    result: dict[str, Any] = {"score": score, "reason": reason, "confidence": confidence}
    for key in ("provider_status", "provider_role", "provider_elapsed_ms", "visual_findings", "rules_source", "fallback_reason", "model_scores", "model_order"):
        if key in decision:
            result[key] = decision[key]
    return result


def _build_answer_preview(decision: dict[str, Any]) -> dict[str, Any]:
    model_scores = decision.get("model_scores") if isinstance(decision.get("model_scores"), dict) else {}
    if model_scores:
        result: dict[str, Any] = {"data.discard": "No"}
        for model_key, model_decision in model_scores.items():
            if not isinstance(model_decision, dict):
                continue
            result[f"data.label_sorce.{model_key}"] = str(model_decision.get("score") or "0")
            result[f"data.label_remark.{model_key}"] = str(model_decision.get("reason") or "")
        return result
    return {
        "data.label_sorce.model_image": decision["score"],
        "data.label_remark.model_image": decision["reason"],
        "data.discard": "No",
    }


def _ensure_live_question_context_for_temp_save(context: dict[str, Any]) -> None:
    source_mode = str(context.get("source_mode") or "")
    item_id = str(context.get("item_id") or "").strip()
    recorded_or_local_sources = {"local-evidence-real-task-sample", "draft-only", "live_mget_answer_list_from_evidence"}
    if not item_id:
        raise TaskAbilityFlowError("未取得当前真实题目 ItemID，不能执行端到端不提交暂存。")
    if source_mode in recorded_or_local_sources:
        raise TaskAbilityFlowError("当前题面来自录制题目或本地证据；录制题目已经提交过，不能用于端到端不提交暂存。请先打开/获取当前真实题后重新执行。")
    if "failed" in source_mode or "recording" in source_mode:
        raise TaskAbilityFlowError(f"当前题面来源不是可暂存的实时题：{source_mode}。请先获取当前真实题后重新执行。")


def _build_temp_draft_payload(store_path: Path, draft: dict[str, Any], context: dict[str, Any], decision: dict[str, str]) -> dict[str, Any]:
    payload = _load_recorded_temp_payload(store_path, draft)
    item_id = str(context.get("item_id") or "")
    if payload:
        answers = payload.get("AuditAnswers") if isinstance(payload.get("AuditAnswers"), list) else []
        answer = answers[0] if answers and isinstance(answers[0], dict) else {}
        if item_id:
            answer["ItemID"] = item_id
        content = _load_json_text(str(answer.get("Content") or "{}"))
        if not isinstance(content, dict):
            content = {}
        if item_id:
            content["itemID"] = item_id
        _merge_context_into_content(content, context)
        _apply_decision_to_content(content, decision)
        answer["Content"] = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        answer.setdefault("ControlData", json.dumps({"Discard": False, "extraAnswer": []}, ensure_ascii=False, separators=(",", ":")))
        return payload

    if not item_id:
        raise TaskAbilityFlowError("真实题暂存缺少 ItemID，不能写入做题界面。")
    content = {
        "item": {
            "uid": str(context.get("uid") or ""),
            "image_gt": str(context.get("image_gt") or ""),
            "model_image": str(context.get("model_image") or ""),
        },
        "type": "neeko",
        "data": {},
        "dataMap": {"checkRemark": None, "discard": "No", "discard_type": [], "discard_remark": None, "label_sorce": {}, "label_remark": {}},
        "itemID": item_id,
        "isAbandoned": False,
    }
    _apply_decision_to_content(content, decision)
    return {
        "AuditAnswers": [
            {
                "ItemID": item_id,
                "Content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                "ControlData": json.dumps({"Discard": False, "extraAnswer": []}, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "NodeID": str(context.get("node_id") or draft.get("node_id") or "1"),
        "StagingTime": "604800",
        "TaskID": str(draft.get("task_id") or ""),
    }


def _load_recorded_temp_payload(store_path: Path, draft: dict[str, Any]) -> dict[str, Any]:
    task_id = str(draft.get("task_id") or "")
    evidence_path = store_path.parent / f"research-chart-{task_id}" / "research-chart-dry-run-payload.json"
    data = _load_json(evidence_path)
    payload = data.get("payload") if isinstance(data, dict) else {}
    return json.loads(json.dumps(payload, ensure_ascii=False)) if isinstance(payload, dict) else {}


def _merge_context_into_content(content: dict[str, Any], context: dict[str, Any]) -> None:
    item = content.setdefault("item", {})
    if not isinstance(item, dict):
        item = {}
        content["item"] = item
    for key in ("uid", "image_gt", "model_image"):
        value = str(context.get(key) or "")
        if value:
            item[key] = value
    for model_key, model_url in _extract_research_chart_model_entries(context):
        if model_url:
            item[model_key] = model_url
        bon_key = f"{model_key}_bon_id"
        bon_value = context.get(bon_key)
        if bon_value not in (None, ""):
            item[bon_key] = bon_value
    current_data = context.get("current_answer_data")
    if isinstance(current_data, dict):
        data = content.setdefault("data", {})
        if isinstance(data, dict):
            for key, value in current_data.items():
                data.setdefault(key, value)


def _apply_decision_to_content(content: dict[str, Any], decision: dict[str, Any]) -> None:
    data = content.setdefault("data", {})
    if not isinstance(data, dict):
        data = {}
        content["data"] = data
    label_score = data.setdefault("label_sorce", {})
    label_reason = data.setdefault("label_remark", {})
    if not isinstance(label_score, dict):
        label_score = {}
        data["label_sorce"] = label_score
    if not isinstance(label_reason, dict):
        label_reason = {}
        data["label_remark"] = label_reason
    model_scores = decision.get("model_scores") if isinstance(decision.get("model_scores"), dict) else {}
    if model_scores:
        for model_key, model_decision in model_scores.items():
            if not isinstance(model_decision, dict):
                continue
            label_score[str(model_key)] = str(model_decision.get("score") or "0")
            label_reason[str(model_key)] = str(model_decision.get("reason") or "")
    else:
        label_score["model_image"] = decision["score"]
        label_reason["model_image"] = decision["reason"]
    data["discard"] = "No"
    data["discard_type"] = []
    data["discard_remark"] = None
    data["checkRemark"] = None


def _extract_research_chart_model_entries(context: dict[str, Any]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for key, value in context.items():
        key_text = str(key)
        if not key_text.startswith("model_image"):
            continue
        if key_text.endswith("_bon_id"):
            continue
        url = str(value or "").strip()
        if not url:
            continue
        entries.append((key_text, url))
    if not entries:
        single = str(context.get("model_image") or "").strip()
        if single:
            entries.append(("model_image", single))
    entries.sort(key=lambda item: (0 if item[0] == "model_image" else 1, item[0]))
    return entries


def _model_images_from_item(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(item, dict):
        return result
    for key, value in item.items():
        key_text = str(key)
        if not key_text.startswith("model_image") or key_text.endswith("_bon_id"):
            continue
        url = str(value or "").strip()
        if url:
            result[key_text] = url
        bon_key = f"{key_text}_bon_id"
        if bon_key in item and item.get(bon_key) not in (None, ""):
            result[bon_key] = item.get(bon_key)
    return result


def _model_images_from_material_resources(resources: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    model_index = 1
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        title = str(resource.get("title") or resource.get("key") or "")
        url = str(resource.get("url") or "").strip()
        lower = title.lower()
        if not url or "原图" in title or "image_gt" in lower:
            continue
        if "ai" in lower or "model" in lower or "generated" in lower:
            key = "model_image" if model_index == 1 else f"model_image{model_index}"
            if key not in result:
                result[key] = url
                model_index += 1
    return result


def _select_temp_save_account(snapshot: dict[str, Any]) -> dict[str, Any]:
    account_user_id = str(snapshot.get("account_user_id") or "")
    account = _find_state_account(account_user_id)
    if account:
        return account
    return {"userId": account_user_id, "displayName": str(snapshot.get("account_name") or "")}


def _execute_temp_save_with_guard(payload: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("AIDP_TEMP_DRAFT_ALLOW_WRITE") != "1":
        return {
            "ok": False,
            "base_resp_status_code": None,
            "error": "missing-env-AIDP_TEMP_DRAFT_ALLOW_WRITE",
            "message": "AIDP_TEMP_DRAFT_ALLOW_WRITE 未设置为 1，后端已拦截真实暂存。",
        }
    cookie = str(account.get("cookie") or "")
    if not cookie:
        return {"ok": False, "base_resp_status_code": None, "error": "account-cookie-missing", "message": "目标账号 Cookie 不可用，不能暂存到真实做题界面。"}
    return _post_temp_save(payload, account)


def _post_temp_save(payload: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    referer = str(account.get("referer") or account.get("operationUrl") or "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1")
    try:
        response = requests.post(
            f"https://aidp.juejin.cn{SUBMIT_TEMP_ENDPOINT}",
            json=payload,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Cookie": str(account.get("cookie") or ""),
                "Referer": referer,
                "Origin": "https://aidp.juejin.cn",
                "Content-Type": "application/json",
                "User-Agent": "aidp-monitor-next/task-ability-temp-save",
                "x-secsdk-csrf-token": "DOWNGRADE",
                "x-backend-org-id": "100",
                "x-web-org-id": "100",
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
            "base_resp_status_code": _base_resp_status_code(data),
            "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "endpoint": SUBMIT_TEMP_ENDPOINT,
            "data": data,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status_code": None,
            "base_resp_status_code": None,
            "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "endpoint": SUBMIT_TEMP_ENDPOINT,
            "error": str(exc),
            "data": None,
        }


def _temp_save_succeeded(result: Optional[dict[str, Any]]) -> bool:
    if not isinstance(result, dict) or not result.get("ok"):
        return False
    status = result.get("base_resp_status_code")
    return status in (0, "0", None)


def _base_resp_status_code(response: Any) -> Optional[int]:
    if isinstance(response, dict) and isinstance(response.get("BaseResp"), dict):
        value = response["BaseResp"].get("StatusCode")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _write_review_artifact(review_root: Path, artifact: dict[str, Any]) -> Path:
    review_root.mkdir(parents=True, exist_ok=True)
    path = review_root / f"{artifact['draft_id']}-{_now().strftime('%Y%m%d%H%M%S')}.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _default_review_root(store_path: Path, draft: dict[str, Any]) -> Path:
    task_id = str(draft.get("task_id") or "unknown-task")
    return store_path.parent / f"research-chart-{task_id}" / "real-no-submit-reviews"


def _production_state_path() -> Path:
    return _resolve_path(get_settings().production_state_path)


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


def _load_json(path: Path) -> Any:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _load_json_text(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        return {}


def _safe_uid(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\\\/:*?\"<>|#]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    return text[:180] or "sample"


def _num(*values: Any) -> int:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(float(str(value).replace(",", "").strip()))
        except ValueError:
            continue
    return 0
