import json
from pathlib import Path

from app.services.research_chart_task_service import (
    RESEARCH_CHART_TASK_ID,
    build_research_chart_ability_draft,
    build_research_chart_dry_run_payload,
    extract_research_chart_samples,
    upsert_research_chart_ability_draft,
)


def _write_recording(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)

    def payload(item_id: str, uid: str, score: str, reason: str) -> dict:
        content = {
            "item": {
                "uid": uid,
                "image_gt": f"https://example.test/{uid}/gt.png",
                "model_image": f"https://example.test/{uid}/generated.png",
            },
            "itemID": item_id,
            "data": {
                "checkRemark": None,
                "discard": "No",
                "discard_type": [],
                "discard_remark": None,
                "label_sorce": {"model_image": score},
                "label_remark": {"model_image": reason},
            },
            "dataMap": {},
        }
        return {
            "TaskID": RESEARCH_CHART_TASK_ID,
            "NodeID": "1",
            "StagingTime": "604800",
            "AuditAnswers": [
                {
                    "ItemID": item_id,
                    "Content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                    "ControlData": json.dumps({"Discard": False, "extraAnswer": []}, ensure_ascii=False),
                }
            ],
        }

    document = {
        "recording_id": "opr-research-chart-test",
        "recording": {
            "task_id": RESEARCH_CHART_TASK_ID,
            "page_url": f"https://aidp.juejin.cn/operation/task-v2/{RESEARCH_CHART_TASK_ID}/mark-v3/1",
            "network": [
                {
                    "url": "https://aidp.juejin.cn/api/dispatch/SubmitTempItemAnswer",
                    "request_body": json.dumps(payload("item-0", "chart-0.png", "0", "点位和网格明显不一致"), ensure_ascii=False),
                    "response_body": json.dumps({"BaseResp": {"StatusCode": 0}}, ensure_ascii=False),
                },
                {
                    "url": "https://aidp.juejin.cn/api/dispatch/SubmitTempItemAnswer",
                    "request_body": json.dumps(payload("item-1", "chart-1.png", "1", "轻微文字间距偏差"), ensure_ascii=False),
                    "response_body": json.dumps({"BaseResp": {"StatusCode": 0}}, ensure_ascii=False),
                },
                {
                    "url": "https://aidp.juejin.cn/api/dispatch/SubmitTempItemAnswer",
                    "request_body": json.dumps(payload("item-2", "chart-2.png", "2", "精准复刻"), ensure_ascii=False),
                    "response_body": json.dumps({"BaseResp": {"StatusCode": 0}}, ensure_ascii=False),
                },
            ],
        },
    }
    (root / "opr-research-chart-test.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def test_extract_research_chart_samples_dedupes_scores_and_fields(tmp_path: Path) -> None:
    _write_recording(tmp_path)

    summary = extract_research_chart_samples(tmp_path)

    assert summary["task_id"] == RESEARCH_CHART_TASK_ID
    assert summary["node_id"] == "1"
    assert summary["unique_items"] == 3
    assert summary["score_counts"] == {"0": 1, "1": 1, "2": 1}
    assert summary["field_mapping"]["score"] == "data.label_sorce.model_image"
    assert summary["field_mapping"]["reason"] == "data.label_remark.model_image"
    assert summary["examples_by_score"]["0"][0]["reason"] == "点位和网格明显不一致"
    assert summary["examples_by_score"]["1"][0]["has_image_gt"]
    assert summary["examples_by_score"]["2"][0]["has_model_image"]


def test_build_research_chart_ability_draft_is_strict_and_review_only(tmp_path: Path) -> None:
    _write_recording(tmp_path)
    summary = extract_research_chart_samples(tmp_path)

    draft = build_research_chart_ability_draft(summary)

    assert "RFT科研图表还原" in draft
    assert "完全符合才给 2 分" in draft
    assert "约 95% 相似" in draft
    assert "不确定时给 0 分" in draft
    assert "data.label_sorce.model_image" in draft
    assert "本阶段只做 dry-run" in draft


def test_build_research_chart_dry_run_payload_maps_only_answer_fields(tmp_path: Path) -> None:
    _write_recording(tmp_path)
    summary = extract_research_chart_samples(tmp_path)

    dry_run = build_research_chart_dry_run_payload(summary["latest_payload"], score="0", reason="文字和曲线位置明显偏差")
    content = json.loads(dry_run["payload"]["AuditAnswers"][0]["Content"])

    assert dry_run["writes_remote"] is False
    assert dry_run["submits_remote"] is False
    assert dry_run["allowed_endpoint"] == "/api/dispatch/SubmitTempItemAnswer"
    assert content["data"]["label_sorce"]["model_image"] == "0"
    assert content["data"]["label_remark"]["model_image"] == "文字和曲线位置明显偏差"
    assert content["data"]["discard"] == "No"
    assert set(content["data"].keys()) >= {"label_sorce", "label_remark", "discard", "discard_type"}


def test_upsert_research_chart_ability_draft_writes_frontend_draft_store(tmp_path: Path) -> None:
    _write_recording(tmp_path / "recordings")
    summary = extract_research_chart_samples(tmp_path / "recordings")
    draft_text = build_research_chart_ability_draft(summary)
    store_path = tmp_path / "task-abilities" / "ability-drafts.json"

    first = upsert_research_chart_ability_draft(store_path, summary, draft_text)
    second = upsert_research_chart_ability_draft(store_path, summary, draft_text)
    stored = json.loads(store_path.read_text(encoding="utf-8"))

    assert first["task_id"] == RESEARCH_CHART_TASK_ID
    assert second["id"] == first["id"]
    assert len(stored["items"]) == 1
    assert stored["items"][0]["task_name"] == "RFT科研图表还原-正式(随机5000题)"
    assert "本阶段只做 dry-run" in stored["items"][0]["system_ai_draft"]
