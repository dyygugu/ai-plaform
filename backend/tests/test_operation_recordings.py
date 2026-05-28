import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class OperationRecordingTests(unittest.TestCase):
    def test_upload_full_recording_stores_sanitized_artifact(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{Path(tmpdir) / 'aidp-test.db'}"
            os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(Path(tmpdir) / "operation-recordings")
            os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
            settings_module = importlib.import_module("app.core.settings")
            settings_module.get_settings.cache_clear()
            main_module = importlib.import_module("app.main")
            app = main_module.create_app()

            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/operation-recordings",
                    json={
                        "mode": "full",
                        "source": "aidp-score-helper-extension",
                        "account_user_id": "account-sample-002",
                        "task_id": "7634537456234385161",
                        "page_url": "https://aidp.juejin.cn/operation/task-v2/demo/mark-v3/",
                        "started_at": "2026-05-07T12:00:00Z",
                        "ended_at": "2026-05-07T12:01:00Z",
                        "events": [{"type": "click", "text": "提交", "authorization": "Bearer secret-token"}],
                        "network": [{"url": "/api/demo?token=secret-token", "request_body": {"cookie": "sid=secret-token"}}],
                        "dom_snapshots": [{"title": "返修题", "token": "secret-token"}],
                        "screenshots": [{"label": "end", "data_url": "data:image/jpeg;base64,abc"}],
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            artifact_path = Path(payload["artifact_path"])
            self.assertTrue(artifact_path.exists())

            stored = artifact_path.read_text(encoding="utf-8")
            self.assertIn('"mode": "full"', stored)
            self.assertIn("[REDACTED]", stored)
            self.assertNotIn("secret-token", stored)
            self.assertEqual(payload["mode"], "full")
            self.assertEqual(payload["event_count"], 1)
            self.assertEqual(payload["network_count"], 1)
            self.assertEqual(payload["operation_claim_analysis"]["status"], "not_captured")
            self.assertEqual(payload["operation_claim_analysis"]["candidate_count"], 0)

    def test_upload_recording_reports_operation_claim_candidates(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{Path(tmpdir) / 'aidp-test.db'}"
            os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(Path(tmpdir) / "operation-recordings")
            os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
            settings_module = importlib.import_module("app.core.settings")
            settings_module.get_settings.cache_clear()
            main_module = importlib.import_module("app.main")
            app = main_module.create_app()

            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/operation-recordings",
                    json={
                        "mode": "full",
                        "source": "aidp-score-helper-extension",
                        "account_user_id": "account-sample-002",
                        "task_id": "7637771731901861641",
                        "page_url": "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1",
                        "started_at": "2026-05-09T13:21:20Z",
                        "ended_at": "2026-05-09T13:21:35Z",
                        "events": [{"type": "click", "text": "处理"}],
                        "network": [
                            {
                                "type": "request",
                                "method": "POST",
                                "url": "https://aidp.juejin.cn/task/agreement/check",
                                "request_body": {"task_ids": ["7637771731901861641"]},
                            },
                            {
                                "type": "request",
                                "method": "POST",
                                "url": "https://aidp.juejin.cn/api/dispatch/Receive",
                                "request_body": {"TaskID": "7637771731901861641", "NodeID": 1},
                            },
                        ],
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            analysis = response.json()["operation_claim_analysis"]
            self.assertEqual(analysis["status"], "candidate_found")
            self.assertEqual(analysis["candidate_count"], 1)
            self.assertEqual(analysis["candidates"][0]["path"], "/api/dispatch/Receive")
            self.assertIn("人工复核", analysis["message"])

    def test_upload_recording_registers_task_learning_package(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{Path(tmpdir) / 'aidp-test.db'}"
            os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(Path(tmpdir) / "operation-recordings")
            os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
            settings_module = importlib.import_module("app.core.settings")
            settings_module.get_settings.cache_clear()
            main_module = importlib.import_module("app.main")
            app = main_module.create_app()

            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/operation-recordings",
                    json={
                        "recording_id": "rec_20260518_223100",
                        "mode": "full",
                        "source": "browser_extension",
                        "account_user_id": "account-sample-002",
                        "task_id": "7639402643386830630",
                        "task_id_candidates": [{"value": "7639402643386830630", "source": "url", "confidence": "high"}],
                        "page_url": "https://aidp.juejin.cn/operation/task-v2?page=1",
                        "recorded_at": "2026-05-18T22:31:00+08:00",
                        "detected_actions": ["fill_score", "fill_reason", "click_temp_save"],
                        "events": [],
                        "network": [],
                        "screenshots": [],
                    },
                )

                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["task_id"], "7639402643386830630")
                self.assertEqual(payload["learning_package"]["learning_package_id"], "rec_20260518_223100")
                self.assertEqual(payload["learning_package"]["completeness"], "complete")

                packages = client.get("/api/v1/task-abilities/7639402643386830630/learning-packages")
                self.assertEqual(packages.status_code, 200, packages.text)
                package_payload = packages.json()
                self.assertEqual(package_payload["selected_learning_package_id"], "rec_20260518_223100")
                self.assertEqual(package_payload["items"][0]["learning_package_id"], "rec_20260518_223100")

    def test_learning_package_summary_includes_recording_context(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{Path(tmpdir) / 'aidp-test.db'}"
            os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(Path(tmpdir) / "operation-recordings")
            os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
            settings_module = importlib.import_module("app.core.settings")
            settings_module.get_settings.cache_clear()
            main_module = importlib.import_module("app.main")
            app = main_module.create_app()
            learning_package_module = importlib.import_module("app.services.learning_package_service")

            request_body = {
                "TaskID": "7639402643386830630",
                "NodeID": "1",
                "AuditAnswers": [
                    {
                        "ItemID": "item-1",
                        "Content": json.dumps(
                            {
                                "item": {"image_gt": "https://example.com/ref.png", "model_image": "https://example.com/model.png"},
                                "data": {
                                    "label_sorce": {"model_image": "2"},
                                    "label_remark": {"model_image": "结构一致"},
                                    "discard": "No",
                                },
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            }

            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/operation-recordings",
                    json={
                        "recording_id": "rec_20260521_020000",
                        "mode": "full",
                        "source": "browser_extension",
                        "account_user_id": "account-sample-002",
                        "task_id": "7639402643386830630",
                        "task_id_candidates": [{"value": "7639402643386830630", "source": "url", "confidence": "high"}],
                        "page_url": "https://aidp.juejin.cn/operation/task-v2/demo/mark-v3/",
                        "recorded_at": "2026-05-21T02:00:00+08:00",
                        "detected_actions": ["fill_score", "fill_reason", "click_temp_save"],
                        "dom_snapshots": [{"title": "mark-v3 评分页"}],
                        "network": [
                            {
                                "type": "request",
                                "method": "POST",
                                "url": "https://aidp.juejin.cn/api/dispatch/Receive",
                                "request_body": json.dumps({"Filter": {"TaskID": "7639402643386830630", "NodeID": 1}}, ensure_ascii=False),
                            },
                            {
                                "type": "request",
                                "method": "POST",
                                "url": "https://aidp.juejin.cn/api/dispatch/SubmitTempItemAnswer",
                                "request_body": json.dumps(request_body, ensure_ascii=False),
                            },
                        ],
                        "screenshots": [],
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            summary = learning_package_module.get_selected_learning_package_summary("7639402643386830630")
            self.assertIn("关键接口：/api/dispatch/Receive、/api/dispatch/SubmitTempItemAnswer", summary.summary_text)
            self.assertIn("题面材料字段：image_gt、model_image", summary.summary_text)
            self.assertIn("暂存答案字段：data.label_sorce.model_image、data.label_remark.model_image、data.discard", summary.summary_text)
            self.assertIn("页面快照：mark-v3 评分页", summary.summary_text)
            self.assertIn("领题候选：/api/dispatch/Receive", summary.summary_text)

    def test_aidp_page_origin_can_upload_recording_directly(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{Path(tmpdir) / 'aidp-test.db'}"
            os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(Path(tmpdir) / "operation-recordings")
            os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
            settings_module = importlib.import_module("app.core.settings")
            settings_module.get_settings.cache_clear()
            main_module = importlib.import_module("app.main")
            app = main_module.create_app()

            with TestClient(app) as client:
                response = client.options(
                    "/api/v1/operation-recordings",
                    headers={
                        "Origin": "https://aidp.juejin.cn",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type",
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers.get("access-control-allow-origin"), "https://aidp.juejin.cn")

    def test_aidp_page_origin_private_network_preflight_allows_local_upload(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{Path(tmpdir) / 'aidp-test.db'}"
            os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(Path(tmpdir) / "operation-recordings")
            os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
            settings_module = importlib.import_module("app.core.settings")
            settings_module.get_settings.cache_clear()
            main_module = importlib.import_module("app.main")
            app = main_module.create_app()

            with TestClient(app) as client:
                response = client.options(
                    "/api/v1/operation-recordings",
                    headers={
                        "Origin": "https://aidp.juejin.cn",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type",
                        "Access-Control-Request-Private-Network": "true",
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers.get("access-control-allow-origin"), "https://aidp.juejin.cn")
            self.assertEqual(response.headers.get("access-control-allow-private-network"), "true")


if __name__ == "__main__":
    unittest.main()
