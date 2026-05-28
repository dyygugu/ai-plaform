import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.v1.router import api_router
from app.api.v1.routes import task_auto_runs as task_auto_runs_route
from app.db.base import Base
from app.db.session import get_db
from app.schemas.task_auto_runs import TaskAutoRunAccountState
from app.schemas.task_auto_runs import TaskAutoRunPreflightCheck
from app.schemas.task_auto_runs import TaskAutoRunPreflightResponse
from app.schemas.task_auto_runs import TaskAutoRunStartRequest
from app.services.task_auto_run_service import (
    RESEARCH_CHART_TASK_ID,
    RESEARCH_CHART_TASK_IDS,
    TaskAutoRunAdapterSnapshot,
    TaskAutoRunBon8Adapter,
    TaskAutoRunResearchChartAdapter,
    check_task_auto_run_preflight,
    get_task_auto_run,
    start_task_auto_run,
    stop_task_auto_run,
)
from app.services.task_rules import utc_now


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _category_transport(_account, _kind, path, _body):
    if path != "/dispatcher/search_item/category":
        raise AssertionError(f"unexpected remote call: {path}")
    return {
        "statusCode": 200,
        "elapsedMs": 1,
        "body": {
            "BaseResp": {"StatusCode": 0},
            "Data": [
                {
                    "ItemID": "item-1",
                    "Content": "{\"mediaUrls\":[\"https://example.test/input.png\"]}",
                    "Status": 4,
                }
            ],
            "TotalMap": {"0": 1},
        },
    }


def _account_loader(user_id):
    return {
        "userId": user_id,
        "name": f"用户{user_id[-4:]}",
        "cookie": "sessionid=test",
        "operationUrl": "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1",
        "tasks": [
            {"id": RESEARCH_CHART_TASK_ID, "receiveEnable": True, "frontendNotSubmitted": 1, "frontendRepairCount": 0, "poolPendingSubmit": 10},
            {"id": "7639402643386830630", "receiveEnable": True, "frontendNotSubmitted": 1, "frontendRepairCount": 0, "poolPendingSubmit": 10},
        ],
    }


