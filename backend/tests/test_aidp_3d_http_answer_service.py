import json

import pytest

from app.services.aidp_3d_http_answer_service import (
    AIDP_3D_RUBRIC_NODE_ID,
    AIDP_3D_RUBRIC_TASK_ID,
    Aidp3DAnswerError,
    Aidp3DHttpAnswerService,
    Aidp3DLedger,
    Aidp3DRuntime,
    build_temp_payload,
    validate_payload_not_empty,
    validate_submit_item_response,
)


def _content() -> dict:
    return {
        "id": "case-001",
        "category": "chair",
        "ref_img": {"tos_url": "https://example.test/ref.png"},
        "latest_screenshot": {"tos_url": "https://example.test/latest.png"},
        "artifact_views": {
            "front": {"tos_url": "https://example.test/front.png"},
            "right": {"tos_url": "https://example.test/right.png"},
            "back": {"tos_url": "https://example.test/back.png"},
        },
        "rubrics": {
            "rubrics": [
                {"id": "S1-B1", "description": "整体形体一致"},
                {"id": "A-B1", "description": "颜色材质一致"},
            ]
        },
    }


def _decision() -> dict:
    return {
        "rubrics_reasonable": True,
        "rubrics_reasonable_reason": "合理",
        "rubric_items": [
            {"rubric_id": "S1-B1", "verdict": "satisfied", "reason": ""},
            {"rubric_id": "A-B1", "verdict": "unsatisfied", "reason": "候选物体颜色偏深，与参考图不一致。"},
        ],
        "dimension_scores": {
            "S1": {"score": 4, "reason": "整体轮廓接近，但局部比例略有偏差。"},
            "S2": {"score": 4, "reason": "主要结构保留，少量细节缺失。"},
            "A": {"score": 3, "reason": "材质和颜色有明显差异。"},
        },
        "discard": {"selected": False, "reason": ""},
        "evidence_summary": "候选与参考总体相似，但颜色材质有差异。",
        "confidence": "high",
    }


def test_build_temp_payload_keeps_data_and_datamap_equal() -> None:
    payload = build_temp_payload(
        account={"template_id": "template-1"},
        item_id="item-1",
        content=_content(),
        decision=_decision(),
        task_id=AIDP_3D_RUBRIC_TASK_ID,
        node_id="1",
    )

    shape = validate_payload_not_empty(payload)
    answer_content = json.loads(payload["AuditAnswers"][0]["Content"])

    assert answer_content["templateID"] == "template-1"
    assert answer_content["dataMap"] == answer_content["data"]
    assert shape["rubricResultsCount"] == 2
    assert answer_content["data"]["rubricsReason"] == "合理"
    assert answer_content["data"]["rubricResults"][1]["failReason"] == "候选物体颜色偏深，与参考图不一致。"


def test_low_confidence_is_rejected_before_payload_build() -> None:
    decision = _decision()
    decision["confidence"] = "medium"

    with pytest.raises(Aidp3DAnswerError, match="LOW_CONFIDENCE"):
        build_temp_payload(
            account={"template_id": "template-1"},
            item_id="item-1",
            content=_content(),
            decision=decision,
            task_id=AIDP_3D_RUBRIC_TASK_ID,
            node_id="1",
        )


def test_submit_response_requires_modified_ans_version() -> None:
    body = {
        "SubmitItemResponse": {
            "Errors": [],
            "AnsVersions": [{"ItemID": "item-1", "AnsModified": False}],
        }
    }

    with pytest.raises(Aidp3DAnswerError, match="READBACK_MISMATCH"):
        validate_submit_item_response(body, "item-1")


def test_ledger_blocks_submitted_and_unknown_in_progress(tmp_path) -> None:
    ledger = Aidp3DLedger(tmp_path)
    ledger.begin(AIDP_3D_RUBRIC_TASK_ID, "account-1", "item-1", run_id="run-1")
    ledger.mark_submitted(AIDP_3D_RUBRIC_TASK_ID, "account-1", "item-1", evidence={"ok": True})

    with pytest.raises(Aidp3DAnswerError, match="DUPLICATE_SUBMITTED"):
        ledger.begin(AIDP_3D_RUBRIC_TASK_ID, "account-1", "item-1", run_id="run-2")

    ledger.begin(AIDP_3D_RUBRIC_TASK_ID, "account-1", "item-2", run_id="run-3")
    with pytest.raises(Aidp3DAnswerError, match="LEDGER_IN_PROGRESS_UNKNOWN"):
        ledger.begin(AIDP_3D_RUBRIC_TASK_ID, "account-1", "item-2", run_id="run-4")


