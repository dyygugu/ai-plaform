import json
import os
import inspect
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Optional, Sequence
from uuid import uuid4

import requests
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.schemas.bon8_production import Bon8ProductionRunResponse, Bon8ProductionStartRequest
from app.schemas.task_auto_runs import (
    TaskAutoRunAccountState,
    TaskAutoRunPreflightCheck,
    TaskAutoRunPreflightResponse,
    TaskAutoRunResponse,
    TaskAutoRunStartRequest,
)
from app.services.bon8_production_service import (
    BON8_NODE_ID,
    BON8_TASK_ID,
    get_bon8_production_run,
    start_bon8_production,
    stop_bon8_production_run,
)
from app.services.aidp_3d_http_answer_service import (
    AIDP_3D_RUBRIC_MAX_PARALLEL_ACCOUNTS,
    AIDP_3D_RUBRIC_NODE_ID,
    AIDP_3D_RUBRIC_TASK_ID,
    AIDP_3D_RUBRIC_TEMPLATE_ID,
    Aidp3DAnswerError,
    Aidp3DHttpAnswerService,
)
from app.services.runtime_account_service import load_production_state, load_runtime_account
from app.services.task_ability_service import (
    TaskAbilityFlowError,
    build_task_ability_run_context,
    get_enabled_task_ability_draft,
    get_task_ability_run_gate,
    run_task_ability_real_no_submit,
)
from app.services.task_rules import utc_now

RESEARCH_CHART_TASK_ID = "7638992213846740763"
RESEARCH_CHART_FULL_DATASET_TASK_ID = "7639402643386830630"
RESEARCH_CHART_TASK_IDS = {RESEARCH_CHART_TASK_ID, RESEARCH_CHART_FULL_DATASET_TASK_ID}
TASK_AUTO_RUN_PREFIX = "task-auto-"
RESEARCH_CHART_MAX_PARALLEL_ACCOUNTS = 5


@dataclass
class TaskAutoRunAdapterSnapshot:
    adapter_key: str
    adapter_run_id: str
    task_id: str
    node_id: str
    status: str
    stop_requested: bool
    accounts: list[TaskAutoRunAccountState]
    last_error: str
    next_step: str
    message: str
    raw_adapter_run: dict[str, Any]


class TaskAutoRunBon8Adapter:
    adapter_key = "bon8"
    supported_task_ids = {BON8_TASK_ID}

    def __init__(self, *, account_loader=load_runtime_account, transport=None, bon8_state_dir: Optional[Path] = None) -> None:
        self.account_loader = account_loader
        self.transport = transport
        self.bon8_state_dir = bon8_state_dir

    def start(self, db: Session, request: TaskAutoRunStartRequest) -> TaskAutoRunAdapterSnapshot:
        bon8_request = Bon8ProductionStartRequest(
            account_user_ids=request.account_user_ids,
            task_id=request.task_id or BON8_TASK_ID,
            node_id=request.node_id or BON8_NODE_ID,
            write_audit=request.write_audit,
        )
        run = start_bon8_production(
            db,
            bon8_request,
            account_loader=self.account_loader,
            transport=self.transport,
            state_dir=self.bon8_state_dir,
        )
        snapshot = _snapshot_from_bon8(run)
        snapshot.raw_adapter_run = {**snapshot.raw_adapter_run, "run_config": request.run_config}
        return snapshot

    def preflight(self, request: TaskAutoRunStartRequest) -> TaskAutoRunPreflightResponse:
        account_ids = _normalize_account_ids(request.account_user_ids)
        ability_draft = get_enabled_task_ability_draft(str(request.task_id or BON8_TASK_ID))
        checks: list[TaskAutoRunPreflightCheck] = [
            TaskAutoRunPreflightCheck(
                key="ability_published",
                title="题型能力发布",
                status="passed" if ability_draft else "blocked",
                detail="bon8 已在 AI 标注能力工作台发布，可进入统一任务控制台。" if ability_draft else "bon8 还没有进入 AI 标注能力工作台的已发布能力。",
                next_step="" if ability_draft else "先让 bon8 进入 AI 标注能力工作台并处于有做题能力状态。",
            )
        ]
        if not account_ids:
            checks.append(
                TaskAutoRunPreflightCheck(
                    key="selected_accounts",
                    title="执行账号",
                    status="blocked",
                    detail="当前未选择任何账号。",
                    next_step="先选择至少一个有 bon8 当前处理中题的账号。",
                )
            )
            return TaskAutoRunPreflightResponse(
                generated_at=utc_now(),
                task_id=str(request.task_id or BON8_TASK_ID),
                node_id=str(request.node_id or BON8_NODE_ID),
                adapter_key=self.adapter_key,
                status="blocked",
                can_start=False,
                runnable_account_count=0,
                checks=checks,
                message="bon8 自检发现未选择执行账号。",
                next_step="选择有 bon8 当前处理中题的账号后再启动自动做题。",
            )

        cookie_ok = 0
        current_item_ok = 0
        missing_current_item: list[str] = []
        for account_id in account_ids:
            account = self.account_loader(account_id) or {}
            if account.get("cookie"):
                cookie_ok += 1
            if account.get("cookie") and self._has_current_processing_item(account, str(request.task_id or BON8_TASK_ID), str(request.node_id or BON8_NODE_ID)):
                current_item_ok += 1
            else:
                missing_current_item.append(str(account.get("name") or account_id))

        checks.append(
            TaskAutoRunPreflightCheck(
                key="account_cookie",
                title="账号 Cookie",
                status="passed" if cookie_ok == len(account_ids) else "blocked",
                detail=f"可用 Cookie 账号 {cookie_ok}/{len(account_ids)}。",
                next_step="" if cookie_ok == len(account_ids) else "先同步或重登缺 Cookie 的账号。",
            )
        )
        checks.append(
            TaskAutoRunPreflightCheck(
                key="current_processing_item",
                title="已领取当前题",
                status="passed" if current_item_ok == len(account_ids) else "blocked",
                detail=(
                    f"所选账号都已存在 bon8 当前处理中题，可直接进入自动做题。"
                    if current_item_ok == len(account_ids)
                    else "以下账号当前没有已领取的 bon8 处理中题：" + "、".join(missing_current_item[:4])
                ),
                next_step="" if current_item_ok == len(account_ids) else "先只选择已有 bon8 已领取处理中题的账号；pending-only 账号本轮不进入通用自动做题。",
            )
        )
        blocked = [item for item in checks if item.required and item.status != "passed"]
        can_start = not blocked
        return TaskAutoRunPreflightResponse(
            generated_at=utc_now(),
            task_id=str(request.task_id or BON8_TASK_ID),
            node_id=str(request.node_id or BON8_NODE_ID),
            adapter_key=self.adapter_key,
            status="ready" if can_start else "blocked",
            can_start=can_start,
            runnable_account_count=len(account_ids) if can_start else 0,
            checks=checks,
            message="bon8 自检通过；已按 AI 标注能力工作台主流程接入统一任务控制台，当前只会处理已领取的处理中题。" if can_start else "bon8 自检发现阻塞项，未启动自动做题。",
            next_step="可以启动 bon8 自动做题。" if can_start else "先确保所选账号都有 bon8 已领取的处理中题。",
        )

    def get(self, adapter_run_id: str) -> TaskAutoRunAdapterSnapshot:
        return _snapshot_from_bon8(get_bon8_production_run(adapter_run_id, state_dir=self.bon8_state_dir))

    def stop(self, adapter_run_id: str) -> TaskAutoRunAdapterSnapshot:
        return _snapshot_from_bon8(stop_bon8_production_run(adapter_run_id, state_dir=self.bon8_state_dir))

    def _has_current_processing_item(self, account: dict[str, Any], task_id: str, node_id: str) -> bool:
        if not account or not account.get("cookie"):
            return False
        remote = self.transport or _post_aidp
        body = {
            "TaskID": str(task_id),
            "NodeID": int(node_id or BON8_NODE_ID),
            "ItemCategoryType": 0,
            "Filter": {},
            "PageRequest": {"PageNo": 0, "PageSize": 1},
        }
        try:
            result = remote(account, "agw", "/dispatcher/search_item/category", body)
        except Exception:
            return False
        payload = result.get("body") if isinstance(result, dict) else {}
        data = payload.get("Data") if isinstance(payload, dict) else []
        return any(isinstance(item, dict) and str(item.get("ItemID") or "") for item in (data or []))


