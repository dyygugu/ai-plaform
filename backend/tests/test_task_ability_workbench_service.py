import hashlib
import json
from time import perf_counter
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.task_ability_service import (
    TaskAbilityFlowError,
    build_task_ability_payload_debug,
    chat_task_ability,
    approve_task_ability_version,
    create_task_ability_draft,
    create_task_ability_replay_report,
    get_latest_task_ability_live_http_test_report,
    get_task_ability_run_gate,
    get_task_ability_run_config,
    list_prompt_snapshots,
    replay_task_ability_testset,
    record_task_ability_run,
    update_task_ability_draft,
    update_task_ability_run_config,
    run_task_ability_real_no_submit,
    _build_3d_rubric_ai_messages,
    _build_live_question_context_from_category,
    _normalize_3d_rubric_ai_decision,
    _parse_3d_rubric_ai_decision,
    _build_research_chart_ai_messages,
    _parse_research_chart_ai_decision,
)
from app.schemas.task_ability import TaskAbilityDraftCreateRequest
from app.services.learning_package_service import get_selected_learning_package_summary


def _write_store(path: Path, *, capability_enabled: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "version": "ability-v8",
                        "status": "有做题能力" if capability_enabled else "待审核真实不提交结果",
                        "task_name": "RFT科研图表还原-正式(全量数据)",
                        "task_id": "7639402643386830630",
                        "specific_rules": "严格对比",
                        "sample_data": "样例",
                        "related_content": "",
                        "system_ai_draft": "草稿",
                        "system_ai_trace_id": "",
                        "provider_status": "local",
                        "next_step": "下一步",
                        "flow_stage": "capability_enabled" if capability_enabled else "real_no_submit_review",
                        "capability_enabled": capability_enabled,
                        "real_no_submit_review": {
                            "review_status": "人工已通过" if capability_enabled else "待人工审核",
                            "saved_to_task_ui": True,
                            "approved_at": "2026-05-16T00:00:00+00:00" if capability_enabled else "",
                        },
                        "created_at": "2026-05-16T00:00:00+00:00",
                        "updated_at": "2026-05-16T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _ability_prompt_fingerprint(
    *,
    system_ai_draft: str = "草稿",
    specific_rules: str = "严格对比",
    sample_data: str = "样例",
    related_content: str = "",
    source_config: dict | None = None,
    field_mapping: dict | None = None,
    validation_rules: dict | None = None,
    task_type: str = "",
    ability_source: str = "",
) -> str:
    payload = {
        "system_ai_draft": system_ai_draft,
        "specific_rules": specific_rules,
        "sample_data": sample_data,
        "related_content": related_content,
        "source_config": source_config or {},
        "field_mapping": field_mapping or {},
        "validation_rules": validation_rules or {},
        "task_type": task_type,
        "ability_source": ability_source,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_allowed_live_report(
    store: Path,
    *,
    task_id: str = "7639402643386830630",
    draft_id: str = "draft-1",
    prompt_fingerprint: str = "",
    report_name: str = "live-allow",
) -> None:
    review_root = store.parent / f"research-chart-{task_id}" / "real-no-submit-reviews"
    review_root.mkdir(parents=True, exist_ok=True)
    (review_root / f"{report_name}.json").write_text(
        json.dumps(
            {
                "ok": True,
                "draft_id": draft_id,
                "task_id": task_id,
                "prompt": {
                    "fingerprint": prompt_fingerprint or _ability_prompt_fingerprint(),
                },
                "saved_to_task_ui": True,
                "submits_remote": False,
                "review_status": "待人工审核",
                "question_context": {"item_id": "item-allow"},
                "ai_decision": {
                    "rubric_items": [
                        {"rubric_id": "R1", "verdict": "满足", "reason": "主体结构与参考一致，未发现明显缺失。"},
                    ]
                },
                "created_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_3d_ability_store(path: Path, *, enabled: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "draft-3d-1",
                        "version": "ability-test-3d",
                        "status": "有做题能力" if enabled else "待审核真实不提交结果",
                        "task_name": "Blender_3D 人标支持-0703",
                        "task_id": "7658232870117527347",
                        "task_type": "3d_rubric_eval",
                        "ability_source": "assistant_authored",
                        "source_config": {"manual": "3D 标注手册 + 单题实操"},
                        "field_mapping": {"payload_kind": "aidp_neeko_3d_rubric_v1"},
                        "validation_rules": {"require_unsatisfied_reason": True, "required_dimensions": ["S1", "S2", "A"]},
                        "specific_rules": "先看参考图和候选图，逐条判断 rubric，三维度独立评分。",
                        "sample_data": "参考图、候选图、多视角截图、Rubrics。",
                        "related_content": "3D 标注手册：看不清默认不满足，不能脑补。",
                        "system_ai_draft": "输出严格 JSON。",
                        "system_ai_trace_id": "",
                        "provider_status": "test",
                        "next_step": "待真实题不提交审核。",
                        "flow_stage": "capability_enabled" if enabled else "real_no_submit_review",
                        "capability_enabled": enabled,
                        "real_no_submit_review": {
                            "review_status": "人工已通过" if enabled else "待人工审核",
                            "saved_to_task_ui": enabled,
                        },
                        "created_at": "2026-07-03T00:00:00+00:00",
                        "updated_at": "2026-07-03T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _valid_3d_decision() -> dict:
    return {
        "task_type": "3d_rubric_eval",
        "rubrics_reasonable": True,
        "rubrics_reasonable_reason": "Rubrics 围绕参考海螺的整体形体、结构和材质颜色展开。",
        "rubric_items": [
            {
                "rubric_id": "1",
                "verdict": "unsatisfied",
                "reason": "候选整体更像竖向卷曲带状/螺旋管，未保留横向左宽右窄的海螺剪影。",
            },
            {
                "rubric_id": "2",
                "verdict": "satisfied",
                "reason": "",
            },
        ],
        "dimension_scores": {
            "S1": {"score": 2, "reason": "仍能看出贝壳类对象，但整体轮廓偏差明显。"},
            "S2": {"score": 3, "reason": "关键元素有召回，但装配关系不够准确。"},
            "A": {"score": 4, "reason": "颜色、条纹和光泽基本接近参考。"},
        },
        "discard": {"selected": False, "reason": ""},
        "evidence_summary": "参考为横向海螺，候选结构偏竖向卷曲但材质接近。",
        "confidence": "medium",
    }


def _valid_3d_context_rubric(rubric_id: str = "1", question: str = "整体轮廓是否一致") -> dict:
    return {
        "rubric_id": rubric_id,
        "text": question,
        "question": question,
        "pass_criterion": "候选模型符合参考图中的关键形体、结构或材质要求。",
        "fail_criterion": "候选模型明显缺失或偏离参考图中的关键形体、结构或材质要求。",
    }


def _write_learning_package_index(store: Path, *, task_id: str = "7639402643386830630", package_id: str = "rec-1") -> None:
    package_dir = store.parent / task_id / "learning-packages"
    package_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = package_dir / f"{package_id}.json"
    artifact_path.write_text(
        json.dumps(
            {
                "recording_id": package_id,
                "operation_claim_analysis": {"candidates": [{"path": "/api/dispatch/Receive"}]},
                "recording": {
                    "mode": "full",
                    "network": [
                        {"type": "request", "method": "POST", "url": "https://aidp.juejin.cn/api/dispatch/Receive"},
                        {
                            "type": "request",
                            "method": "POST",
                            "url": "https://aidp.juejin.cn/api/dispatch/SubmitTempItemAnswer",
                            "request_body": json.dumps(
                                {
                                    "AuditAnswers": [
                                        {
                                            "ItemID": "item-1",
                                            "Content": json.dumps(
                                                {
                                                    "item": {
                                                        "image_gt": "https://example.com/ref.png",
                                                        "model_image": "https://example.com/ai.png",
                                                    },
                                                    "data": {
                                                        "label_sorce": {"model_image": "1"},
                                                        "label_remark": {"model_image": "差异偏笼统"},
                                                        "discard": "No",
                                                    },
                                                },
                                                ensure_ascii=False,
                                            ),
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    "dom_snapshots": [{"title": "科研图评分页"}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (package_dir / "index.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "selected_learning_package_id": package_id,
                "items": [
                    {
                        "learning_package_id": package_id,
                        "recording_id": package_id,
                        "task_id": task_id,
                        "display_name": "真实插件录制包",
                        "source": "browser_extension",
                        "uploaded_at": "2026-05-21T02:00:00+08:00",
                        "status": "parsed",
                        "completeness": "complete",
                        "detected_actions": ["fill_score", "fill_reason", "click_temp_save"],
                        "page_url": "https://aidp.juejin.cn/operation/task-v2/demo/mark-v3/",
                        "task_id_candidates": [{"value": task_id, "source": "url", "confidence": "high"}],
                        "artifact_path": str(artifact_path),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_single_sample_testset(store: Path, *, task_id: str = "7639402643386830630") -> None:
    testset_dir = store.parent / task_id / "testsets"
    sample_dir = store.parent / task_id / "submitted-history" / "samples"
    testset_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)
    (testset_dir / "current.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "task_name": "科研图",
                "sample_pool_count": 1,
                "testset_id": f"testset-{task_id}-fixed10",
                "sample_count": 1,
                "sample_ids": ["uid-1"],
                "source": "submitted_history",
                "created_at": "2026-05-16T00:00:00+00:00",
                "path": str(testset_dir / "current.json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sample_dir / "uid-1.json").write_text(
        json.dumps(
            {
                "uid": "uid-1",
                "item_id": "item-1",
                "task_id": task_id,
                "task_name": "科研图",
                "account_id": "account-sample-002",
                "source": "submitted_history_http",
                "submitted_nodes": [],
                "primary_output": {
                    "item": {
                        "uid": "uid-1",
                        "image_gt": "https://example.com/ref.png",
                        "model_image": "https://example.com/ai.png",
                    },
                    "data": {
                        "label_sorce": {"model_image": "2"},
                        "label_remark": {"model_image": "完全一致"},
                        "discard": "No",
                    },
                },
                "raw": {},
                "synced_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_list_prompt_snapshots_returns_latest_first(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    snapshot_dir = store.parent / "7639402643386830630" / "prompt-history"
    _write_store(store)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "prompt-old.json").write_text(
        json.dumps(
            {
                "snapshot_id": "prompt-old",
                "task_id": "7639402643386830630",
                "draft_id": "draft-1",
                "task_name": "旧版",
                "ability_version": "ability-v7",
                "created_at": "2026-05-15T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (snapshot_dir / "prompt-new.json").write_text(
        json.dumps(
            {
                "snapshot_id": "prompt-new",
                "task_id": "7639402643386830630",
                "draft_id": "draft-1",
                "task_name": "新版",
                "ability_version": "ability-v8",
                "created_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    snapshots = list_prompt_snapshots("7639402643386830630", store_path=store)

    assert [item["snapshot_id"] for item in snapshots] == ["prompt-new", "prompt-old"]


def test_create_task_ability_draft_preserves_3d_source_metadata(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"

    draft = create_task_ability_draft(
        TaskAbilityDraftCreateRequest(
            task_name="Blender_3D 人标支持-0703",
            task_id="7658232870117527347",
            task_type="3d_rubric_eval",
            ability_source="assistant_authored",
            source_config={"manual": "3D 标注手册 + 单题实操"},
            field_mapping={"payload_kind": "aidp_neeko_3d_rubric_v1"},
            validation_rules={"require_unsatisfied_reason": True, "required_dimensions": ["S1", "S2", "A"]},
            specific_rules="逐条判断 Rubric，三维度独立评分。",
            sample_data="参考图、候选图、多视角截图。",
            related_content="看不清默认不满足。",
            system_ai_draft="输出 3D rubric JSON。",
            provider_status="test",
        ),
        store_path=store,
    )

    assert draft.task_type == "3d_rubric_eval"
    assert draft.ability_source == "assistant_authored"
    assert draft.field_mapping["payload_kind"] == "aidp_neeko_3d_rubric_v1"
    assert draft.validation_rules["required_dimensions"] == ["S1", "S2", "A"]


def test_update_task_ability_draft_can_clear_task_type_and_resets_gate(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_3d_ability_store(store, enabled=True)

    updated = update_task_ability_draft(
        "draft-3d-1",
        {"task_type": "", "ability_source": "platform_form"},
        store_path=store,
    )

    assert updated["task_type"] == ""
    assert updated["flow_stage"] == "real_no_submit_ready"
    assert updated["capability_enabled"] is False
    assert updated["real_no_submit_review"]["review_status"] == "待重新验证"


def test_3d_rubric_decision_requires_reason_for_unsatisfied_rubric() -> None:
    decision = _valid_3d_decision()
    decision["rubric_items"][0]["reason"] = ""

    try:
        _normalize_3d_rubric_ai_decision(decision)
    except TaskAbilityFlowError as exc:
        assert "Rubric 1" in str(exc)
        assert "不满足原因" in str(exc)
    else:
        raise AssertionError("缺少不满足原因时必须阻塞 3D 判题结果。")


def test_3d_rubric_reasonable_decision_defaults_reason_to_reasonable_label() -> None:
    decision = _valid_3d_decision()
    decision["rubrics_reasonable"] = True
    decision["rubrics_reasonable_reason"] = ""

    normalized = _normalize_3d_rubric_ai_decision(decision)

    assert normalized["rubrics_reasonable_reason"] == "合理"


def test_3d_rubric_reasonable_decision_forces_reasonable_label() -> None:
    decision = _valid_3d_decision()
    decision["rubrics_reasonable"] = True
    decision["rubrics_reasonable_reason"] = "Rubrics 围绕参考对象、结构和材质展开，所以整体合理。"

    normalized = _normalize_3d_rubric_ai_decision(decision)

    assert normalized["rubrics_reasonable"] is True
    assert normalized["rubrics_reasonable_reason"] == "合理"


def test_3d_rubric_decision_parses_string_false_and_requires_reason() -> None:
    decision = _valid_3d_decision()
    decision["rubrics_reasonable"] = "false"
    decision["rubrics_reasonable_reason"] = ""

    try:
        _normalize_3d_rubric_ai_decision(decision)
    except TaskAbilityFlowError as exc:
        assert "Rubrics 判为不合理时必须填写原因" in str(exc)
    else:
        raise AssertionError("字符串 false 也必须按不合理处理，且缺少原因时阻断。")


def test_3d_rubric_decision_accepts_string_false_with_reason() -> None:
    decision = _valid_3d_decision()
    decision["rubrics_reasonable"] = "false"
    decision["rubrics_reasonable_reason"] = "Rubrics 与参考对象无关。"

    normalized = _normalize_3d_rubric_ai_decision(decision)

    assert normalized["rubrics_reasonable"] is False
    assert normalized["rubrics_reasonable_reason"] == "Rubrics 与参考对象无关。"


def test_3d_rubric_decision_accepts_json_false_with_reason() -> None:
    decision = _valid_3d_decision()
    decision["rubrics_reasonable"] = False
    decision["rubrics_reasonable_reason"] = "Rubrics 与参考对象无关。"

    normalized = _normalize_3d_rubric_ai_decision(decision)

    assert normalized["rubrics_reasonable"] is False
    assert normalized["rubrics_reasonable_reason"] == "Rubrics 与参考对象无关。"


def test_3d_rubric_decision_parses_discard_string_false_as_not_selected() -> None:
    decision = _valid_3d_decision()
    decision["discard"] = {"selected": "false", "reason": ""}

    normalized = _normalize_3d_rubric_ai_decision(decision)

    assert normalized["discard"]["selected"] is False


def test_3d_real_no_submit_builds_structured_review_without_remote_write(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_3d_ability_store(store)

    artifact = run_task_ability_real_no_submit(
        "draft-3d-1",
        store_path=store,
        question_context={
            "source_mode": "live_search_item_category",
            "item_id": "item-3d-1",
            "uid": "uid-3d-1",
            "node_id": "1",
            "reference_image": "https://example.test/reference.png",
            "candidate_images": [{"label": "front", "url": "https://example.test/front.png"}],
            "rubrics": [_valid_3d_context_rubric("1", "整体轮廓是否一致"), _valid_3d_context_rubric("2", "比例是否清楚")],
        },
        ai_decision=_valid_3d_decision(),
        allow_temp_save=False,
    )

    assert artifact["ok"] is True
    assert artifact["submits_remote"] is False
    assert artifact["writes_remote"] is False
    assert artifact["ai_decision"]["task_type"] == "3d_rubric_eval"
    assert artifact["answer_preview"]["rubric_items.1.verdict"] == "unsatisfied"
    assert artifact["answer_preview"]["dimension_scores.S1.score"] == 2
    assert artifact["review_status"] == "待人工审核"


def test_3d_real_no_submit_blocks_temp_save_without_verified_payload_mapping(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_3d_ability_store(store)

    try:
        run_task_ability_real_no_submit(
            "draft-3d-1",
            store_path=store,
            question_context={"source_mode": "live_search_item_category", "item_id": "item-3d-1", "node_id": "1"},
            ai_decision=_valid_3d_decision(),
            allow_temp_save=True,
        )
    except TaskAbilityFlowError as exc:
        assert "3D 暂存字段映射尚未验证" in str(exc)
    else:
        raise AssertionError("3D 任务未验证真实字段映射前不能暂存到远端。")


def test_3d_real_no_submit_rejects_draft_only_context(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_3d_ability_store(store)

    try:
        run_task_ability_real_no_submit(
            "draft-3d-1",
            store_path=store,
            ai_decision=_valid_3d_decision(),
            allow_temp_save=False,
        )
    except TaskAbilityFlowError as exc:
        assert "3D 真实题上下文" in str(exc)
    else:
        raise AssertionError("3D 真实题不提交不能在 draft-only 空题面上生成审核件。")


def test_3d_live_category_context_extracts_submitted_item_images_and_rubrics() -> None:
    posted_payloads: list[dict] = []

    class FakeCategoryResponse:
        status_code = 200

        def __init__(self, items: list[dict]) -> None:
            self._items = items

        def json(self) -> dict:
            return {"BaseResp": {"StatusCode": 0, "StatusMessage": ""}, "Data": {"Data": self._items}}

    content = {
        "ref_img": {"tos_url": "https://example.test/ref.jpg"},
        "latest_screenshot": {"tos_url": "https://example.test/latest.png"},
        "artifact_views": {
            "front": {"tos_url": "https://example.test/front.png"},
            "three_quarter": {"tos_url": "https://example.test/three-quarter.png"},
        },
        "rubrics": {
            "rubric_design_note": "先看整体海螺剪影，再看结构和材质。",
            "target_summary": "参考图是一枚横向海螺壳。",
            "rubrics": [
                {
                    "id": "S1-B1",
                    "dimension": "形体",
                    "question": "是否保留左宽右尖的横向海螺剪影？",
                    "pass_criterion": "左侧体量更大，右侧逐渐收尖。",
                    "fail_criterion": "整体变成球形或对称贝壳。",
                }
            ],
        },
    }
    submitted_item = {
        "ItemID": "7658288177744908083",
        "Status": 7,
        "Content": json.dumps(content, ensure_ascii=False),
    }

    def fake_post(_url: str, *, json: dict, **_kwargs: object) -> FakeCategoryResponse:
        posted_payloads.append(json)
        if json["ItemCategoryType"] == 0:
            return FakeCategoryResponse([])
        return FakeCategoryResponse([submitted_item])

    with patch(
        "app.services.task_ability_service._find_state_account",
        return_value={"cookie": "cookie=1", "referer": "https://aidp.juejin.cn/operation/task-v2?page=1"},
    ):
        with patch("app.services.task_ability_service.requests.post", side_effect=fake_post):
            context = _build_live_question_context_from_category(
                {"task_id": "7658232870117527347", "task_type": "3d_rubric_eval"},
                {"task_id": "7658232870117527347", "account_user_id": "7630778503730253620"},
            )

    assert context is not None
    assert [item["ItemCategoryType"] for item in posted_payloads] == [0, 1]
    assert context["source_mode"] == "live_search_item_category"
    assert context["item_category_type"] == 1
    assert context["item_id"] == "7658288177744908083"
    assert context["reference_image"] == "https://example.test/ref.jpg"
    assert context["candidate_images"] == [
        {"label": "front", "url": "https://example.test/front.png"},
        {"label": "three_quarter", "url": "https://example.test/three-quarter.png"},
    ]
    assert context["rubrics"][0]["rubric_id"] == "S1-B1"
    assert "海螺剪影" in context["rubrics"][0]["question"]
    assert context["extra_context"]["target_summary"] == "参考图是一枚横向海螺壳。"


def test_3d_live_category_context_rejects_empty_rubric_items(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_3d_ability_store(store)
    incomplete_context = {
        "source_mode": "live_search_item_category",
        "item_id": "item-3d-1",
        "reference_image": "https://example.test/reference.png",
        "candidate_images": [{"label": "front", "url": "https://example.test/front.png"}],
        "rubrics": [{"rubric_id": "S1-B1", "dimension": "", "question": "", "pass_criterion": "", "fail_criterion": ""}],
    }

    try:
        run_task_ability_real_no_submit(
            "draft-3d-1",
            store_path=store,
            question_context=incomplete_context,
            ai_decision=_valid_3d_decision(),
            allow_temp_save=False,
        )
    except TaskAbilityFlowError as exc:
        assert "Rubric S1-B1 缺少题目或判定标准" in str(exc)
    else:
        raise AssertionError("3D live rubric 缺少题目/判定标准时必须阻断。")


def test_3d_latest_live_http_report_uses_3d_review_root(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_3d_ability_store(store)

    artifact = run_task_ability_real_no_submit(
        "draft-3d-1",
        store_path=store,
        question_context={
            "source_mode": "live_search_item_category",
            "item_id": "item-3d-1",
            "uid": "uid-3d-1",
            "node_id": "1",
            "reference_image": "https://example.test/reference.png",
            "candidate_images": [{"label": "front", "url": "https://example.test/front.png"}],
            "rubrics": [_valid_3d_context_rubric("1", "整体轮廓是否一致")],
        },
        ai_decision=_valid_3d_decision(),
        allow_temp_save=False,
    )

    latest = get_latest_task_ability_live_http_test_report("7658232870117527347", store_path=store)
    gate = get_task_ability_run_gate("7658232870117527347", store_path=store)

    assert latest["report_id"] == Path(artifact["review_artifact_path"]).stem
    assert latest["task_id"] == "7658232870117527347"
    assert gate["live_test_report"]["report_id"] == latest["report_id"]


def test_3d_rubric_ai_messages_keep_manual_rules_current_item_and_output_schema_separate() -> None:
    messages = _build_3d_rubric_ai_messages(
        {
            "item_id": "item-3d-1",
            "reference_image": "https://example.test/reference.png",
            "candidate_images": [{"label": "front", "url": "https://example.test/front.png"}],
            "rubrics": [_valid_3d_context_rubric("1", "整体轮廓是否一致")],
        },
        {
            "task_name": "Blender_3D 人标支持-0703",
            "task_id": "7658232870117527347",
            "version": "ability-test-3d",
            "system_ai_draft": "3D 标注手册：先看图，再逐条判断 rubric。",
        },
        {"model": "test-model"},
    )

    user_content = messages[1]["content"]
    assert isinstance(user_content, list)
    user_text = user_content[0]["text"]
    assert "manual_rules" in user_text
    assert "current_item_input" in user_text
    assert "output_schema" in user_text
    assert "rubric_items" in user_text
    assert "rubrics_reasonable=true 时 rubrics_reasonable_reason 必须填写“合理”" in user_text
    assert "rubrics_reasonable=false 时 rubrics_reasonable_reason 简短说明不合理原因" in user_text
    assert user_content[1]["image_url"]["url"] == "https://example.test/reference.png"
    assert user_content[2]["image_url"]["url"] == "https://example.test/front.png"


def test_parse_3d_rubric_ai_decision_uses_same_validation_rules() -> None:
    parsed = _parse_3d_rubric_ai_decision(json.dumps(_valid_3d_decision(), ensure_ascii=False))

    assert parsed["task_type"] == "3d_rubric_eval"
    assert parsed["rubric_items"][0]["verdict"] == "unsatisfied"
    assert parsed["dimension_scores"]["A"]["score"] == 4


def test_3d_real_no_submit_calls_task_ai_provider_when_decision_not_injected(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_3d_ability_store(store)

    class FakeProviderResponse:
        ok = True
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(_valid_3d_decision(), ensure_ascii=False)}}]}

    with patch(
        "app.services.task_ability_service.get_task_ai_runtime_prompt",
        return_value={"provider_configured": True, "base_url": "https://provider.test/v1", "api_key": "test-key", "model": "vision-test", "timeout_seconds": 1},
    ):
        with patch("app.services.task_ability_service.requests.post", return_value=FakeProviderResponse()) as post:
            artifact = run_task_ability_real_no_submit(
                "draft-3d-1",
                store_path=store,
                question_context={
                    "source_mode": "live_search_item_category",
                    "item_id": "item-3d-1",
                    "reference_image": "https://example.test/reference.png",
                    "candidate_images": [{"label": "front", "url": "https://example.test/front.png"}],
                    "rubrics": [_valid_3d_context_rubric("1", "整体轮廓是否一致"), _valid_3d_context_rubric("2", "比例是否清楚")],
                },
                allow_temp_save=False,
            )

    assert post.called
    assert artifact["ai_decision"]["provider_role"] == "task_ai_3d"
    assert artifact["writes_remote"] is False
    assert artifact["submits_remote"] is False


def test_3d_real_no_submit_can_use_system_ai_for_vision_when_task_ai_is_empty(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_3d_ability_store(store)

    class FakeProviderResponse:
        ok = True
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(_valid_3d_decision(), ensure_ascii=False)}}]}

    posted_payloads: list[dict] = []

    def fake_post(_url: str, *, json: dict, **_kwargs: object) -> FakeProviderResponse:
        posted_payloads.append(json)
        return FakeProviderResponse()

    with patch(
        "app.services.task_ability_service.get_task_ai_runtime_prompt",
        return_value={"provider_configured": False, "base_url": "", "api_key": "", "model": "", "timeout_seconds": 1},
    ):
        with patch(
            "app.services.task_ability_service.get_system_ai_runtime_prompt",
            return_value={"provider_configured": True, "base_url": "https://provider.test/v1", "api_key": "test-key", "model": "system-vision", "timeout_seconds": 1},
        ):
            with patch("app.services.task_ability_service.requests.post", side_effect=fake_post):
                artifact = run_task_ability_real_no_submit(
                    "draft-3d-1",
                    store_path=store,
                    question_context={
                        "source_mode": "live_search_item_category",
                        "item_id": "item-3d-1",
                        "reference_image": "https://example.test/reference.png",
                        "candidate_images": [{"label": "front", "url": "https://example.test/front.png"}],
                        "rubrics": [_valid_3d_context_rubric("1", "整体轮廓是否一致")],
                    },
                    allow_temp_save=False,
                    use_system_ai_for_vision=True,
                )

    assert posted_payloads[0]["model"] == "system-vision"
    assert artifact["ai_decision"]["provider_role"] == "system_ai_3d_vision"
    assert artifact["writes_remote"] is False
    assert artifact["submits_remote"] is False


def test_get_latest_live_http_test_report_returns_latest_artifact(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    review_root = store.parent / "research-chart-7639402643386830630" / "real-no-submit-reviews"
    _write_store(store)
    review_root.mkdir(parents=True, exist_ok=True)
    (review_root / "draft-1-20260515000000.json").write_text(
        json.dumps({"draft_id": "draft-1", "saved_to_task_ui": False, "created_at": "2026-05-15T00:00:00+00:00"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (review_root / "draft-1-20260516000000.json").write_text(
        json.dumps({"draft_id": "draft-1", "saved_to_task_ui": True, "created_at": "2026-05-16T00:00:00+00:00"}, ensure_ascii=False),
        encoding="utf-8",
    )

    latest = get_latest_task_ability_live_http_test_report("7639402643386830630", store_path=store)

    assert latest["report_id"] == "draft-1-20260516000000"
    assert latest["saved_to_task_ui"] is True


def test_task_run_gate_requires_trial_before_production(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    _write_allowed_live_report(store)

    initial_gate = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert initial_gate["can_start_trial"] is True
    assert initial_gate["can_start_production"] is False

    record_task_ability_run(
        "7639402643386830630",
        "trial",
        {
            "run_id": "task-auto-trial-1",
            "status": "running_auto",
            "selected_account_count": 1,
            "generated_at": "2026-05-16T01:00:00+00:00",
        },
        store_path=store,
    )

    gated = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert gated["can_start_production"] is False
    assert gated["last_trial_run"]["run_id"] == "task-auto-trial-1"
    assert "试运行" in gated["next_step"]

    record_task_ability_run(
        "7639402643386830630",
        "trial",
        {
            "run_id": "task-auto-trial-pass",
            "status": "completed",
            "selected_account_count": 1,
            "healthy_account_count": 1,
            "abnormal_account_count": 0,
            "health_ok": True,
            "generated_at": "2026-05-16T01:30:00+00:00",
        },
        store_path=store,
    )

    passed_gate = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert passed_gate["can_start_production"] is True
    assert passed_gate["last_trial_run"]["run_id"] == "task-auto-trial-pass"

    record_task_ability_run(
        "7639402643386830630",
        "production",
        {
            "run_id": "task-auto-prod-1",
            "status": "running_auto",
            "selected_account_count": 1,
            "generated_at": "2026-05-16T02:00:00+00:00",
        },
        store_path=store,
    )

    after_production = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert after_production["last_production_run"]["run_id"] == "task-auto-prod-1"


def test_task_run_gate_blocks_stale_live_report_after_prompt_change(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    _write_allowed_live_report(store)

    before = get_task_ability_run_gate("7639402643386830630", store_path=store)
    assert before["can_start_trial"] is True

    update_task_ability_draft("draft-1", {"system_ai_draft": "草稿已经改动，需要重新 live 验证"}, store_path=store)
    after = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert after["can_start_trial"] is False
    assert after["step3_review_status"] == "blocked"
    assert "已过期" in after["step3_review_message"]


def test_task_run_gate_blocks_stale_trial_after_prompt_change_and_new_step3(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    _write_allowed_live_report(store)
    record_task_ability_run(
        "7639402643386830630",
        "trial",
        {
            "run_id": "task-auto-trial-old-prompt",
            "status": "completed",
            "selected_account_count": 1,
            "healthy_account_count": 1,
            "abnormal_account_count": 0,
            "health_ok": True,
            "generated_at": "2026-05-16T01:30:00+00:00",
        },
        store_path=store,
    )
    before = get_task_ability_run_gate("7639402643386830630", store_path=store)
    assert before["can_start_production"] is True

    update_task_ability_draft("draft-1", {"system_ai_draft": "草稿-v2"}, store_path=store)
    _write_allowed_live_report(
        store,
        prompt_fingerprint=_ability_prompt_fingerprint(system_ai_draft="草稿-v2"),
        report_name="live-allow-v2",
    )
    payload = json.loads(store.read_text(encoding="utf-8-sig"))
    for item in payload["items"]:
        if item.get("id") == "draft-1":
            item["real_no_submit_review"] = {
                "review_status": "待人工审核",
                "saved_to_task_ui": True,
            }
            break
    store.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    approve_task_ability_version("7639402643386830630", store_path=store)

    after = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert after["can_start_trial"] is True
    assert after["can_start_production"] is False
    assert after["last_trial_run"]["run_id"] == "task-auto-trial-old-prompt"
    assert "试运行" in after["next_step"]
    with pytest.raises(TaskAbilityFlowError, match="试运行"):
        record_task_ability_run(
            "7639402643386830630",
            "production",
            {
                "run_id": "task-auto-prod-should-block",
                "status": "running_auto",
                "selected_account_count": 1,
                "generated_at": "2026-05-16T02:00:00+00:00",
            },
            store_path=store,
        )


def test_task_run_gate_blocks_stale_trial_after_field_mapping_change_and_new_step3(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    _write_allowed_live_report(store)
    record_task_ability_run(
        "7639402643386830630",
        "trial",
        {
            "run_id": "task-auto-trial-old-mapping",
            "status": "completed",
            "selected_account_count": 1,
            "healthy_account_count": 1,
            "abnormal_account_count": 0,
            "health_ok": True,
            "generated_at": "2026-05-16T01:30:00+00:00",
        },
        store_path=store,
    )
    before = get_task_ability_run_gate("7639402643386830630", store_path=store)
    assert before["can_start_production"] is True

    new_mapping = {"payload_kind": "research_chart_v2", "score_path": "data.label_sorce.model_image"}
    update_task_ability_draft("draft-1", {"field_mapping": new_mapping}, store_path=store)
    _write_allowed_live_report(
        store,
        prompt_fingerprint=_ability_prompt_fingerprint(field_mapping=new_mapping),
        report_name="live-allow-mapping-v2",
    )
    payload = json.loads(store.read_text(encoding="utf-8-sig"))
    for item in payload["items"]:
        if item.get("id") == "draft-1":
            item["real_no_submit_review"] = {
                "review_status": "待人工审核",
                "saved_to_task_ui": True,
            }
            break
    store.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    approve_task_ability_version("7639402643386830630", store_path=store)

    after = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert after["can_start_trial"] is True
    assert after["can_start_production"] is False
    assert after["last_trial_run"]["run_id"] == "task-auto-trial-old-mapping"


def test_task_run_gate_blocks_step4_without_live_report(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)

    gate = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert gate["step3_review_status"] == "blocked"
    assert gate["can_start_trial"] is False
    assert gate["can_start_production"] is False
    assert "还没有 Step3 live 审核件" in gate["next_step"]


def test_task_run_gate_blocks_review_with_unlikely_precision(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store)
    review_root = store.parent / "research-chart-7639402643386830630" / "real-no-submit-reviews"
    review_root.mkdir(parents=True, exist_ok=True)
    (review_root / "live-precision.json").write_text(
        json.dumps(
                {
                    "ok": True,
                    "draft_id": "draft-1",
                    "prompt": {"fingerprint": _ability_prompt_fingerprint()},
                    "saved_to_task_ui": True,
                "submits_remote": False,
                "review_status": "待人工审核",
                "question_context": {"item_id": "item-1"},
                "ai_decision": {"reason": "候选图颜色为 #33a7ff，边缘偏移 12px。"},
                "created_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    gate = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert gate["can_approve"] is False
    assert gate["step3_review_status"] == "needs_review"
    assert "人工复核" in gate["next_step"]


def test_task_run_gate_blocks_partial_missing_rubric_reason(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store)
    review_root = store.parent / "research-chart-7639402643386830630" / "real-no-submit-reviews"
    review_root.mkdir(parents=True, exist_ok=True)
    (review_root / "live-partial-missing-reason.json").write_text(
        json.dumps(
                {
                    "ok": True,
                    "draft_id": "draft-1",
                    "prompt": {"fingerprint": _ability_prompt_fingerprint()},
                    "saved_to_task_ui": True,
                "submits_remote": False,
                "review_status": "待人工审核",
                "question_context": {"item_id": "item-1"},
                "ai_decision": {
                    "rubric_items": [
                        {"rubric_id": "R1", "verdict": "满足", "reason": "主体结构与参考一致。"},
                        {"rubric_id": "R2", "verdict": "不满足", "reason": ""},
                    ]
                },
                "created_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    gate = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert gate["can_approve"] is False
    assert gate["step3_review_status"] == "blocked"
    assert gate["step3_reason_count"] == 2
    assert "缺少原因" in gate["next_step"]


def test_approve_enabled_task_still_requires_latest_step3_gate(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    review_root = store.parent / "research-chart-7639402643386830630" / "real-no-submit-reviews"
    review_root.mkdir(parents=True, exist_ok=True)
    (review_root / "live-needs-review.json").write_text(
        json.dumps(
                {
                    "ok": True,
                    "draft_id": "draft-1",
                    "prompt": {"fingerprint": _ability_prompt_fingerprint()},
                    "saved_to_task_ui": True,
                "submits_remote": False,
                "review_status": "待人工审核",
                "question_context": {"item_id": "item-1"},
                "ai_decision": {"reason": "候选图颜色为 #33a7ff，边缘偏移 12px。"},
                "created_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        approve_task_ability_version("7639402643386830630", store_path=store)
    except TaskAbilityFlowError as exc:
        assert "人工复核" in str(exc)
    else:
        raise AssertionError("latest Step3 needs_review must block approve even when capability is already enabled")


def test_task_run_gate_counts_nested_qwen_reasons_without_duplication(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store)
    review_root = store.parent / "research-chart-7639402643386830630" / "real-no-submit-reviews"
    review_root.mkdir(parents=True, exist_ok=True)
    (review_root / "live-nested.json").write_text(
        json.dumps(
                {
                    "ok": True,
                    "draft_id": "draft-1",
                    "prompt": {"fingerprint": _ability_prompt_fingerprint()},
                    "saved_to_task_ui": True,
                "submits_remote": False,
                "review_status": "待人工审核",
                "question_context": {"item_id": "item-1"},
                "ai_decision": {
                    "rubric_items": [{"rubric_id": "R1", "verdict": "满足", "reason": "主体结构与参考一致。"}],
                    "dimension_scores": {"S1": {"score": 4, "reason": "基础形体整体匹配。"}},
                    "discard": {"selected": False, "reason": "未发现空图或错图。"},
                },
                "created_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    gate = get_task_ability_run_gate("7639402643386830630", store_path=store)

    assert gate["step3_review_status"] == "allow"
    assert gate["step3_reason_count"] == 3


def test_replay_task_ability_testset_builds_compare_rows(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    testset_dir = store.parent / "7639402643386830630" / "testsets"
    sample_dir = store.parent / "7639402643386830630" / "submitted-history" / "samples"
    testset_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)
    (testset_dir / "current.json").write_text(
        json.dumps(
            {
                "task_id": "7639402643386830630",
                "task_name": "科研图",
                "sample_pool_count": 2,
                "testset_id": "testset-1",
                "sample_count": 1,
                "sample_ids": ["uid-1"],
                "source": "submitted_history",
                "created_at": "2026-05-16T00:00:00+00:00",
                "path": str(testset_dir / "current.json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sample_dir / "uid-1.json").write_text(
        json.dumps(
            {
                "uid": "uid-1",
                "item_id": "item-1",
                "task_id": "7639402643386830630",
                "task_name": "科研图",
                "account_id": "account-sample-002",
                "source": "submitted_history_http",
                "submitted_nodes": [],
                "primary_output": {
                    "item": {"uid": "uid-1", "image_gt": "https://example.com/gt.png", "model_image": "https://example.com/model.png"},
                    "data": {
                        "label_sorce": {"model_image": "2"},
                        "label_remark": {"model_image": "完全一致"},
                        "discard": "No",
                    },
                },
                "raw": {},
                "synced_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.task_ability_service._build_task_ai_decision_for_research_chart",
        lambda context, draft, require_provider, prefer_system_ai=False: {"score": "2", "reason": "完全一致", "confidence": "high"},
    )

    result = replay_task_ability_testset("7639402643386830630", store_path=store)

    assert result["sample_count"] == 1
    assert result["items"][0]["uid"] == "uid-1"
    assert result["items"][0]["compare_status"] == "matched"
    assert result["items"][0]["difference_count"] == 0
    assert result["cards"][0]["item_id"] == "item-1"
    assert result["cards"][0]["images"]["original"]["available"] is True
    assert result["cards"][0]["images"]["ai"]["available"] is True
    assert result["cards"][0]["score"] == "2"
    assert "完全一致" in result["cards"][0]["reason"]


def test_payload_debug_builds_payload_for_selected_sample(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    testset_dir = store.parent / "7639402643386830630" / "testsets"
    sample_dir = store.parent / "7639402643386830630" / "submitted-history" / "samples"
    testset_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)
    (testset_dir / "current.json").write_text(
        json.dumps(
            {
                "task_id": "7639402643386830630",
                "task_name": "科研图",
                "sample_pool_count": 2,
                "testset_id": "testset-1",
                "sample_count": 1,
                "sample_ids": ["uid-1"],
                "source": "submitted_history",
                "created_at": "2026-05-16T00:00:00+00:00",
                "path": str(testset_dir / "current.json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sample_dir / "uid-1.json").write_text(
        json.dumps(
            {
                "uid": "uid-1",
                "item_id": "item-1",
                "task_id": "7639402643386830630",
                "task_name": "科研图",
                "account_id": "account-sample-002",
                "source": "submitted_history_http",
                "submitted_nodes": [],
                "primary_output": {
                    "item": {"uid": "uid-1", "image_gt": "https://example.com/gt.png", "model_image": "https://example.com/model.png"},
                    "data": {
                        "label_sorce": {"model_image": "2"},
                        "label_remark": {"model_image": "完全一致"},
                        "discard": "No",
                    },
                },
                "raw": {},
                "synced_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.task_ability_service._build_task_ai_decision_for_research_chart",
        lambda context, draft, require_provider, prefer_system_ai=False: {"score": "1", "reason": "有轻微差异", "confidence": "medium"},
    )

    result = build_task_ability_payload_debug("7639402643386830630", "uid-1", store_path=store)

    assert result["uid"] == "uid-1"
    assert result["error_message"] == ""
    assert result["source_context"]["task_id"] == "7639402643386830630"
    assert result["payload_preview"]["TaskID"] == "7639402643386830630"
    assert result["generated_answer_preview"]["data.label_sorce.model_image"] == "1"


def test_chat_task_ability_wraps_task_context(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    testset_dir = store.parent / "7639402643386830630" / "testsets"
    sample_dir = store.parent / "7639402643386830630" / "submitted-history" / "samples"
    testset_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)
    (testset_dir / "current.json").write_text(
        json.dumps(
            {
                "task_id": "7639402643386830630",
                "task_name": "科研图",
                "sample_pool_count": 2,
                "testset_id": "testset-1",
                "sample_count": 1,
                "sample_ids": ["uid-1"],
                "source": "submitted_history",
                "created_at": "2026-05-16T00:00:00+00:00",
                "path": str(testset_dir / "current.json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sample_dir / "uid-1.json").write_text(
        json.dumps(
            {
                "uid": "uid-1",
                "item_id": "item-1",
                "task_id": "7639402643386830630",
                "task_name": "科研图",
                "account_id": "account-sample-002",
                "source": "submitted_history_http",
                "submitted_nodes": [],
                "primary_output": {"item": {"uid": "uid-1"}, "data": {"label_sorce": {"model_image": "2"}}},
                "raw": {},
                "synced_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = chat_task_ability(None, "7639402643386830630", {"message": "请优化提示词", "history": [], "use_provider": False}, store_path=store)

    assert result["provider_status"] == "local_workspace_fallback"
    assert "固定测试集" in result["answer"]


def test_chat_task_ability_accepts_learning_package_alias_and_falls_back_on_provider_error(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    _write_learning_package_index(store, package_id="rec-1")

    monkeypatch.setattr(
        "app.services.task_ability_service.get_system_ai_runtime_prompt",
        lambda: {
            "provider_configured": True,
            "base_url": "https://system-ai.example/v1",
            "api_key": "system-key",
            "model": "gpt-test",
            "timeout_seconds": 5,
        },
    )

    def explode_provider(runtime: dict, prompt: str, history: list[dict]) -> str:
        raise TaskAbilityFlowError("provider returned HTTP 400: invalid messages")

    monkeypatch.setattr("app.services.task_ability_service._call_task_workspace_chat_provider", explode_provider)

    result = chat_task_ability(
        None,
        "7639402643386830630",
        {"message": "理由太笼统，请根据回放结果优化 Prompt", "learning_package_id": "rec-1", "history": [], "use_provider": True},
        store_path=store,
    )

    assert result["provider_status"] == "provider_error_fallback"
    assert result["context_summary"]["selected_learning_package_id"] == "rec-1"
    assert "provider returned HTTP 400" in result["answer"]
    assert "关键接口" in result["context_summary"]["learning_package_summary"]


def test_chat_task_ability_rejects_foreign_explicit_learning_package(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    _write_learning_package_index(store, package_id="rec-1")

    try:
        chat_task_ability(
            None,
            "7639402643386830630",
            {"message": "请优化", "selected_learning_package_id": "rec-other", "history": [], "use_provider": False},
            store_path=store,
        )
    except TaskAbilityFlowError as exc:
        assert "不属于当前任务" in str(exc)
    else:
        raise AssertionError("expected explicit foreign learning package to be rejected")


def test_replay_report_saves_latest_summary_and_chat_marks_stale_after_prompt_edit(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    _write_learning_package_index(store, package_id="rec-1")
    _write_single_sample_testset(store)
    monkeypatch.setattr(
        "app.services.task_ability_service._build_task_ai_decision_for_research_chart",
        lambda context, draft, require_provider, prefer_system_ai=False: {"score": "1", "reason": "理由偏短", "confidence": "medium"},
    )

    report = create_task_ability_replay_report("7639402643386830630", store_path=store)

    latest_path = store.parent / "7639402643386830630" / "latest-replay-summary.json"
    assert latest_path.exists()
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    assert latest["replay_id"] == report["report_id"]
    assert latest["selected_learning_package_id"] == "rec-1"
    assert latest["items"][0]["item_id"] == "item-1"
    assert latest["items"][0]["score"] == "1"
    assert latest["items"][0]["reason"] == "理由偏短"

    update_task_ability_draft("draft-1", {"system_ai_draft": "草稿已被用户修改"}, store_path=store)

    chat = chat_task_ability(
        None,
        "7639402643386830630",
        {"message": "请根据最新回放结果优化 Prompt", "history": [], "use_provider": False},
        store_path=store,
    )

    replay_summary = chat["context_summary"]["latest_replay_summary"]
    assert replay_summary["is_stale_for_current_prompt"] is True
    assert replay_summary["items"][0]["reason"] == "理由偏短"


def test_research_chart_task_ai_messages_keep_prompt_item_input_and_output_schema_separate() -> None:
    messages = _build_research_chart_ai_messages(
        {
            "task_id": "7639402643386830630",
            "item_id": "item-1",
            "uid": "uid-1",
            "task_text": "请评价科研图",
            "image_gt": "https://example.com/ref.png",
            "model_image": "https://example.com/ai.png",
            "web_url": "https://aidp.example/item-1",
            "extra_context": {"scene": "replay"},
        },
        {
            "task_id": "7639402643386830630",
            "task_name": "RFT科研图表还原-正式(全量数据)",
            "system_ai_draft": "评分理由必须具体指出差异",
        },
        {"pre_prompt": "系统稳定规则", "skills": []},
    )

    user_content = messages[-1]["content"][0]["text"]
    assert "prompt_template" in user_content
    assert "current_item_input" in user_content
    assert "output_schema" in user_content
    assert "original_image" in user_content
    assert "ai_image" in user_content
    assert "web_url" in user_content
    assert "extra_context" in user_content
    assert "下面的 JSON 是输入上下文，不是输出模板" in user_content
    assert "禁止复述 prompt_template、current_item_input 或 output_schema" in user_content
    assert "你不要输出任何 data.*、discard、checkRemark 或表单字段" in user_content
    assert "只输出 JSON：" not in user_content


def test_research_chart_task_ai_messages_leave_remote_image_urls_unfetched() -> None:
    with patch("app.services.task_ability_service.requests.get", side_effect=AssertionError("message construction must not download images")) as get:
        messages = _build_research_chart_ai_messages(
            {
                "task_id": "7639402643386830630",
                "item_id": "item-1",
                "uid": "uid-1",
                "task_text": "请评价科研图",
                "image_gt": "https://example.com/ref.png",
                "model_image": "https://example.com/ai.png",
            },
            {
                "task_id": "7639402643386830630",
                "task_name": "RFT科研图表还原-正式(全量数据)",
                "system_ai_draft": "评分理由必须具体指出差异",
            },
            {"pre_prompt": "系统稳定规则", "skills": []},
        )

    image_urls = [part["image_url"]["url"] for part in messages[-1]["content"] if part.get("type") == "image_url"]
    assert image_urls == ["https://example.com/ref.png", "https://example.com/ai.png"]
    assert get.call_count == 0


def test_research_chart_task_ai_messages_keep_full_prompt_in_three_layer_input_only() -> None:
    messages = _build_research_chart_ai_messages(
        {
            "task_id": "7639402643386830630",
            "item_id": "item-1",
            "uid": "uid-1",
            "task_text": "请评价科研图",
            "image_gt": "https://example.com/ref.png",
            "model_image": "https://example.com/ai.png",
        },
        {
            "task_id": "7639402643386830630",
            "task_name": "RFT科研图表还原-正式(全量数据)",
            "system_ai_draft": "完整 Prompt 草稿：理由必须指出原图和 AI 图在标题、坐标轴、曲线趋势上的具体差异，禁止只写没对上或可以。",
            "specific_rules": "若 0 分，至少写两个关键差异点。",
        },
        {"pre_prompt": "系统稳定规则", "skills": []},
    )

    system_content = messages[0]["content"]
    user_content = messages[-1]["content"][0]["text"]
    assert "最高优先级" not in system_content
    assert "完整 Prompt 草稿：理由必须指出原图和 AI 图在标题、坐标轴、曲线趋势上的具体差异" not in system_content
    assert "若 0 分，至少写两个关键差异点。" not in system_content
    assert "prompt_template" in user_content
    assert "完整 Prompt 草稿：理由必须指出原图和 AI 图在标题、坐标轴、曲线趋势上的具体差异" in user_content
    assert "若 0 分，至少写两个关键差异点。" not in user_content


def test_research_chart_task_ai_messages_do_not_send_sidecar_rule_notes_as_prompt() -> None:
    messages = _build_research_chart_ai_messages(
        {
            "task_id": "7639402643386830630",
            "item_id": "item-1",
            "uid": "uid-1",
            "task_text": "请评价科研图",
            "image_gt": "https://example.com/ref.png",
            "model_image": "https://example.com/ai.png",
        },
        {
            "task_id": "7639402643386830630",
            "task_name": "RFT科研图表还原-正式(全量数据)",
            "system_ai_draft": "完整 Prompt 草稿：必须具体比较原图和 AI 图的文字、坐标轴、曲线趋势、图例和布局差异。",
            "specific_rules": "AI建议：0 分优先使用没对上，1 分优先使用差一点，2 分优先使用可以。",
            "sample_data": "AI补充样例建议：理由写短词。",
            "related_content": "AI对话建议：不要解释原因。",
        },
        {"pre_prompt": "", "skills": []},
    )

    user_content = messages[-1]["content"][0]["text"]
    assert "完整 Prompt 草稿：必须具体比较原图和 AI 图" in user_content
    assert "specific_rules" not in user_content
    assert "sample_data" not in user_content
    assert "related_content" not in user_content
    assert "没对上" not in user_content
    assert "差一点" not in user_content
    assert "可以" not in user_content


def test_research_chart_task_ai_retry_messages_use_plain_three_layer_input() -> None:
    messages = _build_research_chart_ai_messages(
        {
            "task_id": "7639402643386830630",
            "item_id": "item-1",
            "uid": "uid-1",
            "task_text": "请评价科研图",
            "image_gt": "https://example.com/ref.png",
            "model_image": "https://example.com/ai.png",
        },
        {
            "task_id": "7639402643386830630",
            "task_name": "RFT科研图表还原-正式(全量数据)",
            "version": "ability-v1",
            "system_ai_draft": "完整 Prompt 草稿：必须具体比较两张图。",
        },
        {"pre_prompt": "", "skills": []},
        format_retry=True,
    )

    user_content = messages[-1]["content"][0]["text"]
    assert "这是格式纠错重试" in user_content
    assert "【第一层：完整 Prompt 草稿】" in user_content
    assert "【第二层：当前题目信息】" in user_content
    assert "【第三层：输出规则】" in user_content
    assert "prompt_template" not in user_content
    assert "current_item_input" not in user_content
    assert "output_schema" not in user_content


def test_parse_research_chart_ai_decision_rejects_unexplained_short_reasons() -> None:
    try:
        _parse_research_chart_ai_decision(json.dumps({"score": "0", "reason": "没对上", "confidence": "high"}, ensure_ascii=False))
    except TaskAbilityFlowError as exc:
        assert "无依据短理由" in str(exc)
    else:
        raise AssertionError("expected unexplained short reason to be rejected")


def test_parse_research_chart_ai_decision_rejects_echoed_input_context() -> None:
    echoed = json.dumps(
        {
            "prompt_template": {"rules": "完整 Prompt 草稿"},
            "current_item_input": {"item_id": "item-1"},
            "output_schema": {"score": "字符串 0/1/2"},
        },
        ensure_ascii=False,
    )

    try:
        _parse_research_chart_ai_decision(echoed)
    except TaskAbilityFlowError as exc:
        assert "复述了输入上下文" in str(exc)
    else:
        raise AssertionError("expected echoed input context to be rejected")


def test_parse_research_chart_ai_decision_repairs_missing_outer_brace() -> None:
    content = """
{
  "score": "2",
  "reason": "两图标题、坐标轴、曲线趋势和主要点位均一致，未发现影响理解的差异。",
  "confidence": "high",
  "visual_findings": ["标题一致", "曲线趋势一致"]


"""

    decision = _parse_research_chart_ai_decision(content)

    assert decision["score"] == "2"
    assert "曲线趋势" in decision["reason"]
    assert decision["visual_findings"] == ["标题一致", "曲线趋势一致"]


def test_parse_research_chart_ai_decision_extracts_required_fields_from_truncated_extra_fields() -> None:
    content = """
{
  "score": "2",
  "reason": "两图标题、坐标轴、曲线趋势和主要点位均一致，未发现影响理解的差异。",
  "confidence": "high",
  "visual_findings": ["标题一致", "曲线趋势一致"],
  "data.label_sorce.model_image": "2",
  "data.label_remark.model_image": "两图标题、坐标
"""

    decision = _parse_research_chart_ai_decision(content)

    assert decision["score"] == "2"
    assert "主要点位" in decision["reason"]
    assert decision["confidence"] == "high"
    assert decision["visual_findings"] == ["标题一致", "曲线趋势一致"]


def test_research_chart_task_ai_messages_keep_system_wrapper_minimal_and_do_not_inject_runtime_layers() -> None:
    messages = _build_research_chart_ai_messages(
        {
            "task_id": "7639402643386830630",
            "item_id": "item-1",
            "uid": "uid-1",
            "task_text": "请评价科研图",
            "image_gt": "https://example.com/ref.png",
            "model_image": "https://example.com/ai.png",
        },
        {
            "task_id": "7639402643386830630",
            "task_name": "RFT科研图表还原-正式(全量数据)",
            "system_ai_draft": "完整 Prompt 草稿：必须指出原图和 AI 图的具体差异，禁止只写没对上或差一点。",
            "specific_rules": "若 0 分，至少写两个关键差异点。",
        },
        {"pre_prompt": "只输出返修评分 JSON。", "skills": ["rft-score"]},
    )

    system_content = messages[0]["content"]
    user_content = messages[-1]["content"][0]["text"]
    assert "系统 AI 注入的做题前置提示词" not in system_content
    assert "可用 skills" not in system_content
    assert "完整 Prompt 草稿为最高优先级" not in system_content
    assert "完整 Prompt 草稿：必须指出原图和 AI 图的具体差异" in user_content
    assert "若 0 分，至少写两个关键差异点" not in user_content


def test_learning_package_summary_includes_parse_failure_reason(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    package_dir = store.parent / "7639402643386830630" / "learning-packages"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "index.json").write_text(
        json.dumps(
            {
                "task_id": "7639402643386830630",
                "selected_learning_package_id": "rec-parse-failed",
                "items": [
                    {
                        "learning_package_id": "rec-parse-failed",
                        "recording_id": "rec-parse-failed",
                        "task_id": "7639402643386830630",
                        "task_name": "科研图",
                        "display_name": "失败录制包",
                        "source": "browser_extension",
                        "uploaded_at": "2026-05-21T02:00:00+08:00",
                        "status": "parse_failed",
                        "completeness": "partial",
                        "parse_failure_reason": "无法提取暂存接口字段",
                        "artifact_path": str(package_dir / "missing.json"),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = get_selected_learning_package_summary("7639402643386830630", "rec-parse-failed", root_dir=store.parent)

    assert "解析失败原因：无法提取暂存接口字段" in summary.summary_text


def test_replay_task_ability_testset_reports_field_level_differences(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    testset_dir = store.parent / "7639402643386830630" / "testsets"
    sample_dir = store.parent / "7639402643386830630" / "submitted-history" / "samples"
    testset_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)
    (testset_dir / "current.json").write_text(
        json.dumps(
            {
                "task_id": "7639402643386830630",
                "task_name": "科研图",
                "sample_pool_count": 2,
                "testset_id": "testset-1",
                "sample_count": 1,
                "sample_ids": ["uid-1"],
                "source": "submitted_history",
                "created_at": "2026-05-16T00:00:00+00:00",
                "path": str(testset_dir / "current.json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sample_dir / "uid-1.json").write_text(
        json.dumps(
            {
                "uid": "uid-1",
                "item_id": "item-1",
                "task_id": "7639402643386830630",
                "task_name": "科研图",
                "account_id": "account-sample-002",
                "source": "submitted_history_http",
                "submitted_nodes": [],
                "primary_output": {
                    "item": {"uid": "uid-1", "image_gt": "https://example.com/gt.png", "model_image": "https://example.com/model.png"},
                    "data": {"label_sorce": {"model_image": "2"}, "label_remark": {"model_image": "完全一致"}, "discard": "No"},
                },
                "raw": {},
                "synced_at": "2026-05-16T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.task_ability_service._build_task_ai_decision_for_research_chart",
        lambda context, draft, require_provider, prefer_system_ai=False: {"score": "1", "reason": "存在轻微差异", "confidence": "medium"},
    )

    result = replay_task_ability_testset("7639402643386830630", store_path=store)

    assert result["items"][0]["compare_status"] == "different"
    assert result["items"][0]["difference_count"] >= 1
    assert "data.label_sorce.model_image" in result["items"][0]["difference_fields"]
    assert result["items"][0]["difference_summary"]


def test_task_run_config_can_be_saved_and_read_back(tmp_path: Path) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)

    saved = update_task_ability_run_config(
        "7639402643386830630",
        {
            "mode": "normal",
            "rate_limit_per_minute": 7,
            "trial_max_items_per_account": 4,
            "production_max_items_per_account": 60,
            "consecutive_fail_threshold": 5,
        },
        store_path=store,
    )
    loaded = get_task_ability_run_config("7639402643386830630", store_path=store)

    assert saved["mode"] == "normal"
    assert loaded["rate_limit_per_minute"] == 7
    assert loaded["production_max_items_per_account"] == 60


def test_replay_task_ability_testset_keeps_report_when_one_sample_fails(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    testset_dir = store.parent / "7639402643386830630" / "testsets"
    sample_dir = store.parent / "7639402643386830630" / "submitted-history" / "samples"
    testset_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)
    (testset_dir / "current.json").write_text(
        json.dumps(
            {
                "task_id": "7639402643386830630",
                "task_name": "科研图",
                "sample_pool_count": 2,
                "testset_id": "testset-1",
                "sample_count": 2,
                "sample_ids": ["uid-1", "uid-2"],
                "source": "submitted_history",
                "created_at": "2026-05-16T00:00:00+00:00",
                "path": str(testset_dir / "current.json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for uid in ("uid-1", "uid-2"):
        (sample_dir / f"{uid}.json").write_text(
            json.dumps(
                {
                    "uid": uid,
                    "item_id": uid,
                    "task_id": "7639402643386830630",
                    "task_name": "科研图",
                    "account_id": "account-sample-002",
                    "source": "submitted_history_http",
                    "submitted_nodes": [],
                    "primary_output": {
                        "item": {"uid": uid, "image_gt": "https://example.com/gt.png", "model_image": "https://example.com/model.png"},
                        "data": {"label_sorce": {"model_image": "2"}, "label_remark": {"model_image": "完全一致"}, "discard": "No"},
                    },
                    "raw": {},
                    "synced_at": "2026-05-16T00:00:00+00:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def fake_decision(context, draft, require_provider, prefer_system_ai=False):
        if context["uid"] == "uid-2":
            raise ValueError("sample replay exploded")
        return {"score": "2", "reason": "完全一致", "confidence": "high"}

    monkeypatch.setattr("app.services.task_ability_service._build_task_ai_decision_for_research_chart", fake_decision)

    result = replay_task_ability_testset("7639402643386830630", store_path=store)

    assert result["sample_count"] == 2
    assert result["items"][0]["compare_status"] == "matched"
    assert result["items"][1]["compare_status"] == "error"
    assert "sample replay exploded" in result["items"][1]["error_message"]


def test_replay_task_ability_testset_runs_samples_in_parallel(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "task-abilities" / "ability-drafts.json"
    _write_store(store, capability_enabled=True)
    testset_dir = store.parent / "7639402643386830630" / "testsets"
    sample_dir = store.parent / "7639402643386830630" / "submitted-history" / "samples"
    testset_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_ids = ["uid-1", "uid-2", "uid-3"]
    (testset_dir / "current.json").write_text(
        json.dumps(
            {
                "task_id": "7639402643386830630",
                "task_name": "科研图",
                "sample_pool_count": 3,
                "testset_id": "testset-1",
                "sample_count": 3,
                "sample_ids": sample_ids,
                "source": "submitted_history",
                "created_at": "2026-05-16T00:00:00+00:00",
                "path": str(testset_dir / "current.json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for uid in sample_ids:
        (sample_dir / f"{uid}.json").write_text(
            json.dumps(
                {
                    "uid": uid,
                    "item_id": uid,
                    "task_id": "7639402643386830630",
                    "task_name": "科研图",
                    "account_id": "account-sample-002",
                    "source": "submitted_history_http",
                    "submitted_nodes": [],
                    "primary_output": {
                        "item": {"uid": uid, "image_gt": "https://example.com/gt.png", "model_image": "https://example.com/model.png"},
                        "data": {"label_sorce": {"model_image": "2"}, "label_remark": {"model_image": "完全一致"}, "discard": "No"},
                    },
                    "raw": {},
                    "synced_at": "2026-05-16T00:00:00+00:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def slow_decision(_context, _draft, require_provider, prefer_system_ai=False):
        del prefer_system_ai, require_provider
        import time
        time.sleep(0.2)
        return {"score": "2", "reason": "完全一致", "confidence": "high"}

    monkeypatch.setattr("app.services.task_ability_service._build_task_ai_decision_for_research_chart", slow_decision)

    started = perf_counter()
    result = replay_task_ability_testset("7639402643386830630", store_path=store)
    elapsed = perf_counter() - started

    assert result["sample_count"] == 3
    assert elapsed < 0.5