def test_submit_one_records_real_search_readback(tmp_path) -> None:
    calls: list[str] = []

    def transport(_account: dict, _kind: str, path: str, _body: dict) -> dict:
        calls.append(path)
        if path == "/dispatcher/search_item/category" and calls.count(path) == 1:
            return _search_result("item-1", _content())
        if path == "/api/dispatch/SubmitTempItemAnswer":
            return {"statusCode": 200, "elapsedMs": 3, "body": {"BaseResp": {"StatusCode": 0}}, "text": ""}
        if path == "/api/dispatch/SubmitItemAndReceive":
            return {
                "statusCode": 200,
                "elapsedMs": 5,
                "body": {
                    "SubmitItemResponse": {
                        "BaseResp": {"StatusCode": 0},
                        "Errors": [],
                        "AnsVersions": [{"ItemID": "item-1", "AnsModified": True}],
                    },
                    "ReceiveResponse": {
                        "BaseResp": {"StatusCode": 0},
                        "Items": [{"Item": {"ItemID": "item-2"}}],
                    },
                },
                "text": "",
            }
        if path == "/dispatcher/search_item/category":
            return _search_result("item-2", _content())
        raise AssertionError(f"unexpected path {path}")

    service = Aidp3DHttpAnswerService(
        transport=transport,
        qwen_decider=lambda _runtime, _content: _decision(),
        runtime_loader=lambda: Aidp3DRuntime(base_url="https://qwen.example.test", api_key="secret"),
        ledger=Aidp3DLedger(tmp_path),
    )

    result = service.submit_one(
        account={"template_id": "template-1", "cookie": "session=ok"},
        account_user_id="account-1",
        task_id=AIDP_3D_RUBRIC_TASK_ID,
        node_id=AIDP_3D_RUBRIC_NODE_ID,
        run_id="run-1",
    )

    assert result["success"] is True
    assert result["readback_result"]["item_id"] == "item-2"
    assert result["readback_result"]["raw_result"]["body"]["BaseResp"]["StatusCode"] == 0


def test_submit_one_temp_save_only_does_not_call_formal_submit(tmp_path) -> None:
    calls: list[str] = []

    def transport(_account: dict, _kind: str, path: str, _body: dict) -> dict:
        calls.append(path)
        if path == "/dispatcher/search_item/category":
            return _search_result("item-1", _content())
        if path == "/api/dispatch/SubmitTempItemAnswer":
            return {"statusCode": 200, "elapsedMs": 3, "body": {"BaseResp": {"StatusCode": 0}}, "text": ""}
        raise AssertionError(f"formal submit must not be called in temp-save-only mode: {path}")

    service = Aidp3DHttpAnswerService(
        transport=transport,
        qwen_decider=lambda _runtime, _content: _decision(),
        runtime_loader=lambda: Aidp3DRuntime(base_url="https://qwen.example.test", api_key="secret"),
        ledger=Aidp3DLedger(tmp_path),
    )

    result = service.submit_one(
        account={"template_id": "template-1", "cookie": "session=ok"},
        account_user_id="account-1",
        task_id=AIDP_3D_RUBRIC_TASK_ID,
        node_id=AIDP_3D_RUBRIC_NODE_ID,
        run_id="run-1",
        submit_remote=False,
    )

    ledger = json.loads((tmp_path / AIDP_3D_RUBRIC_TASK_ID / "account-1" / "item-1.json").read_text(encoding="utf-8"))
    assert calls == ["/dispatcher/search_item/category", "/api/dispatch/SubmitTempItemAnswer"]
    assert result["success"] is True
    assert result["submits_remote"] is False
    assert result["temp_save_only"] is True
    assert ledger["status"] == "temp_saved"


def test_temp_save_failure_marks_ledger_failed_not_unknown(tmp_path) -> None:
    def transport(_account: dict, _kind: str, path: str, _body: dict) -> dict:
        if path == "/dispatcher/search_item/category":
            return _search_result("item-1", _content())
        if path == "/api/dispatch/SubmitTempItemAnswer":
            return {"statusCode": 200, "elapsedMs": 3, "body": {"BaseResp": {"StatusCode": 1}}, "text": ""}
        raise AssertionError(f"unexpected path {path}")

    service = Aidp3DHttpAnswerService(
        transport=transport,
        qwen_decider=lambda _runtime, _content: _decision(),
        runtime_loader=lambda: Aidp3DRuntime(base_url="https://qwen.example.test", api_key="secret"),
        ledger=Aidp3DLedger(tmp_path),
    )

    with pytest.raises(Aidp3DAnswerError, match="SUBMIT_FAILED"):
        service.submit_one(
            account={"template_id": "template-1", "cookie": "session=ok"},
            account_user_id="account-1",
            task_id=AIDP_3D_RUBRIC_TASK_ID,
            node_id=AIDP_3D_RUBRIC_NODE_ID,
            run_id="run-1",
        )

    ledger_path = tmp_path / AIDP_3D_RUBRIC_TASK_ID / "account-1" / "item-1.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["status"] == "failed"


