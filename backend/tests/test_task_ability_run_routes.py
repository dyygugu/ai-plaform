import importlib
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _create_app(tmpdir: str):
    os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{Path(tmpdir) / 'aidp-test.db'}"
    os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(Path(tmpdir) / "operation-recordings")
    os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
    os.environ["AIDP_PRODUCTION_STATE_PATH"] = str(Path(tmpdir) / "production-state.json")
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    main_module = importlib.import_module("app.main")
    return main_module.create_app()


def test_task_ability_approve_trial_and_production_routes() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(tmpdir)
        with patch("app.api.v1.routes.task_abilities.approve_task_ability_version_by_task", return_value={"ok": True, "status": "有做题能力", "flow_stage": "capability_enabled", "capability_enabled": True}):
            with patch("app.api.v1.routes.task_abilities.get_task_ability_run_gate", side_effect=[
                {"can_start_production": True, "next_step": "", "task_id": "7639402643386830630"},
            ]):
                with patch("app.api.v1.routes.task_abilities.start_task_auto_run", return_value={"run_id": "task-auto-1", "adapter_key": "research_chart", "adapter_run_id": "research-chart-1", "task_id": "7639402643386830630", "node_id": "1", "ability_version": "ability-v8", "status": "running_auto", "stop_requested": False, "selected_account_count": 1, "healthy_account_count": 1, "abnormal_account_count": 0, "health_ok": True, "accounts": [], "last_error": "", "next_step": "", "message": "ok", "raw_adapter_run": {}, "generated_at": "2026-05-16T00:00:00+00:00"}):
                    with patch("app.api.v1.routes.task_abilities.record_task_ability_run") as record_run:
                        with TestClient(app) as client:
                            approve = client.post("/api/v1/task-abilities/7639402643386830630/approve", json={})
                            assert approve.status_code == 200, approve.text
                            trial = client.post("/api/v1/task-abilities/7639402643386830630/trial-run", json={"account_user_ids": ["account-sample-002"], "node_id": "1"})
                            assert trial.status_code == 200, trial.text
                            production = client.post("/api/v1/task-abilities/7639402643386830630/production-run", json={"account_user_ids": ["account-sample-002"], "node_id": "1"})
                            assert production.status_code == 200, production.text
                            assert record_run.call_count == 2


def test_task_ability_workbench_routes_and_production_gate() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(tmpdir)
        with patch("app.api.v1.routes.task_abilities.list_prompt_snapshots", return_value=[{"snapshot_id": "prompt-1"}]):
            with patch("app.api.v1.routes.task_abilities.get_latest_task_ability_live_http_test_report", return_value={"report_id": "live-1", "saved_to_task_ui": True}):
                with patch("app.api.v1.routes.task_abilities.get_task_ability_run_gate", return_value={"task_id": "7639402643386830630", "can_start_production": False, "next_step": "请先完成试运行并人工确认"}):
                    with patch("app.api.v1.routes.task_abilities.start_task_auto_run") as start_run:
                        with TestClient(app) as client:
                            snapshots = client.get("/api/v1/task-abilities/7639402643386830630/prompt/snapshots")
                            assert snapshots.status_code == 200, snapshots.text
                            latest = client.get("/api/v1/task-abilities/7639402643386830630/live-http-test/latest")
                            assert latest.status_code == 200, latest.text
                            gate = client.get("/api/v1/task-abilities/7639402643386830630/run-gate")
                            assert gate.status_code == 200, gate.text
                            production = client.post("/api/v1/task-abilities/7639402643386830630/production-run", json={"account_user_ids": ["account-sample-002"], "node_id": "1"})
                            assert production.status_code == 400, production.text
                            start_run.assert_not_called()


