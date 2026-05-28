import json
from time import perf_counter
from pathlib import Path

from app.services.task_ability_service import (
    TaskAbilityFlowError,
    build_task_ability_payload_debug,
    chat_task_ability,
    create_task_ability_replay_report,
    get_latest_task_ability_live_http_test_report,
    get_task_ability_run_gate,
    get_task_ability_run_config,
    list_prompt_snapshots,
    replay_task_ability_testset,
    record_task_ability_run,
    update_task_ability_draft,
    update_task_ability_run_config,
    _build_research_chart_ai_messages,
    _parse_research_chart_ai_decision,
)
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

    assert gated["can_start_production"] is True
    assert gated["last_trial_run"]["run_id"] == "task-auto-trial-1"

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
