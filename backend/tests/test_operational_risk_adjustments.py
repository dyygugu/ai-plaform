import unittest
import os
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.account import AccountStatus, AidpAccount
from app.models.ai import AiActionConfirmation
from app.models.audit import AuditLog
from app.models.backup import BackupJob
from app.models.ops import EarningsSnapshot, RestoreDrill
from app.models.score_loop import ScoreLoopCase
from app.models.task import RuntimeConfig, TaskCatalogEvent, TaskCatalogItem, TaskRuleConfig, TaskVisibility
from app.models.worker import Worker, WorkerEvent, WorkerEventType, WorkerStatus
from app.schemas.worker import WorkerEventReportRequest
from app.services.account_coverage_service import build_account_coverage_summary
from app.services.data_quality_service import build_data_quality_summary
from app.services.final_acceptance_service import build_final_acceptance_matrix
from app.services.ops_risk_service import build_fault_diagnosis, build_operational_risk_summary
from app.services.score_loop_service import capture_score_case, create_ai_draft, review_score_case
from app.services import worker_service
from app.services.worker_service import report_worker_event
from app.schemas.score_loop import ScoreLoopCaptureRequest, ScoreLoopDraftRequest, ScoreLoopReviewRequest
from app.schemas.task import TaskCatalogRefreshRequest
from app.api.v1.routes.tasks import _run_task_catalog_refresh
from app.core.settings import get_settings
from pydantic import ValidationError
import json


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _account(user_id: str, status: AccountStatus) -> AidpAccount:
    return AidpAccount(user_id=user_id, display_name=f"用户{user_id[-8:]}", status=status, auth_mode="client-cookie")


