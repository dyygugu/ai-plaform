import json
import unittest

from app.services.bon8_payload_service import build_bon8_submit_temp_payload


def _content() -> dict:
    return {
        "isVideoNeed": False,
        "mediaUrls": ["https://example.test/input.png"],
        "model1": {"html": "https://example.test/model1.html"},
        "model2": {"html": "https://example.test/model2.html"},
        "model3": {"html": "https://example.test/model3.html"},
        "prompt": "把图里的流程做出来",
        "scoringGuidelines": "完美符合1分；有瑕疵0分。",
    }


def _decoded_content(payload: dict) -> dict:
    return json.loads(payload["AuditAnswers"][0]["Content"])


class Bon8PayloadServiceTests(unittest.TestCase):
    def test_build_bon8_payload_maps_scores_to_checkbox_issues_with_score_reasons_without_audit_remarks(self) -> None:
        payload = build_bon8_submit_temp_payload(
            task_id="task-1",
            node_id=1,
            item_id="item-1",
            item_content=_content(),
            scores={"model1": "0", "model2": "1", "model3": "2"},
            sort_models=["model3", "model2", "model1"],
            score_reasons={
                "model1": "白屏且核心内容缺失。",
                "model2": "结构接近但功能入口不足。",
                "model3": "整体最完整。",
            },
        )

        decoded = _decoded_content(payload)
        data = decoded["data"]
        data_map = decoded["dataMap"]

        self.assertEqual(payload["TaskID"], "task-1")
        self.assertEqual(payload["NodeID"], "1")
        self.assertEqual(payload["AuditAnswers"][0]["ItemID"], "item-1")
        self.assertEqual(data["overallScore"], {"model1": "0", "model2": "1", "model3": "2"})
        self.assertEqual(data["sceneConsistencyIssues"], {"model1": ["视觉不足"]})
        self.assertEqual(data["objectCompletenessIssues"], {"model1": ["功能不足"], "model2": ["功能不足"]})
        self.assertEqual(
            data["modelRemarks"],
            {"model1": "白屏且核心内容缺失。", "model2": "结构接近但功能入口不足。", "model3": "整体最完整。"},
        )
        self.assertEqual(data["sceneConsistencyRemarks"], data["modelRemarks"])
        self.assertEqual(
            data["lowScoreReason"],
            {"model1": "白屏且核心内容缺失。", "model2": "结构接近但功能入口不足。"},
        )
        self.assertEqual(data["sortModels"], ["model3", "model2", "model1"])
        self.assertEqual(data_map, data)

        for audit_remark_field in (
            "checkRemark",
            "discard_remark",
            "videoLowScoreReason",
        ):
            self.assertNotIn(audit_remark_field, data)
            self.assertNotIn(audit_remark_field, data_map)

    def test_build_bon8_payload_rejects_multiple_best_models(self) -> None:
        with self.assertRaisesRegex(ValueError, "只能有一个 2 分"):
            build_bon8_submit_temp_payload(
                task_id="task-1",
                node_id=1,
                item_id="item-1",
                item_content=_content(),
                scores={"model1": "2", "model2": "2"},
            )


if __name__ == "__main__":
    unittest.main()
