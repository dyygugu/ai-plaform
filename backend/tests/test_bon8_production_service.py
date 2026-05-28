import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.ai import AiActionConfirmation, AiActionConfirmationStatus
from app.schemas.bon8_production import Bon8ProductionStartRequest
from app.services.bon8_production_service import (
    approve_bon8_run_confirmation,
    build_bon8_production_status,
    build_bon8_production_timer_event,
    get_bon8_production_run,
    mark_bon8_account_operation_needed,
    plan_bon8_parallel_account_ticks,
    prepare_bon8_first_item_review,
    reject_bon8_run_confirmation,
    start_bon8_production,
    stop_bon8_production_run,
    submit_approved_bon8_first_item,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _category_transport(_account, _kind, path, _body):
    if path != "/dispatcher/search_item/category":
        raise AssertionError(f"unexpected remote write: {path}")
    return {
        "statusCode": 200,
        "elapsedMs": 1,
        "body": {
            "BaseResp": {"StatusCode": 0},
            "Data": [
                {
                    "ItemID": "item-1",
                    "Content": "{\"mediaUrls\":[\"https://example.test/input.png\"],\"model1\":{\"html\":\"https://example.test/model1.html\"}}",
                    "Status": 4,
                }
            ],
            "TotalMap": {"0": 1},
        },
    }


def _ok_base(elapsed_ms=1, data=None, total_map=None):
    body = {"BaseResp": {"StatusCode": 0}}
    if data is not None:
        body["Data"] = data
    if total_map is not None:
        body["TotalMap"] = total_map
    return {"statusCode": 200, "elapsedMs": elapsed_ms, "body": body}


class Bon8ProductionServiceTests(unittest.TestCase):
    def test_start_creates_first_item_review_run_state(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-002", "account-sample-004"]),
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=Path(temp_dir),
            )

            self.assertTrue(result.run_id.startswith("bon8-"))
            self.assertEqual(result.mode, "first_item_review")
            self.assertEqual(result.status, "waiting_first_confirm")
            self.assertFalse(result.auto_submit_allowed)
            self.assertEqual(result.selected_account_count, 2)
            self.assertEqual(result.seed_account_id, "account-sample-002")
            self.assertEqual(result.submit_count, 0)
            self.assertEqual(result.gate_status, "waiting_review")
            self.assertIsNotNone(result.confirmation_sheet)
            self.assertEqual(result.confirmation_sheet.account_user_id, "account-sample-002")
            self.assertEqual(result.confirmation_sheet.item_id, "item-1")
            self.assertEqual(result.accounts[0].status, "waiting_first_confirm")
            self.assertEqual(result.accounts[1].status, "waiting_first_gate")
            self.assertTrue((Path(temp_dir) / f"{result.run_id}.json").exists())
            saved = get_bon8_production_run(result.run_id, state_dir=Path(temp_dir))
            self.assertEqual(saved.run_id, result.run_id)
        db.close()

    def test_first_item_review_run_can_be_approved_rejected_or_stopped(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-002"]),
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=Path(temp_dir),
            )

            approved = approve_bon8_run_confirmation(result.run_id, result.confirmation_sheet.confirmation_id, state_dir=Path(temp_dir))
            self.assertEqual(approved.mode, "first_item_approved")
            self.assertEqual(approved.status, "waiting_first_submit")
            self.assertFalse(approved.auto_submit_allowed)
            self.assertEqual(approved.gate_status, "approved_pending_submit")
            self.assertEqual(approved.accounts[0].status, "waiting_first_submit")
            self.assertIn("正式提交首题", approved.next_step)

            stopped = stop_bon8_production_run(result.run_id, state_dir=Path(temp_dir))
            self.assertEqual(stopped.status, "stopped")
            self.assertTrue(stopped.stop_requested)

            rejected_start = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-004"]),
                account_loader=lambda user_id: {"userId": user_id, "name": "用户22449629285", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=Path(temp_dir),
            )
            rejected = reject_bon8_run_confirmation(
                rejected_start.run_id,
                rejected_start.confirmation_sheet.confirmation_id,
                rejected_reason="AI 判分不符合人工审核",
                state_dir=Path(temp_dir),
            )
            self.assertEqual(rejected.status, "blocked")
            self.assertEqual(rejected.gate_status, "rejected")
            self.assertEqual(rejected.confirmation_sheet.rejected_reason, "AI 判分不符合人工审核")
        db.close()

    def test_submit_approved_first_item_calls_submit_item_then_readback_and_enables_parallel(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            result = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-002"]),
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=state_dir,
            )
            payload_path = state_dir / "first-item-payload.json"
            payload_path.write_text(
                '{"AuditAnswers":[{"ItemID":"item-1","Content":"{}"}]}',
                encoding="utf-8",
            )
            run = get_bon8_production_run(result.run_id, state_dir=state_dir)
            run.confirmation_sheet.review_payload_path = str(payload_path)
            from app.services.bon8_production_service import _write_run_state

            _write_run_state(run, state_dir=state_dir)
            approve_bon8_run_confirmation(result.run_id, result.confirmation_sheet.confirmation_id, state_dir=state_dir)

            calls = []

            def transport(_account, kind, path, body):
                calls.append((kind, path, body))
                if path == "/api/dispatch/SubmitItem":
                    return _ok_base(elapsed_ms=11)
                if path == "/dispatcher/search_item/category":
                    return _ok_base(elapsed_ms=13, data=[], total_map={"0": 0, "1": 1})
                raise AssertionError(f"unexpected call: {path}")

            submitted = submit_approved_bon8_first_item(
                result.run_id,
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=transport,
                state_dir=state_dir,
            )

            self.assertEqual(calls[0][1], "/api/dispatch/SubmitItem")
            self.assertEqual(calls[0][2], {"TaskID": "7637771731901861641", "NodeID": 1, "Status": 4, "Answers": [{"ItemID": "item-1", "Content": "{}"}]})
            self.assertEqual(calls[1][1], "/dispatcher/search_item/category")
            self.assertEqual(submitted.mode, "auto_parallel")
            self.assertEqual(submitted.status, "running_auto")
            self.assertEqual(submitted.gate_status, "approved")
            self.assertTrue(submitted.auto_submit_allowed)
            self.assertEqual(submitted.submit_count, 1)
            self.assertEqual(submitted.accounts[0].status, "running_auto")
            self.assertEqual(submitted.attempts[0].submit_status, "submitted")
            self.assertEqual(submitted.attempts[0].readback_status, "readback_ok")
        db.close()

    def test_prepare_first_item_review_builds_payload_temp_saves_and_verifies_without_submit_item(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            result = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-002"]),
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=state_dir,
            )
            calls = []

            def transport(_account, kind, path, body):
                calls.append((kind, path, body))
                if path == "/api/dispatch/SubmitTempItemAnswer":
                    return _ok_base(elapsed_ms=7)
                if path == "/dispatcher/verify/submit":
                    return _ok_base(elapsed_ms=9)
                raise AssertionError(f"unexpected remote write: {path}")

            prepared = prepare_bon8_first_item_review(
                result.run_id,
                scores={"model1": "0", "model2": "1", "model3": "2"},
                sort_models=["model3", "model2", "model1"],
                score_reasons={
                    "model1": "白屏且核心内容缺失。",
                    "model2": "结构接近但功能入口不足。",
                    "model3": "整体最完整。",
                },
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=transport,
                state_dir=state_dir,
            )

            self.assertEqual([call[1] for call in calls], ["/api/dispatch/SubmitTempItemAnswer", "/dispatcher/verify/submit"])
            self.assertNotIn("/api/dispatch/SubmitItem", [call[1] for call in calls])
            self.assertTrue(Path(prepared.confirmation_sheet.review_payload_path).exists())
            self.assertEqual(prepared.confirmation_sheet.payload_check["status"], "passed")
            self.assertEqual(prepared.confirmation_sheet.temp_save_result["baseRespStatusCode"], 0)
            self.assertEqual(prepared.confirmation_sheet.verify_submit_result["baseRespStatusCode"], 0)
            self.assertEqual(prepared.attempts[0].payload_check_status, "passed")
            self.assertEqual(prepared.attempts[0].temp_save_status, "saved")
            self.assertEqual(prepared.attempts[0].verify_submit_status, "verified")
            submit_request = calls[1][2]["SubmitItemRequest"]
            self.assertEqual(submit_request["Answers"], calls[0][2]["AuditAnswers"])
        db.close()

    def test_parallel_tick_planner_creates_one_active_attempt_per_account_without_duplicates(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            result = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-002", "account-sample-004"]),
                account_loader=lambda user_id: {"userId": user_id, "name": f"用户{user_id[-4:]}", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=state_dir,
            )
            payload_path = state_dir / "first-item-payload.json"
            payload_path.write_text('{"AuditAnswers":[{"ItemID":"item-1","Content":"{}"}]}', encoding="utf-8")
            run = get_bon8_production_run(result.run_id, state_dir=state_dir)
            run.confirmation_sheet.review_payload_path = str(payload_path)
            from app.services.bon8_production_service import _write_run_state

            _write_run_state(run, state_dir=state_dir)
            approve_bon8_run_confirmation(result.run_id, result.confirmation_sheet.confirmation_id, state_dir=state_dir)
            submit_approved_bon8_first_item(
                result.run_id,
                account_loader=lambda user_id: {"userId": user_id, "name": f"用户{user_id[-4:]}", "cookie": "sessionid=test"},
                transport=lambda _account, _kind, path, _body: _ok_base(data=[], total_map={"0": 0, "1": 1}) if path == "/dispatcher/search_item/category" else _ok_base(),
                state_dir=state_dir,
            )

            planned = plan_bon8_parallel_account_ticks(result.run_id, state_dir=state_dir)
            planned_again = plan_bon8_parallel_account_ticks(result.run_id, state_dir=state_dir)

            active_by_account = {}
            for attempt in planned_again.attempts:
                if attempt.stage == "queued_account_tick" and attempt.finished_at is None:
                    active_by_account.setdefault(attempt.account_user_id, 0)
                    active_by_account[attempt.account_user_id] += 1
            self.assertEqual(active_by_account, {"account-sample-002": 1, "account-sample-004": 1})
            self.assertEqual(len(planned_again.attempts), len(planned.attempts))
        db.close()

    def test_operation_claim_needed_is_marked_once_when_account_has_no_current_item(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            result = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-002"]),
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=state_dir,
            )
            payload_path = state_dir / "first-item-payload.json"
            payload_path.write_text('{"AuditAnswers":[{"ItemID":"item-1","Content":"{}"}]}', encoding="utf-8")
            run = get_bon8_production_run(result.run_id, state_dir=state_dir)
            run.confirmation_sheet.review_payload_path = str(payload_path)
            from app.services.bon8_production_service import _write_run_state

            _write_run_state(run, state_dir=state_dir)
            approve_bon8_run_confirmation(result.run_id, result.confirmation_sheet.confirmation_id, state_dir=state_dir)
            submit_approved_bon8_first_item(
                result.run_id,
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=lambda _account, _kind, path, _body: _ok_base(data=[], total_map={"0": 0, "1": 1}) if path == "/dispatcher/search_item/category" else _ok_base(),
                state_dir=state_dir,
            )
            plan_bon8_parallel_account_ticks(result.run_id, state_dir=state_dir)

            marked = mark_bon8_account_operation_needed(result.run_id, "account-sample-002", state_dir=state_dir)
            marked_again = mark_bon8_account_operation_needed(result.run_id, "account-sample-002", state_dir=state_dir)

            self.assertEqual(marked_again.accounts[0].status, "waiting_operation_claim")
            self.assertEqual(marked_again.accounts[0].current_stage, "等待 operation 处理领题接口")
            self.assertEqual(marked_again.accounts[0].no_item_count, 1)
            self.assertIn("尚未捕获", marked_again.accounts[0].last_error)
            operation_attempts = [attempt for attempt in marked_again.attempts if attempt.stage == "operation_claim_needed"]
            self.assertEqual(len(operation_attempts), 1)
            self.assertEqual(operation_attempts[0].account_user_id, "account-sample-002")
            self.assertEqual(operation_attempts[0].error_code, "operation-claim-not-ready")
            self.assertIn("不会伪造领题成功", operation_attempts[0].error_message)
            self.assertIn("operation 处理领题接口尚未捕获", marked_again.next_step)
        db.close()

    def test_status_allows_auto_submit_after_three_approved_bon8_confirmations(self) -> None:
        db = _session()
        try:
            for index in range(3):
                db.add(
                    AiActionConfirmation(
                        status=AiActionConfirmationStatus.APPROVED,
                        action_key=f"bon8_submit_account-sample-002_item-{index}",
                        title="bon8 提交确认",
                        risk_level="high",
                        source="bon8-production",
                        source_trace_id=f"trace-{index}",
                        message="approved",
                        rollback_hint="readback",
                        payload_json="{}",
                        requested_by="bon8-production",
                        trace_id=f"confirm-{index}",
                    )
                )
            db.commit()

            status = build_bon8_production_status(db)

            self.assertEqual(status.manual_confirmed_count, 1)
            self.assertTrue(status.auto_submit_allowed)
            self.assertEqual(status.next_mode, "first_item_review")
            self.assertNotIn("前 3 题", status.message)
        finally:
            db.close()

    def test_each_finished_item_builds_ai_timer_event_with_clear_stage_names(self) -> None:
        event = build_bon8_production_timer_event(
            account_user_id="account-sample-002",
            account_name="用户样例002",
            task_id="7637771731901861641",
            item_id="7637774211302166322",
            status="submitted",
            timings_ms={
                "claim": 1200,
                "read": 8,
                "upstreamAiElapsedMs": 27779,
                "payloadBuild": 1,
                "submitTemp": 923,
                "verifySubmit": 734,
                "submitItem": 792,
                "categoryAfter": 793,
                "total": 31930,
            },
        )

        self.assertEqual(event.source, "bon8_production")
        self.assertEqual(event.total_ms, 31930)
        self.assertEqual(event.item_id, "7637774211302166322")
        self.assertEqual(event.stages[0].stage, "领题")
        self.assertIn("上游 AI 往返", [stage.stage for stage in event.stages])
        self.assertIn("提交后回读", [stage.stage for stage in event.stages])

    def test_each_finished_item_timer_event_requires_total_time(self) -> None:
        with self.assertRaises(ValueError):
            build_bon8_production_timer_event(
                account_user_id="account-sample-002",
                account_name="用户样例002",
                task_id="7637771731901861641",
                item_id="7637774211302166322",
                status="submitted",
                timings_ms={"upstreamAiElapsedMs": 27779},
            )


if __name__ == "__main__":
    unittest.main()