def _write_research_chart_ability_store(
    path: Path,
    *,
    enabled: bool = True,
    draft_id: str = "research-draft-1",
    version: str = "ability-test-research-chart",
    task_id: str = RESEARCH_CHART_TASK_ID,
    task_name: str = "RFT科研图表还原-正式(随机5000题)",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    review = {
        "review_status": "人工已通过" if enabled else "待人工审核",
        "saved_to_task_ui": enabled,
        "item_id": "review-item-1",
    }
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": draft_id,
                        "version": version,
                        "status": "有做题能力" if enabled else "待审核真实不提交结果",
                        "task_name": task_name,
                        "task_id": task_id,
                        "flow_stage": "capability_enabled" if enabled else "real_no_submit_review",
                        "capability_enabled": enabled,
                        "real_no_submit_review": review,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class TaskAutoRunServiceTests(unittest.TestCase):
    def test_task_auto_run_route_falls_back_to_default_adapters_when_app_state_is_missing(self) -> None:
        fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        class FakeAdapter:
            adapter_key = "research_chart"

        with patch.object(task_auto_runs_route, "default_task_auto_run_adapters", return_value=[FakeAdapter()]):
            adapter = task_auto_runs_route._adapter_by_key(fake_request, "research_chart")

        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.adapter_key, "research_chart")

    def test_default_ability_store_path_uses_production_state_root(self) -> None:
        from app.services import task_auto_run_service as service

        class _Settings:
            production_state_path = r"D:\tmp\aidp-data\production-state.json"

        with patch.object(service, "get_settings", return_value=_Settings()):
            path = service._default_ability_store_path()

        self.assertEqual(path, Path(r"D:\tmp\aidp-data\task-abilities\ability-drafts.json"))

    def test_default_task_auto_run_state_dir_uses_production_state_root(self) -> None:
        from app.services import task_auto_run_service as service

        class _Settings:
            production_state_path = r"D:\tmp\aidp-data\production-state.json"

        with patch.object(service, "get_settings", return_value=_Settings()):
            path = service._state_dir()

        self.assertEqual(path, Path(r"D:\tmp\aidp-data\production-runs\task-auto-runs"))

    def test_default_task_auto_run_evidence_dir_uses_production_state_root(self) -> None:
        from app.services import task_auto_run_service as service

        class _Settings:
            production_state_path = r"D:\tmp\aidp-data\production-state.json"

        with patch.object(service, "get_settings", return_value=_Settings()):
            path = service._evidence_root()

        self.assertEqual(path, Path(r"D:\tmp\aidp-data\production-runs\task-auto-run-evidence"))

    def test_research_chart_task_id_set_includes_full_dataset_task(self) -> None:
        self.assertIn("7639402643386830630", RESEARCH_CHART_TASK_IDS)

    def test_start_bon8_creates_generic_run_and_persists_adapter_mapping(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            result = start_task_auto_run(
                db,
                TaskAutoRunStartRequest(
                    task_id="7637771731901861641",
                    node_id="1",
                    account_user_ids=["account-sample-002", "account-sample-004"],
                ),
                adapters=[
                    TaskAutoRunBon8Adapter(account_loader=_account_loader, transport=_category_transport),
                    TaskAutoRunResearchChartAdapter(),
                ],
                state_dir=state_dir,
            )

            self.assertTrue(result.run_id.startswith("task-auto-"))
            self.assertEqual(result.adapter_key, "bon8")
            self.assertTrue(result.adapter_run_id.startswith("bon8-"))
            self.assertEqual(result.task_id, "7637771731901861641")
            self.assertEqual(result.selected_account_count, 2)
            self.assertEqual(result.accounts[0].status, "waiting_first_confirm")
            self.assertEqual(result.accounts[0].current_item_id, "item-1")
            self.assertTrue(result.health_ok)
            self.assertEqual(result.abnormal_account_count, 0)
            self.assertIn("任务操作台", result.message)

            saved = get_task_auto_run(result.run_id, adapters=[], state_dir=state_dir)
            self.assertEqual(saved.run_id, result.run_id)
            self.assertEqual(saved.adapter_run_id, result.adapter_run_id)
        db.close()

    def test_partially_overlapping_bon8_run_is_rejected_until_stopped(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            adapters = [TaskAutoRunBon8Adapter(account_loader=_account_loader, transport=_category_transport)]
            first_request = TaskAutoRunStartRequest(
                task_id="7637771731901861641",
                node_id="1",
                account_user_ids=["account-sample-002"],
            )
            overlapping_request = TaskAutoRunStartRequest(
                task_id="7637771731901861641",
                node_id="1",
                account_user_ids=["account-sample-002", "account-sample-004"],
            )
            first = start_task_auto_run(db, first_request, adapters=adapters, state_dir=state_dir)

            with self.assertRaises(ValueError) as duplicate:
                start_task_auto_run(db, overlapping_request, adapters=adapters, state_dir=state_dir)
            self.assertIn("已经有运行中的自动做题", str(duplicate.exception))

            stopped = stop_task_auto_run(first.run_id, adapters=adapters, state_dir=state_dir)
            self.assertEqual(stopped.status, "stopped")
            restarted = start_task_auto_run(db, overlapping_request, adapters=adapters, state_dir=state_dir)
            self.assertNotEqual(restarted.run_id, first.run_id)
        db.close()

    def test_research_chart_start_can_extend_existing_run_with_new_accounts(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True)
            adapter = TaskAutoRunResearchChartAdapter(
                ability_store_path=ability_store,
                state_dir=root / "research-runs",
                account_loader=lambda user_id: {
                    "userId": user_id,
                    "name": f"用户{user_id[-4:]}",
                    "cookie": "sessionid=test",
                    "operationUrl": "https://aidp.juejin.cn/operation/task-v2?page=1",
                    "tasks": [{"id": RESEARCH_CHART_TASK_ID, "frontendNotSubmitted": 0, "frontendRepairCount": 0, "poolPendingSubmit": 10}],
                },
            )
            first = start_task_auto_run(
                db,
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[adapter],
                state_dir=root / "generic-runs",
            )

            extended = start_task_auto_run(
                db,
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-002", "account-sample-004"],
                ),
                adapters=[adapter],
                state_dir=root / "generic-runs",
            )

            self.assertEqual(extended.run_id, first.run_id)
            self.assertEqual(extended.selected_account_count, 2)
            self.assertEqual(sorted(account.account_user_id for account in extended.accounts), ["account-sample-002", "account-sample-004"])
        db.close()

    def test_exact_duplicate_start_reuses_existing_run_instead_of_400(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            adapters = [TaskAutoRunBon8Adapter(account_loader=_account_loader, transport=_category_transport)]
            request = TaskAutoRunStartRequest(
                task_id="7637771731901861641",
                node_id="1",
                account_user_ids=["account-sample-002"],
            )

            first = start_task_auto_run(db, request, adapters=adapters, state_dir=state_dir)
            duplicated = start_task_auto_run(db, request, adapters=adapters, state_dir=state_dir)

            self.assertEqual(duplicated.run_id, first.run_id)
            self.assertEqual(duplicated.adapter_run_id, first.adapter_run_id)
            self.assertEqual(duplicated.task_id, first.task_id)
        db.close()

    def test_bon8_preflight_reports_published_ability_and_current_item_requirement(self) -> None:
        adapter = TaskAutoRunBon8Adapter(
            account_loader=lambda user_id: {
                "userId": user_id,
                "name": f"用户{user_id[-4:]}",
                "cookie": "sessionid=test",
                "tasks": [{"id": "7637771731901861641", "frontendNotSubmitted": 1, "poolPendingSubmit": 0}],
            },
            transport=lambda _account, _kind, path, _body: (
                {
                    "statusCode": 200,
                    "elapsedMs": 1,
                    "body": {
                        "BaseResp": {"StatusCode": 0},
                        "Data": [],
                    },
                }
                if path == "/dispatcher/search_item/category"
                else (_ for _ in ()).throw(AssertionError(path))
            ),
        )

        result = adapter.preflight(
            TaskAutoRunStartRequest(
                task_id="7637771731901861641",
                node_id="1",
                account_user_ids=["account-sample-002"],
            )
        )

        self.assertFalse(result.can_start)
        checks = {item.key: item for item in result.checks}
        self.assertEqual(checks["ability_published"].status, "passed")
        self.assertEqual(checks["current_processing_item"].status, "blocked")
        self.assertIn("已领取", checks["current_processing_item"].detail)
        self.assertIn("已领取的处理中题", result.next_step)

    def test_research_chart_adapter_blocks_when_ability_store_is_missing(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = start_task_auto_run(
                db,
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[TaskAutoRunResearchChartAdapter(ability_store_path=root / "missing" / "ability-drafts.json", state_dir=root / "research-runs", account_loader=_account_loader)],
                state_dir=root / "generic-runs",
            )

            self.assertEqual(result.adapter_key, "research_chart")
            self.assertEqual(result.status, "blocked")
            self.assertFalse(result.health_ok)
            self.assertEqual(result.accounts[0].status, "ability_not_enabled")
            self.assertIn("题型能力", result.accounts[0].last_error)
        db.close()

    def test_research_chart_start_requires_enabled_ability_before_running(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=False)

            result = start_task_auto_run(
                db,
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[TaskAutoRunResearchChartAdapter(ability_store_path=ability_store, state_dir=root / "research-runs", account_loader=_account_loader)],
                state_dir=root / "generic-runs",
            )

            self.assertEqual(result.adapter_key, "research_chart")
            self.assertEqual(result.status, "blocked")
            self.assertFalse(result.health_ok)
            self.assertEqual(result.accounts[0].status, "ability_not_enabled")
            self.assertIn("题型能力库", result.next_step)
        db.close()

    def test_research_chart_preflight_reports_no_runnable_accounts_without_starting(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True)

            result = check_task_auto_run_preflight(
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=[],
                ),
                adapters=[
                    TaskAutoRunResearchChartAdapter(
                        ability_store_path=ability_store,
                        state_dir=root / "research-runs",
                        evidence_root=root / "evidence",
                    )
                ],
            )

            self.assertFalse(result.can_start)
            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.runnable_account_count, 0)
            self.assertIn("当前无可执行题", result.message)
            self.assertEqual(result.checks[0].key, "selected_accounts")
            self.assertEqual(result.checks[0].status, "blocked")
        db.close()

    def test_research_chart_preflight_checks_ability_cookie_and_evidence_without_submitting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True)

            def account_loader(user_id):
                return {
                    "userId": user_id,
                    "name": "用户自检",
                    "cookie": "sessionid=test",
                    "operationUrl": "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1",
                    "tasks": [{"id": RESEARCH_CHART_TASK_ID, "receiveEnable": True, "frontendNotSubmitted": 1, "frontendRepairCount": 0, "poolPendingSubmit": 10}],
                }

            result = check_task_auto_run_preflight(
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[
                    TaskAutoRunResearchChartAdapter(
                        ability_store_path=ability_store,
                        state_dir=root / "research-runs",
                        evidence_root=root / "evidence",
                        account_loader=account_loader,
                    )
                ],
            )

            self.assertTrue(result.can_start)
            self.assertEqual(result.status, "ready")
            self.assertEqual(result.runnable_account_count, 1)
            self.assertIn("不会提交", result.message)
            checks = {item.key: item for item in result.checks}
            self.assertEqual(checks["ability_published"].status, "passed")
            self.assertEqual(checks["account_cookie"].status, "passed")
            self.assertEqual(checks["evidence_storage"].status, "passed")

    def test_research_chart_adapter_supports_full_dataset_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(
                ability_store,
                enabled=True,
                task_id="7639402643386830630",
                task_name="RFT科研图表还原-正式(全量数据)",
            )

            result = check_task_auto_run_preflight(
                TaskAutoRunStartRequest(
                    task_id="7639402643386830630",
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[
                    TaskAutoRunResearchChartAdapter(
                        ability_store_path=ability_store,
                        state_dir=root / "research-runs",
                        evidence_root=root / "evidence",
                        account_loader=lambda _user_id: {
                            "userId": "account-sample-002",
                            "name": "用户全量",
                            "cookie": "sessionid=test",
                            "operationUrl": "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1",
                            "tasks": [{"id": "7639402643386830630", "receiveEnable": True, "frontendNotSubmitted": 1, "frontendRepairCount": 0, "poolPendingSubmit": 10}],
                        },
                    )
                ],
            )

            self.assertTrue(result.can_start)
            self.assertEqual(result.task_id, "7639402643386830630")
            self.assertEqual(result.adapter_key, "research_chart")

    def test_research_chart_adapter_accepts_migrated_published_ability_for_new_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            ability_store.parent.mkdir(parents=True, exist_ok=True)
            ability_store.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "research-chart-full",
                                "version": "ability-full",
                                "status": "有做题能力",
                                "task_name": "RFT科研图表还原-正式(全量数据)",
                                "task_id": "7639402643386830630",
                                "flow_stage": "capability_enabled",
                                "capability_enabled": True,
                                "real_no_submit_review": {
                                    "review_status": "沿用已发布同类型能力",
                                    "migrated_from_task_id": RESEARCH_CHART_TASK_ID,
                                    "migrated_from_draft_id": "draft-old",
                                    "saved_to_task_ui": False,
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = check_task_auto_run_preflight(
                TaskAutoRunStartRequest(
                    task_id="7639402643386830630",
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[
                    TaskAutoRunResearchChartAdapter(
                        ability_store_path=ability_store,
                        state_dir=root / "research-runs",
                        evidence_root=root / "evidence",
                        account_loader=lambda _user_id: {
                            "userId": "account-sample-002",
                            "name": "用户全量",
                            "cookie": "sessionid=test",
                            "operationUrl": "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1",
                            "tasks": [{"id": "7639402643386830630", "receiveEnable": True, "frontendNotSubmitted": 1, "frontendRepairCount": 0, "poolPendingSubmit": 10}],
                        },
                    )
                ],
            )

            self.assertTrue(result.can_start)
            self.assertEqual(result.status, "ready")
            self.assertTrue((root / "evidence").exists())

    def test_research_chart_adapter_runs_one_temp_save_tick_without_formal_submit(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True)
            ability_calls = []

            def fake_ability_runner(draft_id: str, **kwargs):
                ability_calls.append({"draft_id": draft_id, "kwargs": kwargs})
                return {
                    "ok": True,
                    "task_id": RESEARCH_CHART_TASK_ID,
                    "stage": "端到端做题不提交：已暂存待人工审核",
                    "saved_to_task_ui": True,
                    "writes_remote": True,
                    "submits_remote": False,
                    "question_context": {"item_id": "live-item-1", "source_mode": "live_search_item_category"},
                    "saved_answer": {"data.label_sorce.model_image": "0", "data.label_remark.model_image": "两图差异明显"},
                    "ai_decision": {"score": "0", "reason": "两图差异明显", "confidence": "high"},
                    "temp_draft_result": {"ok": True, "base_resp_status_code": 0},
                }

            adapter = TaskAutoRunResearchChartAdapter(
                ability_store_path=ability_store,
                review_root=root / "reviews",
                state_dir=root / "research-runs",
                ability_runner=fake_ability_runner,
                account_loader=_account_loader,
            )
            result = start_task_auto_run(
                db,
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[adapter],
                state_dir=root / "generic-runs",
            )

            self.assertEqual(result.status, "running_auto")
            ticked = adapter.tick(result.adapter_run_id)

            self.assertEqual(ticked.status, "running_auto")
            self.assertEqual(ticked.accounts[0].status, "temp_saved_waiting_submit")
            self.assertEqual(ticked.accounts[0].current_item_id, "live-item-1")
            self.assertTrue(ticked.accounts[0].healthy)
            self.assertIn("未正式提交", ticked.message)
            self.assertEqual(ability_calls[0]["draft_id"], "research-draft-1")
            self.assertEqual(ability_calls[0]["kwargs"]["target_account_user_id"], "account-sample-002")
            self.assertTrue(ability_calls[0]["kwargs"]["allow_temp_save"])
            self.assertFalse(ability_calls[0]["kwargs"]["use_system_ai_for_vision"])
        db.close()

    def test_research_chart_tick_fans_out_up_to_five_accounts_concurrently(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True)
            current = 0
            max_concurrent = 0
            lock = threading.Lock()

            def fake_ability_runner(draft_id: str, **kwargs):
                nonlocal current, max_concurrent
                with lock:
                    current += 1
                    max_concurrent = max(max_concurrent, current)
                time.sleep(0.15)
                with lock:
                    current -= 1
                account_id = str(kwargs["target_account_user_id"])
                return {
                    "ok": True,
                    "task_id": RESEARCH_CHART_TASK_ID,
                    "stage": "端到端做题不提交：已暂存待人工审核",
                    "saved_to_task_ui": True,
                    "writes_remote": True,
                    "submits_remote": False,
                    "question_context": {"item_id": f"item-{account_id[-4:]}", "source_mode": "live_search_item_category"},
                    "saved_answer": {"data.label_sorce.model_image": "0", "data.label_remark.model_image": "两图差异明显"},
                    "ai_decision": {"score": "0", "reason": "两图差异明显", "confidence": "high"},
                    "temp_draft_result": {"ok": True, "base_resp_status_code": 0},
                }

            adapter = TaskAutoRunResearchChartAdapter(
                ability_store_path=ability_store,
                review_root=root / "reviews",
                state_dir=root / "research-runs",
                ability_runner=fake_ability_runner,
                account_loader=lambda user_id: {
                    "userId": user_id,
                    "name": f"用户{user_id[-4:]}",
                    "cookie": "sessionid=test",
                    "operationUrl": "https://aidp.juejin.cn/operation/task-v2?page=1",
                    "tasks": [{"id": RESEARCH_CHART_TASK_ID, "frontendNotSubmitted": 1, "frontendRepairCount": 0, "poolPendingSubmit": 10}],
                },
            )
            result = start_task_auto_run(
                db,
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=[
                        "account-sample-002",
                        "account-sample-007",
                        "account-sample-004",
                        "account-sample-005",
                        "account-sample-003",
                    ],
                ),
                adapters=[adapter],
                state_dir=root / "generic-runs",
            )

            ticked = adapter.tick(result.adapter_run_id)

            self.assertEqual(ticked.status, "running_auto")
            self.assertEqual(len(ticked.accounts), 5)
            self.assertGreaterEqual(max_concurrent, 5)
        db.close()

    def test_research_chart_preflight_allows_pending_only_accounts_to_start_with_auto_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True)

            result = check_task_auto_run_preflight(
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-004"],
                ),
                adapters=[
                    TaskAutoRunResearchChartAdapter(
                        ability_store_path=ability_store,
                        state_dir=root / "research-runs",
                        evidence_root=root / "evidence",
                        account_loader=lambda _user_id: {
                            "userId": "account-sample-004",
                            "name": "用户待领题",
                            "cookie": "sessionid=test",
                            "operationUrl": "https://aidp.juejin.cn/operation/task-v2?page=1",
                            "tasks": [{"id": RESEARCH_CHART_TASK_ID, "receiveEnable": False, "frontendNotSubmitted": 0, "frontendRepairCount": 0, "poolPendingSubmit": 10}],
                        },
                    )
                ],
            )

            self.assertTrue(result.can_start)
            checks = {item.key: item for item in result.checks}
            self.assertEqual(checks["auto_receive_ready"].status, "passed")

    def test_research_chart_preflight_allows_processing_account_even_when_receive_enable_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True)

            result = check_task_auto_run_preflight(
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[
                    TaskAutoRunResearchChartAdapter(
                        ability_store_path=ability_store,
                        state_dir=root / "research-runs",
                        evidence_root=root / "evidence",
                        account_loader=lambda _user_id: {
                            "userId": "account-sample-002",
                            "name": "用户处理中",
                            "cookie": "sessionid=test",
                            "operationUrl": "https://aidp.juejin.cn/operation/task-v2?page=1",
                            "tasks": [{"id": RESEARCH_CHART_TASK_ID, "receiveEnable": False, "frontendNotSubmitted": 1, "frontendRepairCount": 0, "poolPendingSubmit": 10}],
                        },
                    )
                ],
            )

            self.assertTrue(result.can_start)
            checks = {item.key: item for item in result.checks}
            self.assertEqual(checks["auto_receive_ready"].status, "passed")

    def test_research_chart_running_run_switches_to_latest_published_ability_version(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True, draft_id="research-draft-v1", version="v1")
            ability_calls = []

            def fake_ability_runner(draft_id: str, **kwargs):
                ability_calls.append({"draft_id": draft_id, "kwargs": kwargs})
                return {
                    "ok": True,
                    "task_id": RESEARCH_CHART_TASK_ID,
                    "saved_to_task_ui": True,
                    "writes_remote": True,
                    "submits_remote": False,
                    "question_context": {"item_id": "live-version-item", "source_mode": "live_search_item_category"},
                    "temp_draft_payload": {},
                    "temp_draft_result": {"ok": True, "base_resp_status_code": 0},
                }

            adapter = TaskAutoRunResearchChartAdapter(
                ability_store_path=ability_store,
                review_root=root / "reviews",
                state_dir=root / "research-runs",
                ability_runner=fake_ability_runner,
                account_loader=_account_loader,
            )
            result = start_task_auto_run(
                db,
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[adapter],
                state_dir=root / "generic-runs",
            )

            _write_research_chart_ability_store(ability_store, enabled=True, draft_id="research-draft-v2", version="v2")
            ticked = adapter.tick(result.adapter_run_id)
            refreshed = get_task_auto_run(result.run_id, adapters=[adapter], state_dir=root / "generic-runs")

            self.assertEqual(ability_calls[0]["draft_id"], "research-draft-v2")
            self.assertEqual(ticked.raw_adapter_run["draft_id"], "research-draft-v2")
            self.assertEqual(ticked.raw_adapter_run["ability_version"], "v2")
            self.assertEqual(ticked.raw_adapter_run["previous_ability_version"], "v1")
            self.assertEqual(refreshed.ability_version, "v2")
        db.close()

    def test_research_chart_adapter_formal_submit_gate_verifies_submits_and_readbacks(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True)
            submit_payload = {
                "TaskID": RESEARCH_CHART_TASK_ID,
                "NodeID": "1",
                "AuditAnswers": [
                    {
                        "ItemID": "live-submit-item",
                        "Content": json.dumps({"data": {"label_sorce": {"model_image": "0"}, "label_remark": {"model_image": "差异明显"}}}, ensure_ascii=False),
                    }
                ],
            }

            def fake_ability_runner(_draft_id: str, **_kwargs):
                return {
                    "ok": True,
                    "task_id": RESEARCH_CHART_TASK_ID,
                    "saved_to_task_ui": True,
                    "writes_remote": True,
                    "submits_remote": False,
                    "question_context": {"item_id": "live-submit-item", "source_mode": "live_search_item_category"},
                    "temp_draft_payload": submit_payload,
                    "temp_draft_result": {"ok": True, "base_resp_status_code": 0},
                }

            remote_calls = []

            def fake_transport(_account: dict, kind: str, path: str, body: dict):
                remote_calls.append({"kind": kind, "path": path, "body": body})
                if path == "/dispatcher/verify/submit":
                    return {"statusCode": 200, "elapsedMs": 3, "body": {"BaseResp": {"StatusCode": 0}}}
                if path == "/api/dispatch/SubmitItemAndReceive":
                    return {
                        "statusCode": 200,
                        "elapsedMs": 4,
                        "body": {
                            "BaseResp": {"StatusCode": 0},
                            "SubmitItemResponse": {"BaseResp": {"StatusCode": 0}},
                            "ReceiveResponse": {
                                "BaseResp": {"StatusCode": 0},
                                "Items": [
                                    {
                                        "Item": {
                                            "ItemID": "next-live-item",
                                            "TaskID": RESEARCH_CHART_TASK_ID,
                                            "NodeID": 1,
                                            "Status": 4,
                                        }
                                    }
                                ],
                            },
                        },
                    }
                raise AssertionError(f"unexpected remote path: {path}")

            adapter = TaskAutoRunResearchChartAdapter(
                ability_store_path=ability_store,
                review_root=root / "reviews",
                state_dir=root / "research-runs",
                evidence_root=root / "evidence",
                ability_runner=fake_ability_runner,
                account_loader=_account_loader,
                transport=fake_transport,
            )
            result = start_task_auto_run(
                db,
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[adapter],
                state_dir=root / "generic-runs",
            )

            ticked = adapter.tick(result.adapter_run_id)

            self.assertEqual([call["path"] for call in remote_calls], ["/dispatcher/verify/submit", "/api/dispatch/SubmitItemAndReceive"])
            self.assertEqual(remote_calls[0]["body"]["SubmitItemRequest"]["Answers"][0]["ItemID"], "live-submit-item")
            self.assertEqual(ticked.accounts[0].status, "submitted")
            self.assertEqual(ticked.accounts[0].current_stage, "正式提交并自动领取下一题")
            self.assertTrue(ticked.accounts[0].healthy)
            evidence = ticked.raw_adapter_run["account_evidence"]["account-sample-002"]
            self.assertTrue(evidence["submits_remote"])
            self.assertTrue(evidence["readback_ok"])
            self.assertEqual(evidence["submit_result"]["baseRespStatusCode"], 0)
            self.assertEqual(evidence["next_item_id"], "next-live-item")
            self.assertIn("正式提交", ticked.message)
            detail_files = list((root / "evidence" / "details").glob("*/*.json"))
            self.assertEqual(len(detail_files), 1)
            detail = json.loads(detail_files[0].read_text(encoding="utf-8"))
            self.assertEqual(detail["task_id"], RESEARCH_CHART_TASK_ID)
            self.assertEqual(detail["account_user_id"], "account-sample-002")
            self.assertEqual(detail["item_id"], "live-submit-item")
            self.assertEqual(detail["status"], "submitted")
            self.assertEqual(detail["retention_days"], 7)
            aggregate_files = list((root / "evidence" / "aggregates" / "daily").glob("*.json"))
            self.assertEqual(len(aggregate_files), 1)
            aggregate = json.loads(aggregate_files[0].read_text(encoding="utf-8"))
            key = f"{RESEARCH_CHART_TASK_ID}:account-sample-002"
            self.assertEqual(aggregate["items"][key]["submitted"], 1)
            self.assertEqual(aggregate["items"][key]["last_item_id"], "live-submit-item")
        db.close()

    def test_research_chart_adapter_does_not_submit_when_verify_gate_fails(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True)

            def fake_ability_runner(_draft_id: str, **_kwargs):
                return {
                    "ok": True,
                    "saved_to_task_ui": True,
                    "writes_remote": True,
                    "submits_remote": False,
                    "question_context": {"item_id": "verify-fail-item", "source_mode": "live_search_item_category"},
                    "temp_draft_payload": {
                        "TaskID": RESEARCH_CHART_TASK_ID,
                        "NodeID": "1",
                        "AuditAnswers": [{"ItemID": "verify-fail-item", "Content": "{}"}],
                    },
                }

            remote_calls = []

            def fake_transport(_account: dict, kind: str, path: str, body: dict):
                remote_calls.append({"kind": kind, "path": path, "body": body})
                if path == "/dispatcher/verify/submit":
                    return {"statusCode": 200, "elapsedMs": 3, "body": {"BaseResp": {"StatusCode": 1001, "StatusMessage": "blocked"}}}
                raise AssertionError(f"SubmitItem must not be called after verify failure: {path}")

            adapter = TaskAutoRunResearchChartAdapter(
                ability_store_path=ability_store,
                review_root=root / "reviews",
                state_dir=root / "research-runs",
                ability_runner=fake_ability_runner,
                account_loader=_account_loader,
                transport=fake_transport,
            )
            result = start_task_auto_run(
                db,
                TaskAutoRunStartRequest(
                    task_id=RESEARCH_CHART_TASK_ID,
                    node_id="1",
                    account_user_ids=["account-sample-002"],
                ),
                adapters=[adapter],
                state_dir=root / "generic-runs",
            )

            ticked = adapter.tick(result.adapter_run_id)

            self.assertEqual([call["path"] for call in remote_calls], ["/dispatcher/verify/submit"])
            self.assertEqual(ticked.status, "blocked")
            self.assertEqual(ticked.accounts[0].status, "isolated_failed")
            self.assertIn("提交前校验失败", ticked.accounts[0].last_error)
            evidence = ticked.raw_adapter_run["account_evidence"]["account-sample-002"]
            self.assertFalse(evidence["submits_remote"])
            self.assertEqual(evidence["verify_result"]["baseRespStatusCode"], 1001)
        db.close()

    def test_task_auto_run_routes_start_read_and_stop_generic_run(self) -> None:
        class FakeAdapter:
            adapter_key = "fake"
            supported_task_ids = {"fake-task"}

            def preflight(self, request):
                return TaskAutoRunPreflightResponse(
                    generated_at=utc_now(),
                    task_id=request.task_id,
                    node_id=request.node_id,
                    adapter_key=self.adapter_key,
                    status="ready",
                    can_start=True,
                    runnable_account_count=len(request.account_user_ids),
                    checks=[
                        TaskAutoRunPreflightCheck(
                            key="adapter_ready",
                            title="执行器",
                            status="passed",
                            detail="fake adapter ready",
                        )
                    ],
                    message="自检通过；该检查不会提交、暂存或领取题目。",
                    next_step="可以启动自动做题。",
                )

            def start(self, _db, request):
                return TaskAutoRunAdapterSnapshot(
                    adapter_key=self.adapter_key,
                    adapter_run_id="adapter-run-1",
                    task_id=request.task_id,
                    node_id=request.node_id,
                    status="running_auto",
                    stop_requested=False,
                    accounts=[
                        TaskAutoRunAccountState(
                            account_user_id=request.account_user_ids[0],
                            status="running_auto",
                            current_stage="执行中",
                        )
                    ],
                    last_error="",
                    next_step="继续观察运行状态。",
                    message="fake adapter started",
                    raw_adapter_run={"ok": True},
                )

            def get(self, adapter_run_id):
                return TaskAutoRunAdapterSnapshot(
                    adapter_key=self.adapter_key,
                    adapter_run_id=adapter_run_id,
                    task_id="fake-task",
                    node_id="1",
                    status="running_auto",
                    stop_requested=False,
                    accounts=[TaskAutoRunAccountState(account_user_id="account-1", status="running_auto")],
                    last_error="",
                    next_step="继续观察运行状态。",
                    message="fake adapter refreshed",
                    raw_adapter_run={"ok": True},
                )

            def stop(self, adapter_run_id):
                snapshot = self.get(adapter_run_id)
                snapshot.status = "stopped"
                snapshot.stop_requested = True
                snapshot.message = "fake adapter stopped"
                return snapshot

        db = _session()
        app = FastAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            app.state.task_auto_run_adapters = [FakeAdapter()]
            app.state.task_auto_run_state_dir = Path(temp_dir)
            app.dependency_overrides[get_db] = lambda: db
            app.include_router(api_router, prefix="/api/v1")
            with TestClient(app) as client:
                preflight = client.post(
                    "/api/v1/task-auto-runs/preflight",
                    json={"task_id": "fake-task", "node_id": "1", "account_user_ids": ["account-1"]},
                )
                self.assertEqual(preflight.status_code, 200, preflight.text)
                self.assertTrue(preflight.json()["can_start"])
                self.assertEqual(preflight.json()["checks"][0]["key"], "adapter_ready")
                self.assertIn("不会提交", preflight.json()["message"])

                started = client.post(
                    "/api/v1/task-auto-runs/start",
                    json={"task_id": "fake-task", "node_id": "1", "account_user_ids": ["account-1"]},
                )
                self.assertEqual(started.status_code, 200, started.text)
                self.assertEqual(started.json()["adapter_key"], "fake")
                self.assertEqual(started.json()["status"], "running_auto")

                run_id = started.json()["run_id"]
                fetched = client.get(f"/api/v1/task-auto-runs/runs/{run_id}")
                self.assertEqual(fetched.status_code, 200, fetched.text)
                self.assertEqual(fetched.json()["run_id"], run_id)
                self.assertEqual(fetched.json()["adapter_run_id"], "adapter-run-1")

                active = client.get("/api/v1/task-auto-runs/active", params={"task_id": "fake-task", "account_user_ids": ["account-1"]})
                self.assertEqual(active.status_code, 200, active.text)
                self.assertEqual(active.json()["run_id"], run_id)
                self.assertEqual(active.json()["task_id"], "fake-task")

                stopped = client.post(f"/api/v1/task-auto-runs/runs/{run_id}/stop")
                self.assertEqual(stopped.status_code, 200, stopped.text)
                self.assertEqual(stopped.json()["status"], "stopped")
                self.assertTrue(stopped.json()["stop_requested"])
        db.close()

    def test_task_auto_run_worker_start_invokes_research_chart_adapter_tick(self) -> None:
        db = _session()
        app = FastAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ability_store = root / "task-abilities" / "ability-drafts.json"
            _write_research_chart_ability_store(ability_store, enabled=True)
            tick_calls = []

            def fake_ability_runner(draft_id: str, **kwargs):
                tick_calls.append({"draft_id": draft_id, "kwargs": kwargs})
                return {
                    "ok": True,
                    "task_id": RESEARCH_CHART_TASK_ID,
                    "stage": "端到端做题不提交：已暂存待人工审核",
                    "saved_to_task_ui": True,
                    "writes_remote": True,
                    "submits_remote": False,
                    "question_context": {"item_id": "live-route-item", "source_mode": "live_search_item_category"},
                    "saved_answer": {"data.label_sorce.model_image": "0", "data.label_remark.model_image": "差异明显"},
                    "ai_decision": {"score": "0", "reason": "差异明显", "confidence": "high"},
                    "temp_draft_result": {"ok": True, "base_resp_status_code": 0},
                }

            app.state.task_auto_run_adapters = [
                TaskAutoRunResearchChartAdapter(
                    ability_store_path=ability_store,
                    review_root=root / "reviews",
                    state_dir=root / "research-runs",
                    ability_runner=fake_ability_runner,
                    account_loader=_account_loader,
                )
            ]
            app.state.task_auto_run_state_dir = root / "generic-runs"
            app.dependency_overrides[get_db] = lambda: db
            app.include_router(api_router, prefix="/api/v1")
            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/task-auto-runs/start",
                    json={"task_id": RESEARCH_CHART_TASK_ID, "node_id": "1", "account_user_ids": ["account-sample-002"]},
                )
                self.assertEqual(started.status_code, 200, started.text)
                run_id = started.json()["run_id"]

                worker = client.post(f"/api/v1/task-auto-runs/runs/{run_id}/worker/start", json={"interval_seconds": 1})
                self.assertEqual(worker.status_code, 200, worker.text)
                self.assertTrue(worker.json()["last_ok"])
                self.assertEqual(worker.json()["cycle_count"], 1)
                self.assertFalse(tick_calls[0]["kwargs"]["use_system_ai_for_vision"])
                self.assertTrue(worker.json()["active"])

                fetched = client.get(f"/api/v1/task-auto-runs/runs/{run_id}")
                self.assertEqual(fetched.json()["accounts"][0]["status"], "temp_saved_waiting_submit")
                self.assertEqual(fetched.json()["accounts"][0]["current_item_id"], "live-route-item")
                self.assertEqual(len(tick_calls), 1)
                worker_status = client.get(f"/api/v1/task-auto-runs/runs/{run_id}/worker/status")
                self.assertEqual(worker_status.status_code, 200, worker_status.text)
                self.assertTrue(worker_status.json()["active"])
        db.close()

    def test_task_auto_run_worker_start_runs_bon8_first_tick_immediately(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        app = FastAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tick_calls: list[str] = []
            app.state.task_auto_run_adapters = [TaskAutoRunBon8Adapter(account_loader=_account_loader, transport=_category_transport)]
            app.state.task_auto_run_state_dir = root / "generic-runs"
            from app.services.bon8_worker_service import Bon8RunWorkerRegistry

            app.state.bon8_run_worker_registry = Bon8RunWorkerRegistry(tick_func=lambda run_id: tick_calls.append(run_id) or {"ok": True})
            app.dependency_overrides[get_db] = lambda: db
            app.include_router(api_router, prefix="/api/v1")
            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/task-auto-runs/start",
                    json={"task_id": "7637771731901861641", "node_id": "1", "account_user_ids": ["account-sample-002"], "write_audit": False},
                )
                self.assertEqual(started.status_code, 200, started.text)
                run_id = started.json()["run_id"]

                worker = client.post(f"/api/v1/task-auto-runs/runs/{run_id}/worker/start", json={"interval_seconds": 1})
                self.assertEqual(worker.status_code, 200, worker.text)
                self.assertTrue(worker.json()["last_ok"])
                self.assertEqual(worker.json()["cycle_count"], 1)
                self.assertTrue(worker.json()["active"])
                self.assertEqual(tick_calls, [started.json()["adapter_run_id"]])
        db.close()


if __name__ == "__main__":
    unittest.main()
