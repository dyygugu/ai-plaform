import importlib
import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


def account_fixtures() -> list[dict[str, object]]:
    return [
        {"userId": "account-sample-001", "name": "用户样例001", "enabled": True, "authMode": "client-cookie", "cookie": "redacted"},
        {"userId": "account-sample-002", "name": "用户样例002", "enabled": True, "authMode": "client-cookie", "cookie": "redacted"},
        {"userId": "account-sample-003", "name": "用户样例003", "enabled": True, "authMode": "client-cookie", "cookie": "redacted"},
        {"userId": "account-sample-004", "name": "用户样例004", "enabled": True, "authMode": "client-cookie", "needsRelogin": True, "cookie": "redacted"},
        {"userId": "account-sample-005", "name": "用户样例005", "enabled": True, "authMode": "client-cookie", "cookie": "redacted"},
        {"userId": "account-sample-006", "name": "用户样例006", "enabled": True, "authMode": "client-cookie", "cookie": "redacted"},
        {"userId": "account-sample-007", "name": "用户样例007", "enabled": True, "authMode": "client-cookie", "cookie": "redacted"},
    ]


def write_native_account_fixtures(tmpdir: str) -> tuple[Path, Path]:
    production_state_path = Path(tmpdir) / "production-state.json"
    session_accounts_path = Path(tmpdir) / "session-accounts.json"
    accounts = account_fixtures()
    production_state_path.write_text(json.dumps({"accounts": accounts, "stale": False}, ensure_ascii=False), encoding="utf-8")
    session_accounts_path.write_text(json.dumps({"accounts": accounts}, ensure_ascii=False), encoding="utf-8")
    return production_state_path, session_accounts_path

def configure_test_env(tmpdir: str) -> None:
    production_state_path, session_accounts_path = write_native_account_fixtures(tmpdir)
    os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{Path(tmpdir) / 'aidp-test.db'}"
    os.environ["AIDP_BACKUP_LOCAL_ROOT"] = str(Path(tmpdir) / "backups")
    os.environ["AIDP_TASK_SAMPLE_ROOT"] = str(Path(tmpdir) / "samples")
    os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(Path(tmpdir) / "operation-recordings")
    os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
    os.environ["AIDP_PRODUCTION_STATE_PATH"] = str(production_state_path)
    os.environ["AIDP_SESSION_ACCOUNTS_PATH"] = str(session_accounts_path)
    os.environ["AIDP_AI_RUNTIME_CONFIG_PATH"] = str(Path(tmpdir) / "ai-runtime-config.json")
    os.environ["AIDP_PUBLIC_BASE_URL"] = "http://127.0.0.1:8789"



def ensure_pending_confirmation_fixture(source_trace_id: str, ai_job_id, context: dict[str, object], action_key: str = "switch_domain") -> None:
    session_module = importlib.import_module("app.db.session")
    schemas_module = importlib.import_module("app.schemas.ai")
    confirmation_module = importlib.import_module("app.services.ai_confirmation_service")
    db = session_module.SessionLocal()
    try:
        action = schemas_module.AiIncidentAction(
            key=action_key,
            title="Smoke 高危动作确认",
            risk_level="high",
            status="requires_confirmation",
            requires_confirmation=True,
            allowed_by_policy=False,
            message="Smoke 验证：正式域名切换必须进入确认队列。",
            rollback_hint="保持当前域名和反代配置不变。",
        )
        confirmation_module.create_confirmation_requests(db, source_trace_id, ai_job_id, [action], context, write_audit_enabled=True)
        db.commit()
    finally:
        db.close()