def test_task_ability_step2_routes() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(tmpdir)
        captured = {}

        def fake_chat(db, task_id, payload):
            captured["task_id"] = task_id
            captured["payload"] = payload
            return {"trace_id": "trace-1", "provider_status": "local", "answer": "ok", "context_summary": {}, "message": "ok"}

        with patch("app.api.v1.routes.task_abilities.chat_task_ability", side_effect=fake_chat):
            with patch("app.api.v1.routes.task_abilities.replay_task_ability_testset", return_value={"task_id": "7639402643386830630", "sample_count": 1, "items": [{"uid": "uid-1"}]}):
                with patch("app.api.v1.routes.task_abilities.build_task_ability_payload_debug", return_value={"task_id": "7639402643386830630", "uid": "uid-1", "payload_preview": {"TaskID": "7639402643386830630"}}):
                    with patch("app.api.v1.routes.task_abilities.list_task_learning_packages", return_value=type("Resp", (), {"model_dump": lambda self, mode='json': {"task_id": "7639402643386830630", "selected_learning_package_id": "rec-1", "items": [{"learning_package_id": "rec-1"}]}})()):
                        with patch("app.api.v1.routes.task_abilities.save_selected_learning_package", return_value=type("Resp", (), {"model_dump": lambda self, mode='json': {"task_id": "7639402643386830630", "selected_learning_package_id": "rec-1", "message": "已切换当前学习包。"}})()):
                            with TestClient(app) as client:
                                 chat = client.post("/api/v1/task-abilities/7639402643386830630/chat", json={"message": "请优化提示词", "history": [], "use_provider": False, "selected_learning_package_id": "rec-1"})
                                 assert chat.status_code == 200, chat.text
                                 assert captured["payload"]["selected_learning_package_id"] == "rec-1"
                                 packages = client.get("/api/v1/task-abilities/7639402643386830630/learning-packages")
                                 assert packages.status_code == 200, packages.text
                                 selected = client.post("/api/v1/task-abilities/7639402643386830630/selected-learning-package", json={"learning_package_id": "rec-1"})
                                 assert selected.status_code == 200, selected.text
                                 selected_alias = client.post(
                                     "/api/v1/task-abilities/7639402643386830630/selected-learning-package",
                                     json={"selected_learning_package_id": "rec-1"},
                                 )
                                 assert selected_alias.status_code == 200, selected_alias.text
                                 replay = client.get("/api/v1/task-abilities/7639402643386830630/replay")
                                 assert replay.status_code == 200, replay.text
                                 payload = client.get("/api/v1/task-abilities/7639402643386830630/payload-preview/uid-1")
                                 assert payload.status_code == 200, payload.text


def test_task_ability_run_config_route_and_trial_run_payload() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(tmpdir)
        captured = {}

        def fake_start(_db, request, **_kwargs):
            captured["run_config"] = getattr(request, "run_config", {})
            return {"run_id": "task-auto-1", "adapter_key": "research_chart", "adapter_run_id": "research-chart-1", "task_id": "7639402643386830630", "node_id": "1", "ability_version": "ability-v8", "status": "running_auto", "stop_requested": False, "selected_account_count": 1, "healthy_account_count": 1, "abnormal_account_count": 0, "health_ok": True, "accounts": [], "last_error": "", "next_step": "", "message": "ok", "raw_adapter_run": {}, "generated_at": "2026-05-16T00:00:00+00:00"}

        with patch("app.api.v1.routes.task_abilities.update_task_ability_run_config", return_value={"mode": "safe", "rate_limit_per_minute": 5, "trial_max_items_per_account": 3, "production_max_items_per_account": 50, "consecutive_fail_threshold": 3}):
            with patch("app.api.v1.routes.task_abilities.start_task_auto_run", side_effect=fake_start):
                with patch("app.api.v1.routes.task_abilities.record_task_ability_run"):
                    with TestClient(app) as client:
                        config_resp = client.put("/api/v1/task-abilities/7639402643386830630/run-config", json={"mode": "safe", "rate_limit_per_minute": 5, "trial_max_items_per_account": 3, "production_max_items_per_account": 50, "consecutive_fail_threshold": 3})
                        assert config_resp.status_code == 200, config_resp.text
                        trial = client.post(
                            "/api/v1/task-abilities/7639402643386830630/trial-run",
                            json={
                                "account_user_ids": ["account-sample-002"],
                                "node_id": "1",
                                "run_config": {
                                    "mode": "safe",
                                    "rate_limit_per_minute": 5,
                                    "trial_max_items_per_account": 3,
                                    "production_max_items_per_account": 50,
                                    "consecutive_fail_threshold": 3,
                                },
                            },
                        )
                        assert trial.status_code == 200, trial.text
                        assert captured["run_config"]["trial_max_items_per_account"] == 3


