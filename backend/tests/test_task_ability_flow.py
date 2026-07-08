import json
from pathlib import Path

import pytest
import requests

from app.services.task_ability_service import (
    approve_task_ability_version,
    create_prompt_snapshot,
    get_task_ability_live_http_test_report,
    TaskAbilityFlowError,
    approve_task_ability_draft,
    approve_task_ability_real_no_submit,
    list_task_ability_drafts,
    restore_prompt_snapshot,
    run_task_ability_live_http_test,
    run_task_ability_dry_run,
    run_task_ability_real_no_submit,
    update_task_ability_draft,
    _prompt_fingerprint,
    _temp_save_succeeded,
)


def _write_recorded_temp_payload(store: Path, *, task_id: str = "7638992213846740763") -> None:
    payload_dir = store.parent / f"research-chart-{task_id}"
    payload_dir.mkdir(parents=True, exist_ok=True)
    content = {
        "item": {
            "uid": "recorded-uid",
            "image_gt": "https://example.com/recorded-gt.png",
            "model_image": "https://example.com/recorded-model.png",
            "model_image1": "https://example.com/recorded-model1.png",
            "model_image1_bon_id": 2,
            "model_image2": "https://example.com/recorded-model2.png",
            "model_image2_bon_id": 3,
        },
        "type": "neeko",
        "data": {
            "discard": "No",
            "discard_type": [],
            "discard_remark": None,
            "checkRemark": None,
            "label_sorce": {"model_image": "0", "model_image1": "0", "model_image2": "0"},
            "label_remark": {"model_image": "recorded", "model_image1": "recorded", "model_image2": "recorded"},
        },
        "dataMap": {"checkRemark": None, "discard": "No", "discard_type": [], "discard_remark": None, "label_sorce": {}, "label_remark": {}},
        "itemID": "recorded-item",
        "isAbandoned": False,
    }
    payload = {
        "AuditAnswers": [
            {
                "ItemID": "recorded-item",
                "Content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                "ControlData": json.dumps({"Discard": False, "extraAnswer": []}, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "NodeID": "1",
        "StagingTime": "604800",
        "TaskID": task_id,
    }
    (payload_dir / "research-chart-dry-run-payload.json").write_text(
        json.dumps(
            {
                "payload": payload,
                "temp_save_verification": {
                    "base_resp_status_code": 0,
                    "saved_to_task_ui": True,
                    "submits_remote": False,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_allowed_live_report(store: Path, *, task_id: str = "7638992213846740763", draft_id: str = "draft-1") -> None:
    store_payload = json.loads(store.read_text(encoding="utf-8"))
    drafts = store_payload.get("items", []) if isinstance(store_payload, dict) else []
    draft = next((item for item in drafts if isinstance(item, dict) and item.get("id") == draft_id), {})
    review_root = store.parent / f"research-chart-{task_id}" / "real-no-submit-reviews"
    review_root.mkdir(parents=True, exist_ok=True)
    (review_root / "live-ok.json").write_text(
        json.dumps(
            {
                "ok": True,
                "draft_id": draft_id,
                "task_id": task_id,
                "prompt": {"fingerprint": _prompt_fingerprint(draft)},
                "saved_to_task_ui": True,
                "submits_remote": False,
                "review_status": "待人工审核",
                "question_context": {"item_id": "item-1"},
                "ai_decision": {"score": "0", "reason": "两图存在明显差异，文字和点位都不一致。", "confidence": "high"},
                "created_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_temp_save_succeeded_requires_explicit_base_resp_zero() -> None:
    assert _temp_save_succeeded({"ok": True, "status_code": 200, "base_resp_status_code": 0}) is True
    assert _temp_save_succeeded({"ok": True, "status_code": 200, "base_resp_status_code": "0"}) is True
    assert _temp_save_succeeded({"ok": True, "status_code": 200, "base_resp_status_code": None}) is False
    assert _temp_save_succeeded({"ok": True, "status_code": 200}) is False
    assert _temp_save_succeeded({"ok": True, "status_code": 200, "base_resp_status_code": -1}) is False


def test_run_task_ability_real_no_submit_creates_human_review_without_remote_submit(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        queue_snapshot={
            "task_id": "7638992213846740763",
            "pending": 0,
            "processing": 1,
            "repair": 0,
            "account_user_id": "account-1",
            "account_name": "用户1",
        },
        question_context={
            "source_mode": "test-live-category-item",
            "item_id": "item-1",
            "uid": "chart-a.png",
            "image_gt": "https://example.com/gt.png",
            "model_image": "https://example.com/model.png",
        },
        ai_decision={"score": "0", "reason": "文字和点位存在明显偏差", "confidence": "medium"},
    )

    assert result["ok"] is True
    assert result["stage"] == "端到端做题不提交：待人工审核"
    assert result["writes_remote"] is False
    assert result["submits_remote"] is False
    assert result["queue_snapshot"]["has_executable_item"] is True
    assert result["answer_preview"]["data.label_sorce.model_image"] == "0"
    assert result["review_status"] == "待人工审核"
    assert Path(result["review_artifact_path"]).exists()


def test_run_task_ability_real_no_submit_refuses_temp_save_without_recorded_payload(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    with pytest.raises(TaskAbilityFlowError, match="录制验证的暂存 payload"):
        run_task_ability_real_no_submit(
            "draft-1",
            store_path=store,
            review_root=review_root,
            queue_snapshot={
                "task_id": "7638992213846740763",
                "pending": 0,
                "processing": 1,
                "repair": 0,
                "account_user_id": "account-1",
                "account_name": "用户1",
            },
            question_context={
                "source_mode": "test-live-category-item",
                "item_id": "item-1",
                "uid": "chart-a.png",
                "image_gt": "https://example.com/gt.png",
                "model_image": "https://example.com/model.png",
                "current_answer_data": {"discard": "No"},
            },
            ai_decision={"score": "0", "reason": "文字和点位存在明显偏差", "confidence": "medium"},
            allow_temp_save=True,
            temp_save_executor=fake_temp_save,
        )

    assert calls == []


def test_run_task_ability_real_no_submit_refuses_malformed_recorded_payload(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    payload_dir = store.parent / "research-chart-7638992213846740763"
    payload_dir.mkdir(parents=True, exist_ok=True)
    (payload_dir / "research-chart-dry-run-payload.json").write_text(
        json.dumps(
            {
                "payload": {"TaskID": "7638992213846740763", "NodeID": "1"},
                "temp_save_verification": {"base_resp_status_code": 0, "saved_to_task_ui": True, "submits_remote": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    with pytest.raises(TaskAbilityFlowError, match="录制暂存 payload 缺少 AuditAnswers"):
        run_task_ability_real_no_submit(
            "draft-1",
            store_path=store,
            review_root=review_root,
            queue_snapshot={
                "task_id": "7638992213846740763",
                "pending": 0,
                "processing": 1,
                "repair": 0,
                "account_user_id": "account-1",
                "account_name": "用户1",
            },
            question_context={
                "source_mode": "test-live-category-item",
                "item_id": "item-1",
                "uid": "chart-a.png",
                "image_gt": "https://example.com/gt.png",
                "model_image": "https://example.com/model.png",
            },
            ai_decision={"score": "0", "reason": "文字和点位存在明显偏差", "confidence": "medium"},
            allow_temp_save=True,
            temp_save_executor=fake_temp_save,
        )

    assert calls == []


def test_run_task_ability_real_no_submit_refuses_unverified_recorded_payload(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store)
    dry_run_path = store.parent / "research-chart-7638992213846740763" / "research-chart-dry-run-payload.json"
    data = json.loads(dry_run_path.read_text(encoding="utf-8"))
    data.pop("temp_save_verification", None)
    dry_run_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    with pytest.raises(TaskAbilityFlowError, match="显式 temp_save_verification"):
        run_task_ability_real_no_submit(
            "draft-1",
            store_path=store,
            review_root=review_root,
            queue_snapshot={
                "task_id": "7638992213846740763",
                "pending": 0,
                "processing": 1,
                "repair": 0,
                "account_user_id": "account-1",
                "account_name": "用户1",
            },
            question_context={
                "source_mode": "test-live-category-item",
                "item_id": "item-1",
                "uid": "chart-a.png",
                "image_gt": "https://example.com/gt.png",
                "model_image": "https://example.com/model.png",
            },
            ai_decision={"score": "0", "reason": "文字和点位存在明显偏差", "confidence": "medium"},
            allow_temp_save=True,
            temp_save_executor=fake_temp_save,
        )

    assert calls == []


def test_run_task_ability_real_no_submit_refuses_recorded_payload_without_json_content(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store)
    dry_run_path = store.parent / "research-chart-7638992213846740763" / "research-chart-dry-run-payload.json"
    data = json.loads(dry_run_path.read_text(encoding="utf-8"))
    data["payload"]["AuditAnswers"][0]["Content"] = "not-json"
    dry_run_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    with pytest.raises(TaskAbilityFlowError, match="Content 不是合法 JSON 对象"):
        run_task_ability_real_no_submit(
            "draft-1",
            store_path=store,
            review_root=review_root,
            queue_snapshot={
                "task_id": "7638992213846740763",
                "pending": 0,
                "processing": 1,
                "repair": 0,
                "account_user_id": "account-1",
                "account_name": "用户1",
            },
            question_context={
                "source_mode": "test-live-category-item",
                "item_id": "item-1",
                "uid": "chart-a.png",
                "image_gt": "https://example.com/gt.png",
                "model_image": "https://example.com/model.png",
            },
            ai_decision={"score": "0", "reason": "文字和点位存在明显偏差", "confidence": "medium"},
            allow_temp_save=True,
            temp_save_executor=fake_temp_save,
        )

    assert calls == []


def test_run_task_ability_real_no_submit_refuses_recorded_payload_with_empty_content_shape(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store)
    dry_run_path = store.parent / "research-chart-7638992213846740763" / "research-chart-dry-run-payload.json"
    data = json.loads(dry_run_path.read_text(encoding="utf-8"))
    data["payload"]["AuditAnswers"][0]["Content"] = "{}"
    dry_run_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    with pytest.raises(TaskAbilityFlowError, match="Content 缺少已录制字段结构"):
        run_task_ability_real_no_submit(
            "draft-1",
            store_path=store,
            review_root=review_root,
            queue_snapshot={
                "task_id": "7638992213846740763",
                "pending": 0,
                "processing": 1,
                "repair": 0,
                "account_user_id": "account-1",
                "account_name": "用户1",
            },
            question_context={
                "source_mode": "test-live-category-item",
                "item_id": "item-1",
                "uid": "chart-a.png",
                "image_gt": "https://example.com/gt.png",
                "model_image": "https://example.com/model.png",
            },
            ai_decision={"score": "0", "reason": "文字和点位存在明显偏差", "confidence": "medium"},
            allow_temp_save=True,
            temp_save_executor=fake_temp_save,
        )

    assert calls == []


def test_run_task_ability_real_no_submit_refuses_recorded_payload_without_answer_leaf_fields(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store)
    dry_run_path = store.parent / "research-chart-7638992213846740763" / "research-chart-dry-run-payload.json"
    data = json.loads(dry_run_path.read_text(encoding="utf-8"))
    data["payload"]["AuditAnswers"][0]["Content"] = json.dumps(
        {
            "item": {"uid": "", "image_gt": "", "model_image": ""},
            "data": {"label_sorce": {}, "label_remark": {}, "discard": "No", "discard_type": [], "discard_remark": None, "checkRemark": None},
            "dataMap": {"label_sorce": {}, "label_remark": {}, "discard": "No", "discard_type": [], "discard_remark": None, "checkRemark": None},
            "itemID": "recorded-item",
            "isAbandoned": False,
        },
        ensure_ascii=False,
    )
    dry_run_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    with pytest.raises(TaskAbilityFlowError, match="缺少已录制答案字段"):
        run_task_ability_real_no_submit(
            "draft-1",
            store_path=store,
            review_root=review_root,
            queue_snapshot={
                "task_id": "7638992213846740763",
                "pending": 0,
                "processing": 1,
                "repair": 0,
                "account_user_id": "account-1",
                "account_name": "用户1",
            },
            question_context={
                "source_mode": "test-live-category-item",
                "item_id": "item-1",
                "uid": "chart-a.png",
                "image_gt": "https://example.com/gt.png",
                "model_image": "https://example.com/model.png",
            },
            ai_decision={"score": "0", "reason": "文字和点位存在明显偏差", "confidence": "medium"},
            allow_temp_save=True,
            temp_save_executor=fake_temp_save,
        )

    assert calls == []


def test_run_task_ability_real_no_submit_refuses_legacy_verification_fields_without_explicit_temp_save_verification(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store)
    dry_run_path = store.parent / "research-chart-7638992213846740763" / "research-chart-dry-run-payload.json"
    data = json.loads(dry_run_path.read_text(encoding="utf-8"))
    data.pop("temp_save_verification", None)
    data["temp_draft_result"] = {"base_resp_status_code": 0}
    data["saved_to_task_ui"] = True
    dry_run_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    with pytest.raises(TaskAbilityFlowError, match="显式 temp_save_verification"):
        run_task_ability_real_no_submit(
            "draft-1",
            store_path=store,
            review_root=review_root,
            queue_snapshot={
                "task_id": "7638992213846740763",
                "pending": 0,
                "processing": 1,
                "repair": 0,
                "account_user_id": "account-1",
                "account_name": "用户1",
            },
            question_context={
                "source_mode": "test-live-category-item",
                "item_id": "item-1",
                "uid": "chart-a.png",
                "image_gt": "https://example.com/gt.png",
                "model_image": "https://example.com/model.png",
            },
            ai_decision={"score": "0", "reason": "文字和点位存在明显偏差", "confidence": "medium"},
            allow_temp_save=True,
            temp_save_executor=fake_temp_save,
        )

    assert calls == []


def test_run_task_ability_real_no_submit_temp_saves_answer_for_page_review(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store)
    calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0, "data": {"BaseResp": {"StatusCode": 0}}}

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        queue_snapshot={
            "task_id": "7638992213846740763",
            "pending": 0,
            "processing": 1,
            "repair": 0,
            "account_user_id": "account-1",
            "account_name": "用户1",
        },
        question_context={
            "source_mode": "test-live-category-item",
            "item_id": "item-1",
            "uid": "chart-a.png",
            "image_gt": "https://example.com/gt.png",
            "model_image": "https://example.com/model.png",
            "current_answer_data": {"discard": "No"},
        },
        ai_decision={"score": "0", "reason": "文字和点位存在明显偏差", "confidence": "medium"},
        allow_temp_save=True,
        temp_save_executor=fake_temp_save,
    )

    assert result["ok"] is True
    assert result["stage"] == "端到端做题不提交：已暂存待人工审核"
    assert result["writes_remote"] is True
    assert result["submits_remote"] is False
    assert result["saved_to_task_ui"] is True
    assert result["saved_answer"]["data.label_sorce.model_image"] == "0"
    assert "已保存到真实做题界面" in result["ui_review_hint"]
    assert result["temp_draft_result"]["base_resp_status_code"] == 0
    assert result["temp_draft_payload"]["AuditAnswers"][0]["ItemID"] == "item-1"
    assert calls and calls[0]["payload"]["AuditAnswers"][0]["ItemID"] == "item-1"
    assert "SubmitItem" not in json.dumps(calls[0]["payload"], ensure_ascii=False)

    saved = json.loads(store.read_text(encoding="utf-8"))
    draft = next(item for item in saved["items"] if item.get("id") == "draft-1")
    review = draft["real_no_submit_review"]
    assert draft["flow_stage"] == "real_no_submit_review"
    assert draft["capability_enabled"] is False
    assert review["review_status"] == "待人工审核"
    assert review["writes_remote"] is True
    assert review["submits_remote"] is False
    assert review["saved_to_task_ui"] is True
    assert review["item_id"] == "item-1"
    assert review["score"] == "0"
    assert review["source_mode"] == "test-live-category-item"
    assert review["account_user_id"] == "account-1"
    assert review["account_name"] == "用户1"
    assert "已保存到真实做题界面" in review["ui_review_hint"]


def test_run_task_ability_real_no_submit_uses_live_category_current_item_for_temp_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    state = tmp_path / "production-state.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store)
    state.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "userId": "account-1",
                        "name": "用户1",
                        "cookie": "sessionid=live-cookie;",
                        "operationUrl": "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.task_ability_service._production_state_path", lambda: state)

    calls: list[dict] = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        def __init__(self, body: dict) -> None:
            self._body = body

        def json(self) -> dict:
            return self._body

    def fake_post(url: str, json: dict, headers: dict, timeout: int) -> FakeResponse:
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        assert headers["Cookie"] == "sessionid=live-cookie;"
        return FakeResponse(
            {
                "BaseResp": {"StatusCode": 0},
                "Data": [
                        {
                            "ItemID": "live-current-item",
                            "Content": json_module.dumps(
                                {
                                    "uid": "live-uid",
                                    "image_gt": "https://example.com/live-gt.png",
                                    "model_image": "https://example.com/live-model.png",
                                },
                                ensure_ascii=False,
                            ),
                        "Status": 4,
                    }
                ],
            }
        )

    json_module = json
    monkeypatch.setattr(requests, "post", fake_post)

    temp_calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        temp_calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        queue_snapshot={
            "task_id": "7638992213846740763",
            "pending": 0,
            "processing": 1,
            "repair": 0,
            "account_user_id": "account-1",
            "account_name": "用户1",
        },
        ai_decision={"score": "0", "reason": "测试中只验证实时当前题取题链路", "confidence": "medium"},
        allow_temp_save=True,
        temp_save_executor=fake_temp_save,
    )

    assert result["ok"] is True
    assert result["question_context"]["source_mode"] == "live_search_item_category"
    assert result["question_context"]["item_id"] == "live-current-item"
    assert result["question_context"]["sends_network"] is True
    assert result["question_context"]["image_gt"] == "https://example.com/live-gt.png"
    assert temp_calls[0]["payload"]["AuditAnswers"][0]["ItemID"] == "live-current-item"
    assert calls[0]["json"]["ItemCategoryType"] == 0
    assert calls[0]["headers"]["Agw-Js-Conv"] == "str"
    assert calls[0]["headers"]["X-JS-REQ"] == "1"
    assert "/dispatcher/search_item/category" in calls[0]["url"]


def test_run_task_ability_real_no_submit_calls_task_ai_for_research_chart_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store)
    monkeypatch.setattr(
        "app.services.task_ability_service.get_task_ai_runtime_prompt",
        lambda: {
            "provider_configured": True,
            "base_url": "https://task-ai.example/v1",
            "api_key": "test-key",
            "model": "vision-model",
            "timeout_seconds": 20,
            "pre_prompt": "只输出科研图评分 JSON",
            "skills": ["research-chart"],
            "md_files": [],
        },
    )
    provider_calls: list[dict] = []

    class FakeProviderResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "score": "1",
                                    "reason": "整体图表类型和坐标布局接近，但右图部分刻度文字和点位位置存在轻微偏差。",
                                    "confidence": "medium",
                                    "visual_findings": ["刻度文字有轻微差异", "点位位置略偏"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    def fake_post(url: str, headers: dict, json: dict, timeout: int) -> FakeProviderResponse:
        provider_calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeProviderResponse()

    class FakeImageResponse:
        ok = True
        status_code = 200
        headers = {"Content-Type": "image/png"}
        content = b"\x89PNG\r\n\x1a\nFAKEPNG"

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, timeout: int):
        return FakeImageResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    temp_calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        temp_calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        queue_snapshot={
            "task_id": "7638992213846740763",
            "pending": 0,
            "processing": 1,
            "repair": 0,
            "account_user_id": "account-1",
            "account_name": "用户1",
        },
        question_context={
            "source_mode": "live_search_item_category",
            "sends_network": True,
            "item_id": "live-item",
            "uid": "chart-uid",
            "image_gt": "https://example.com/gt.png",
            "model_image": "https://example.com/model.png",
            "current_answer_data": {"discard": "No"},
        },
        allow_temp_save=True,
        temp_save_executor=fake_temp_save,
    )

    assert result["saved_answer"]["data.label_sorce.model_image"] == "1"
    assert "刻度文字" in result["saved_answer"]["data.label_remark.model_image"]
    assert result["ai_decision"]["provider_status"] == "provider_ok"
    assert result["ai_decision"]["visual_findings"]
    messages = provider_calls[0]["json"]["messages"]
    user_content = messages[1]["content"]
    image_urls = [part["image_url"]["url"] for part in user_content if part.get("type") == "image_url"]
    assert len(image_urls) == 2
    assert image_urls == ["https://example.com/gt.png", "https://example.com/model.png"]
    content = json.loads(temp_calls[0]["payload"]["AuditAnswers"][0]["Content"])
    assert content["data"]["label_sorce"]["model_image"] == "1"
    assert "点位位置" in content["data"]["label_remark"]["model_image"]


def test_run_task_ability_real_no_submit_scores_multi_model_images_and_writes_per_model_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(全量数据)",
                        "task_id": "7639402643386830630",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-14T00:00:00+00:00",
                        "updated_at": "2026-05-14T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store, task_id="7639402643386830630")
    monkeypatch.setattr(
        "app.services.task_ability_service.get_task_ai_runtime_prompt",
        lambda: {
            "provider_configured": True,
            "base_url": "https://task-ai.example/v1",
            "api_key": "test-key",
            "model": "vision-model",
            "timeout_seconds": 20,
            "pre_prompt": "",
            "skills": [],
            "md_files": [],
        },
    )

    provider_calls: list[dict] = []
    responses = iter(
        [
            {"score": "1", "reason": "模型1轻微遮挡。", "confidence": "high", "visual_findings": ["model1"]},
            {"score": "0", "reason": "模型2线条连线过多。", "confidence": "high", "visual_findings": ["model2"]},
        ]
    )

    class FakeProviderResponse:
        ok = True
        status_code = 200
        text = "{}"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            payload = next(responses)
            return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    class FakeImageResponse:
        ok = True
        status_code = 200
        headers = {"Content-Type": "image/png"}
        content = b"\x89PNG\r\n\x1a\nFAKEPNG"

        def raise_for_status(self) -> None:
            return None

    def fake_post(url: str, headers: dict, json: dict, timeout: int) -> FakeProviderResponse:
        provider_calls.append({"url": url, "json": json})
        return FakeProviderResponse()

    def fake_temp_save(payload: dict, account: dict) -> dict:
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", lambda url, timeout: FakeImageResponse())

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        queue_snapshot={
            "task_id": "7639402643386830630",
            "pending": 0,
            "processing": 1,
            "repair": 0,
            "account_user_id": "account-1",
            "account_name": "用户1",
        },
        question_context={
            "source_mode": "live_search_item_category",
            "sends_network": True,
            "item_id": "live-item",
            "uid": "chart-uid",
            "image_gt": "https://example.com/gt.png",
            "model_image1": "https://example.com/model1.png",
            "model_image1_bon_id": 2,
            "model_image2": "https://example.com/model2.png",
            "model_image2_bon_id": 3,
            "current_answer_data": {"discard": "No"},
        },
        allow_temp_save=True,
        temp_save_executor=fake_temp_save,
    )

    assert result["saved_answer"]["data.label_sorce.model_image1"] == "1"
    assert result["saved_answer"]["data.label_sorce.model_image2"] == "0"
    assert "遮挡" in result["saved_answer"]["data.label_remark.model_image1"]
    assert "连线过多" in result["saved_answer"]["data.label_remark.model_image2"]
    content = json.loads(result["temp_draft_payload"]["AuditAnswers"][0]["Content"])
    assert content["data"]["label_sorce"]["model_image1"] == "1"
    assert content["data"]["label_sorce"]["model_image2"] == "0"
    assert len(provider_calls) == 2


def test_run_task_ability_real_no_submit_can_target_account_and_use_system_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    state = tmp_path / "production-state.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store)
    state.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "userId": "other-account",
                        "name": "其他账号",
                        "cookie": "sessionid=other;",
                        "tasks": [{"id": "7638992213846740763", "frontendNotSubmitted": 3, "poolPendingSubmit": 0}],
                    },
                    {
                        "userId": "account-sample-002",
                        "name": "用户样例002",
                        "cookie": "sessionid=target;",
                        "operationUrl": "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1",
                        "tasks": [{"id": "7638992213846740763", "frontendNotSubmitted": 1, "poolPendingSubmit": 0}],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.task_ability_service._production_state_path", lambda: state)
    monkeypatch.setattr(
        "app.services.task_ability_service.get_system_ai_runtime_prompt",
        lambda: {
            "provider_configured": True,
            "base_url": "https://system-ai.example/v1",
            "api_key": "system-key",
            "model": "system-vision",
            "timeout_seconds": 20,
            "pre_prompt": "",
            "skills": [],
            "md_files": [],
        },
    )
    monkeypatch.setattr(
        "app.services.task_ability_service.get_task_ai_runtime_prompt",
        lambda: {"provider_configured": True, "base_url": "https://task-ai.example/v1", "api_key": "task-key", "model": "task-text", "timeout_seconds": 20},
    )
    calls: list[dict] = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        def __init__(self, body: dict) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._body

    def fake_post(url: str, json: dict, headers: dict, timeout: int) -> FakeResponse:
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if "search_item/category" in url:
            assert headers["Cookie"] == "sessionid=target;"
            return FakeResponse(
                {
                    "BaseResp": {"StatusCode": 0},
                    "Data": [
                        {
                            "ItemID": "target-live-item",
                            "Content": json_module.dumps(
                                {"uid": "u", "image_gt": "https://example.com/gt.png", "model_image": "https://example.com/model.png"},
                                ensure_ascii=False,
                            ),
                            "Status": 4,
                        }
                    ],
                }
            )
        assert url == "https://system-ai.example/v1/chat/completions"
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json_module.dumps(
                                {"required_output": {"score": 0, "reason": "两图存在明显差异。", "confidence": "high", "visual_findings": ["差异明显"]}},
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

    json_module = json
    monkeypatch.setattr(requests, "post", fake_post)
    temp_calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        temp_calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        target_account_user_id="account-sample-002",
        use_system_ai_for_vision=True,
        allow_temp_save=True,
        temp_save_executor=fake_temp_save,
    )

    assert result["queue_snapshot"]["account_user_id"] == "account-sample-002"
    assert result["question_context"]["item_id"] == "target-live-item"
    assert result["ai_decision"]["provider_role"] == "system_ai_vision"
    assert result["saved_answer"]["data.label_sorce.model_image"] == "0"
    assert temp_calls[0]["account"]["userId"] == "account-sample-002"
    assert temp_calls[0]["payload"]["AuditAnswers"][0]["ItemID"] == "target-live-item"
    assert all("task-ai.example" not in call["url"] for call in calls)


def test_run_task_ability_real_no_submit_refuses_recorded_item_for_temp_save(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    with pytest.raises(TaskAbilityFlowError, match="录制题目"):
        run_task_ability_real_no_submit(
            "draft-1",
            store_path=store,
            review_root=review_root,
            queue_snapshot={
                "task_id": "7638992213846740763",
                "pending": 0,
                "processing": 1,
                "repair": 0,
                "account_user_id": "account-1",
                "account_name": "用户1",
            },
            question_context={
                "source_mode": "local-evidence-real-task-sample",
                "item_id": "submitted-recording-item",
                "uid": "recorded.png",
                "image_gt": "https://example.com/recorded-gt.png",
                "model_image": "https://example.com/recorded-model.png",
            },
            ai_decision={"score": "0", "reason": "录制题不能用于端到端暂存", "confidence": "low"},
            allow_temp_save=True,
            temp_save_executor=fake_temp_save,
        )


def test_run_task_ability_real_no_submit_can_claim_pending_only_item_before_scoring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    state = tmp_path / "production-state.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store)
    state.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "userId": "account-sample-002",
                        "name": "用户样例002",
                        "cookie": "sessionid=target;",
                        "operationUrl": "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1",
                        "tasks": [{"id": "7638992213846740763", "frontendNotSubmitted": 0, "poolPendingSubmit": 5, "receiveEnable": True}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.task_ability_service._production_state_path", lambda: state)
    monkeypatch.setattr(
        "app.services.task_ability_service.get_system_ai_runtime_prompt",
        lambda: {
            "provider_configured": True,
            "base_url": "https://system-ai.example/v1",
            "api_key": "system-key",
            "model": "system-vision",
            "timeout_seconds": 20,
            "pre_prompt": "",
            "skills": [],
            "md_files": [],
        },
    )

    calls: list[dict] = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        def __init__(self, body: dict) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._body

    def fake_post(url: str, json: dict, headers: dict, timeout: int) -> FakeResponse:
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if "PreReceive" in url:
            assert json == {"Filter": {"Type": 1, "TaskID": "7638992213846740763", "NodeID": 1, "Count": 1, "StatusList": []}}
            return FakeResponse({"BaseResp": {"StatusCode": 0}, "Items": [{"ItemID": "claimed-item"}]})
        if "Receive" in url and "PreReceive" not in url:
            raise AssertionError("PreReceive 成功时不应再回落到 Receive")
        if "search_item/category" in url:
            return FakeResponse(
                {
                    "BaseResp": {"StatusCode": 0},
                    "Data": [
                        {
                            "ItemID": "claimed-item",
                            "Content": json_module.dumps(
                                {"uid": "u", "image_gt": "https://example.com/gt.png", "model_image": "https://example.com/model.png"},
                                ensure_ascii=False,
                            ),
                            "Status": 4,
                        }
                    ],
                }
            )
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json_module.dumps(
                                {"required_output": {"score": 0, "reason": "两图存在明显差异。", "confidence": "high", "visual_findings": ["差异明显"]}},
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

    json_module = json
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", lambda url, timeout: type("Img", (), {"ok": True, "status_code": 200, "headers": {"Content-Type": "image/png"}, "content": b"PNG", "raise_for_status": lambda self: None})())
    temp_calls: list[dict] = []

    def fake_temp_save(payload: dict, account: dict) -> dict:
        temp_calls.append({"payload": payload, "account": account})
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0}

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        target_account_user_id="account-sample-002",
        use_system_ai_for_vision=True,
        allow_temp_save=True,
        allow_claim_receive=True,
        temp_save_executor=fake_temp_save,
    )

    assert result["queue_snapshot"]["claim_required"] is False
    assert result["question_context"]["item_id"] == "claimed-item"
    assert temp_calls[0]["payload"]["AuditAnswers"][0]["ItemID"] == "claimed-item"
    assert any("PreReceive" in call["url"] for call in calls)
    saved = json.loads(store.read_text(encoding="utf-8"))
    draft = next(item for item in saved["items"] if item.get("id") == "draft-1")
    assert draft.get("flow_stage") == "real_no_submit_review"
    assert draft["task_queue_snapshot"]["claim_required"] is False


def test_run_task_ability_real_no_submit_reports_claim_failure_when_auto_claim_chain_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    state = tmp_path / "production-state.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "userId": "account-sample-004",
                        "name": "阻塞账号",
                        "cookie": "sessionid=blocked;",
                        "operationUrl": "https://aidp.juejin.cn/operation/lite/setting/account/personal-center?org=AIDP%20Coding&tab=2",
                        "tasks": [{"id": "7638992213846740763", "frontendNotSubmitted": 0, "poolPendingSubmit": 5, "receiveEnable": False}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.task_ability_service._production_state_path", lambda: state)

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        def __init__(self, body: dict) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._body

    def fake_post(url: str, json: dict, headers: dict, timeout: int) -> FakeResponse:
        if "Receive" in url and "PreReceive" not in url:
            return FakeResponse({"BaseResp": {"StatusCode": 1001, "StatusMessage": "blocked"}})
        if "PreReceive" in url:
            return FakeResponse({"BaseResp": {"StatusCode": 1002, "StatusMessage": "blocked"}})
        if "search_item/category" in url:
            return FakeResponse({"BaseResp": {"StatusCode": 0}, "Data": []})
        raise AssertionError(url)

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(TaskAbilityFlowError, match="自动获取当前题失败"):
        run_task_ability_real_no_submit(
            "draft-1",
            store_path=store,
            review_root=review_root,
            target_account_user_id="account-sample-004",
            allow_temp_save=False,
            allow_claim_receive=True,
        )


def test_run_task_ability_real_no_submit_falls_back_to_helper_claim_when_http_claim_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    state = tmp_path / "production-state.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "userId": "account-sample-004",
                        "name": "待领题账号",
                        "cookie": "sessionid=blocked;",
                        "operationUrl": "https://aidp.juejin.cn/operation/task-v2?page=1",
                        "tasks": [{"id": "7638992213846740763", "frontendNotSubmitted": 0, "poolPendingSubmit": 5, "receiveEnable": False}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.task_ability_service._production_state_path", lambda: state)
    monkeypatch.setattr(
        "app.services.task_ability_service.create_browser_open_session",
        lambda user_id, target: {"ok": True, "userId": user_id, "target": target, "token": "claim-token"},
    )
    monkeypatch.setattr(
        "app.services.task_ability_service.get_system_ai_runtime_prompt",
        lambda: {
            "provider_configured": True,
            "base_url": "https://system-ai.example/v1",
            "api_key": "system-key",
            "model": "system-vision",
            "timeout_seconds": 20,
            "pre_prompt": "",
            "skills": [],
            "md_files": [],
        },
    )

    calls: list[dict] = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        def __init__(self, body: dict, ok: bool = True, status_code: int = 200) -> None:
            self._body = body
            self.ok = ok
            self.status_code = status_code
            self.text = json.dumps(body, ensure_ascii=False)

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._body

    def fake_post(url: str, json: dict, headers: dict | None = None, timeout: int = 20) -> FakeResponse:
        calls.append({"url": url, "json": json})
        if "Receive" in url and "PreReceive" not in url:
            return FakeResponse({"BaseResp": {"StatusCode": 1001, "StatusMessage": "blocked"}}, ok=False, status_code=400)
        if "PreReceive" in url:
            return FakeResponse({"BaseResp": {"StatusCode": 1002, "StatusMessage": "blocked"}}, ok=False, status_code=400)
        if "aidp-claim-task" in url:
            return FakeResponse({"ok": True, "status": "navigated"})
        if "search_item/category" in url:
            return FakeResponse(
                {
                    "BaseResp": {"StatusCode": 0},
                    "Data": [
                        {
                            "ItemID": "claimed-helper-item",
                            "Content": json_module.dumps(
                                {"uid": "u", "image_gt": "https://example.com/gt.png", "model_image": "https://example.com/model.png"},
                                ensure_ascii=False,
                            ),
                            "Status": 4,
                        }
                    ],
                }
            )
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json_module.dumps(
                                {"required_output": {"score": 0, "reason": "两图存在明显差异。", "confidence": "high", "visual_findings": ["差异明显"]}},
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

    json_module = json
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", lambda url, timeout: type("Img", (), {"ok": True, "status_code": 200, "headers": {"Content-Type": "image/png"}, "content": b"PNG", "raise_for_status": lambda self: None})())

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        target_account_user_id="account-sample-004",
        use_system_ai_for_vision=True,
        allow_temp_save=False,
        allow_claim_receive=True,
    )

    assert result["question_context"]["item_id"] == "claimed-helper-item"
    assert any("aidp-claim-task" in call["url"] for call in calls)


def test_run_task_ability_real_no_submit_prefers_browser_signed_claim_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    state = tmp_path / "production-state.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "userId": "account-sample-003",
                        "name": "浏览器签名账号",
                        "cookie": "sessionid=browser;",
                        "operationUrl": "https://aidp.juejin.cn/operation/task-v2?page=1",
                        "tasks": [{"id": "7638992213846740763", "frontendNotSubmitted": 0, "poolPendingSubmit": 5, "receiveEnable": False}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.task_ability_service._production_state_path", lambda: state)
    monkeypatch.setattr(
        "app.services.task_ability_service._claim_pending_item_via_browser",
        lambda account, task_id, node_id: {
            "source": "browser_mark_v3_auto",
            "question_context": {
                "source_mode": "browser_signed_receive",
                "sends_network": True,
                "item_id": "browser-item",
                "uid": "browser-uid",
                "image_gt": "https://example.com/gt.png",
                "model_image": "https://example.com/model.png",
                "current_answer_data": {},
            },
        },
    )
    monkeypatch.setattr(
        "app.services.task_ability_service._build_live_question_context_from_category",
        lambda draft, snapshot: None,
    )

    def unexpected_post(*args, **kwargs):
        raise AssertionError("browser 主路径成功时不应再回落到旧 requests.post 领题链")

    monkeypatch.setattr(requests, "post", unexpected_post)
    monkeypatch.setattr(requests, "get", lambda url, timeout: type("Img", (), {"ok": True, "status_code": 200, "headers": {"Content-Type": "image/png"}, "content": b"PNG", "raise_for_status": lambda self: None})())

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        target_account_user_id="account-sample-003",
        ai_decision={"score": "0", "reason": "浏览器签名链测试", "confidence": "high"},
        allow_temp_save=False,
        allow_claim_receive=True,
    )

    assert result["question_context"]["item_id"] == "browser-item"
    assert result["question_context"]["claim_source"] == "browser_mark_v3_auto"


def test_run_task_ability_real_no_submit_uses_category_after_browser_side_effect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    state = tmp_path / "production-state.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "userId": "account-sample-003",
                        "name": "浏览器签名账号",
                        "cookie": "sessionid=browser;",
                        "operationUrl": "https://aidp.juejin.cn/operation/task-v2?page=1",
                        "tasks": [{"id": "7638992213846740763", "frontendNotSubmitted": 0, "poolPendingSubmit": 5, "receiveEnable": False}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.task_ability_service._production_state_path", lambda: state)
    monkeypatch.setattr(
        "app.services.task_ability_service._claim_pending_item_via_browser",
        lambda account, task_id, node_id: {"source": "browser_mark_v3_opened"},
    )
    monkeypatch.setattr(
        "app.services.task_ability_service._build_live_question_context_from_category",
        lambda draft, snapshot: {
            "source_mode": "live_search_item_category",
            "sends_network": True,
            "item_id": "category-item",
            "uid": "category-uid",
            "image_gt": "https://example.com/gt.png",
            "model_image": "https://example.com/model.png",
            "current_answer_data": {},
        },
    )

    def unexpected_post(*args, **kwargs):
        raise AssertionError("browser 已造成领取副作用并能 category 回读时，不应继续回落到旧 HTTP/helper 领题链")

    monkeypatch.setattr(requests, "post", unexpected_post)
    monkeypatch.setattr(requests, "get", lambda url, timeout: type("Img", (), {"ok": True, "status_code": 200, "headers": {"Content-Type": "image/png"}, "content": b"PNG", "raise_for_status": lambda self: None})())

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        target_account_user_id="account-sample-003",
        ai_decision={"score": "0", "reason": "浏览器副作用回读测试", "confidence": "high"},
        allow_temp_save=False,
        allow_claim_receive=True,
    )

    assert result["question_context"]["item_id"] == "category-item"
    assert result["question_context"]["claim_source"] == "browser_mark_v3_opened"


def test_approve_task_ability_real_no_submit_marks_capability_available(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "待审核真实不提交结果",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "草稿",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "等待人工审核真实不提交结果",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                        "real_no_submit_review": {"review_status": "待人工审核", "item_id": "item-1", "saved_to_task_ui": True},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_allowed_live_report(store)

    result = approve_task_ability_real_no_submit("draft-1", store_path=store)

    assert result["ok"] is True
    assert result["status"] == "有做题能力"
    saved = json.loads(store.read_text(encoding="utf-8"))
    draft = next(item for item in saved["items"] if item.get("id") == "draft-1")
    assert draft["capability_enabled"] is True
    assert draft["status"] == "有做题能力"


def test_run_task_ability_real_no_submit_does_not_downgrade_already_enabled_capability(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "有做题能力",
                        "task_name": "RFT科研图表还原-正式(全量数据)",
                        "task_id": "7639402643386830630",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "草稿",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "已启用任务定制做题能力；正式提交仍走高风险确认和回读验证。",
                        "created_at": "2026-05-14T00:00:00+00:00",
                        "updated_at": "2026-05-14T00:00:00+00:00",
                        "flow_stage": "capability_enabled",
                        "capability_enabled": True,
                        "real_no_submit_review": {
                            "review_status": "人工已通过",
                            "saved_to_task_ui": True,
                            "approved_at": "2026-05-14T00:00:00+00:00",
                            "item_id": "old-item",
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_recorded_temp_payload(store, task_id="7639402643386830630")

    def fake_temp_save(payload: dict, account: dict) -> dict:
        return {"ok": True, "status_code": 200, "base_resp_status_code": 0, "data": {"BaseResp": {"StatusCode": 0}}}

    result = run_task_ability_real_no_submit(
        "draft-1",
        store_path=store,
        review_root=review_root,
        queue_snapshot={
            "task_id": "7639402643386830630",
            "pending": 10,
            "processing": 1,
            "repair": 0,
            "account_user_id": "account-sample-002",
            "account_name": "用户1",
        },
        question_context={
            "source_mode": "live_search_item_category",
            "sends_network": True,
            "item_id": "new-item",
            "uid": "chart-uid",
            "image_gt": "https://example.com/gt.png",
            "model_image": "https://example.com/model.png",
            "current_answer_data": {"discard": "No"},
        },
        ai_decision={"score": "0", "reason": "新一题执行结果", "confidence": "high"},
        allow_temp_save=True,
        temp_save_executor=fake_temp_save,
    )

    assert result["saved_to_task_ui"] is True
    saved = json.loads(store.read_text(encoding="utf-8"))
    draft = next(item for item in saved["items"] if item.get("id") == "draft-1")
    assert draft["capability_enabled"] is True
    assert draft["flow_stage"] == "capability_enabled"
    assert draft["status"] == "有做题能力"
    assert draft["real_no_submit_review"]["review_status"] == "人工已通过"


def test_approve_task_ability_real_no_submit_requires_saved_task_ui_result(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "待审核真实不提交结果",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "草稿",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "等待人工审核真实不提交结果",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                        "real_no_submit_review": {"review_status": "待人工审核", "item_id": "item-1", "saved_to_task_ui": False},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(TaskAbilityFlowError, match="保存到真实做题界面"):
        approve_task_ability_real_no_submit("draft-1", store_path=store)


def test_approve_task_ability_draft_moves_to_real_no_submit_step(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "草稿",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "草稿",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "人工审核草稿",
                        "created_at": "2026-05-13T00:00:00+00:00",
                        "updated_at": "2026-05-13T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = approve_task_ability_draft("draft-1", store_path=store)

    assert result["status"] == "草稿已确认"
    assert result["flow_stage"] == "real_no_submit_ready"


def test_list_task_ability_drafts_seeds_bon8_published_ability_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    monkeypatch.setattr("app.services.task_ability_service._store_path", lambda: store)
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8")

    result = list_task_ability_drafts()

    bon8 = next(item for item in result.items if item.task_id == "7637771731901861641")
    assert bon8.capability_enabled is True
    assert bon8.flow_stage == "capability_enabled"
    assert "bon8" in bon8.task_name.lower()
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert any(item.get("task_id") == "7637771731901861641" for item in saved["items"])


def test_update_task_ability_draft_resets_enabled_state_after_prompt_change(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "bon8-task-ability",
                        "version": "ability-20260515-bon8",
                        "status": "有做题能力",
                        "task_name": "RFT人标支持VLM Coding（bon8草图与流程图）-正式队列",
                        "task_id": "7637771731901861641",
                        "specific_rules": "旧规则",
                        "sample_data": "旧样例",
                        "related_content": "旧相关内容",
                        "system_ai_draft": "旧提示词",
                        "system_ai_trace_id": "",
                        "provider_status": "provider_ok",
                        "next_step": "已启用任务定制做题能力。",
                        "flow_stage": "capability_enabled",
                        "capability_enabled": True,
                        "real_no_submit_review": {
                            "review_status": "人工已通过",
                            "saved_to_task_ui": True,
                            "approved_at": "2026-05-15T00:00:00+00:00",
                        },
                        "created_at": "2026-05-15T00:00:00+00:00",
                        "updated_at": "2026-05-15T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    updated = update_task_ability_draft(
        "bon8-task-ability",
        {
            "system_ai_draft": "新提示词：更严格区分布局和功能缺陷",
            "specific_rules": "新规则",
        },
        store_path=store,
    )

    assert updated["system_ai_draft"] == "新提示词：更严格区分布局和功能缺陷"
    assert updated["specific_rules"] == "新规则"
    assert updated["capability_enabled"] is False
    assert updated["flow_stage"] == "real_no_submit_ready"
    assert updated["status"] == "草稿已确认"
    assert updated["real_no_submit_review"]["review_status"] == "待重新验证"


def test_run_task_ability_real_no_submit_supports_bon8_unified_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "bon8-reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "bon8-task-ability",
                        "version": "ability-20260515-bon8",
                        "status": "草稿已确认",
                        "task_name": "RFT人标支持VLM Coding（bon8草图与流程图）-正式队列",
                        "task_id": "7637771731901861641",
                        "specific_rules": "bon8 统一规则",
                        "sample_data": "bon8 样例",
                        "related_content": "bon8 相关内容",
                        "system_ai_draft": "bon8 手调提示词",
                        "system_ai_trace_id": "",
                        "provider_status": "provider_ok",
                        "next_step": "进入端到端做题不提交",
                        "created_at": "2026-05-15T00:00:00+00:00",
                        "updated_at": "2026-05-15T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.task_ability_service._run_bon8_task_ability_real_no_submit",
        lambda draft_id, **kwargs: {
            "ok": True,
            "stage": "端到端做题不提交：已暂存待人工审核",
            "draft_id": draft_id,
            "task_id": "7637771731901861641",
            "task_name": "RFT人标支持VLM Coding（bon8草图与流程图）-正式队列",
            "writes_remote": True,
            "submits_remote": False,
            "sends_network": True,
            "queue_snapshot": {
                "task_id": "7637771731901861641",
                "pending": 0,
                "processing": 1,
                "repair": 0,
                "account_user_id": "account-sample-002",
                "account_name": "用户样例002",
                "has_executable_item": True,
                "claim_required": False,
            },
            "question_context": {"item_id": "bon8-item-1", "source_mode": "live_search_item_category"},
            "answer_preview": {"ai_scores": {"model3": "2"}, "reasons": {"model3": "最佳"}},
            "saved_answer": {"ai_scores": {"model3": "2"}, "reasons": {"model3": "最佳"}},
            "saved_to_task_ui": True,
            "temp_draft_result": {"BaseResp": {"StatusCode": 0}},
            "temp_draft_payload_preview": {"TaskID": "7637771731901861641"},
            "review_artifact_path": str(review_root / "bon8-review.json"),
            "ui_review_hint": "bon8 已保存到真实做题界面但未正式提交。",
            "review_status": "待人工审核",
            "message": "bon8 统一流程已生成待审核件。",
        },
    )

    result = run_task_ability_real_no_submit(
        "bon8-task-ability",
        store_path=store,
        review_root=review_root,
        target_account_user_id="account-sample-002",
    )

    assert result["writes_remote"] is True
    assert result["submits_remote"] is False
    assert result["saved_to_task_ui"] is True
    assert result["queue_snapshot"]["account_user_id"] == "account-sample-002"
    assert result["question_context"]["item_id"] == "bon8-item-1"


def test_create_and_restore_prompt_snapshot_roundtrip(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-v1",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "旧规则",
                        "sample_data": "旧样例",
                        "related_content": "",
                        "system_ai_draft": "旧 Prompt",
                        "task_type": "research_chart",
                        "ability_source": "platform_form",
                        "source_config": {"source": "old"},
                        "field_mapping": {"score": "old_score"},
                        "validation_rules": {"required": ["old_score"]},
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "flow_stage": "real_no_submit_ready",
                        "capability_enabled": False,
                        "real_no_submit_review": {},
                        "created_at": "2026-05-16T00:00:00+00:00",
                        "updated_at": "2026-05-16T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    snapshot = create_prompt_snapshot("7638992213846740763", note="保存旧版", store_path=store)
    updated = update_task_ability_draft(
        "draft-1",
        {
            "system_ai_draft": "新 Prompt",
            "specific_rules": "新规则",
            "task_type": "research_chart_v2",
            "ability_source": "assistant",
            "source_config": {"source": "new"},
            "field_mapping": {"score": "new_score"},
            "validation_rules": {"required": ["new_score"]},
        },
        store_path=store,
    )
    restored = restore_prompt_snapshot("7638992213846740763", snapshot["snapshot_id"], store_path=store)

    assert snapshot["task_id"] == "7638992213846740763"
    assert updated["system_ai_draft"] == "新 Prompt"
    assert restored["system_ai_draft"] == "旧 Prompt"
    assert restored["specific_rules"] == "旧规则"
    assert restored["task_type"] == "research_chart"
    assert restored["ability_source"] == "platform_form"
    assert restored["source_config"] == {"source": "old"}
    assert restored["field_mapping"] == {"score": "old_score"}
    assert restored["validation_rules"] == {"required": ["old_score"]}
    assert restored["flow_stage"] == "real_no_submit_ready"


def test_approve_task_ability_version_approves_latest_task_draft(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-test",
                        "status": "待审核真实不提交结果",
                        "task_name": "RFT科研图表还原-正式(随机5000题)",
                        "task_id": "7638992213846740763",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "草稿",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "等待人工审核真实不提交结果",
                        "created_at": "2026-05-16T00:00:00+00:00",
                        "updated_at": "2026-05-16T00:00:00+00:00",
                        "real_no_submit_review": {"review_status": "待人工审核", "item_id": "item-1", "saved_to_task_ui": True},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_allowed_live_report(store)

    result = approve_task_ability_version("7638992213846740763", store_path=store)

    assert result["ok"] is True
    assert result["capability_enabled"] is True
    assert result["flow_stage"] == "capability_enabled"


def test_run_task_ability_live_http_test_uses_latest_task_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = tmp_path / "task-abilities" / "reviews"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-live",
                        "version": "ability-live-v1",
                        "status": "草稿已确认",
                        "task_name": "RFT科研图表还原-正式(全量数据)",
                        "task_id": "7639402643386830630",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "草稿",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "执行真实题不提交",
                        "flow_stage": "real_no_submit_ready",
                        "capability_enabled": False,
                        "real_no_submit_review": {},
                        "created_at": "2026-05-16T00:00:00+00:00",
                        "updated_at": "2026-05-16T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.task_ability_service.run_task_ability_real_no_submit",
        lambda draft_id, **kwargs: {
            "ok": True,
            "draft_id": draft_id,
            "task_id": "7639402643386830630",
            "stage": "端到端做题不提交：已暂存待人工审核",
            "saved_to_task_ui": True,
            "submits_remote": False,
            "writes_remote": True,
            "review_artifact_path": str(review_root / "draft-live-20260516000000.json"),
            "queue_snapshot": {"account_user_id": "account-sample-002"},
        },
    )

    result = run_task_ability_live_http_test(
        "7639402643386830630",
        store_path=store,
        review_root=review_root,
        account_user_id="account-sample-002",
    )

    assert result["task_id"] == "7639402643386830630"
    assert result["draft_id"] == "draft-live"
    assert result["report_id"] == "draft-live-20260516000000"
    assert result["saved_to_task_ui"] is True


def test_get_task_ability_live_http_test_report_reads_saved_artifact(tmp_path: Path) -> None:
    report_root = tmp_path / "task-abilities" / "7639402643386830630" / "real-no-submit-reviews"
    report_root.mkdir(parents=True)
    report_path = report_root / "draft-live-20260516000000.json"
    report_path.write_text(
        json.dumps(
            {
                "ok": True,
                "task_id": "7639402643386830630",
                "draft_id": "draft-live",
                "saved_to_task_ui": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = get_task_ability_live_http_test_report(
        "7639402643386830630",
        "draft-live-20260516000000",
        review_root=report_root,
    )

    assert result["task_id"] == "7639402643386830630"
    assert result["draft_id"] == "draft-live"


def test_run_task_ability_dry_run_blocks_unknown_draft(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(TaskAbilityFlowError):
        run_task_ability_dry_run("missing", store_path=store)


def test_run_task_ability_dry_run_supports_full_dataset_research_chart_task(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    dry_run_dir = tmp_path / "task-abilities" / "research-chart-7639402643386830630"
    dry_run_path = dry_run_dir / "research-chart-dry-run-payload.json"
    store.parent.mkdir(parents=True)
    dry_run_dir.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-full",
                        "version": "ability-full",
                        "status": "有做题能力",
                        "task_name": "RFT科研图表还原-正式(全量数据)",
                        "task_id": "7639402643386830630",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "只输出 score/reason/confidence",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "已启用",
                        "created_at": "2026-05-14T00:00:00+00:00",
                        "updated_at": "2026-05-14T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    dry_run_path.write_text(
        json.dumps(
            {
                "payload": {
                    "TaskID": "7639402643386830630",
                    "NodeID": "1",
                    "AuditAnswers": [{"ItemID": "item-1", "Content": "{\"itemID\":\"item-1\"}"}],
                },
                "field_diff": {"data.label_sorce.model_image": "0"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_task_ability_dry_run("draft-full", store_path=store)

    assert result["ok"] is True
    assert result["task_id"] == "7639402643386830630"
    assert "research-chart-7639402643386830630" in result["evidence_path"]
