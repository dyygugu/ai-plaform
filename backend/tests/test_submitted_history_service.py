import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _sample_payload(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "node_id": 1,
        "submitted": {
            "submitted_total": 3,
            "item_ids": ["item-1", "item-2", "item-3"],
            "status_counts": {"3": 2, "7": 1},
            "items": [
                {"ItemID": "item-1", "Status": 3},
                {"ItemID": "item-2", "Status": 3},
                {"ItemID": "item-3", "Status": 7},
            ],
        },
        "answers": {
            "answer_key_count": 3,
            "nonempty_answer_key_count": 3,
            "answer_list": {
                "item-1": [{"NodeName": "标注", "NodeAnswer": "{\"item\":{\"uid\":\"sample-001\"},\"answer\":{\"score\":\"1\"}}"}],
                "item-2": [{"NodeName": "质检", "NodeAnswer": "{\"item\":{\"uid\":\"sample-002\"},\"answer\":{\"score\":\"2\"}}"}],
                "item-3": [{"NodeName": "标注", "NodeAnswer": "{\"item\":{\"uid\":\"sample-003\"},\"answer\":{\"score\":\"3\"}}"}],
            },
        },
        "sample_item_ids": ["item-1", "item-2", "item-3"],
    }


class SubmittedHistoryApiTests(unittest.TestCase):
    def _create_app(self, tmpdir: str):
        os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{Path(tmpdir) / 'aidp-test.db'}"
        os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(Path(tmpdir) / "operation-recordings")
        os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
        os.environ["AIDP_PRODUCTION_STATE_PATH"] = str(Path(tmpdir) / "production-state.json")
        settings_module = importlib.import_module("app.core.settings")
        settings_module.get_settings.cache_clear()
        main_module = importlib.import_module("app.main")
        return main_module.create_app()

    def test_sync_submitted_history_writes_manifest_and_samples(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            app = self._create_app(tmpdir)
            task_id = "7639402643386830630"
            with patch("app.services.submitted_history_service.read_all_submitted_task_payloads", return_value=_sample_payload(task_id)):
                with patch("app.services.submitted_history_service.load_account_with_cookie", return_value={"userId": "account-sample-002", "name": "用户样例002", "cookie": "sessionid=test"}):
                    with TestClient(app) as client:
                        response = client.post(f"/api/v1/tasks/{task_id}/submitted-history/sync", json={"account_id": "account-sample-002"})

                        self.assertEqual(response.status_code, 200, response.text)
                        payload = response.json()
                        self.assertEqual(payload["task_id"], task_id)
                        self.assertEqual(payload["sample_count"], 3)
                        self.assertEqual(payload["new_count"], 3)
                        self.assertTrue(Path(payload["manifest_path"]).exists())

                        stats = client.get(f"/api/v1/tasks/{task_id}/submitted-history/stats")
                        self.assertEqual(stats.status_code, 200, stats.text)
                        self.assertEqual(stats.json()["sample_count"], 3)
                        self.assertEqual(stats.json()["sample_pool_count"], 3)

                        listed = client.get(f"/api/v1/tasks/{task_id}/submitted-history")
                        self.assertEqual(listed.status_code, 200, listed.text)
                        self.assertEqual(len(listed.json()["items"]), 3)

                        sample = client.get(f"/api/v1/tasks/{task_id}/submitted-history/sample-001")
                        self.assertEqual(sample.status_code, 200, sample.text)
                        self.assertEqual(sample.json()["uid"], "sample-001")
                        self.assertEqual(sample.json()["item_id"], "item-1")

    def test_generate_and_save_fixed_testset_from_submitted_history(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            app = self._create_app(tmpdir)
            task_id = "7639402643386830630"
            with patch("app.services.submitted_history_service.read_all_submitted_task_payloads", return_value=_sample_payload(task_id)):
                with patch("app.services.submitted_history_service.load_account_with_cookie", return_value={"userId": "account-sample-002", "name": "用户样例002", "cookie": "sessionid=test"}):
                    with TestClient(app) as client:
                        sync = client.post(f"/api/v1/tasks/{task_id}/submitted-history/sync", json={"account_id": "account-sample-002"})
                        self.assertEqual(sync.status_code, 200, sync.text)

                        generated = client.post(f"/api/v1/tasks/{task_id}/testset/generate", json={"sample_count": 2})
                        self.assertEqual(generated.status_code, 200, generated.text)
                        generated_payload = generated.json()
                        self.assertEqual(generated_payload["sample_count"], 2)
                        self.assertEqual(len(generated_payload["sample_ids"]), 2)

                        saved = client.post(f"/api/v1/tasks/{task_id}/testset/save", json={"sample_ids": generated_payload["sample_ids"]})
                        self.assertEqual(saved.status_code, 200, saved.text)
                        saved_payload = saved.json()
                        self.assertEqual(saved_payload["task_id"], task_id)
                        self.assertTrue(Path(saved_payload["path"]).exists())

                        fetched = client.get(f"/api/v1/tasks/{task_id}/testset")
                        self.assertEqual(fetched.status_code, 200, fetched.text)
                        self.assertEqual(fetched.json()["sample_ids"], generated_payload["sample_ids"])


if __name__ == "__main__":
    unittest.main()