def test_post_submit_readback_auth_error_marks_blocked_unknown_with_remote_evidence(tmp_path) -> None:
    calls: list[str] = []

    def transport(_account: dict, _kind: str, path: str, _body: dict) -> dict:
        calls.append(path)
        if path == "/dispatcher/search_item/category" and calls.count(path) == 1:
            return _search_result("item-1", _content())
        if path == "/api/dispatch/SubmitTempItemAnswer":
            return {"statusCode": 200, "elapsedMs": 3, "body": {"BaseResp": {"StatusCode": 0}}, "text": ""}
        if path == "/api/dispatch/SubmitItemAndReceive":
            return _submit_receive_result("item-1", "item-2")
        if path == "/dispatcher/search_item/category":
            return {"statusCode": 401, "elapsedMs": 2, "body": {"BaseResp": {"StatusCode": 401}}, "text": "auth expired"}
        raise AssertionError(f"unexpected path {path}")

    service = Aidp3DHttpAnswerService(
        transport=transport,
        qwen_decider=lambda _runtime, _content: _decision(),
        runtime_loader=lambda: Aidp3DRuntime(base_url="https://qwen.example.test", api_key="secret"),
        ledger=Aidp3DLedger(tmp_path),
    )

    with pytest.raises(Aidp3DAnswerError) as raised:
        service.submit_one(
            account={"template_id": "template-1", "cookie": "session=ok"},
            account_user_id="account-1",
            task_id=AIDP_3D_RUBRIC_TASK_ID,
            node_id=AIDP_3D_RUBRIC_NODE_ID,
            run_id="run-1",
        )

    ledger = json.loads((tmp_path / AIDP_3D_RUBRIC_TASK_ID / "account-1" / "item-1.json").read_text(encoding="utf-8"))
    assert ledger["status"] == "blocked_unknown"
    assert raised.value.evidence["submits_remote"] is True
    assert raised.value.evidence["readback_ok"] is False


def test_receive_next_without_search_readback_is_mismatch(tmp_path) -> None:
    calls: list[str] = []

    def transport(_account: dict, _kind: str, path: str, _body: dict) -> dict:
        calls.append(path)
        if path == "/dispatcher/search_item/category" and calls.count(path) == 1:
            return _search_result("item-1", _content())
        if path == "/api/dispatch/SubmitTempItemAnswer":
            return {"statusCode": 200, "elapsedMs": 3, "body": {"BaseResp": {"StatusCode": 0}}, "text": ""}
        if path == "/api/dispatch/SubmitItemAndReceive":
            return _submit_receive_result("item-1", "item-2")
        if path == "/dispatcher/search_item/category":
            return {"statusCode": 200, "elapsedMs": 2, "body": {"BaseResp": {"StatusCode": 0}, "Data": []}, "text": ""}
        raise AssertionError(f"unexpected path {path}")

    service = Aidp3DHttpAnswerService(
        transport=transport,
        qwen_decider=lambda _runtime, _content: _decision(),
        runtime_loader=lambda: Aidp3DRuntime(base_url="https://qwen.example.test", api_key="secret"),
        ledger=Aidp3DLedger(tmp_path),
    )

    with pytest.raises(Aidp3DAnswerError, match="READBACK_MISMATCH"):
        service.submit_one(
            account={"template_id": "template-1", "cookie": "session=ok"},
            account_user_id="account-1",
            task_id=AIDP_3D_RUBRIC_TASK_ID,
            node_id=AIDP_3D_RUBRIC_NODE_ID,
            run_id="run-1",
        )

    ledger = json.loads((tmp_path / AIDP_3D_RUBRIC_TASK_ID / "account-1" / "item-1.json").read_text(encoding="utf-8"))
    assert ledger["status"] == "blocked_unknown"
    assert ledger["evidence"]["submits_remote"] is True


def _search_result(item_id: str, content: dict) -> dict:
    return {
        "statusCode": 200,
        "elapsedMs": 2,
        "body": {
            "BaseResp": {"StatusCode": 0},
            "Data": [{"ItemID": item_id, "Content": json.dumps(content, ensure_ascii=False)}],
        },
        "text": "",
    }


def _submit_receive_result(item_id: str, next_item_id: str) -> dict:
    return {
        "statusCode": 200,
        "elapsedMs": 5,
        "body": {
            "SubmitItemResponse": {
                "BaseResp": {"StatusCode": 0},
                "Errors": [],
                "AnsVersions": [{"ItemID": item_id, "AnsModified": True}],
            },
            "ReceiveResponse": {
                "BaseResp": {"StatusCode": 0},
                "Items": [{"Item": {"ItemID": next_item_id}}],
            },
        },
        "text": "",
    }
