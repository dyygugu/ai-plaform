import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.schemas.bon8_production import Bon8ProductionStartRequest
from app.services.bon8_ai_judgement_service import (
    execute_bon8_account_tick_with_ai,
    execute_bon8_run_tick_with_ai,
    parse_bon8_ai_judgement,
    prepare_bon8_first_item_review_with_ai,
)
from app.services.bon8_production_service import start_bon8_production
from app.services.bon8_production_service import approve_bon8_run_confirmation, plan_bon8_parallel_account_ticks, submit_approved_bon8_first_item


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _ok_base(elapsed_ms=1, data=None, total_map=None):
    body = {"BaseResp": {"StatusCode": 0}}
    if data is not None:
        body["Data"] = data
    if total_map is not None:
        body["TotalMap"] = total_map
    return {"statusCode": 200, "elapsedMs": elapsed_ms, "body": body}


def _category_transport(_account, _kind, path, _body):
    if path != "/dispatcher/search_item/category":
        raise AssertionError(f"unexpected remote write: {path}")
    item_content = {
        "mediaUrls": ["https://example.test/input.png"],
        **{f"model{index}": {"html": f"https://example.test/model{index}.html"} for index in range(1, 9)},
    }
    return _ok_base(
        elapsed_ms=1,
        data=[{"ItemID": "item-1", "Content": json.dumps(item_content, ensure_ascii=False), "Status": 4}],
        total_map={"0": 1},
    )


def _provider_json(best_model="model3"):
    scores = {f"model{index}": "1" for index in range(1, 9)}
    scores["model1"] = "0"
    scores[best_model] = "2"
    return json.dumps(
        {
            "scores": scores,
            "scoreReasons": {key: f"{key} 中文理由" for key in scores},
            "sortModels": ["model2", best_model, "model1"],
            "bestModel": best_model,
            "summary": "首题 AI 判题完成。",
        },
        ensure_ascii=False,
    )