def test_task_ability_prompt_task_route_and_replay_report_routes() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(tmpdir)
        with patch("app.api.v1.routes.task_abilities.get_task_ability_draft_by_task", return_value={"task_id": "7639402643386830630", "task_name": "科研图"}):
            with patch("app.api.v1.routes.task_abilities.update_task_ability_prompt_by_task", return_value={"task_id": "7639402643386830630", "draft_id": "draft-1", "flow_stage": "real_no_submit_ready"}):
                with patch("app.api.v1.routes.task_abilities.create_task_ability_replay_report", return_value={"task_id": "7639402643386830630", "report_id": "replay-1", "sample_count": 1, "items": []}):
                    with patch("app.api.v1.routes.task_abilities.get_task_ability_replay_report", return_value={"task_id": "7639402643386830630", "report_id": "replay-1", "sample_count": 1, "items": []}):
                        with patch("app.api.v1.routes.task_abilities.build_task_ability_payload_debug", return_value={"task_id": "7639402643386830630", "uid": "uid-1", "payload_preview": {"TaskID": "7639402643386830630"}}):
                            with TestClient(app) as client:
                                read_task = client.get("/api/v1/task-abilities/7639402643386830630")
                                assert read_task.status_code == 200, read_task.text
                                update_prompt = client.put("/api/v1/task-abilities/7639402643386830630/prompt", json={"system_ai_draft": "new prompt"})
                                assert update_prompt.status_code == 200, update_prompt.text
                                replay = client.post("/api/v1/task-abilities/7639402643386830630/replay", json={})
                                assert replay.status_code == 200, replay.text
                                replay_report = client.get("/api/v1/task-abilities/7639402643386830630/replay/replay-1")
                                assert replay_report.status_code == 200, replay_report.text
                                payload = client.post("/api/v1/task-abilities/7639402643386830630/payload/preview", json={"uid": "uid-1"})
                                assert payload.status_code == 200, payload.text


def test_auto_answer_runs_alias_routes() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(tmpdir)
        with patch("app.api.v1.routes.auto_answer_runs.get_task_auto_run", return_value=type("Run", (), {"run_id": "task-auto-1", "adapter_key": "research_chart", "adapter_run_id": "research-chart-1"})()):
            with patch("app.api.v1.routes.auto_answer_runs.stop_task_auto_run", return_value=type("Stopped", (), {"model_dump": lambda self, mode='json': {"run_id": "task-auto-1", "status": "stopped"}})()):
                with patch("app.api.v1.routes.task_auto_runs._generic_worker_registry") as generic_registry:
                    generic_registry.return_value.stop = AsyncMock(return_value=type("WorkerStatus", (), {"active": False, "running": False, "cycle_count": 0, "last_ok": True, "last_error": None, "last_started_at": None, "last_finished_at": None, "interval_seconds": 5, "next_run_at": None})())
                    with patch("app.api.v1.routes.task_auto_runs._generic_worker_status_response", return_value=type("Resp", (), {"model_dump": lambda self, mode='json': {"run_id": "task-auto-1", "active": False}})()):
                        with TestClient(app) as client:
                            paused = client.post("/api/v1/auto-answer-runs/task-auto-1/pause", json={})
                            assert paused.status_code == 200, paused.text
                            stopped = client.post("/api/v1/auto-answer-runs/task-auto-1/stop", json={})
                            assert stopped.status_code == 200, stopped.text