class OperationalRiskAdjustmentTests(unittest.TestCase):
    def test_data_quality_ignores_disabled_accounts_for_production_baseline(self) -> None:
        db = _session()
        try:
            for index in range(7):
                db.add(_account(f"76307785037302536{index:02d}", AccountStatus.ACTIVE))
            db.add(_account("7630778503730253699", AccountStatus.DISABLED))
            db.add(_account("pending-20260505174327", AccountStatus.NEEDS_LOGIN))
            db.commit()

            summary = build_data_quality_summary(db)
            account_check = next(item for item in summary.checks if item.key == "accounts")

            self.assertEqual(account_check.status, "passed")
            self.assertEqual(summary.account_count, 7)
            self.assertIn("停用/非生产 2 个", account_check.actual)
        finally:
            db.close()

    def test_user_facing_task_quality_ignores_recycled_catalog_rows(self) -> None:
        db = _session()
        try:
            active_id = "7630778503730253600"
            disabled_id = "7630778503730253699"
            db.add(_account(active_id, AccountStatus.ACTIVE))
            db.add(_account(disabled_id, AccountStatus.DISABLED))
            db.add(
                TaskCatalogItem(
                    source_account_user_id=active_id,
                    raw_task_name="活跃任务 task-active",
                    task_short_name="活跃任务",
                    task_id="task-active",
                    task_name_id="活跃任务task-active",
                    pending_raw="3",
                    task_status_raw="进行中",
                )
            )
            db.add(
                TaskCatalogItem(
                    source_account_user_id=disabled_id,
                    raw_task_name="回收任务 task-disabled",
                    task_short_name="回收任务",
                    task_id="task-disabled",
                    task_name_id="回收任务task-disabled",
                    pending_raw="9",
                    task_status_raw="进行中",
                    visibility=TaskVisibility.VISIBLE,
                )
            )
            db.commit()

            quality = build_data_quality_summary(db)
            coverage = build_account_coverage_summary(db)

            self.assertEqual(quality.task_count, 1)
            self.assertEqual([item.task_id for item in coverage.task_items], ["task-active"])
            self.assertEqual([row.user_id for row in coverage.matrix], [active_id])
        finally:
            db.close()

    def test_operational_risk_summary_deduplicates_account_login_warning(self) -> None:
        db = _session()
        try:
            db.add(_account("7630778503730253600", AccountStatus.NEEDS_LOGIN))
            db.commit()

            summary = build_operational_risk_summary(db)
            account_risks = [item for item in summary.items if item.key == "account_needs_login"]

            self.assertEqual(len(account_risks), 1)
            self.assertIn("alerts.slo", account_risks[0].sources)
            self.assertIn("incidents", account_risks[0].sources)
        finally:
            db.close()

    def test_fault_diagnosis_turns_account_risk_into_actionable_chain(self) -> None:
        db = _session()
        try:
            db.add(_account("7630778503730253600", AccountStatus.NEEDS_LOGIN))
            db.commit()

            diagnosis = build_fault_diagnosis(db)

            self.assertGreaterEqual(diagnosis.fault_count, 1)
            account_diagnosis = next(item for item in diagnosis.items if item.key == "account_needs_login")

            self.assertEqual(account_diagnosis.error_location, "账号健康")
            self.assertIn("用户", account_diagnosis.affected_scope)
            self.assertTrue(account_diagnosis.accurate_error)
            self.assertIn("/accounts", account_diagnosis.evidence_links)
            self.assertGreaterEqual(len(account_diagnosis.next_actions), 2)
            self.assertIn("重新登录", " ".join(account_diagnosis.next_actions))
        finally:
            db.close()

    def test_fault_diagnosis_replays_worker_error_log_context(self) -> None:
        db = _session()
        try:
            db.add(Worker(worker_id="worker-a", display_name="主机A", status=WorkerStatus.DEGRADED, version="1.2.3", current_account_user_id="7630778503730253600", current_task_id="task-42", last_error="领取任务失败"))
            db.add(WorkerEvent(worker_id="worker-a", event_type=WorkerEventType.EVENT_REPORT, account_user_id="7630778503730253600", task_id="task-42", severity="error", message="HTTP 502: 领取任务失败", trace_id="trace-worker-502"))
            db.add(WorkerEvent(worker_id="worker-a", event_type=WorkerEventType.BIND_ACCOUNT, account_user_id="7630778503730253600", task_id="", severity="info", message="绑定账号", trace_id="trace-info"))
            db.commit()

            diagnosis = build_fault_diagnosis(db)
            worker_diagnosis = next(item for item in diagnosis.items if item.key == "worker_error_worker-a")

            self.assertEqual(worker_diagnosis.error_location, "Worker 日志")
            self.assertIn("主机A", worker_diagnosis.affected_scope)
            self.assertIn("HTTP 502", worker_diagnosis.accurate_error)
            self.assertIn("/workers", worker_diagnosis.evidence_links)
            self.assertEqual(worker_diagnosis.worker_log_replay[0].trace_id, "trace-worker-502")
            self.assertEqual(worker_diagnosis.worker_log_replay[0].task_id, "task-42")
            self.assertNotIn("trace-info", [item.trace_id for item in worker_diagnosis.worker_log_replay])
        finally:
            db.close()

    def test_fault_diagnosis_ignores_acceptance_sample_worker_logs(self) -> None:
        db = _session()
        try:
            db.add(Worker(worker_id="local-worker-p7", display_name="本地采集 Worker", status=WorkerStatus.DEGRADED, last_error="验收样例：等待真实 Worker 接入"))
            db.add(WorkerEvent(worker_id="local-worker-p7", event_type=WorkerEventType.EVENT_REPORT, severity="warning", message="验收样例：等待真实 Worker 接入", trace_id="trace-sample"))
            db.commit()

            diagnosis = build_fault_diagnosis(db)

            self.assertNotIn("worker_error_local-worker-p7", [item.key for item in diagnosis.items])
        finally:
            db.close()

    def test_worker_structured_log_payload_flows_into_fault_diagnosis(self) -> None:
        db = _session()
        try:
            _worker, event = report_worker_event(db, WorkerEventReportRequest(
                worker_id="task-worker-1",
                event_type="event_report",
                account_user_id="7630778503730253600",
                task_id="score-task-88",
                severity="error",
                stage="ai_draft",
                step="call_provider",
                error_code="AI_PROVIDER_502",
                error_detail="502 Bad Gateway",
                retryable=True,
                duration_ms=1234,
                message="做题 AI 草稿生成失败",
            ))
            db.commit()

            diagnosis = build_fault_diagnosis(db)
            worker_diagnosis = next(item for item in diagnosis.items if item.key == "worker_error_task-worker-1")
            replay = worker_diagnosis.worker_log_replay[0]

            self.assertIn("AI_PROVIDER_502", event.message)
            self.assertIn("AI_PROVIDER_502", worker_diagnosis.accurate_error)
            self.assertIn("502 Bad Gateway", worker_diagnosis.accurate_error)
            self.assertEqual(replay.stage, "ai_draft")
            self.assertEqual(replay.step, "call_provider")
            self.assertEqual(replay.error_code, "AI_PROVIDER_502")
            self.assertEqual(replay.error_detail, "502 Bad Gateway")
            self.assertTrue(replay.retryable)
            self.assertEqual(replay.duration_ms, 1234)
        finally:
            db.close()

    def test_worker_structured_log_payload_flows_into_notification_data(self) -> None:
        db = _session()
        calls: list[dict[str, object]] = []
        original_send = worker_service.send_error_notification

        def _fake_send(**kwargs):
            calls.append(kwargs)

        worker_service.send_error_notification = _fake_send
        try:
            report_worker_event(db, WorkerEventReportRequest(
                worker_id="task-worker-1",
                event_type="event_report",
                account_user_id="7630778503730253600",
                task_id="score-task-88",
                severity="error",
                stage="ai_draft",
                step="call_provider",
                error_code="AI_PROVIDER_502",
                error_detail="502 Bad Gateway",
                retryable=True,
                duration_ms=1234,
                message="做题 AI 草稿生成失败",
            ))
        finally:
            worker_service.send_error_notification = original_send
            db.close()

        self.assertEqual(len(calls), 1)
        data = calls[0]["data"]
        self.assertEqual(data["worker_id"], "task-worker-1")
        self.assertEqual(data["account_user_id"], "7630778503730253600")
        self.assertEqual(data["task_id"], "score-task-88")
        self.assertEqual(data["stage"], "ai_draft")
        self.assertEqual(data["step"], "call_provider")
        self.assertEqual(data["error_code"], "AI_PROVIDER_502")
        self.assertEqual(data["error_detail"], "502 Bad Gateway")
        self.assertTrue(data["retryable"])
        self.assertEqual(data["duration_ms"], 1234)

    def test_worker_event_contract_rejects_unknown_stage_step_and_error_code(self) -> None:
        valid = WorkerEventReportRequest(
            worker_id="task-worker-1",
            severity="error",
            stage="ai_draft",
            step="call_provider",
            error_code="AI_PROVIDER_502",
            message="AI provider failed",
        )

        self.assertEqual(valid.stage, "ai_draft")
        self.assertEqual(valid.step, "call_provider")
        self.assertEqual(valid.error_code, "AI_PROVIDER_502")

        with self.assertRaises(ValidationError):
            WorkerEventReportRequest(worker_id="task-worker-1", stage="random_stage", step="call_provider")
        with self.assertRaises(ValidationError):
            WorkerEventReportRequest(worker_id="task-worker-1", stage="ai_draft", step="random_step")
        with self.assertRaises(ValidationError):
            WorkerEventReportRequest(worker_id="task-worker-1", stage="ai_draft", step="call_provider", error_code="RANDOM_ERROR")

    def test_score_loop_actions_emit_standard_worker_events(self) -> None:
        db = _session()
        try:
            case, _capture_trace = capture_score_case(db, ScoreLoopCaptureRequest(
                account_user_id="7630778503730253600",
                question_text="图片整体美观度评分，选择 1-5 分",
                choices=["1", "2", "3", "4", "5"],
                write_audit=False,
            ))
            create_ai_draft(db, case.id, ScoreLoopDraftRequest(use_provider=False, write_audit=False))
            review_score_case(db, case.id, ScoreLoopReviewRequest(decision="approve", final_answer="3", request_submit=True, write_audit=False))

            events = db.query(WorkerEvent).filter(WorkerEvent.worker_id == "score-loop-api").order_by(WorkerEvent.id.asc()).all()
            structured = [json.loads(event.message) for event in events]
            stage_steps = [(item["stage"], item["step"]) for item in structured]

            self.assertIn(("task_refresh", "parse_task_catalog"), stage_steps)
            self.assertIn(("ai_draft", "save_draft"), stage_steps)
            self.assertIn(("manual_confirmation", "queue_confirmation"), stage_steps)
            self.assertIn("CONFIRMATION_PENDING", [item.get("error_code", "") for item in structured])
        finally:
            db.close()

    def test_task_catalog_refresh_emits_standard_worker_event(self) -> None:
        db = _session()
        old_sample_root = os.environ.get("AIDP_TASK_SAMPLE_ROOT")
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["AIDP_TASK_SAMPLE_ROOT"] = tmpdir
            get_settings.cache_clear()
            try:
                _run_task_catalog_refresh(TaskCatalogRefreshRequest(
                    source_account_user_id="7630778503730253600",
                    sample_payload={"tasks": [{"taskId": "score-task-1", "taskName": "评分", "pending": "2"}]},
                ), db)

                events = db.query(WorkerEvent).filter(WorkerEvent.worker_id == "task-refresh-api").order_by(WorkerEvent.id.asc()).all()
                structured = [json.loads(event.message) for event in events]

                self.assertIn(("task_refresh", "finish"), [(item["stage"], item["step"]) for item in structured])
                self.assertIn("导入", structured[-1]["message"])
            finally:
                if old_sample_root is None:
                    os.environ.pop("AIDP_TASK_SAMPLE_ROOT", None)
                else:
                    os.environ["AIDP_TASK_SAMPLE_ROOT"] = old_sample_root
                get_settings.cache_clear()
                db.close()

    def test_final_acceptance_uses_production_account_baseline(self) -> None:
        db = _session()
        try:
            for index in range(7):
                db.add(_account(f"76307785037302536{index:02d}", AccountStatus.ACTIVE))
            db.add(_account("pending-20260505174327", AccountStatus.NEEDS_LOGIN))
            db.add(TaskCatalogItem(source_account_user_id="7630778503730253600", raw_task_name="任务", task_short_name="任务", task_id="task-1", task_name_id="任务task-1", pending_raw="0"))
            db.commit()

            matrix = build_final_acceptance_matrix(db)
            account_item = next(item for item in matrix.items if item.key == "accounts")

            self.assertEqual(account_item.status, "passed")
            self.assertIn("生产账号 7 个", account_item.message)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