class Bon8AiJudgementServiceTests(unittest.TestCase):
    def test_parse_bon8_ai_judgement_requires_one_best_and_normalizes_order(self) -> None:
        judgement = parse_bon8_ai_judgement(f"```json\n{_provider_json('model3')}\n```", upstream_ai_elapsed_ms=37)

        self.assertEqual(judgement["scores"]["model3"], "2")
        self.assertEqual(judgement["bestModel"], "model3")
        self.assertEqual(judgement["sortModels"][0], "model3")
        self.assertEqual(set(judgement["sortModels"]), {f"model{index}" for index in range(1, 9)})
        self.assertEqual(judgement["provider_elapsed_ms"], 37)
        self.assertIn("model8", judgement["scoreReasons"])

    def test_parse_bon8_ai_judgement_rejects_multiple_best_models(self) -> None:
        payload = json.loads(_provider_json("model3"))
        payload["scores"]["model4"] = "2"

        with self.assertRaisesRegex(ValueError, "必须且只能返回一个 2 分最佳模型"):
            parse_bon8_ai_judgement(json.dumps(payload, ensure_ascii=False))

    def test_prepare_first_item_review_with_ai_calls_provider_then_review_only_executor(self) -> None:
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
            provider_inputs = []

            def provider_client(item_content, _runtime):
                provider_inputs.append(item_content)
                return {"content": _provider_json("model5"), "elapsed_ms": 23, "provider_status": "provider_ok"}

            remote_calls = []

            def transport(_account, kind, path, body):
                remote_calls.append((kind, path, body))
                if path == "/api/dispatch/SubmitTempItemAnswer":
                    return _ok_base(elapsed_ms=7)
                if path == "/dispatcher/verify/submit":
                    return _ok_base(elapsed_ms=9)
                raise AssertionError(f"unexpected remote write: {path}")

            prepared = prepare_bon8_first_item_review_with_ai(
                result.run_id,
                provider_client=provider_client,
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=transport,
                state_dir=state_dir,
            )

            self.assertEqual(len(provider_inputs), 1)
            self.assertEqual([call[1] for call in remote_calls], ["/api/dispatch/SubmitTempItemAnswer", "/dispatcher/verify/submit"])
            self.assertNotIn("/api/dispatch/SubmitItem", [call[1] for call in remote_calls])
            self.assertEqual(prepared.confirmation_sheet.ai_scores["model5"], "2")
            self.assertEqual(prepared.confirmation_sheet.model_order[0], "model5")
            self.assertEqual(prepared.confirmation_sheet.issue_options["providerStatus"], "provider_ok")
            self.assertEqual(prepared.confirmation_sheet.issue_options["bestModel"], "model5")
            self.assertEqual(prepared.confirmation_sheet.timings["provider_elapsed_ms"], 23)
            self.assertTrue(Path(prepared.confirmation_sheet.review_payload_path).exists())
        db.close()

    def test_prepare_first_item_review_with_ai_passes_task_ability_prompt_to_provider_runtime(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            result = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-002"]),
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=state_dir,
                task_ability_prompt="bon8 手调提示词：严格比较布局、功能和文案一致性。",
            )
            provider_runtimes = []

            def provider_client(_item_content, runtime):
                provider_runtimes.append(runtime)
                return {"content": _provider_json("model5"), "elapsed_ms": 23, "provider_status": "provider_ok"}

            prepare_bon8_first_item_review_with_ai(
                result.run_id,
                provider_client=provider_client,
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=lambda _account, _kind, path, _body: _ok_base(data=[], total_map={"0": 0}) if path == "/dispatcher/search_item/category" else _ok_base(),
                state_dir=state_dir,
            )

            self.assertEqual(len(provider_runtimes), 1)
            self.assertEqual(provider_runtimes[0].get("task_ability_prompt"), "bon8 手调提示词：严格比较布局、功能和文案一致性。")
        db.close()

    def test_execute_auto_account_tick_with_ai_submits_current_item_and_records_timer(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            first = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-002"]),
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=state_dir,
            )
            prepare_bon8_first_item_review_with_ai(
                first.run_id,
                provider_client=lambda _content, _runtime: {"content": _provider_json("model3"), "elapsed_ms": 17, "provider_status": "provider_ok"},
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=lambda _account, _kind, path, _body: _ok_base(data=[], total_map={"0": 0}) if path == "/dispatcher/search_item/category" else _ok_base(),
                state_dir=state_dir,
            )
            approve_bon8_run_confirmation(first.run_id, first.confirmation_sheet.confirmation_id, state_dir=state_dir)
            submit_approved_bon8_first_item(
                first.run_id,
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=lambda _account, _kind, path, _body: _ok_base(data=[], total_map={"0": 0}) if path == "/dispatcher/search_item/category" else _ok_base(),
                state_dir=state_dir,
            )
            plan_bon8_parallel_account_ticks(first.run_id, state_dir=state_dir)
            calls = []

            def transport(_account, kind, path, body):
                calls.append((kind, path, body))
                if path == "/dispatcher/search_item/category" and len([call for call in calls if call[1] == path]) == 1:
                    item_content = {
                        "mediaUrls": ["https://example.test/next.png"],
                        **{f"model{index}": {"html": f"https://example.test/next-model{index}.html"} for index in range(1, 9)},
                    }
                    return _ok_base(data=[{"ItemID": "item-2", "Content": json.dumps(item_content, ensure_ascii=False), "Status": 4}], total_map={"0": 1})
                if path == "/dispatcher/search_item/category":
                    return _ok_base(data=[], total_map={"0": 0, "1": 2})
                return _ok_base()

            updated = execute_bon8_account_tick_with_ai(
                first.run_id,
                "account-sample-002",
                provider_client=lambda _content, _runtime: {"content": _provider_json("model6"), "elapsed_ms": 19, "provider_status": "provider_ok"},
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=transport,
                state_dir=state_dir,
                timer_event_log_path=state_dir / "events.jsonl",
            )

            self.assertEqual([call[1] for call in calls], ["/dispatcher/search_item/category", "/api/dispatch/SubmitTempItemAnswer", "/dispatcher/verify/submit", "/api/dispatch/SubmitItem", "/dispatcher/search_item/category"])
            self.assertEqual(calls[3][2]["TaskID"], "7637771731901861641")
            self.assertEqual(calls[3][2]["Status"], 4)
            self.assertEqual(updated.submit_count, 2)
            self.assertEqual(updated.accounts[0].status, "running_auto")
            self.assertEqual(updated.accounts[0].success_count, 2)
            self.assertEqual(updated.attempts[-1].stage, "submitted")
            self.assertEqual(updated.attempts[-1].item_id, "item-2")
            self.assertEqual(updated.attempts[-1].timer_status, "recorded")
            self.assertTrue((state_dir / "events.jsonl").exists())
        db.close()

    def test_execute_auto_account_tick_marks_operation_needed_when_no_current_item(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            first = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-002"]),
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=state_dir,
            )
            prepare_bon8_first_item_review_with_ai(
                first.run_id,
                provider_client=lambda _content, _runtime: {"content": _provider_json("model3"), "elapsed_ms": 17, "provider_status": "provider_ok"},
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=lambda _account, _kind, path, _body: _ok_base(data=[], total_map={"0": 0}) if path == "/dispatcher/search_item/category" else _ok_base(),
                state_dir=state_dir,
            )
            approve_bon8_run_confirmation(first.run_id, first.confirmation_sheet.confirmation_id, state_dir=state_dir)
            submit_approved_bon8_first_item(
                first.run_id,
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=lambda _account, _kind, path, _body: _ok_base(data=[], total_map={"0": 0}) if path == "/dispatcher/search_item/category" else _ok_base(),
                state_dir=state_dir,
            )
            plan_bon8_parallel_account_ticks(first.run_id, state_dir=state_dir)
            calls = []

            def transport(_account, _kind, path, _body):
                calls.append(path)
                if path == "/dispatcher/search_item/category":
                    return _ok_base(data=[], total_map={"0": 0})
                raise AssertionError(f"unexpected submit call: {path}")

            updated = execute_bon8_account_tick_with_ai(
                first.run_id,
                "account-sample-002",
                provider_client=lambda _content, _runtime: {"content": _provider_json("model6"), "elapsed_ms": 19, "provider_status": "provider_ok"},
                account_loader=lambda user_id: {"userId": user_id, "name": "用户样例002", "cookie": "sessionid=test"},
                transport=transport,
                state_dir=state_dir,
            )

            self.assertEqual(calls, ["/dispatcher/search_item/category"])
            self.assertEqual(updated.accounts[0].status, "waiting_operation_claim")
            self.assertEqual(updated.accounts[0].no_item_count, 1)
            self.assertEqual(updated.attempts[-1].stage, "operation_claim_needed")
        db.close()

    def test_execute_run_tick_plans_and_runs_each_account_once(self) -> None:
        db = _session()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            first = start_bon8_production(
                db,
                Bon8ProductionStartRequest(account_user_ids=["account-sample-002", "account-sample-004"]),
                account_loader=lambda user_id: {"userId": user_id, "name": f"用户{user_id[-4:]}", "cookie": "sessionid=test"},
                transport=_category_transport,
                state_dir=state_dir,
            )
            prepare_bon8_first_item_review_with_ai(
                first.run_id,
                provider_client=lambda _content, _runtime: {"content": _provider_json("model3"), "elapsed_ms": 17, "provider_status": "provider_ok"},
                account_loader=lambda user_id: {"userId": user_id, "name": f"用户{user_id[-4:]}", "cookie": "sessionid=test"},
                transport=lambda _account, _kind, path, _body: _ok_base(data=[], total_map={"0": 0}) if path == "/dispatcher/search_item/category" else _ok_base(),
                state_dir=state_dir,
            )
            approve_bon8_run_confirmation(first.run_id, first.confirmation_sheet.confirmation_id, state_dir=state_dir)
            submit_approved_bon8_first_item(
                first.run_id,
                account_loader=lambda user_id: {"userId": user_id, "name": f"用户{user_id[-4:]}", "cookie": "sessionid=test"},
                transport=lambda _account, _kind, path, _body: _ok_base(data=[], total_map={"0": 0}) if path == "/dispatcher/search_item/category" else _ok_base(),
                state_dir=state_dir,
            )
            calls = []
            provider_inputs = []

            def transport(account, kind, path, body):
                user_id = account["userId"]
                calls.append((user_id, kind, path, body))
                user_category_calls = [call for call in calls if call[0] == user_id and call[2] == "/dispatcher/search_item/category"]
                if path == "/dispatcher/search_item/category" and user_id.endswith("3620") and len(user_category_calls) == 1:
                    item_content = {
                        "mediaUrls": ["https://example.test/run-tick.png"],
                        **{f"model{index}": {"html": f"https://example.test/run-model{index}.html"} for index in range(1, 9)},
                    }
                    return _ok_base(data=[{"ItemID": "item-run-2", "Content": json.dumps(item_content, ensure_ascii=False), "Status": 4}], total_map={"0": 1})
                if path == "/dispatcher/search_item/category":
                    return _ok_base(data=[], total_map={"0": 0})
                return _ok_base()

            def provider_client(item_content, _runtime):
                provider_inputs.append(item_content)
                return {"content": _provider_json("model7"), "elapsed_ms": 21, "provider_status": "provider_ok"}

            updated = execute_bon8_run_tick_with_ai(
                first.run_id,
                provider_client=provider_client,
                account_loader=lambda user_id: {"userId": user_id, "name": f"用户{user_id[-4:]}", "cookie": "sessionid=test"},
                transport=transport,
                state_dir=state_dir,
                timer_event_log_path=state_dir / "events.jsonl",
            )

            self.assertEqual(len(provider_inputs), 1)
            submit_calls = [call for call in calls if call[2] == "/api/dispatch/SubmitItem"]
            self.assertEqual(len(submit_calls), 1)
            account_status = {account.account_user_id: account.status for account in updated.accounts}
            self.assertEqual(account_status["account-sample-002"], "running_auto")
            self.assertEqual(account_status["account-sample-004"], "waiting_operation_claim")
            self.assertEqual(updated.submit_count, 2)
            operation_attempts = [attempt for attempt in updated.attempts if attempt.stage == "operation_claim_needed"]
            self.assertEqual(len(operation_attempts), 1)
            active_queued = [attempt for attempt in updated.attempts if attempt.stage == "queued_account_tick" and attempt.finished_at is None]
            self.assertEqual(active_queued, [])
        db.close()


if __name__ == "__main__":
    unittest.main()