class TaskAutoRunResearchChartAdapter:
    adapter_key = "research_chart"
    supported_task_ids = RESEARCH_CHART_TASK_IDS

    def __init__(
        self,
        *,
        ability_store_path: Optional[Path] = None,
        review_root: Optional[Path] = None,
        state_dir: Optional[Path] = None,
        evidence_root: Optional[Path] = None,
        ability_runner=None,
        account_loader=load_runtime_account,
        transport=None,
    ) -> None:
        self.ability_store_path = ability_store_path
        self.review_root = review_root
        self.state_dir = state_dir
        self.evidence_root = evidence_root
        self.ability_runner = ability_runner or run_task_ability_real_no_submit
        self.account_loader = account_loader
        self.transport = transport or _post_aidp
        self._formal_submit_lock = threading.Lock()

    def start(self, _db: Session, request: TaskAutoRunStartRequest) -> TaskAutoRunAdapterSnapshot:
        blocked_accounts = self._blocked_auto_receive_accounts(request.account_user_ids, str(request.task_id))
        if blocked_accounts:
            raise ValueError("以下账号当前不能进入自动循环：" + _summarize_blocked_accounts(blocked_accounts))
        draft = self._enabled_draft(str(request.task_id))
        if not draft:
            accounts = [
                TaskAutoRunAccountState(
                    account_user_id=account_id,
                    status="ability_not_enabled",
                    current_stage="题型能力未发布",
                    healthy=False,
                    last_error="科研图题型能力尚未在 AI 标注能力工作台完成真实题审核并发布。",
                )
                for account_id in _normalize_account_ids(request.account_user_ids)
            ]
            snapshot = TaskAutoRunAdapterSnapshot(
                adapter_key=self.adapter_key,
                adapter_run_id=f"research-chart-blocked-{uuid4().hex[:12]}",
                task_id=str(request.task_id),
                node_id=str(request.node_id or "1"),
                status="blocked",
                stop_requested=False,
                accounts=accounts,
                last_error="科研图题型能力未发布，不能启动自动做题。",
                next_step="先到 AI 标注能力工作台完成草稿、真实题不提交审核并发布能力。",
                message="科研图自动做题被能力发布闸门阻止。",
                raw_adapter_run={"adapter": self.adapter_key, "executor_status": "ability_not_enabled"},
            )
            self._write_snapshot(snapshot)
            return snapshot

        run_context = build_task_ability_run_context(draft)
        accounts = [
            TaskAutoRunAccountState(
                account_user_id=account_id,
                status="running_auto",
                current_stage="等待科研图自动做题 tick",
                healthy=True,
            )
            for account_id in _normalize_account_ids(request.account_user_ids)
        ]
        snapshot = TaskAutoRunAdapterSnapshot(
            adapter_key=self.adapter_key,
            adapter_run_id=f"research-chart-{uuid4().hex[:12]}",
            task_id=str(request.task_id),
            node_id=str(request.node_id or "1"),
            status="running_auto",
            stop_requested=False,
            accounts=accounts,
            last_error="",
            next_step="后台 tick 将逐账号读取真实当前题、AI 看图评分并暂存，正式提交仍由下一段提交闸门处理。",
            message="科研图自动做题 run 已启动，等待后台 tick 执行真实题暂存。",
            raw_adapter_run={
                "adapter": self.adapter_key,
                "executor_status": "ready",
                **run_context,
                "submits_remote": False,
                "run_config": request.run_config,
            },
        )
        self._write_snapshot(snapshot)
        return snapshot

    def preflight(self, request: TaskAutoRunStartRequest) -> TaskAutoRunPreflightResponse:
        account_ids = _normalize_account_ids(request.account_user_ids)
        checks: list[TaskAutoRunPreflightCheck] = []
        if not account_ids:
            checks.append(
                TaskAutoRunPreflightCheck(
                    key="selected_accounts",
                    title="可执行账号",
                    status="blocked",
                    detail="当前无可执行题账号，不能启动自动提交。",
                    next_step="刷新生产数据，等待该任务出现待处理、处理中或返修题后再启动。",
                )
            )
            return TaskAutoRunPreflightResponse(
                generated_at=utc_now(),
                task_id=str(request.task_id),
                node_id=str(request.node_id or "1"),
                adapter_key=self.adapter_key,
                status="blocked",
                can_start=False,
                runnable_account_count=0,
                checks=checks,
                message="当前无可执行题，已阻止启动自动做题。",
                next_step="等待有题后再做单账号真实冒烟。",
            )

        draft = self._enabled_draft(str(request.task_id))
        checks.append(
            TaskAutoRunPreflightCheck(
                key="ability_published",
                title="题型能力发布",
                status="passed" if draft else "blocked",
                detail="题型能力已发布，可读取最新版本。" if draft else "题型能力未发布或真实题审核未通过。",
                next_step="" if draft else "先到 AI 标注能力工作台完成真实题不提交审核并发布能力。",
            )
        )
        cookie_ok_count = 0
        for account_id in account_ids:
            account = self._load_account_context(account_id)
            if account and account.get("cookie"):
                cookie_ok_count += 1
        checks.append(
            TaskAutoRunPreflightCheck(
                key="account_cookie",
                title="账号 Cookie",
                status="passed" if cookie_ok_count == len(account_ids) else "blocked",
                detail=f"可用账号 {cookie_ok_count}/{len(account_ids)}。",
                next_step="" if cookie_ok_count == len(account_ids) else "先同步或重新登录缺 Cookie 的账号。",
            )
        )
        blocked_accounts = self._blocked_auto_receive_accounts(account_ids, str(request.task_id))
        checks.append(
            TaskAutoRunPreflightCheck(
                key="auto_receive_ready",
                title="自动领题资格",
                status="blocked" if blocked_accounts else "passed",
                detail=(
                    "已确认所选账号都满足 ReceiveEnable=true 且 operationUrl 指向任务页。"
                    if not blocked_accounts
                    else "以下账号当前不能进入自动循环：" + _summarize_blocked_accounts(blocked_accounts)
                ),
                next_step="" if not blocked_accounts else "刷新生产数据，只选择 ReceiveEnable=true 且任务页链接正确的账号后再启动。",
            )
        )
        evidence_status = "passed"
        evidence_detail = ""
        try:
            evidence_root = _evidence_root(self.evidence_root)
            evidence_status, evidence_detail = _check_evidence_storage_preflight(evidence_root)
        except Exception as exc:  # noqa: BLE001 - preflight should report filesystem problems.
            evidence_status = "blocked"
            evidence_detail = f"证据目录不可写：{exc}"
        checks.append(
            TaskAutoRunPreflightCheck(
                key="evidence_storage",
                title="证据目录",
                status=evidence_status,
                detail=evidence_detail,
                next_step="" if evidence_status == "passed" else "修复数据目录权限后再启动自动做题。",
            )
        )
        blocked = [item for item in checks if item.required and item.status != "passed"]
        can_start = not blocked
        return TaskAutoRunPreflightResponse(
            generated_at=utc_now(),
            task_id=str(request.task_id),
            node_id=str(request.node_id or "1"),
            adapter_key=self.adapter_key,
            status="ready" if can_start else "blocked",
            can_start=can_start,
            runnable_account_count=len(account_ids) if can_start else 0,
            checks=checks,
            message="自检通过；该检查不会提交、暂存或领取题目。" if can_start else "自检发现阻塞项，未启动自动做题。",
            next_step="可以启动自动做题；有题后建议先单账号一轮冒烟。" if can_start else "先处理阻塞项。",
        )

    def get(self, adapter_run_id: str) -> TaskAutoRunAdapterSnapshot:
        return self._read_snapshot(adapter_run_id)

    def stop(self, adapter_run_id: str) -> TaskAutoRunAdapterSnapshot:
        snapshot = self.get(adapter_run_id)
        snapshot.status = "stopped"
        snapshot.stop_requested = True
        snapshot.message = "科研图自动做题 run 已立即停止。"
        snapshot.next_step = "如需继续，刷新任务后重新启动自动做题。"
        snapshot.accounts = [
            _copy_model(account, update={"status": "stopped", "current_stage": "已停止", "healthy": True})
            for account in snapshot.accounts
        ]
        self._write_snapshot(snapshot)
        return snapshot

    def tick(self, adapter_run_id: str) -> TaskAutoRunAdapterSnapshot:
        snapshot = self.get(adapter_run_id)
        if snapshot.stop_requested or snapshot.status == "stopped":
            return snapshot
        snapshot = self._validate_bound_published_ability(snapshot)
        if snapshot.status == "blocked":
            self._write_snapshot(snapshot)
            return snapshot
        draft_id = str(snapshot.raw_adapter_run.get("draft_id") or "")
        if not draft_id:
            snapshot.status = "blocked"
            snapshot.last_error = "科研图 run 缺少题型能力草稿 ID。"
            snapshot.next_step = "重新从任务操作台启动该任务。"
            self._write_snapshot(snapshot)
            return snapshot

        next_accounts: list[TaskAutoRunAccountState] = []
        account_evidence = snapshot.raw_adapter_run.get("account_evidence") if isinstance(snapshot.raw_adapter_run.get("account_evidence"), dict) else {}
        any_formal_submit = False
        runnable_accounts: list[TaskAutoRunAccountState] = []
        results_by_account: dict[str, dict[str, Any]] = {}
        for account in snapshot.accounts:
            if account.status in {"stopped", "isolated_failed", "ability_not_enabled"}:
                continue
            eligibility = self._auto_receive_eligibility(account.account_user_id, snapshot.task_id)
            if not eligibility["auto_receive_ready"]:
                continue
            runnable_accounts.append(account)

        if runnable_accounts:
            with ThreadPoolExecutor(max_workers=min(RESEARCH_CHART_MAX_PARALLEL_ACCOUNTS, len(runnable_accounts))) as executor:
                future_map = {
                    executor.submit(self._run_account_tick, draft_id, snapshot, account): account.account_user_id
                    for account in runnable_accounts
                }
                for future in as_completed(future_map):
                    account_user_id = future_map[future]
                    try:
                        results_by_account[account_user_id] = future.result()
                    except Exception as exc:  # noqa: BLE001 - per-account failure must not stop others.
                        results_by_account[account_user_id] = {"error": exc}

        for account in snapshot.accounts:
            if account.status in {"stopped", "isolated_failed", "ability_not_enabled"}:
                next_accounts.append(account)
                continue
            eligibility = self._auto_receive_eligibility(account.account_user_id, snapshot.task_id)
            if not eligibility["auto_receive_ready"]:
                blocker = str(eligibility["reason"] or "当前账号不能进入自动循环。")
                account_evidence[account.account_user_id] = {
                    "attempted": False,
                    "submits_remote": False,
                    "item_id": account.current_item_id,
                    "message": blocker,
                    "error": blocker,
                }
                next_accounts.append(
                    _copy_model(
                        account,
                        update={
                            "status": "isolated_failed",
                            "current_stage": "自动领题资格阻塞",
                            "healthy": False,
                            "last_error": blocker,
                        },
                    )
                )
                continue
            result = results_by_account.get(account.account_user_id) or {}
            exc = result.get("error")
            if exc is not None:
                next_accounts.append(
                    _copy_model(
                        account,
                        update={
                            "status": "isolated_failed",
                            "current_stage": "科研图 tick 失败",
                            "healthy": False,
                            "last_error": str(exc),
                        },
                    )
                )
                continue
            artifact = result.get("artifact") if isinstance(result.get("artifact"), dict) else {}
            submit_evidence = result.get("submit_evidence") if isinstance(result.get("submit_evidence"), dict) else {}
            item_id = str(result.get("item_id") or "")
            self._record_account_evidence(snapshot, account, artifact, submit_evidence, item_id)
            account_evidence[account.account_user_id] = submit_evidence
            submits_remote = bool(submit_evidence.get("success"))
            any_formal_submit = any_formal_submit or submits_remote
            if submit_evidence.get("attempted") and not submits_remote:
                next_accounts.append(
                    _copy_model(
                        account,
                        update={
                            "status": "isolated_failed",
                            "current_item_id": item_id,
                            "current_stage": "正式提交闸门失败",
                            "healthy": False,
                            "last_error": str(submit_evidence.get("error") or "科研图正式提交或回读失败。"),
                        },
                    )
                )
                continue
            status = "submitted" if submits_remote else "temp_saved_waiting_submit"
            next_item_id = str(submit_evidence.get("next_item_id") or "")
            current_stage = "正式提交并自动领取下一题" if submits_remote and next_item_id else "正式提交并回读成功" if submits_remote else "已暂存，等待正式提交闸门"
            next_accounts.append(
                _copy_model(
                    account,
                    update={
                        "status": status,
                        "current_item_id": next_item_id or item_id,
                        "current_stage": current_stage,
                        "healthy": True,
                        "last_error": "",
                    },
                )
            )

        snapshot.accounts = next_accounts
        failed_count = sum(1 for account in next_accounts if not account.healthy)
        if failed_count == len(next_accounts) and next_accounts:
            snapshot.status = "blocked"
            snapshot.last_error = "科研图所有账号本轮 tick 均失败。"
            snapshot.next_step = "检查账号 Cookie、当前题、AI 视觉模型和暂存接口后重试。"
            snapshot.message = "科研图自动做题 tick 未成功。"
        else:
            if _ability_run_mode(snapshot) == "trial":
                snapshot.status = "completed"
                snapshot.next_step = "试运行已完成；请检查账号结果和回读证据后，再人工决定是否进入生产运行。"
                snapshot.message = "科研图试运行 tick 已完成。" if any_formal_submit else "科研图试运行已完成真实题暂存，未正式提交。"
            else:
                snapshot.status = "running_auto"
                snapshot.next_step = "继续观察账号运行状态；正式提交成功的账号会继续下一轮，暂存账号等待提交 payload 证据。"
                snapshot.message = "科研图 tick 已完成正式提交和回读。" if any_formal_submit else "科研图 tick 已完成真实题暂存，未正式提交。"
            snapshot.last_error = ""
        snapshot.raw_adapter_run = {
            **snapshot.raw_adapter_run,
            "last_tick_at": utc_now().isoformat(),
            "submits_remote": any_formal_submit,
            "account_evidence": account_evidence,
        }
        self._write_snapshot(snapshot)
        return snapshot

    def _run_account_tick(self, draft_id: str, snapshot: TaskAutoRunAdapterSnapshot, account: TaskAutoRunAccountState) -> dict[str, Any]:
        artifact = self.ability_runner(
            draft_id,
            store_path=self.ability_store_path,
            review_root=self.review_root,
            target_account_user_id=account.account_user_id,
            use_system_ai_for_vision=False,
            allow_temp_save=True,
            allow_claim_receive=True,
        )
        context = artifact.get("question_context") if isinstance(artifact.get("question_context"), dict) else {}
        item_id = str(context.get("item_id") or "")
        submit_evidence = self._formal_submit_if_ready(snapshot, account, artifact, item_id)
        return {
            "artifact": artifact,
            "submit_evidence": submit_evidence,
            "item_id": item_id,
        }

    def _formal_submit_if_ready(
        self,
        snapshot: TaskAutoRunAdapterSnapshot,
        account_state: TaskAutoRunAccountState,
        artifact: dict[str, Any],
        item_id: str,
    ) -> dict[str, Any]:
        if not _artifact_temp_save_verified(artifact):
            return {
                "attempted": True,
                "success": False,
                "submits_remote": False,
                "item_id": item_id,
                "error": "暂存未验证成功，已阻止继续运行。",
            }
        with self._formal_submit_lock:
            guard = self._formal_submit_guard(snapshot, account_state, item_id)
            if guard is not None:
                return guard
            payload = artifact.get("temp_draft_payload") if isinstance(artifact.get("temp_draft_payload"), dict) else {}
            answers = payload.get("AuditAnswers") if isinstance(payload.get("AuditAnswers"), list) else []
            if not answers:
                return {
                    "attempted": True,
                    "success": False,
                    "submits_remote": False,
                    "item_id": item_id,
                    "error": "暂存 payload 缺少 AuditAnswers，已阻止正式提交。",
                }
            account = self._load_account_context(account_state.account_user_id)
            if not account or not account.get("cookie"):
                return {
                    "attempted": True,
                    "success": False,
                    "submits_remote": False,
                    "item_id": item_id,
                    "error": "账号 Cookie 不可用，不能正式提交。",
                }
            submit_request = {
                "TaskID": str(payload.get("TaskID") or snapshot.task_id),
                "NodeID": _node_id_value(payload.get("NodeID") or snapshot.node_id),
                "Status": 4,
                "Answers": answers,
            }
            verify_result = self.transport(
                account,
                "agw",
                "/dispatcher/verify/submit",
                {"SubmitItemRequest": submit_request, "Verifiers": ["ItemRepeatVerifier"]},
            )
            verify_ok = _remote_base_status_code(verify_result) == 0
            if not verify_ok:
                return {
                    "attempted": True,
                    "success": False,
                    "submits_remote": False,
                    "item_id": item_id,
                    "verify_result": _compact_remote_result(verify_result),
                    "error": "提交前校验失败，未调用 SubmitItem。",
                }
            receive_request = {"Filter": {"Type": 1, "TaskID": str(snapshot.task_id), "NodeID": _node_id_value(snapshot.node_id), "Count": 1, "StatusList": []}}
            submit_result = self.transport(
                account,
                "api",
                "/api/dispatch/SubmitItemAndReceive",
                {"SubmitItemRequest": submit_request, "ReceiveRequest": receive_request},
            )
            submit_ok = _submit_and_receive_submit_ok(submit_result)
            readback_ok = _submit_and_receive_receive_ok(submit_result)
            next_item_id = _submit_and_receive_next_item_id(submit_result)
            success = submit_ok and readback_ok
            submitted_at = utc_now().isoformat() if success else ""
            if success:
                self._record_formal_submit_success(snapshot, account_state.account_user_id, submitted_at)
            return {
                "attempted": True,
                "success": success,
                "submits_remote": success,
                "item_id": item_id,
                "verify_result": _compact_remote_result(verify_result),
                "submit_result": _compact_remote_result(submit_result),
                "readback_result": _compact_remote_result(submit_result),
                "readback_ok": readback_ok,
                "next_item_id": next_item_id,
                "submitted_at": submitted_at,
                "error": "" if success else "正式提交或回读失败。",
            }

    def _formal_submit_guard(self, snapshot: TaskAutoRunAdapterSnapshot, account_state: TaskAutoRunAccountState, item_id: str) -> Optional[dict[str, Any]]:
        run_mode = _ability_run_mode(snapshot)
        if run_mode != "production":
            return {
                "attempted": False,
                "success": False,
                "submits_remote": False,
                "item_id": item_id,
                "message": "当前不是 Step4 production 模式，仅允许暂存，不执行正式提交。",
            }
        gate = get_task_ability_run_gate(snapshot.task_id, store_path=self.ability_store_path)
        if not gate.get("can_start_production"):
            return {
                "attempted": True,
                "success": False,
                "submits_remote": False,
                "item_id": item_id,
                "error": str(gate.get("next_step") or "当前 Step4 生产门禁未放行，已阻止正式提交。"),
            }
        limit = _submit_limit_for_run(snapshot)
        if limit is not None:
            counts = snapshot.raw_adapter_run.get("submit_counts") if isinstance(snapshot.raw_adapter_run.get("submit_counts"), dict) else {}
            submitted = _num(counts.get(account_state.account_user_id))
            if submitted >= limit:
                return {
                    "attempted": False,
                    "success": False,
                    "submits_remote": False,
                    "item_id": item_id,
                    "limit_reached": True,
                    "message": f"账号已达到本次运行提交上限 {limit}，未继续正式提交。",
                }
        wait_seconds = _rate_limit_wait_seconds(snapshot)
        if wait_seconds > 0:
            return {
                "attempted": False,
                "success": False,
                "submits_remote": False,
                "item_id": item_id,
                "rate_limited": True,
                "message": f"提交速率限制中，约 {wait_seconds} 秒后再尝试。",
            }
        return None

    def _record_formal_submit_success(self, snapshot: TaskAutoRunAdapterSnapshot, account_user_id: str, submitted_at: str) -> None:
        counts = snapshot.raw_adapter_run.get("submit_counts") if isinstance(snapshot.raw_adapter_run.get("submit_counts"), dict) else {}
        next_counts = dict(counts)
        next_counts[account_user_id] = _num(next_counts.get(account_user_id)) + 1
        snapshot.raw_adapter_run["submit_counts"] = next_counts
        snapshot.raw_adapter_run["last_formal_submit_at"] = submitted_at
        snapshot.raw_adapter_run["last_formal_submit_epoch"] = utc_now().timestamp()

    def _record_account_evidence(
        self,
        snapshot: TaskAutoRunAdapterSnapshot,
        account_state: TaskAutoRunAccountState,
        artifact: dict[str, Any],
        submit_evidence: dict[str, Any],
        item_id: str,
    ) -> None:
        if not submit_evidence.get("attempted"):
            return
        now = utc_now()
        day = now.date().isoformat()
        status = "submitted" if submit_evidence.get("success") else "failed"
        root = _evidence_root(self.evidence_root)
        detail = {
            "schema_version": 1,
            "retention_days": 7,
            "created_at": now.isoformat(),
            "run_id": snapshot.adapter_run_id,
            "adapter_key": self.adapter_key,
            "task_id": snapshot.task_id,
            "node_id": snapshot.node_id,
            "account_user_id": account_state.account_user_id,
            "account_name": account_state.account_name,
            "item_id": item_id,
            "status": status,
            "writes_remote": bool(artifact.get("writes_remote")),
            "submits_remote": bool(submit_evidence.get("submits_remote")),
            "saved_to_task_ui": bool(artifact.get("saved_to_task_ui")),
            "source_mode": str((artifact.get("question_context") if isinstance(artifact.get("question_context"), dict) else {}).get("source_mode") or ""),
            "evidence": submit_evidence,
        }
        detail_dir = root / "details" / day
        detail_dir.mkdir(parents=True, exist_ok=True)
        detail_path = detail_dir / f"{_safe_file_part(snapshot.adapter_run_id)}-{_safe_file_part(account_state.account_user_id)}-{_safe_file_part(item_id)}.json"
        _write_json_file(detail_path, detail)
        aggregate_path = root / "aggregates" / "daily" / f"{day}.json"
        aggregate = _load_json_file(aggregate_path)
        if not isinstance(aggregate, dict) or not isinstance(aggregate.get("items"), dict):
            aggregate = {"schema_version": 1, "date": day, "items": {}}
        key = f"{snapshot.task_id}:{account_state.account_user_id}"
        item = aggregate["items"].get(key) if isinstance(aggregate["items"].get(key), dict) else {}
        item = {
            "date": day,
            "task_id": snapshot.task_id,
            "account_user_id": account_state.account_user_id,
            "account_name": account_state.account_name,
            "total": int(item.get("total") or 0) + 1,
            "submitted": int(item.get("submitted") or 0) + (1 if status == "submitted" else 0),
            "failed": int(item.get("failed") or 0) + (1 if status != "submitted" else 0),
            "temp_saved": int(item.get("temp_saved") or 0) + (1 if artifact.get("saved_to_task_ui") else 0),
            "last_item_id": item_id,
            "last_status": status,
            "last_updated_at": now.isoformat(),
        }
        aggregate["items"][key] = item
        aggregate_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_file(aggregate_path, aggregate)

    def _validate_bound_published_ability(self, snapshot: TaskAutoRunAdapterSnapshot) -> TaskAutoRunAdapterSnapshot:
        draft = self._enabled_draft(snapshot.task_id)
        if not draft:
            snapshot.status = "blocked"
            snapshot.last_error = "科研图题型能力已不可用，运行中自动做题已暂停。"
            snapshot.next_step = "回到 AI 标注能力工作台重新发布能力后，再从任务操作台重启自动做题。"
            snapshot.message = "科研图题型能力未发布或已撤回，已阻止继续使用旧版本执行。"
            snapshot.raw_adapter_run = {
                **snapshot.raw_adapter_run,
                "executor_status": "ability_not_enabled",
                "submits_remote": False,
            }
            snapshot.accounts = [
                account
                if account.status == "stopped"
                else _copy_model(
                    account,
                    update={
                        "status": "ability_not_enabled",
                        "current_stage": "题型能力未发布",
                        "healthy": False,
                        "last_error": "科研图题型能力已不可用。",
                    },
                )
                for account in snapshot.accounts
            ]
            return snapshot
        current_context = build_task_ability_run_context(draft)
        stored_context = {
            "draft_id": str(snapshot.raw_adapter_run.get("draft_id") or ""),
            "ability_version": str(snapshot.raw_adapter_run.get("ability_version") or ""),
            "prompt_fingerprint": str(snapshot.raw_adapter_run.get("prompt_fingerprint") or ""),
        }
        missing_keys = [key for key, value in stored_context.items() if not value]
        mismatch_keys = [key for key, value in stored_context.items() if value and value != current_context.get(key)]
        if missing_keys or mismatch_keys:
            reason = "运行中的题型能力配置已变化，请重新执行 Step3/Step4 试运行后再继续。"
            next_raw = dict(snapshot.raw_adapter_run)
            next_raw["current_context"] = current_context
            next_raw["context_mismatch_keys"] = missing_keys + mismatch_keys
            next_raw["executor_status"] = "ability_context_stale"
            snapshot.raw_adapter_run = next_raw
            snapshot.status = "blocked"
            snapshot.last_error = reason
            snapshot.next_step = "停止当前 run，使用最新能力重新做真实题审核、试运行和生产授权。"
            snapshot.message = "科研图自动做题已阻断：能力上下文与启动时不一致。"
            snapshot.accounts = [
                account
                if account.status == "stopped"
                else _copy_model(
                    account,
                    update={
                        "status": "ability_context_stale",
                        "current_stage": "能力配置已变化",
                        "healthy": False,
                        "last_error": reason,
                    },
                )
                for account in snapshot.accounts
            ]
        return snapshot

    def _enabled_draft(self, task_id: str) -> Optional[dict[str, Any]]:
        path = self.ability_store_path or _default_ability_store_path()
        data = _load_json_file(path)
        items = data.get("items", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        item = next((entry for entry in items if isinstance(entry, dict) and str(entry.get("task_id") or "") == str(task_id)), None)
        if not isinstance(item, dict):
            return None
        review = item.get("real_no_submit_review") if isinstance(item.get("real_no_submit_review"), dict) else {}
        migrated_clone = bool(review.get("migrated_from_task_id")) and bool(review.get("migrated_from_draft_id"))
        if bool(item.get("capability_enabled")) and (
            (review.get("review_status") == "人工已通过" and review.get("saved_to_task_ui"))
            or migrated_clone
        ):
            return item
        return None

    def _state_path(self, adapter_run_id: str) -> Path:
        safe_run_id = "".join(ch for ch in str(adapter_run_id) if ch.isalnum() or ch in {"-", "_"})
        if not safe_run_id:
            raise ValueError("adapter_run_id 不能为空。")
        return _research_state_dir(self.state_dir) / f"{safe_run_id}.json"

    def _write_snapshot(self, snapshot: TaskAutoRunAdapterSnapshot) -> None:
        path = self._state_path(snapshot.adapter_run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "adapter_key": snapshot.adapter_key,
            "adapter_run_id": snapshot.adapter_run_id,
            "task_id": snapshot.task_id,
            "node_id": snapshot.node_id,
            "status": snapshot.status,
            "stop_requested": snapshot.stop_requested,
            "accounts": [_model_to_dict(account) for account in snapshot.accounts],
            "last_error": snapshot.last_error,
            "next_step": snapshot.next_step,
            "message": snapshot.message,
            "raw_adapter_run": snapshot.raw_adapter_run,
        }
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _read_snapshot(self, adapter_run_id: str) -> TaskAutoRunAdapterSnapshot:
        path = self._state_path(adapter_run_id)
        if not path.exists():
            raise ValueError(f"科研图 adapter run 不存在：{adapter_run_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return TaskAutoRunAdapterSnapshot(
            adapter_key=str(payload.get("adapter_key") or self.adapter_key),
            adapter_run_id=str(payload.get("adapter_run_id") or adapter_run_id),
            task_id=str(payload.get("task_id") or RESEARCH_CHART_TASK_ID),
            node_id=str(payload.get("node_id") or "1"),
            status=str(payload.get("status") or "blocked"),
            stop_requested=bool(payload.get("stop_requested")),
            accounts=[_parse_account_state(item) for item in payload.get("accounts", []) if isinstance(item, dict)],
            last_error=str(payload.get("last_error") or ""),
            next_step=str(payload.get("next_step") or ""),
            message=str(payload.get("message") or ""),
            raw_adapter_run=payload.get("raw_adapter_run") if isinstance(payload.get("raw_adapter_run"), dict) else {},
        )

    def _load_account_context(self, account_user_id: str) -> dict[str, Any]:
        runtime_account = self.account_loader(account_user_id)
        runtime = runtime_account if isinstance(runtime_account, dict) else {}
        state_account = _find_production_state_account(account_user_id)
        merged = dict(state_account)
        merged.update(runtime)
        return merged

    def _auto_receive_eligibility(self, account_user_id: str, task_id: str) -> dict[str, Any]:
        account = self._load_account_context(account_user_id)
        task = _find_account_task(account, task_id)
        processing = _task_processing_count(task)
        repair = _task_repair_count(task)
        pending = _task_pending_count(task)
        has_current_item = processing > 0 or repair > 0
        return {
            "account_user_id": account_user_id,
            "account_name": str(account.get("name") or account.get("displayName") or account_user_id),
            "auto_receive_ready": bool(task and (has_current_item or pending > 0)),
            "reason": _auto_receive_block_reason(task, has_current_item, pending),
        }

    def _blocked_auto_receive_accounts(self, account_user_ids: Sequence[str], task_id: str) -> list[dict[str, Any]]:
        return [item for item in (self._auto_receive_eligibility(account_id, task_id) for account_id in account_user_ids) if not item["auto_receive_ready"]]

    def extend_accounts(self, adapter_run_id: str, account_user_ids: Sequence[str]) -> TaskAutoRunAdapterSnapshot:
        snapshot = self.get(adapter_run_id)
        existing_ids = {account.account_user_id for account in snapshot.accounts}
        for account_id in _normalize_account_ids(list(account_user_ids)):
            if account_id in existing_ids:
                continue
            eligibility = self._auto_receive_eligibility(account_id, snapshot.task_id)
            snapshot.accounts.append(
                TaskAutoRunAccountState(
                    account_user_id=account_id,
                    account_name=str(eligibility.get("account_name") or account_id),
                    status="running_auto" if eligibility["auto_receive_ready"] else "isolated_failed",
                    current_stage="等待科研图自动做题 tick" if eligibility["auto_receive_ready"] else "自动领题资格阻塞",
                    healthy=bool(eligibility["auto_receive_ready"]),
                    last_error="" if eligibility["auto_receive_ready"] else str(eligibility.get("reason") or ""),
                )
            )
        self._write_snapshot(snapshot)
        return snapshot


class TaskAutoRun3DRubricAdapter:
    adapter_key = "3d_rubric"
    supported_task_ids = {AIDP_3D_RUBRIC_TASK_ID}

    def __init__(
        self,
        *,
        state_dir: Optional[Path] = None,
        evidence_root: Optional[Path] = None,
        account_loader=load_runtime_account,
        answer_service: Optional[Aidp3DHttpAnswerService] = None,
        max_parallel_accounts: int = AIDP_3D_RUBRIC_MAX_PARALLEL_ACCOUNTS,
    ) -> None:
        self.state_dir = state_dir
        self.evidence_root = evidence_root
        self.account_loader = account_loader
        self.answer_service = answer_service or Aidp3DHttpAnswerService()
        self.max_parallel_accounts = max(1, int(max_parallel_accounts or AIDP_3D_RUBRIC_MAX_PARALLEL_ACCOUNTS))
        self._formal_submit_lock = threading.Lock()

    def start(self, _db: Session, request: TaskAutoRunStartRequest) -> TaskAutoRunAdapterSnapshot:
        account_ids = _normalize_account_ids(request.account_user_ids)
        accounts = []
        for account_id in account_ids:
            runtime = self._load_account_context(account_id)
            accounts.append(
                TaskAutoRunAccountState(
                    account_user_id=account_id,
                    account_name=str(runtime.get("name") or runtime.get("displayName") or runtime.get("customName") or account_id),
                    status="running_auto" if runtime.get("cookie") else "isolated_failed",
                    current_stage="等待 3D HTTP 自动做题 tick" if runtime.get("cookie") else "账号 Cookie 不可用",
                    healthy=bool(runtime.get("cookie")),
                    last_error="" if runtime.get("cookie") else "账号 Cookie 不可用，不能执行 3D HTTP 自动做题。",
                )
            )
        snapshot = TaskAutoRunAdapterSnapshot(
            adapter_key=self.adapter_key,
            adapter_run_id=f"3d-rubric-{uuid4().hex[:12]}",
            task_id=str(request.task_id or AIDP_3D_RUBRIC_TASK_ID),
            node_id=str(request.node_id or AIDP_3D_RUBRIC_NODE_ID),
            status="running_auto",
            stop_requested=False,
            accounts=accounts,
            last_error="",
            next_step="后台循环将按账号并行、账号内串行执行 3D HTTP 提交；停止按钮会阻止下一轮 tick。",
            message="3D Rubric HTTP 自动做题 run 已启动，等待后台 tick 执行。",
            raw_adapter_run={
                "adapter": self.adapter_key,
                "executor_status": "ready",
                "ability_source": "ai_annotation_workbench_3d",
                "template_id": AIDP_3D_RUBRIC_TEMPLATE_ID,
                "submits_remote": False,
                "run_config": request.run_config,
                "account_evidence": {},
                "submit_counts": {},
            },
        )
        self._write_snapshot(snapshot)
        return snapshot

    def preflight(self, request: TaskAutoRunStartRequest) -> TaskAutoRunPreflightResponse:
        account_ids = _normalize_account_ids(request.account_user_ids)
        checks: list[TaskAutoRunPreflightCheck] = [
            TaskAutoRunPreflightCheck(
                key="adapter_ready",
                title="3D 执行器",
                status="passed",
                detail="3D Rubric 已接入 AI 标注能力工作台后的平台 HTTP 执行器，不依赖旧题型能力库。",
            )
        ]
        if not account_ids:
            checks.append(
                TaskAutoRunPreflightCheck(
                    key="selected_accounts",
                    title="执行账号",
                    status="blocked",
                    detail="当前未选择任何账号。",
                    next_step="先选择有 3D 任务权限且 Cookie 正常的账号。",
                )
            )
        else:
            checks.append(
                TaskAutoRunPreflightCheck(
                    key="selected_accounts",
                    title="执行账号",
                    status="passed",
                    detail=f"已选择 {len(account_ids)} 个账号。",
                )
            )
        cookie_ok = 0
        for account_id in account_ids:
            if self._load_account_context(account_id).get("cookie"):
                cookie_ok += 1
        checks.append(
            TaskAutoRunPreflightCheck(
                key="account_cookie",
                title="账号 Cookie",
                status="passed" if account_ids and cookie_ok == len(account_ids) else "blocked",
                detail=f"可用 Cookie 账号 {cookie_ok}/{len(account_ids)}。",
                next_step="" if account_ids and cookie_ok == len(account_ids) else "先同步或重新登录缺 Cookie 的账号。",
            )
        )
        evidence_status = "passed"
        evidence_detail = ""
        try:
            _evidence_root(self.evidence_root).mkdir(parents=True, exist_ok=True)
            self._state_root().mkdir(parents=True, exist_ok=True)
            evidence_detail = f"证据目录可用：{_evidence_root(self.evidence_root)}"
        except Exception as exc:  # noqa: BLE001
            evidence_status = "blocked"
            evidence_detail = f"证据或状态目录不可写：{exc}"
        checks.append(
            TaskAutoRunPreflightCheck(
                key="evidence_storage",
                title="状态与证据目录",
                status=evidence_status,
                detail=evidence_detail,
                next_step="" if evidence_status == "passed" else "修复数据目录权限后再启动。",
            )
        )
        blocked = [item for item in checks if item.required and item.status != "passed"]
        can_start = not blocked
        return TaskAutoRunPreflightResponse(
            generated_at=utc_now(),
            task_id=str(request.task_id or AIDP_3D_RUBRIC_TASK_ID),
            node_id=str(request.node_id or AIDP_3D_RUBRIC_NODE_ID),
            adapter_key=self.adapter_key,
            status="ready" if can_start else "blocked",
            can_start=can_start,
            runnable_account_count=len(account_ids) if can_start else 0,
            checks=checks,
            message="3D 自检通过；自检不提交、不暂存。" if can_start else "3D 自检发现阻塞项，未启动自动做题。",
            next_step="可以启动 3D HTTP 自动做题；建议先观察首轮证据。" if can_start else "先处理阻塞项。",
        )

    def get(self, adapter_run_id: str) -> TaskAutoRunAdapterSnapshot:
        return self._read_snapshot(adapter_run_id)

    def stop(self, adapter_run_id: str) -> TaskAutoRunAdapterSnapshot:
        snapshot = self.get(adapter_run_id)
        snapshot.status = "stopped"
        snapshot.stop_requested = True
        snapshot.message = "3D HTTP 自动做题 run 已停止。"
        snapshot.next_step = "如需继续，刷新任务后重新启动。"
        snapshot.accounts = [
            _copy_model(account, update={"status": "stopped", "current_stage": "已停止", "healthy": True})
            for account in snapshot.accounts
        ]
        self._write_snapshot(snapshot)
        return snapshot

    def tick(self, adapter_run_id: str, db: Optional[Session] = None) -> TaskAutoRunAdapterSnapshot:
        snapshot = self.get(adapter_run_id)
        if snapshot.stop_requested or snapshot.status == "stopped":
            return snapshot
        account_evidence = snapshot.raw_adapter_run.get("account_evidence") if isinstance(snapshot.raw_adapter_run.get("account_evidence"), dict) else {}
        runnable_accounts = [
            account
            for account in snapshot.accounts
            if account.status not in {"stopped", "isolated_failed", "ability_not_enabled"}
        ]
        results_by_account: dict[str, dict[str, Any]] = {}
        if runnable_accounts:
            with ThreadPoolExecutor(max_workers=min(self.max_parallel_accounts, len(runnable_accounts))) as executor:
                future_map = {
                    executor.submit(self._run_account_tick, snapshot, account): account.account_user_id
                    for account in runnable_accounts
                }
                for future in as_completed(future_map):
                    account_user_id = future_map[future]
                    try:
                        results_by_account[account_user_id] = future.result()
                    except Exception as exc:  # noqa: BLE001 - isolate one account, keep the batch running.
                        results_by_account[account_user_id] = {"error": exc}

        next_accounts: list[TaskAutoRunAccountState] = []
        any_success = False
        any_formal_submit = False
        any_waiting = False
        any_limit_reached = False
        any_rate_limited = False
        for account in snapshot.accounts:
            if account.status in {"stopped", "isolated_failed", "ability_not_enabled"}:
                next_accounts.append(account)
                continue
            result = results_by_account.get(account.account_user_id) or {}
            exc = result.get("error")
            if exc is not None:
                error_code = exc.code if isinstance(exc, Aidp3DAnswerError) else "WORKER_EXCEPTION"
                error_detail = str(exc)
                evidence = getattr(exc, "evidence", None) if isinstance(exc, Aidp3DAnswerError) else None
                evidence = dict(evidence) if isinstance(evidence, dict) else {}
                evidence.update(
                    {
                        "attempted": bool(evidence.get("attempted")),
                        "success": False,
                        "readback_ok": bool(evidence.get("readback_ok")),
                        "error_code": error_code,
                        "error": error_detail,
                        "message": error_detail,
                    }
                )
                account_evidence[account.account_user_id] = evidence
                self._record_account_evidence(snapshot, account, evidence)
                self._record_worker_event(
                    db,
                    account,
                    severity="warning",
                    code=error_code,
                    message=f"3D 自动做题账号失败：{error_code}",
                    detail=error_detail,
                    stage=getattr(exc, "stage", "worker_runtime") or "worker_runtime",
                    step=str(evidence.get("worker_step") or ""),
                )
                next_accounts.append(
                    _copy_model(
                        account,
                        update={
                            "status": "isolated_failed",
                            "current_stage": "3D HTTP tick 失败",
                            "healthy": False,
                            "last_error": error_detail,
                        },
                    )
                )
                continue
            evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
            account_evidence[account.account_user_id] = evidence
            self._record_account_evidence(snapshot, account, evidence)
            if evidence.get("no_current_item"):
                any_waiting = True
                next_accounts.append(
                    _copy_model(
                        account,
                        update={
                            "status": "waiting_items",
                            "current_stage": "当前无 3D 题，等待下一轮",
                            "healthy": True,
                            "last_error": "",
                        },
                    )
                )
                continue
            if evidence.get("limit_reached"):
                any_limit_reached = True
                next_accounts.append(
                    _copy_model(
                        account,
                        update={
                            "status": "completed",
                            "current_item_id": str(evidence.get("item_id") or account.current_item_id),
                            "current_stage": "已达到本次运行提交上限",
                            "healthy": True,
                            "last_error": "",
                        },
                    )
                )
                continue
            if evidence.get("rate_limited"):
                any_rate_limited = True
                next_accounts.append(
                    _copy_model(
                        account,
                        update={
                            "status": "waiting_rate_limit",
                            "current_item_id": str(evidence.get("item_id") or account.current_item_id),
                            "current_stage": "提交速率限制等待中",
                            "healthy": True,
                            "last_error": "",
                        },
                    )
                )
                continue
            if evidence.get("success"):
                any_success = True
                any_formal_submit = any_formal_submit or bool(evidence.get("submits_remote"))
                next_accounts.append(
                    _copy_model(
                        account,
                        update={
                            "status": "submitted" if evidence.get("submits_remote") else "temp_saved_waiting_submit",
                            "current_item_id": str(evidence.get("next_item_id") or evidence.get("item_id") or ""),
                            "current_stage": "3D HTTP 提交并回读成功" if evidence.get("submits_remote") else "3D HTTP 试运行暂存成功",
                            "healthy": True,
                            "last_error": "",
                        },
                    )
                )
                continue
            error_message = str(evidence.get("error") or evidence.get("message") or "3D HTTP tick 未成功。")
            self._record_worker_event(
                db,
                account,
                severity="warning",
                code=str(evidence.get("error_code") or "UNKNOWN_ERROR"),
                message=f"3D 自动做题账号失败：{evidence.get('error_code') or 'UNKNOWN_ERROR'}",
                detail=error_message,
                stage="submit_readback",
                step="submit_answer",
            )
            next_accounts.append(
                _copy_model(
                    account,
                    update={
                        "status": "isolated_failed",
                        "current_item_id": str(evidence.get("item_id") or account.current_item_id),
                        "current_stage": "3D HTTP tick 失败",
                        "healthy": False,
                        "last_error": error_message,
                    },
                )
            )

        snapshot.accounts = next_accounts
        failed_count = sum(1 for account in next_accounts if not account.healthy)
        if next_accounts and failed_count == len(next_accounts):
            snapshot.status = "blocked"
            snapshot.last_error = "3D 所有账号本轮 tick 均失败。"
            snapshot.message = "3D HTTP 自动做题 tick 未成功。"
            snapshot.next_step = "检查账号 Cookie、当前题、qwen3-vl-plus、SubmitTempItemAnswer/SubmitItemAndReceive 回读证据。"
            self._record_worker_event(
                db,
                None,
                severity="error",
                code="WORKER_EXCEPTION",
                message="3D 自动做题整轮失败：所有账号均异常",
                detail=snapshot.last_error,
                stage="worker_runtime",
                step="log_summary",
            )
        else:
            run_mode = _ability_run_mode(snapshot)
            if run_mode == "trial":
                trial_completed = bool(next_accounts) and all(account.status == "temp_saved_waiting_submit" and account.healthy for account in next_accounts)
                snapshot.status = "completed" if trial_completed else "completed_no_item" if any_waiting else "blocked"
                snapshot.message = "3D 试运行已完成所有账号真实题暂存，未正式提交。" if trial_completed else "3D 试运行存在账号无当前题，未形成生产放行记录。" if any_waiting else "3D 试运行未成功。"
                snapshot.next_step = "请检查试运行证据后，再人工决定是否进入生产运行。" if trial_completed else "等待所有选中账号出现当前题后重新试运行。"
            elif next_accounts and all(account.status == "completed" for account in next_accounts):
                snapshot.status = "completed"
                snapshot.message = "3D 生产运行已达到本次账号提交上限。"
                snapshot.next_step = "如需继续，提高提交上限后重新启动生产运行。"
            else:
                snapshot.status = "running_auto"
                snapshot.message = (
                    "3D tick 已完成真实提交和回读。"
                    if any_formal_submit
                    else "3D tick 已达到提交上限，继续等待其他账号。"
                    if any_limit_reached
                    else "3D tick 受速率限制，等待下一轮。"
                    if any_rate_limited
                    else "3D tick 未提交新题，继续等待。"
                    if any_waiting
                    else "3D tick 已完成。"
                )
                snapshot.next_step = "后台循环会继续执行下一轮；需要停止时点击立即停止。"
            snapshot.last_error = ""
        snapshot.raw_adapter_run = {
            **snapshot.raw_adapter_run,
            "last_tick_at": utc_now().isoformat(),
            "submits_remote": any_formal_submit or any(bool(item.get("submits_remote")) for item in account_evidence.values() if isinstance(item, dict)),
            "account_evidence": account_evidence,
        }
        self._write_snapshot(snapshot)
        return snapshot

    def _run_account_tick(self, snapshot: TaskAutoRunAdapterSnapshot, account_state: TaskAutoRunAccountState) -> dict[str, Any]:
        account = self._load_account_context(account_state.account_user_id)
        if not account.get("cookie"):
            raise Aidp3DAnswerError("TASK_PAGE_AUTH_EXPIRED", "账号 Cookie 不可用。", stage="prepare_context", retryable=True)
        submit_remote = _ability_run_mode(snapshot) == "production"
        if submit_remote:
            with self._formal_submit_lock:
                guard = self._formal_submit_guard(snapshot, account_state)
                if guard is not None:
                    return {"evidence": guard}
                evidence = self.answer_service.submit_one(
                    account=account,
                    account_user_id=account_state.account_user_id,
                    task_id=snapshot.task_id,
                    node_id=snapshot.node_id,
                    run_id=snapshot.adapter_run_id,
                    submit_remote=True,
                )
                if evidence.get("success") and evidence.get("submits_remote"):
                    self._record_formal_submit_success(snapshot, account_state.account_user_id, str(evidence.get("submitted_at") or utc_now().isoformat()))
        else:
            evidence = self.answer_service.submit_one(
                account=account,
                account_user_id=account_state.account_user_id,
                task_id=snapshot.task_id,
                node_id=snapshot.node_id,
                run_id=snapshot.adapter_run_id,
                submit_remote=False,
            )
        return {"evidence": evidence}

    def _formal_submit_guard(self, snapshot: TaskAutoRunAdapterSnapshot, account_state: TaskAutoRunAccountState) -> Optional[dict[str, Any]]:
        gate = get_task_ability_run_gate(snapshot.task_id)
        if not gate.get("can_start_production"):
            return {
                "attempted": True,
                "success": False,
                "submits_remote": False,
                "item_id": account_state.current_item_id,
                "error_code": "UNKNOWN_ERROR",
                "error": str(gate.get("next_step") or "当前 Step4 生产门禁未放行，已阻止正式提交。"),
            }
        limit = _submit_limit_for_run(snapshot)
        if limit is not None:
            counts = snapshot.raw_adapter_run.get("submit_counts") if isinstance(snapshot.raw_adapter_run.get("submit_counts"), dict) else {}
            submitted = _num(counts.get(account_state.account_user_id))
            if submitted >= limit:
                return {
                    "attempted": False,
                    "success": False,
                    "submits_remote": False,
                    "item_id": account_state.current_item_id,
                    "limit_reached": True,
                    "message": f"账号已达到本次运行提交上限 {limit}，未继续正式提交。",
                }
        wait_seconds = _rate_limit_wait_seconds(snapshot)
        if wait_seconds > 0:
            return {
                "attempted": False,
                "success": False,
                "submits_remote": False,
                "item_id": account_state.current_item_id,
                "rate_limited": True,
                "message": f"提交速率限制中，约 {wait_seconds} 秒后再尝试。",
            }
        return None

    def _record_formal_submit_success(self, snapshot: TaskAutoRunAdapterSnapshot, account_user_id: str, submitted_at: str) -> None:
        counts = snapshot.raw_adapter_run.get("submit_counts") if isinstance(snapshot.raw_adapter_run.get("submit_counts"), dict) else {}
        next_counts = dict(counts)
        next_counts[account_user_id] = _num(next_counts.get(account_user_id)) + 1
        snapshot.raw_adapter_run["submit_counts"] = next_counts
        snapshot.raw_adapter_run["last_formal_submit_at"] = submitted_at
        snapshot.raw_adapter_run["last_formal_submit_epoch"] = utc_now().timestamp()

    def _record_account_evidence(self, snapshot: TaskAutoRunAdapterSnapshot, account_state: TaskAutoRunAccountState, evidence: dict[str, Any]) -> None:
        if not evidence:
            return
        now = utc_now()
        day = now.date().isoformat()
        status = "submitted" if evidence.get("success") and evidence.get("submits_remote") else "temp_saved" if evidence.get("success") else "waiting" if evidence.get("no_current_item") or evidence.get("rate_limited") or evidence.get("limit_reached") else "failed"
        root = _evidence_root(self.evidence_root)
        detail = {
            "schema_version": 1,
            "retention_days": 7,
            "created_at": now.isoformat(),
            "run_id": snapshot.adapter_run_id,
            "adapter_key": self.adapter_key,
            "task_id": snapshot.task_id,
            "node_id": snapshot.node_id,
            "account_user_id": account_state.account_user_id,
            "account_name": account_state.account_name,
            "item_id": str(evidence.get("item_id") or ""),
            "status": status,
            "writes_remote": bool(evidence.get("attempted")),
            "submits_remote": bool(evidence.get("submits_remote")),
            "saved_to_task_ui": bool(evidence.get("saved_to_task_ui") or evidence.get("success")),
            "source_mode": "3d_http_submit_item_and_receive" if evidence.get("submits_remote") else "3d_http_temp_save_only" if evidence.get("temp_save_only") else "3d_http_guard",
            "evidence": evidence,
        }
        detail_dir = root / "details" / day
        detail_dir.mkdir(parents=True, exist_ok=True)
        item_id = str(evidence.get("item_id") or "no-current-item")
        detail_path = detail_dir / f"{_safe_file_part(snapshot.adapter_run_id)}-{_safe_file_part(account_state.account_user_id)}-{_safe_file_part(item_id)}.json"
        _write_json_file(detail_path, detail)
        aggregate_path = root / "aggregates" / "daily" / f"{day}.json"
        aggregate = _load_json_file(aggregate_path)
        if not isinstance(aggregate, dict) or not isinstance(aggregate.get("items"), dict):
            aggregate = {"schema_version": 1, "date": day, "items": {}}
        key = f"{snapshot.task_id}:{account_state.account_user_id}"
        item = aggregate["items"].get(key) if isinstance(aggregate["items"].get(key), dict) else {}
        item = {
            "date": day,
            "task_id": snapshot.task_id,
            "account_user_id": account_state.account_user_id,
            "account_name": account_state.account_name,
            "total": int(item.get("total") or 0) + 1,
            "submitted": int(item.get("submitted") or 0) + (1 if status == "submitted" else 0),
            "failed": int(item.get("failed") or 0) + (1 if status == "failed" else 0),
            "waiting": int(item.get("waiting") or 0) + (1 if status == "waiting" else 0),
            "temp_saved": int(item.get("temp_saved") or 0) + (1 if status == "temp_saved" else 0),
            "last_item_id": item_id,
            "last_status": status,
            "last_updated_at": now.isoformat(),
        }
        aggregate["items"][key] = item
        aggregate_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_file(aggregate_path, aggregate)

    def _record_worker_event(
        self,
        db: Optional[Session],
        account_state: Optional[TaskAutoRunAccountState],
        *,
        severity: str,
        code: str,
        message: str,
        detail: str,
        stage: str,
        step: str,
    ) -> None:
        if db is None:
            return
        try:
            from app.models.worker import WorkerEventType, WorkerStatus
            from app.schemas.worker import WorkerEventReportRequest
            from app.services.worker_service import add_worker_event, ensure_worker

            stage, step = _normalize_3d_worker_event_stage_step(stage, step)
            worker = ensure_worker(db, "platform-worker")
            worker.display_name = "平台本机执行器"
            worker.status = WorkerStatus.DEGRADED if severity in {"error", "critical"} else WorkerStatus.ONLINE
            worker.current_task_id = AIDP_3D_RUBRIC_TASK_ID
            if account_state is not None:
                worker.current_account_user_id = account_state.account_user_id
            payload = WorkerEventReportRequest(
                worker_id="platform-worker",
                account_user_id=account_state.account_user_id if account_state is not None else "",
                task_id=AIDP_3D_RUBRIC_TASK_ID,
                severity=severity,
                message=message,
                stage=stage,
                step=step,
                error_code=code,
                error_detail=detail,
                retryable=severity != "error",
            ).model_dump(mode="json")
            add_worker_event(
                db,
                "platform-worker",
                WorkerEventType.EVENT_REPORT,
                account_user_id=account_state.account_user_id if account_state is not None else "",
                task_id=AIDP_3D_RUBRIC_TASK_ID,
                severity=severity,
                message=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception:
            return

    def _load_account_context(self, account_user_id: str) -> dict[str, Any]:
        runtime_account = self.account_loader(account_user_id)
        runtime = runtime_account if isinstance(runtime_account, dict) else {}
        state_account = _find_production_state_account(account_user_id)
        merged = dict(state_account)
        merged.update(runtime)
        return merged

    def _state_root(self) -> Path:
        if self.state_dir:
            return Path(self.state_dir)
        return _state_dir() / "3d-rubric-adapter"

    def _state_path(self, adapter_run_id: str) -> Path:
        safe_run_id = "".join(ch for ch in str(adapter_run_id) if ch.isalnum() or ch in {"-", "_"})
        if not safe_run_id:
            raise ValueError("adapter_run_id 不能为空。")
        return self._state_root() / f"{safe_run_id}.json"

    def _write_snapshot(self, snapshot: TaskAutoRunAdapterSnapshot) -> None:
        path = self._state_path(snapshot.adapter_run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "adapter_key": snapshot.adapter_key,
            "adapter_run_id": snapshot.adapter_run_id,
            "task_id": snapshot.task_id,
            "node_id": snapshot.node_id,
            "status": snapshot.status,
            "stop_requested": snapshot.stop_requested,
            "accounts": [_model_to_dict(account) for account in snapshot.accounts],
            "last_error": snapshot.last_error,
            "next_step": snapshot.next_step,
            "message": snapshot.message,
            "raw_adapter_run": snapshot.raw_adapter_run,
        }
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _read_snapshot(self, adapter_run_id: str) -> TaskAutoRunAdapterSnapshot:
        path = self._state_path(adapter_run_id)
        if not path.exists():
            raise ValueError(f"3D adapter run 不存在：{adapter_run_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return TaskAutoRunAdapterSnapshot(
            adapter_key=str(payload.get("adapter_key") or self.adapter_key),
            adapter_run_id=str(payload.get("adapter_run_id") or adapter_run_id),
            task_id=str(payload.get("task_id") or AIDP_3D_RUBRIC_TASK_ID),
            node_id=str(payload.get("node_id") or AIDP_3D_RUBRIC_NODE_ID),
            status=str(payload.get("status") or "blocked"),
            stop_requested=bool(payload.get("stop_requested")),
            accounts=[_parse_account_state(item) for item in payload.get("accounts", []) if isinstance(item, dict)],
            last_error=str(payload.get("last_error") or ""),
            next_step=str(payload.get("next_step") or ""),
            message=str(payload.get("message") or ""),
            raw_adapter_run=payload.get("raw_adapter_run") if isinstance(payload.get("raw_adapter_run"), dict) else {},
        )


def start_task_auto_run(
    db: Session,
    request: TaskAutoRunStartRequest,
    *,
    adapters: Optional[Sequence[Any]] = None,
    state_dir: Optional[Path] = None,
) -> TaskAutoRunResponse:
    account_ids = _normalize_account_ids(request.account_user_ids)
    if not account_ids:
        raise ValueError("请至少选择一个执行账号。")
    normalized_request = _copy_model(request, update={"account_user_ids": account_ids})
    adapter = _resolve_adapter(normalized_request, adapters)
    duplicate = _find_active_duplicate_run(normalized_request.task_id, account_ids, requested_run_config=normalized_request.run_config, adapters=adapters, state_dir=state_dir)
    if duplicate is not None:
        return duplicate
    snapshot = adapter.start(db, normalized_request)
    response = _response_from_snapshot(
        snapshot,
        run_id=f"{TASK_AUTO_RUN_PREFIX}{uuid4().hex[:12]}",
        ability_version=normalized_request.ability_version,
    )
    _write_run_state(response, state_dir=state_dir)
    return response


def check_task_auto_run_preflight(
    request: TaskAutoRunStartRequest,
    *,
    adapters: Optional[Sequence[Any]] = None,
) -> TaskAutoRunPreflightResponse:
    account_ids = _normalize_account_ids(request.account_user_ids)
    normalized_request = _copy_model(request, update={"account_user_ids": account_ids})
    adapter = _resolve_adapter(normalized_request, adapters)
    if hasattr(adapter, "preflight"):
        return adapter.preflight(normalized_request)
    checks = [
        TaskAutoRunPreflightCheck(
            key="adapter_ready",
            title="执行器",
            status="passed",
            detail=f"已找到执行器：{getattr(adapter, 'adapter_key', '')}",
        ),
        TaskAutoRunPreflightCheck(
            key="selected_accounts",
            title="可执行账号",
            status="passed" if account_ids else "blocked",
            detail=f"已选择 {len(account_ids)} 个账号。" if account_ids else "当前无可执行题账号，不能启动自动提交。",
            next_step="" if account_ids else "刷新生产数据，等待该任务出现可执行题后再启动。",
        ),
    ]
    can_start = bool(account_ids)
    return TaskAutoRunPreflightResponse(
        generated_at=utc_now(),
        task_id=str(normalized_request.task_id),
        node_id=str(normalized_request.node_id or "1"),
        adapter_key=str(getattr(adapter, "adapter_key", "")),
        status="ready" if can_start else "blocked",
        can_start=can_start,
        runnable_account_count=len(account_ids) if can_start else 0,
        checks=checks,
        message="自检通过；该检查不会提交、暂存或领取题目。" if can_start else "当前无可执行题，已阻止启动自动做题。",
        next_step="可以启动自动做题。" if can_start else "等待有题后再启动。",
    )


def find_active_task_auto_run(
    task_id: str,
    *,
    account_ids: Optional[list[str]] = None,
    adapters: Optional[Sequence[Any]] = None,
    state_dir: Optional[Path] = None,
) -> Optional[TaskAutoRunResponse]:
    requested = set(_normalize_account_ids(account_ids or []))
    candidates: list[TaskAutoRunResponse] = []
    for run in _list_saved_runs(state_dir=state_dir):
        if run.task_id != str(task_id) or _is_final_status(run.status) or run.stop_requested:
            continue
        active_accounts = {account.account_user_id for account in run.accounts if not _is_final_status(account.status)}
        if requested and not requested.intersection(active_accounts):
            continue
        candidates.append(run)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.generated_at.isoformat() if hasattr(item.generated_at, "isoformat") else str(item.generated_at), reverse=True)
    selected = candidates[0]
    return get_task_auto_run(selected.run_id, adapters=adapters, state_dir=state_dir)


def get_task_auto_run(
    run_id: str,
    *,
    adapters: Optional[Sequence[Any]] = None,
    state_dir: Optional[Path] = None,
) -> TaskAutoRunResponse:
    current = _read_run_state(run_id, state_dir=state_dir)
    adapter = _resolve_adapter_by_key(current.adapter_key, adapters)
    if adapter is None:
        return current
    try:
        snapshot = adapter.get(current.adapter_run_id)
    except Exception:
        return current
    refreshed = _response_from_snapshot(snapshot, run_id=current.run_id, ability_version=current.ability_version)
    _write_run_state(refreshed, state_dir=state_dir)
    return refreshed


def tick_task_auto_run(
    run_id: str,
    *,
    adapters: Optional[Sequence[Any]] = None,
    state_dir: Optional[Path] = None,
    db: Optional[Session] = None,
) -> TaskAutoRunResponse:
    current = _read_run_state(run_id, state_dir=state_dir)
    adapter = _resolve_adapter_by_key(current.adapter_key, adapters)
    if adapter is None or not hasattr(adapter, "tick"):
        raise ValueError("该题型自动执行器尚未接入 tick。")
    tick_signature = inspect.signature(adapter.tick)
    if "db" in tick_signature.parameters:
        snapshot = adapter.tick(current.adapter_run_id, db=db)
    else:
        snapshot = adapter.tick(current.adapter_run_id)
    updated = _response_from_snapshot(snapshot, run_id=current.run_id, ability_version=current.ability_version)
    _write_run_state(updated, state_dir=state_dir)
    return updated


def stop_task_auto_run(
    run_id: str,
    *,
    adapters: Optional[Sequence[Any]] = None,
    state_dir: Optional[Path] = None,
) -> TaskAutoRunResponse:
    current = _read_run_state(run_id, state_dir=state_dir)
    adapter = _resolve_adapter_by_key(current.adapter_key, adapters)
    if adapter is not None:
        snapshot = adapter.stop(current.adapter_run_id)
        stopped = _response_from_snapshot(snapshot, run_id=current.run_id, ability_version=current.ability_version)
    else:
        stopped = current.copy(update={"status": "stopped", "stop_requested": True, "message": "自动做题 run 已停止。"})
    _write_run_state(stopped, state_dir=state_dir)
    return stopped


def pause_task_auto_run(run_id: str, *, state_dir: Optional[Path] = None) -> TaskAutoRunResponse:
    current = _read_run_state(run_id, state_dir=state_dir)
    paused = _copy_model(current, update={"status": "paused", "stop_requested": False, "message": "自动做题 run 已暂停领取新题。"})
    _write_run_state(paused, state_dir=state_dir)
    return paused


def resume_task_auto_run(run_id: str, *, state_dir: Optional[Path] = None) -> TaskAutoRunResponse:
    current = _read_run_state(run_id, state_dir=state_dir)
    if _is_final_status(current.status) or current.stop_requested:
        raise ValueError("已停止或已结束的自动做题 run 不能恢复。")
    resumed = _copy_model(current, update={"status": "running_auto", "stop_requested": False, "message": "自动做题 run 已恢复领取新题。"})
    _write_run_state(resumed, state_dir=state_dir)
    return resumed


def update_task_auto_run_raw_config(run: TaskAutoRunResponse, run_config: dict[str, Any], *, state_dir: Optional[Path] = None) -> TaskAutoRunResponse:
    raw_adapter_run = dict(run.raw_adapter_run or {})
    raw_adapter_run["run_config"] = run_config
    updated = _copy_model(run, update={"raw_adapter_run": raw_adapter_run})
    _write_run_state(updated, state_dir=state_dir)
    return updated


def default_task_auto_run_adapters() -> list[Any]:
    return [TaskAutoRunBon8Adapter(), TaskAutoRunResearchChartAdapter(), TaskAutoRun3DRubricAdapter()]


def _snapshot_from_bon8(run: Bon8ProductionRunResponse) -> TaskAutoRunAdapterSnapshot:
    accounts = [
        TaskAutoRunAccountState(
            account_user_id=account.account_user_id,
            account_name=account.account_name,
            status=account.status,
            current_item_id=account.current_item_id,
            current_stage=account.current_stage,
            healthy=_is_account_healthy(account.status, account.last_error),
            last_error=account.last_error,
        )
        for account in run.accounts
    ]
    return TaskAutoRunAdapterSnapshot(
        adapter_key="bon8",
        adapter_run_id=run.run_id,
        task_id=run.task_id,
        node_id=run.node_id,
        status=run.status,
        stop_requested=run.stop_requested,
        accounts=accounts,
        last_error=run.last_error,
        next_step=run.next_step,
        message=run.message,
        raw_adapter_run=_model_to_dict(run),
    )


def _response_from_snapshot(snapshot: TaskAutoRunAdapterSnapshot, *, run_id: str, ability_version: str = "") -> TaskAutoRunResponse:
    healthy_count = sum(1 for account in snapshot.accounts if account.healthy)
    abnormal_count = len(snapshot.accounts) - healthy_count
    health_ok = abnormal_count == 0 and not snapshot.last_error
    resolved_ability_version = str(snapshot.raw_adapter_run.get("ability_version") or "") or ability_version
    return TaskAutoRunResponse(
        generated_at=utc_now(),
        run_id=run_id,
        adapter_key=snapshot.adapter_key,
        adapter_run_id=snapshot.adapter_run_id,
        task_id=snapshot.task_id,
        node_id=snapshot.node_id,
        ability_version=resolved_ability_version,
        status=snapshot.status,
        stop_requested=snapshot.stop_requested,
        selected_account_count=len(snapshot.accounts),
        healthy_account_count=healthy_count,
        abnormal_account_count=abnormal_count,
        health_ok=health_ok,
        accounts=snapshot.accounts,
        last_error=snapshot.last_error,
        next_step=snapshot.next_step,
        message=f"任务操作台自动做题：{snapshot.message}",
        raw_adapter_run=snapshot.raw_adapter_run,
    )


def _resolve_adapter(request: TaskAutoRunStartRequest, adapters: Optional[Sequence[Any]]) -> Any:
    candidates = list(adapters) if adapters is not None else default_task_auto_run_adapters()
    if request.adapter_key:
        adapter = _resolve_adapter_by_key(request.adapter_key, candidates)
        if adapter is None:
            raise ValueError(f"未找到自动做题执行器：{request.adapter_key}")
        return adapter
    for adapter in candidates:
        if str(request.task_id) in {str(task_id) for task_id in getattr(adapter, "supported_task_ids", set())}:
            return adapter
        supports_task = getattr(adapter, "supports_task", None)
        if callable(supports_task) and supports_task(str(request.task_id)):
            return adapter
    raise ValueError("该任务还没有可执行的 AI 自动做题 adapter。")


def _resolve_adapter_by_key(adapter_key: str, adapters: Optional[Sequence[Any]]) -> Optional[Any]:
    candidates = list(adapters) if adapters is not None else default_task_auto_run_adapters()
    for adapter in candidates:
        if getattr(adapter, "adapter_key", "") == adapter_key:
            return adapter
    return None


def _find_active_duplicate_run(
    task_id: str,
    account_ids: list[str],
    *,
    requested_run_config: Optional[dict[str, Any]] = None,
    adapters: Optional[Sequence[Any]] = None,
    state_dir: Optional[Path] = None,
) -> Optional[TaskAutoRunResponse]:
    requested = set(account_ids)
    requested_mode = _ability_run_mode_from_config(requested_run_config)
    for run in _list_saved_runs(state_dir=state_dir):
        if run.task_id != str(task_id) or _is_final_status(run.status) or run.stop_requested:
            continue
        active_accounts = {account.account_user_id for account in run.accounts if not _is_final_status(account.status)}
        overlap = sorted(requested.intersection(active_accounts))
        if overlap:
            existing_mode = _ability_run_mode_from_response(run)
            if existing_mode != requested_mode:
                raise ValueError(f"账号 {', '.join(overlap)} 已经有运行中的自动做题，但运行模式不同；请先停止原 run 后再启动。")
            if requested == active_accounts:
                return get_task_auto_run(run.run_id, adapters=adapters, state_dir=state_dir)
            if run.adapter_key == "research_chart" and active_accounts.issubset(requested):
                adapter = _resolve_adapter_by_key(run.adapter_key, adapters)
                if adapter is not None and hasattr(adapter, "extend_accounts"):
                    snapshot = adapter.extend_accounts(run.adapter_run_id, sorted(requested))
                    extended = _response_from_snapshot(snapshot, run_id=run.run_id, ability_version=run.ability_version)
                    _write_run_state(extended, state_dir=state_dir)
                    return extended
            raise ValueError(f"账号 {', '.join(overlap)} 已经有运行中的自动做题，请先停止原 run。")
    return None


def _list_saved_runs(*, state_dir: Optional[Path] = None) -> list[TaskAutoRunResponse]:
    base = _state_dir(state_dir)
    if not base.exists():
        return []
    runs: list[TaskAutoRunResponse] = []
    for path in base.glob("*.json"):
        try:
            runs.append(_parse_run_state(json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return runs


def _normalize_account_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        user_id = str(value or "").strip()
        if user_id and user_id not in result:
            result.append(user_id)
    return result


def _is_account_healthy(status: str, last_error: str = "") -> bool:
    lowered = str(status or "").lower()
    if last_error:
        return False
    return not any(token in lowered for token in ("failed", "error", "missing", "no_item", "backoff", "pending"))


def _is_final_status(status: str) -> bool:
    return str(status or "") in {"stopped", "blocked", "completed", "completed_no_item", "failed", "executor_pending"}


def _model_to_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return json.loads(model.json())


def _copy_model(model: Any, *, update: dict[str, Any]) -> Any:
    if hasattr(model, "model_copy"):
        return model.model_copy(update=update)
    return model.copy(update=update)


def _state_dir(state_dir: Optional[Path] = None) -> Path:
    if state_dir:
        return Path(state_dir)
    configured = os.environ.get("AIDP_TASK_AUTO_RUN_STATE_DIR")
    if configured:
        return Path(configured)
    return _data_root() / "production-runs" / "task-auto-runs"


def _run_state_path(run_id: str, *, state_dir: Optional[Path] = None) -> Path:
    safe_run_id = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in {"-", "_"})
    if not safe_run_id:
        raise ValueError("run_id 不能为空。")
    return _state_dir(state_dir) / f"{safe_run_id}.json"


def _write_run_state(run: TaskAutoRunResponse, *, state_dir: Optional[Path] = None) -> None:
    path = _run_state_path(run.run_id, state_dir=state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(_model_to_dict(run), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _read_run_state(run_id: str, *, state_dir: Optional[Path] = None) -> TaskAutoRunResponse:
    path = _run_state_path(run_id, state_dir=state_dir)
    if not path.exists():
        raise ValueError(f"自动做题 run 不存在：{run_id}")
    return _parse_run_state(json.loads(path.read_text(encoding="utf-8")))


def _parse_run_state(payload: dict[str, Any]) -> TaskAutoRunResponse:
    if hasattr(TaskAutoRunResponse, "model_validate"):
        return TaskAutoRunResponse.model_validate(payload)
    return TaskAutoRunResponse.parse_obj(payload)


def _post_aidp(account: dict[str, Any], kind: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    started = perf_counter()
    response = requests.post(f"https://aidp.juejin.cn{path}", headers=_aidp_headers(account, kind), json=body, timeout=30)
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


def _aidp_headers(account: dict[str, Any], kind: str) -> dict[str, str]:
    referer = str(account.get("referer") or account.get("operationUrl") or "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://aidp.juejin.cn",
        "Referer": referer,
        "Cookie": str(account.get("cookie") or ""),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
    }
    if kind == "api":
        headers.update({"x-secsdk-csrf-token": "DOWNGRADE", "x-backend-org-id": "100", "x-web-org-id": "100"})
    else:
        headers.update({"Agw-Js-Conv": "str", "X-JS-REQ": "1", "X-Backend-Side": "4", "X-Backend-Org-Id": "100"})
    return headers


def _category_body(task_id: str, node_id: str) -> dict[str, Any]:
    return {
        "TaskID": str(task_id),
        "NodeID": _node_id_value(node_id),
        "ItemCategoryType": 0,
        "Filter": {},
        "PageRequest": {"PageNo": 0, "PageSize": 1},
    }


def _node_id_value(value: Any) -> Any:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return str(value or "1")


def _ability_run_mode(snapshot: TaskAutoRunAdapterSnapshot) -> str:
    run_config = snapshot.raw_adapter_run.get("run_config") if isinstance(snapshot.raw_adapter_run, dict) else {}
    return _ability_run_mode_from_config(run_config if isinstance(run_config, dict) else {})


def _ability_run_mode_from_response(run: TaskAutoRunResponse) -> str:
    raw_adapter_run = run.raw_adapter_run if isinstance(run.raw_adapter_run, dict) else {}
    run_config = raw_adapter_run.get("run_config") if isinstance(raw_adapter_run.get("run_config"), dict) else {}
    return _ability_run_mode_from_config(run_config)


def _ability_run_mode_from_config(run_config: Optional[dict[str, Any]]) -> str:
    if not isinstance(run_config, dict):
        return ""
    return str(run_config.get("ability_run_mode") or "").strip().lower()


def _run_config(snapshot: TaskAutoRunAdapterSnapshot) -> dict[str, Any]:
    run_config = snapshot.raw_adapter_run.get("run_config") if isinstance(snapshot.raw_adapter_run, dict) else {}
    return run_config if isinstance(run_config, dict) else {}


def _submit_limit_for_run(snapshot: TaskAutoRunAdapterSnapshot) -> Optional[int]:
    run_config = _run_config(snapshot)
    mode = _ability_run_mode(snapshot)
    if mode == "trial":
        return max(1, _num(run_config.get("trial_max_items_per_account")) or 1)
    if mode == "production":
        return max(1, _num(run_config.get("production_max_items_per_account")) or 1)
    return None


def _rate_limit_wait_seconds(snapshot: TaskAutoRunAdapterSnapshot) -> int:
    run_config = _run_config(snapshot)
    mode = _ability_run_mode(snapshot)
    if mode not in {"trial", "production"}:
        return 0
    per_minute = max(1, _num(run_config.get("rate_limit_per_minute")) or 1)
    interval_seconds = 60 / per_minute
    try:
        last_epoch = float(snapshot.raw_adapter_run.get("last_formal_submit_epoch") or 0)
    except (TypeError, ValueError):
        last_epoch = 0
    if last_epoch <= 0:
        return 0
    elapsed = max(0.0, utc_now().timestamp() - last_epoch)
    remaining = interval_seconds - elapsed
    return int(remaining) + 1 if remaining > 0 else 0


def _artifact_temp_save_verified(artifact: dict[str, Any]) -> bool:
    if not isinstance(artifact, dict) or not artifact.get("saved_to_task_ui"):
        return False
    result = artifact.get("temp_draft_result")
    if not isinstance(result, dict):
        return False
    return result.get("base_resp_status_code") in (0, "0")


def _compact_remote_result(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body") if isinstance(result, dict) else {}
    return {
        "statusCode": result.get("statusCode") if isinstance(result, dict) else None,
        "baseRespStatusCode": _remote_base_status_code(result),
        "elapsedMs": result.get("elapsedMs") if isinstance(result, dict) else None,
        "totalMap": body.get("TotalMap") if isinstance(body, dict) else None,
    }


def _remote_base_status_code(result: dict[str, Any]) -> Optional[int]:
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


def _submit_and_receive_submit_ok(result: dict[str, Any]) -> bool:
    body = result.get("body") if isinstance(result, dict) else {}
    submit_body = body.get("SubmitItemResponse") if isinstance(body, dict) else {}
    return _base_resp_status_code_from_body(submit_body) == 0


def _submit_and_receive_receive_ok(result: dict[str, Any]) -> bool:
    body = result.get("body") if isinstance(result, dict) else {}
    receive_body = body.get("ReceiveResponse") if isinstance(body, dict) else {}
    return _base_resp_status_code_from_body(receive_body) == 0


def _submit_and_receive_next_item_id(result: dict[str, Any]) -> str:
    body = result.get("body") if isinstance(result, dict) else {}
    receive_body = body.get("ReceiveResponse") if isinstance(body, dict) else {}
    items = receive_body.get("Items") if isinstance(receive_body, dict) else []
    if not isinstance(items, list) or not items:
        return ""
    first = items[0] if isinstance(items[0], dict) else {}
    item = first.get("Item") if isinstance(first.get("Item"), dict) else {}
    return str(item.get("ItemID") or "")


def _base_resp_status_code_from_body(body: dict[str, Any]) -> Optional[int]:
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
    if _remote_base_status_code(result) != 0:
        return False
    body = result.get("body") if isinstance(result, dict) else {}
    data = body.get("Data") if isinstance(body, dict) else []
    if not isinstance(data, list):
        return False
    return all(str(item.get("ItemID") or "") != str(item_id) for item in data if isinstance(item, dict))


def _parse_account_state(payload: dict[str, Any]) -> TaskAutoRunAccountState:
    if hasattr(TaskAutoRunAccountState, "model_validate"):
        return TaskAutoRunAccountState.model_validate(payload)
    return TaskAutoRunAccountState.parse_obj(payload)


def _research_state_dir(state_dir: Optional[Path] = None) -> Path:
    if state_dir:
        return Path(state_dir)
    return _state_dir() / "research-chart-adapter"


def _default_ability_store_path() -> Path:
    base = Path(get_settings().production_state_path)
    root = base.parent if base.parent != Path("") else Path("data")
    return root / "task-abilities" / "ability-drafts.json"


def _load_json_file(path: Path) -> Any:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _evidence_root(evidence_root: Optional[Path] = None) -> Path:
    if evidence_root:
        return Path(evidence_root)
    configured = os.environ.get("AIDP_TASK_AUTO_RUN_EVIDENCE_DIR")
    if configured:
        return Path(configured)
    return _data_root() / "production-runs" / "task-auto-run-evidence"


def _check_evidence_storage_preflight(evidence_root: Path) -> tuple[str, str]:
    target = Path(evidence_root)
    if target.exists():
        if not target.is_dir():
            return "blocked", f"证据路径不是目录：{target}"
        if not os.access(target, os.W_OK | os.X_OK):
            return "blocked", f"证据目录不可写：{target}"
        return "passed", f"证据目录已存在且可写：{target}"

    probe = target.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists():
        return "blocked", f"证据目录父路径不存在：{target.parent}"
    if not probe.is_dir():
        return "blocked", f"证据目录父路径不是目录：{probe}"
    if not os.access(probe, os.W_OK | os.X_OK):
        return "blocked", f"证据目录父路径不可写：{probe}"
    return "passed", f"证据目录路径可用，启动时创建：{target}"


def _data_root() -> Path:
    base = Path(get_settings().production_state_path)
    if base.parent != Path(""):
        return base.parent
    return Path.cwd() / "data"


def _safe_file_part(value: str) -> str:
    safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})
    return safe or "unknown"


def _normalize_3d_worker_event_stage_step(stage: str, step: str) -> tuple[str, str]:
    answer_steps = {"prepare_context", "call_provider", "parse_answer", "temp_save", "submit_answer", "readback_result", "ledger_update"}
    runtime_steps = {"heartbeat", "bind_account", "claim_task", "version_update", "log_summary"}
    raw_stage = str(stage or "")
    raw_step = str(step or "")
    if raw_stage == "read_current":
        return "3d_http_answer", "prepare_context"
    if raw_stage == "3d_http_answer" and raw_step in answer_steps:
        return raw_stage, raw_step
    if raw_stage in answer_steps:
        return "3d_http_answer", raw_stage
    if raw_step in answer_steps:
        return "3d_http_answer", raw_step
    if raw_stage == "worker_runtime" and raw_step in runtime_steps:
        return raw_stage, raw_step
    return "worker_runtime", "log_summary"


def _find_production_state_account(account_user_id: str) -> dict[str, Any]:
    state = load_production_state()
    accounts = state.get("accounts", []) if isinstance(state, dict) else []
    for account in accounts:
        if isinstance(account, dict) and str(account.get("userId") or account.get("user_id") or "") == str(account_user_id):
            return account
    return {}


def _find_account_task(account: dict[str, Any], task_id: str) -> dict[str, Any]:
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


def _task_processing_count(task: dict[str, Any]) -> int:
    total_map = task.get("frontendCategoryTotalMap") if isinstance(task.get("frontendCategoryTotalMap"), dict) else {}
    return _num(task.get("frontendNotSubmitted"), total_map.get("0"), task.get("processing"), task.get("personalProcessing"))


def _task_repair_count(task: dict[str, Any]) -> int:
    category = task.get("frontendSubmittedCategory") if isinstance(task.get("frontendSubmittedCategory"), dict) else {}
    status_counts = category.get("statusCounts") if isinstance(category.get("statusCounts"), dict) else {}
    return _num(task.get("frontendRepairCount"), status_counts.get("9"), task.get("repair"), task.get("modify"))


def _task_pending_count(task: dict[str, Any]) -> int:
    return _num(task.get("poolPendingSubmit"), task.get("pending"), task.get("todo"))


def _auto_receive_block_reason(task: dict[str, Any], has_current_item: bool, pending: int) -> str:
    if not task:
        return "生产状态里还没有该任务的账号记录，暂时不能判断自动领题资格。"
    if has_current_item:
        return ""
    if pending > 0:
        return ""
    if pending <= 0:
        return "当前账号没有待处理、处理中或返修题，不能进入自动循环。"
    return ""


def _summarize_blocked_accounts(accounts: Sequence[dict[str, Any]]) -> str:
    preview = []
    for item in list(accounts)[:4]:
        preview.append(f"{item['account_name']}：{item['reason']}")
    suffix = "；其余账号同理" if len(accounts) > 4 else ""
    return "；".join(preview) + suffix


def _num(*values: Any) -> int:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(float(str(value).replace(",", "").strip()))
        except ValueError:
            continue
    return 0