def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        configure_test_env(tmpdir)
        settings_module = importlib.import_module("app.core.settings")
        settings_module.get_settings.cache_clear()
        main_module = importlib.import_module("app.main")
        app = main_module.create_app()
        with TestClient(app) as client:
            assert client.get("/api/v1/health").json()["status"] == "ok"
            for account in account_fixtures():
                session = client.post(
                    "/api/v1/accounts/client-session",
                    json={
                        "authoritativeUserId": account["userId"],
                        "authoritativeName": account["name"],
                        "cookie": account["cookie"],
                        "referer": "https://aidp.juejin.cn/operation/tasks",
                        "syncedFrom": "8789-native-smoke",
                    },
                )
                assert session.status_code == 200, session.text
            accounts = client.get("/api/v1/accounts")
            assert accounts.status_code == 200, accounts.text
            assert accounts.json()[0]["user_id"] == "account-sample-002"
            account_health = client.post("/api/v1/accounts/refresh-health")
            assert account_health.status_code == 200, account_health.text
            assert client.get("/api/v1/accounts/legacy-migration/preview").status_code == 404
            native_accounts = client.get("/api/v1/accounts")
            assert native_accounts.status_code == 200, native_accounts.text
            assert len(native_accounts.json()) == 7
            production_dashboard = client.get("/api/v1/accounts/production-dashboard")
            assert production_dashboard.status_code == 200, production_dashboard.text
            assert production_dashboard.json()["account_count"] == 7
            assert all(not account["display_name"].startswith("账号-") for account in production_dashboard.json()["accounts"])
            production_refresh_status = client.get("/api/v1/accounts/refresh-production/status")
            assert production_refresh_status.status_code == 200, production_refresh_status.text
            assert production_refresh_status.json()["enabled"] is False
            seed = client.post(
                "/api/v1/tasks/catalog/seed",
                json={
                    "raw_task_name": "RFT人标_美观度（6.5万）7634515789236309806",
                    "task_status_raw": "进行中",
                    "pending_raw": "123",
                },
            )
            assert seed.status_code == 200, seed.text
            assert seed.json()["item"]["task_name_id"] == "美观度（6.5万）7634515789236309806"
            catalog = client.get("/api/v1/tasks/catalog")
            assert catalog.status_code == 200, catalog.text
            assert catalog.json()["items"][0]["pending_raw"] == "123"
            detail = client.get(f"/api/v1/tasks/catalog/{catalog.json()['items'][0]['id']}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["covered_account_count"] == 1
            rules = client.get("/api/v1/tasks/rules")
            assert rules.status_code == 200, rules.text
            assert "RFT人标_" in rules.json()["prefix_rules"]
            update_rules = client.put("/api/v1/tasks/rules", json={"manual_short_names": {"7634515789236309806": "美观度人工简称"}})
            assert update_rules.status_code == 200, update_rules.text
            task_source = client.put("/api/v1/settings/task-source", json={"task_source_account_user_id": "account-sample-002"})
            assert task_source.status_code == 200, task_source.text
            refresh = client.post(
                "/api/v1/tasks/catalog/refresh",
                json={"sample_payload": {"tasks": [{"title": "RFT人标_美观度（6.5万）", "taskId": "7634515789236309806", "pendingRaw": "4879"}]}},
            )
            assert refresh.status_code == 200, refresh.text
            task_refresh_worker = client.get("/api/v1/workers/task-refresh-api")
            assert task_refresh_worker.status_code == 200, task_refresh_worker.text
            assert any('"stage":"task_refresh"' in item["message"] and '"step":"finish"' in item["message"] for item in task_refresh_worker.json()["log_summary"]["events"])
            assert refresh.json()["imported_count"] == 1
            refreshed_catalog = client.get("/api/v1/tasks/catalog")
            assert refreshed_catalog.json()["stale"] is True
            assert refreshed_catalog.json()["items"][0]["pending_raw"] == ""
            coverage_summary = client.get("/api/v1/accounts/task-coverage/summary")
            assert coverage_summary.status_code == 200, coverage_summary.text
            assert coverage_summary.json()["account_count"] == 7
            assert coverage_summary.json()["source_task_count"] >= 1
            assert len(coverage_summary.json()["matrix"]) == 7
            coverage_matrix = client.get("/api/v1/accounts/task-coverage/matrix")
            assert coverage_matrix.status_code == 200, coverage_matrix.text
            coverage_baseline = client.post("/api/v1/accounts/task-coverage/baseline", json={"write_audit": True, "generate_report": True})
            assert coverage_baseline.status_code == 200, coverage_baseline.text
            assert coverage_baseline.json()["report_path"].endswith(".md")
            sample = client.post("/api/v1/tasks/task-page/sample-capture", json={})
            assert sample.status_code == 200, sample.text
            assert sample.json()["sample_saved"] is True
            backup_test = client.post("/api/v1/backups/test-local")
            assert backup_test.status_code == 200, backup_test.text
            backup = client.post("/api/v1/backups/manual")
            assert backup.status_code == 200, backup.text
            assert backup.json()["status"] == "completed"
            assert client.get("/api/v1/ai/queue").json()["total"] >= 2
            worker_contract = client.get("/api/v1/workers/event-contract")
            assert worker_contract.status_code == 200, worker_contract.text
            assert "ai_draft" in {item["stage"] for item in worker_contract.json()["stages"]}
            assert "AI_PROVIDER_502" in worker_contract.json()["error_codes"]
            heartbeat = client.post("/api/v1/workers/heartbeat", json={"worker_id": "test-worker", "version": "0.1.0"})
            assert heartbeat.status_code == 200, heartbeat.text
            assert heartbeat.json()["status"] == "online"
            bind = client.post("/api/v1/workers/test-worker/bind-account", json={"account_user_id": "account-sample-002"})
            assert bind.status_code == 200, bind.text
            version = client.post("/api/v1/workers/test-worker/version", json={"target_version": "0.1.0-p7"})
            assert version.status_code == 200, version.text
            claim = client.post("/api/v1/workers/test-worker/claim-task", json={"task_id": "7634515789236309806", "account_user_id": "account-sample-002"})
            assert claim.status_code == 200, claim.text
            event = client.post("/api/v1/workers/events", json={"worker_id": "test-worker", "event_type": "log_summary", "severity": "warning", "stage": "worker_runtime", "step": "log_summary", "message": "smoke log"})
            assert event.status_code == 200, event.text
            recording = client.post("/api/v1/operation-recordings", json={"mode": "full", "source": "smoke", "events": [{"type": "click"}], "network": [{"url": "/api?token=secret"}]})
            assert recording.status_code == 200, recording.text
            assert recording.json()["network_count"] == 1
            detail = client.get("/api/v1/workers/test-worker")
            assert detail.status_code == 200, detail.text
            assert detail.json()["log_summary"]["total_events"] >= 4
            rules_center = client.get("/api/v1/rules/center")
            assert rules_center.status_code == 200, rules_center.text
            rule_id = rules_center.json()["versions"][0]["id"]
            diff = client.get(f"/api/v1/rules/versions/{rule_id}/diff")
            assert diff.status_code == 200, diff.text
            canary = client.post(f"/api/v1/rules/versions/{rule_id}/canary", json={"canary_percent": 20})
            assert canary.status_code == 200, canary.text
            publish = client.post(f"/api/v1/rules/versions/{rule_id}/publish", json={})
            assert publish.status_code == 200, publish.text
            rollback = client.post(f"/api/v1/rules/versions/{rule_id}/rollback", json={})
            assert rollback.status_code == 200, rollback.text
            assert len(client.get("/api/v1/audit/logs").json()) >= 1
            alert = client.post("/api/v1/alerts/preview", json={})
            assert alert.status_code == 200, alert.text
            assert "trace_id" in alert.json()["text"]
            alert_rules = client.get("/api/v1/alerts/rules")
            assert alert_rules.status_code == 200, alert_rules.text
            alert_rule_keys = {item["key"] for item in alert_rules.json()}
            assert len(alert_rules.json()) >= 9
            assert "score_submit_confirmation_pending" in alert_rule_keys
            assert "score_unknown_type_paused" in alert_rule_keys
            assert "score_review_backlog" in alert_rule_keys
            alert_slo = client.get("/api/v1/alerts/slo")
            assert alert_slo.status_code == 200, alert_slo.text
            assert len(alert_slo.json()["indicators"]) >= 9
            alert_summary = client.get("/api/v1/alerts/summary")
            assert alert_summary.status_code == 200, alert_summary.text
            assert alert_summary.json()["external_send_enabled"] is False
            alert_eval = client.post("/api/v1/alerts/evaluate", json={"dry_run": True, "write_audit": True, "send_external": False})
            assert alert_eval.status_code == 200, alert_eval.text
            assert alert_eval.json()["external_send_enabled"] is False
            ai_config_initial = client.get("/api/v1/ai/config")
            assert ai_config_initial.status_code == 200, ai_config_initial.text
            assert ai_config_initial.json()["system_ai"]["api_key_configured"] is False
            ai_config_update = client.put(
                "/api/v1/ai/config",
                json={
                    "system_ai": {
                        "base_url": "https://api.example-system.local/v1",
                        "api_key": "test-system-smoke-key",
                        "model": "system-model-smoke",
                        "timeout_seconds": 20,
                        "pre_prompt": "系统 AI 负责运维和配置。",
                        "skills": ["incident-review"],
                        "md_files": ["app/prompts/incident_ai_operator.md"],
                    },
                    "task_ai": {
                        "base_url": "https://api.example-task.local/v1",
                        "api_key": "test-task-smoke-key",
                        "model": "task-model-smoke",
                        "timeout_seconds": 25,
                        "pre_prompt": "做题 AI 仅生成评分草稿。",
                        "skills": ["score-draft"],
                        "md_files": ["notes/projects/aidp-monitor.md"],
                    },
                    "task_ai_managed_by_system_ai": True,
                },
            )
            assert ai_config_update.status_code == 200, ai_config_update.text
            assert ai_config_update.json()["system_ai"]["api_key_configured"] is True
            assert ai_config_update.json()["task_ai"]["api_key_configured"] is True
            assert "test-system-smoke-key" not in ai_config_update.text
            assert "test-task-smoke-key" not in ai_config_update.text
            ai_config_check = client.get("/api/v1/ai/config/check")
            assert ai_config_check.status_code == 200, ai_config_check.text
            assert ai_config_check.json()["status"] == "passed"
            assert ai_config_check.json()["ready_for_system_chat"] is True
            assert ai_config_check.json()["ready_for_task_draft"] is True
            assert "test-system-smoke-key" not in ai_config_check.text
            assert "test-task-smoke-key" not in ai_config_check.text
            ai_chat = client.post("/api/v1/ai/chat", json={"message": "配置后做一次本地聊天自检", "use_provider": False})
            assert ai_chat.status_code == 200, ai_chat.text
            assert ai_chat.json()["provider_status"] == "local_policy"
            incident_ai = client.post("/api/v1/ai/incidents/review", json={"dry_run": True, "allow_high_risk": False, "write_audit": True, "generate_report": False, "use_provider": False})
            assert incident_ai.status_code == 200, incident_ai.text
            incident_ai_json = incident_ai.json()
            assert "前置上下文" in incident_ai_json["permission_model"]
            assert "项目功能地图" in incident_ai_json["guardrail_summary"]
            assert incident_ai_json["context_summary"]["operator_context_file"] == "app/prompts/incident_ai_operator.md"
            assert incident_ai_json["context_summary"]["operator_context_loaded"] is True
            if incident_ai_json["confirmation_request_count"] < 1:
                ensure_pending_confirmation_fixture(incident_ai_json["trace_id"], incident_ai_json["ai_job_id"], incident_ai_json["context_summary"])
            pending_confirmations = client.get("/api/v1/ai/confirmations", params={"status": "pending"})
            assert pending_confirmations.status_code == 200, pending_confirmations.text
            assert pending_confirmations.json()["pending"] >= 1
            confirmation_item = pending_confirmations.json()["items"][0]
            bad_approval = client.post(f"/api/v1/ai/confirmations/{confirmation_item['id']}/approve", json={"operator": "smoke", "note": "bad phrase", "confirm_text": "WRONG", "write_audit": True})
            assert bad_approval.status_code == 400, bad_approval.text
            approval = client.post(f"/api/v1/ai/confirmations/{confirmation_item['id']}/approve", json={"operator": "smoke", "note": "smoke approve", "confirm_text": confirmation_item["confirm_phrase"], "write_audit": True})
            assert approval.status_code == 200, approval.text
            assert approval.json()["item"]["status"] == "approved"
            assert approval.json()["audit_trace_id"]
            assert "不自动执行" in approval.json()["message"]
            incident_ai_second = client.post("/api/v1/ai/incidents/review", json={"dry_run": True, "allow_high_risk": False, "write_audit": True, "generate_report": False, "use_provider": False})
            assert incident_ai_second.status_code == 200, incident_ai_second.text
            incident_ai_second_json = incident_ai_second.json()
            if incident_ai_second_json["confirmation_request_count"] < 1:
                ensure_pending_confirmation_fixture(incident_ai_second_json["trace_id"], incident_ai_second_json["ai_job_id"], incident_ai_second_json["context_summary"])
            pending_confirmations_second = client.get("/api/v1/ai/confirmations", params={"status": "pending"})
            assert pending_confirmations_second.status_code == 200, pending_confirmations_second.text
            reject_item = pending_confirmations_second.json()["items"][0]
            rejection = client.post(f"/api/v1/ai/confirmations/{reject_item['id']}/reject", json={"operator": "smoke", "note": "smoke reject", "write_audit": True})
            assert rejection.status_code == 200, rejection.text
            assert rejection.json()["item"]["status"] == "rejected"
            score_summary = client.get("/api/v1/score-loop/summary")
            assert score_summary.status_code == 200, score_summary.text
            assert score_summary.json()["task_type_key"] == "rft_aesthetic_v1"
            readiness_checks = {item["key"]: item for item in score_summary.json()["readiness_checks"]}
            assert readiness_checks["real_question_available"]["status"] == "blocked"
            assert "不能探测" in readiness_checks["real_question_available"]["detail"]
            assert readiness_checks["safe_score_loop_scaffold"]["status"] == "passed"
            unsupported_capture = client.post("/api/v1/score-loop/cases/capture", json={"task_type_key": "unknown_type", "task_type_name": "未知题型", "question_text": "脱敏未知题面", "choices": ["A", "B"], "write_audit": True})
            assert unsupported_capture.status_code == 200, unsupported_capture.text
            assert unsupported_capture.json()["item"]["status"] == "unsupported_paused"
            unsupported_draft = client.post(f"/api/v1/score-loop/cases/{unsupported_capture.json()['item']['id']}/draft", json={"use_provider": False, "write_audit": True})
            assert unsupported_draft.status_code == 400, unsupported_draft.text
            score_capture = client.post("/api/v1/score-loop/cases/capture", json={"question_text": "图片整体美观度评分，选择 1-5 分", "choices": ["1", "2", "3", "4", "5"], "write_audit": True})
            assert score_capture.status_code == 200, score_capture.text
            score_case_id = score_capture.json()["item"]["id"]
            score_draft = client.post(f"/api/v1/score-loop/cases/{score_case_id}/draft", json={"use_provider": False, "write_audit": True})
            assert score_draft.status_code == 200, score_draft.text
            assert score_draft.json()["item"]["status"] == "draft_ready"
            score_review_initial = client.post(f"/api/v1/score-loop/cases/{score_case_id}/review", json={"decision": "approve", "final_answer": "3", "note": "smoke manual approve", "request_submit": False, "write_audit": True})
            assert score_review_initial.status_code == 200, score_review_initial.text
            assert score_review_initial.json()["item"]["status"] == "manual_approved"
            score_summary_after_review = client.get("/api/v1/score-loop/summary")
            assert score_summary_after_review.status_code == 200, score_summary_after_review.text
            assert score_summary_after_review.json()["gate"]["manual_stable_count"] == 1
            score_review_repeat = client.post(f"/api/v1/score-loop/cases/{score_case_id}/review", json={"decision": "approve", "final_answer": "3", "note": "smoke repeat approve", "request_submit": False, "write_audit": False})
            assert score_review_repeat.status_code == 200, score_review_repeat.text
            score_summary_after_repeat = client.get("/api/v1/score-loop/summary")
            assert score_summary_after_repeat.status_code == 200, score_summary_after_repeat.text
            assert score_summary_after_repeat.json()["gate"]["manual_stable_count"] == 1
            score_review_reject = client.post(f"/api/v1/score-loop/cases/{score_case_id}/review", json={"decision": "reject", "final_answer": "3", "note": "smoke reject after approve", "request_submit": False, "write_audit": False})
            assert score_review_reject.status_code == 200, score_review_reject.text
            score_summary_after_reject = client.get("/api/v1/score-loop/summary")
            assert score_summary_after_reject.status_code == 200, score_summary_after_reject.text
            assert score_summary_after_reject.json()["gate"]["manual_stable_count"] == 0
            score_stable_floor = client.post("/api/v1/score-loop/gate/manual-stable", json={"count_delta": -1, "note": "smoke below zero guard"})
            assert score_stable_floor.status_code == 200, score_stable_floor.text
            assert score_stable_floor.json()["gate"]["manual_stable_count"] == 0
            score_review = client.post(f"/api/v1/score-loop/cases/{score_case_id}/review", json={"decision": "approve", "final_answer": "3", "note": "smoke request submit", "request_submit": True, "write_audit": True})
            assert score_review.status_code == 200, score_review.text
            assert score_review.json()["item"]["status"] == "submit_confirmation_required"
            assert score_review.json()["item"]["submit_confirmation_id"] is not None
            score_worker = client.get("/api/v1/workers/score-loop-api")
            assert score_worker.status_code == 200, score_worker.text
            score_worker_messages = [item["message"] for item in score_worker.json()["log_summary"]["events"]]
            assert any('"stage":"ai_draft"' in message and '"step":"save_draft"' in message for message in score_worker_messages)
            assert any('"stage":"manual_confirmation"' in message and '"error_code":"CONFIRMATION_PENDING"' in message for message in score_worker_messages)
            score_gate_blocked = client.post("/api/v1/score-loop/gate/auto-submit", json={"enabled": True, "force_confirmed": False, "reason": "smoke blocked"})
            assert score_gate_blocked.status_code == 200, score_gate_blocked.text
            assert score_gate_blocked.json()["gate"]["auto_submit_enabled"] is False
            score_stable = client.post("/api/v1/score-loop/gate/manual-stable", json={"count_delta": 2, "note": "smoke reach stable threshold"})
            assert score_stable.status_code == 200, score_stable.text
            assert score_stable.json()["gate"]["ready_for_auto_submit"] is True
            score_gate_enabled = client.post("/api/v1/score-loop/gate/auto-submit", json={"enabled": True, "force_confirmed": False, "reason": "smoke enable"})
            assert score_gate_enabled.status_code == 200, score_gate_enabled.text
            assert score_gate_enabled.json()["gate"]["auto_submit_enabled"] is True
            score_alert_summary = client.get("/api/v1/alerts/summary")
            assert score_alert_summary.status_code == 200, score_alert_summary.text
            score_incident_keys = {item["key"] for item in score_alert_summary.json()["incidents"]}
            assert "score_submit_confirmation_pending" in score_incident_keys
            assert "score_unknown_type_paused" in score_incident_keys
            score_summary_with_alerts = client.get("/api/v1/score-loop/summary")
            assert score_summary_with_alerts.status_code == 200, score_summary_with_alerts.text
            readiness_after_alerts = {item["key"]: item for item in score_summary_with_alerts.json()["readiness_checks"]}
            assert readiness_after_alerts["score_alert_closure"]["status"] == "warning"
            score_cases = client.get("/api/v1/score-loop/cases")
            assert score_cases.status_code == 200, score_cases.text
            assert score_cases.json()["total"] >= 2
            delivery_summary = client.get("/api/v1/delivery/summary")
            assert delivery_summary.status_code == 200, delivery_summary.text
            assert delivery_summary.json()["manual_domain_switch_required"] is True
            delivery_checklist = client.get("/api/v1/delivery/checklist")
            assert delivery_checklist.status_code == 200, delivery_checklist.text
            assert len(delivery_checklist.json()["items"]) >= 6
            delivery_bundle = client.post("/api/v1/delivery/bundle")
            assert delivery_bundle.status_code == 200, delivery_bundle.text
            assert delivery_bundle.json()["bundle_path"].endswith(".md")
            inspection_summary = client.get("/api/v1/inspection/summary")
            assert inspection_summary.status_code == 200, inspection_summary.text
            assert inspection_summary.json()["manual_domain_switch_required"] is True
            inspection_checklist = client.get("/api/v1/inspection/checklist")
            assert inspection_checklist.status_code == 200, inspection_checklist.text
            assert len(inspection_checklist.json()["items"]) >= 6
            inspection_run = client.post("/api/v1/inspection/run", json={"write_audit": True, "generate_report": True})
            assert inspection_run.status_code == 200, inspection_run.text
            assert inspection_run.json()["report_path"].endswith(".md")
            freeze_summary = client.get("/api/v1/freeze/summary")
            assert freeze_summary.status_code == 200, freeze_summary.text
            assert freeze_summary.json()["manual_only"] is True
            freeze_checklist = client.get("/api/v1/freeze/checklist")
            assert freeze_checklist.status_code == 200, freeze_checklist.text
            assert len(freeze_checklist.json()["freeze_items"]) >= 5
            freeze_baseline = client.post("/api/v1/freeze/baseline", json={"write_audit": True, "generate_report": True})
            assert freeze_baseline.status_code == 200, freeze_baseline.text
            assert freeze_baseline.json()["report_path"].endswith(".md")
            data_quality_summary = client.get("/api/v1/data-quality/summary")
            assert data_quality_summary.status_code == 200, data_quality_summary.text
            assert data_quality_summary.json()["account_count"] == 7
            assert data_quality_summary.json()["task_count"] >= 1
            assert data_quality_summary.json()["earnings_row_count"] == 7
            data_quality_checks = client.get("/api/v1/data-quality/checks")
            assert data_quality_checks.status_code == 200, data_quality_checks.text
            assert len(data_quality_checks.json()) >= 5
            data_quality_export = client.post("/api/v1/data-quality/export")
            assert data_quality_export.status_code == 200, data_quality_export.text
            assert data_quality_export.json()["export_path"].endswith(".xlsx")
            data_quality_report = client.post("/api/v1/data-quality/report", json={"write_audit": True, "generate_report": True, "generate_excel": True})
            assert data_quality_report.status_code == 200, data_quality_report.text
            assert data_quality_report.json()["report_path"].endswith(".md")
            incident_summary = client.get("/api/v1/incidents/summary")
            assert incident_summary.status_code == 200, incident_summary.text
            assert incident_summary.json()["external_send_enabled"] is False
            assert incident_summary.json()["runbook_count"] >= 6
            incident_runbooks = client.get("/api/v1/incidents/runbooks")
            assert incident_runbooks.status_code == 200, incident_runbooks.text
            assert len(incident_runbooks.json()) >= 9
            incident_closure_plan = client.get("/api/v1/incidents/closure-plan")
            assert incident_closure_plan.status_code == 200, incident_closure_plan.text
            closure_check_keys = {item["key"] for item in incident_closure_plan.json()["checks"]}
            assert "score_loop_ops_guard" in closure_check_keys
            assert incident_closure_plan.json()["pending_high_risk_confirmations"] >= 1
            incident_closure = client.post("/api/v1/incidents/close-loop", json={"dry_run": True, "write_audit": True, "generate_report": True})
            assert incident_closure.status_code == 200, incident_closure.text
            assert incident_closure.json()["report_path"].endswith(".md")
            assert "score_loop_ops_guard" in {item["key"] for item in incident_closure.json()["plan"]["checks"]}
            final_matrix = client.get("/api/v1/final-acceptance/matrix")
            assert final_matrix.status_code == 200, final_matrix.text
            assert final_matrix.json()["total_count"] >= 10
            assert final_matrix.json()["failed_count"] == 0
            rollback = client.get("/api/v1/final-acceptance/rollback")
            assert rollback.status_code == 200, rollback.text
            assert len(rollback.json()) >= 3
            final_evidence = client.post("/api/v1/final-acceptance/evidence", json={"write_audit": True, "generate_report": True})
            assert final_evidence.status_code == 200, final_evidence.text
            assert final_evidence.json()["report_path"].endswith(".md")
            roadmap_final = client.get("/api/v1/roadmap-final/summary")
            assert roadmap_final.status_code == 200, roadmap_final.text
            assert roadmap_final.json()["total_phases"] == 21
            assert roadmap_final.json()["production_domain"] == "manage.51gugu.uk"
            roadmap_report = client.post("/api/v1/roadmap-final/report", json={"write_audit": True, "generate_report": True})
            assert roadmap_report.status_code == 200, roadmap_report.text
            assert roadmap_report.json()["report_path"].endswith(".md")
            restore = client.post("/api/v1/restore-drills/run")
            assert restore.status_code == 200, restore.text
            assert restore.json()["status"] == "passed"
            ops_jobs = client.get("/api/v1/ops/jobs")
            assert ops_jobs.status_code == 200, ops_jobs.text
            assert len(ops_jobs.json()["jobs"]) >= 5
            cleanup = client.post("/api/v1/ops/jobs/backup_cleanup/run", json={"dry_run": True})
            assert cleanup.status_code == 200, cleanup.text
            assert cleanup.json()["status"] in {"completed", "warning"}
            account_job = client.post("/api/v1/ops/jobs/account_health_refresh/run", json={})
            assert account_job.status_code == 200, account_job.text
            release_gate = client.get("/api/v1/ops/release-gate")
            assert release_gate.status_code == 200, release_gate.text
            assert release_gate.json()["manual_switch_required"] is True
            risk_summary = client.get("/api/v1/ops/risk-summary")
            assert risk_summary.status_code == 200, risk_summary.text
            assert "items" in risk_summary.json()
            fault_diagnosis = client.get("/api/v1/ops/fault-diagnosis")
            assert fault_diagnosis.status_code == 200, fault_diagnosis.text
            assert "items" in fault_diagnosis.json()
            assert "fault_count" in fault_diagnosis.json()
            scheduler_plan = client.get("/api/v1/ops/scheduler/plan")
            assert scheduler_plan.status_code == 200, scheduler_plan.text
            assert "jobs" in scheduler_plan.json()
            scheduler_tick = client.post("/api/v1/ops/scheduler/tick", json={"dry_run": True, "limit": 3})
            assert scheduler_tick.status_code == 200, scheduler_tick.text
            assert scheduler_tick.json()["dry_run"] is True
            runbook = client.get("/api/v1/ops/domain-switch-runbook")
            assert runbook.status_code == 200, runbook.text
            assert runbook.json()["manual_only"] is True
            obs_summary = client.get("/api/v1/observability/summary")
            assert obs_summary.status_code == 200, obs_summary.text
            assert len(obs_summary.json()["metrics"]) >= 6
            collector_guard = client.get("/api/v1/observability/collector-guard")
            assert collector_guard.status_code == 200, collector_guard.text
            assert collector_guard.json()["safe_mode"] is True
            timeline = client.get("/api/v1/observability/timeline")
            assert timeline.status_code == 200, timeline.text
            probes = client.post("/api/v1/observability/probes/run")
            assert probes.status_code == 200, probes.text
            assert probes.json()["status"] in {"passed", "warning", "failed"}
            earnings = client.get("/api/v1/earnings/summary")
            assert earnings.status_code == 200, earnings.text
            export = client.post("/api/v1/earnings/export")
            assert export.status_code == 200, export.text
            assert export.json()["export_path"].endswith(".xlsx")

            login_slots_initial = client.get("/api/v1/accounts/login-slots")
            assert login_slots_initial.status_code == 200, login_slots_initial.text
            new_login_slot = client.post("/api/v1/accounts/login-slots/new", json={})
            assert new_login_slot.status_code == 200, new_login_slot.text
            assert new_login_slot.json()["enabled"] is False
            assert new_login_slot.json()["pending_login"] is True
            relogin_slot = client.post("/api/v1/accounts/account-sample-002/login-slots/relogin", json={})
            assert relogin_slot.status_code == 200, relogin_slot.text
            assert relogin_slot.json()["user_id"] == "account-sample-002"
            rejected_session = client.post("/api/client-session", json={"userId": "pending", "cookie": "x", "referer": "https://aidp.juejin.cn/operation/task-v2"})
            assert rejected_session.status_code == 400, rejected_session.text
            registered_session = client.post("/api/client-session", json={"userId": "7635555555555555555", "name": "新登录账号", "cookie": "sessionid=redacted", "referer": "https://aidp.juejin.cn/operation/task-v2", "loginSessionId": new_login_slot.json()["login_session_id"], "cdpPort": new_login_slot.json()["cdp_port"]})
            assert registered_session.status_code == 200, registered_session.text
            assert registered_session.json()["cookie_saved"] is True
        session_module = importlib.import_module("app.db.session")
        session_module.engine.dispose()
    print("api_integration_smoke_ok=true")


if __name__ == "__main__":
    main()








