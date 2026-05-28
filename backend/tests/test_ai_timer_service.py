import json
import tempfile
import unittest
from pathlib import Path

from app.schemas.ai_timer import AiTimerEventCreate, AiTimerStageDuration
from app.services.ai_timer_service import build_ai_timer_summary, record_ai_timer_event


class AiTimerServiceTests(unittest.TestCase):
    def test_recorded_events_build_efficiency_and_income_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_log = root / "events.jsonl"
            record_ai_timer_event(
                AiTimerEventCreate(
                    account_user_id="account-sample-002",
                    account_name="用户样例002",
                    task_id="7637771731901861641",
                    task_name="bon8",
                    item_id="item-1",
                    status="submitted",
                    total_ms=4000,
                    stages=[
                        AiTimerStageDuration(stage="read", duration_ms=900),
                        AiTimerStageDuration(stage="ai", duration_ms=1500),
                        AiTimerStageDuration(stage="submit", duration_ms=1600),
                    ],
                ),
                event_log_path=event_log,
            )
            record_ai_timer_event(
                AiTimerEventCreate(
                    account_user_id="account-sample-002",
                    task_id="7637771731901861641",
                    item_id="item-2",
                    status="submitted",
                    total_ms=6000,
                    stages=[
                        AiTimerStageDuration(stage="read", duration_ms=1000),
                        AiTimerStageDuration(stage="ai", duration_ms=3000),
                        AiTimerStageDuration(stage="submit", duration_ms=2000),
                    ],
                ),
                event_log_path=event_log,
            )

            summary = build_ai_timer_summary(event_log_path=event_log, production_runs_root=root / "runs", unit_price=0.25)

            self.assertEqual(summary.total_items, 2)
            self.assertEqual(summary.submitted_items, 2)
            self.assertEqual(summary.avg_total_ms, 5000)
            self.assertEqual(summary.p50_total_ms, 5000)
            self.assertEqual(summary.p95_total_ms, 6000)
            self.assertEqual(summary.questions_per_hour, 720)
            self.assertEqual(summary.estimated_hourly_income, 180)
            self.assertEqual(summary.slowest_stage.stage, "上游 AI 往返")
            self.assertEqual(summary.stage_breakdown[0].stage, "上游 AI 往返")

    def test_summary_imports_existing_bon8_http_submit_result_timings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_dir = root / "runs" / "bon8-20260510"
            result_dir.mkdir(parents=True)
            (result_dir / "item-3-new-rule-http-submit-result.json").write_text(
                json.dumps(
                    {
                        "generatedAt": "2026-05-10T10:00:00+0800",
                        "userId": "account-sample-002",
                        "accountName": "用户样例002",
                        "taskId": "7637771731901861641",
                        "itemId": "item-3",
                        "ok": True,
                        "timingsMs": {
                            "payloadBuild": 50,
                            "categoryBefore": 800,
                            "submitTemp": 700,
                            "verifySubmit": 900,
                            "submitItem": 750,
                            "categoryAfter": 850,
                            "total": 4050,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = build_ai_timer_summary(event_log_path=root / "events.jsonl", production_runs_root=root / "runs", unit_price=0.5)

            self.assertEqual(summary.total_items, 1)
            self.assertEqual(summary.submitted_items, 1)
            self.assertEqual(summary.avg_total_ms, 4050)
            self.assertEqual(summary.questions_per_hour, 888.89)
            self.assertEqual(summary.estimated_hourly_income, 444.44)
            self.assertEqual(summary.recent_items[0].source, "bon8_http_result")
            self.assertEqual(summary.recent_items[0].stages[0].stage, "整理答案")
            self.assertEqual(summary.recent_items[0].stages[1].stage, "读提交前状态")

    def test_summary_counts_upstream_ai_roundtrip_from_submit_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_dir = root / "runs" / "bon8-20260510"
            result_dir.mkdir(parents=True)
            (result_dir / "item-4-new-rule-http-submit-result.json").write_text(
                json.dumps(
                    {
                        "generatedAt": "2026-05-10T10:00:00+0800",
                        "userId": "account-sample-002",
                        "accountName": "用户样例002",
                        "taskId": "7637771731901861641",
                        "itemId": "item-4",
                        "ok": True,
                        "provider_elapsed_ms": 2500,
                        "timingsMs": {
                            "submitTemp": 700,
                            "verifySubmit": 900,
                            "submitItem": 750,
                            "categoryAfter": 850,
                            "total": 3200,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = build_ai_timer_summary(event_log_path=root / "events.jsonl", production_runs_root=root / "runs", unit_price=0)

            self.assertEqual(summary.total_items, 1)
            self.assertEqual(summary.avg_total_ms, 5700)
            stages = {item.stage: item.avg_duration_ms for item in summary.stage_breakdown}
            self.assertEqual(stages["上游 AI 往返"], 2500)
            self.assertIn("上游 AI 往返", [item.stage for item in summary.recent_items[0].stages])

    def test_summary_dedupes_same_replay_from_event_and_result_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_log = root / "events.jsonl"
            stages = [
                AiTimerStageDuration(stage="读题", duration_ms=8),
                AiTimerStageDuration(stage="上游 AI 往返", duration_ms=27779),
                AiTimerStageDuration(stage="整理答案", duration_ms=1),
                AiTimerStageDuration(stage="暂存答案", duration_ms=923),
            ]
            record_ai_timer_event(
                AiTimerEventCreate(
                    account_user_id="account-sample-002",
                    account_name="用户样例002",
                    task_id="7637771731901861641",
                    task_name="bon8",
                    item_id="7637774211302166322",
                    status="submitted_replay_duplicate",
                    source="bon8_ai_rejudge_replay",
                    total_ms=31930,
                    stages=stages,
                ),
                event_log_path=event_log,
            )
            result_dir = root / "runs" / "bon8-ai-rejudge-20260510"
            result_dir.mkdir(parents=True)
            (result_dir / "7637774211302166322-ai-rejudge-http-submit-result.json").write_text(
                json.dumps(
                    {
                        "generatedAt": "2026-05-10T12:34:58+0800",
                        "userId": "account-sample-002",
                        "accountName": "用户样例002",
                        "taskId": "7637771731901861641",
                        "itemId": "7637774211302166322",
                        "ok": True,
                        "timingsMs": {
                            "read": 8,
                            "upstreamAiElapsedMs": 27779,
                            "payloadBuild": 1,
                            "submitTemp": 923,
                            "total": 31930,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = build_ai_timer_summary(event_log_path=event_log, production_runs_root=root / "runs", unit_price=0)

            self.assertEqual(summary.total_items, 1)
            self.assertEqual(summary.recent_items[0].source, "bon8_ai_rejudge_replay")
            self.assertEqual(summary.recent_items[0].status, "submitted_replay_duplicate")
            stage_counts = {item.stage: item.sample_count for item in summary.stage_breakdown}
            self.assertEqual(stage_counts["上游 AI 往返"], 1)


if __name__ == "__main__":
    unittest.main()
