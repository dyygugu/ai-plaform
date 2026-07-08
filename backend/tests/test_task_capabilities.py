import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _write_recording(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    content = {
        "item": {
            "html": "https://example.test/item.html",
            "image": "https://example.test/final.png",
            "mediaUrls": ["https://example.test/before.mp4", "https://example.test/after.mp4"],
            "imageScoreGuide": "截图是否美观，完美符合 2 分，普通 1 分，不相干 0 分。",
            "videoGuideline": "网站是否美观，有明确动效或操作。",
        },
        "templateID": "7634454580450266889",
        "type": "neeko",
        "data": {
            "discard": "No",
            "discard_type": [],
            "discard_remark": "RECORDED",
            "beauty_score": "2",
            "motion_richness_score": "1",
            "richness_reason": "录制原因",
            "sceneConsistencyScore": {"product1": "2", "product2": "1"},
            "sceneConsistencyRemarks": {"product1": "原因1", "product2": "原因2"},
            "checkRemark": "录制检查备注",
            "screen_record": {},
            "high_richness_reason": ["有明显视觉动效"],
            "__internalData__": {},
        },
        "dataMap": {
            "discard": "No",
            "discard_type": [],
            "discard_remark": "RECORDED",
            "beauty_score": "2",
            "motion_richness_score": "1",
            "richness_reason": "录制原因",
            "sceneConsistencyScore": {"product1": "2", "product2": "1"},
            "sceneConsistencyRemarks": {"product1": "原因1", "product2": "原因2"},
            "checkRemark": "录制检查备注",
            "screen_record": {},
            "high_richness_reason": ["有明显视觉动效"],
            "__internalData__": {},
        },
        "itemID": "7634878124416814874",
        "isAbandoned": False,
    }
    payload = {
        "AuditAnswers": [
            {
                "ItemID": "7634878124416814874",
                "Content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                "ControlData": json.dumps({"Discard": False, "extraAnswer": []}, ensure_ascii=False),
            }
        ],
        "NodeID": "1",
        "StagingTime": "604800",
        "TaskID": "7634537456234385161",
    }
    document = {
        "recording_id": "opr-test-capability",
        "purpose": "operation-learning-http-replay",
        "sanitized": True,
        "recording": {
            "account_user_id": "",
            "task_id": "7634537456234385161",
            "page_url": "https://aidp.juejin.cn/operation/task-v2/7634537456234385161/mark-v3/1",
            "events": [],
            "network": [
                {
                    "url": "https://aidp.juejin.cn/api/dispatch/SubmitTempItemAnswer",
                    "request_headers": {"Referer": "https://aidp.juejin.cn/operation/task-v2/7634537456234385161/mark-v3/1"},
                    "request_body": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    "response_body": json.dumps({"BaseResp": {"StatusCode": 0}}, ensure_ascii=False),
                },
                {
                    "url": "https://mon.zijieapi.com/monitor_browser/collect/batch/?biz_id=ai_data_platform",
                    "request_headers": {"Referer": "https://aidp.juejin.cn/operation/task-v2/7634537456234385161/mark-v3/1"},
                    "request_body": json.dumps({"common": {"user_id": "account-sample-002"}}, ensure_ascii=False, separators=(",", ":")),
                    "response_body": json.dumps({"ok": True}, ensure_ascii=False),
                }
            ],
        },
    }
    (root / "opr-test-capability.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def _create_app_with_reloaded_runtime():
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    for module_name in list(sys.modules):
        if module_name in {"app.main", "app.db.init_db", "app.db.session"} or module_name == "app.api.v1" or module_name.startswith("app.api.v1."):
            sys.modules.pop(module_name, None)
    main_module = importlib.import_module("app.main")
    return main_module.create_app()


class TaskCapabilityTests(unittest.TestCase):
    def test_catalog_marks_capability_only_for_matching_recording(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            tmp = Path(tmpdir)
            os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{tmp / 'aidp-test.db'}"
            os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(tmp / "operation-recordings")
            os.environ["AIDP_PRODUCTION_STATE_PATH"] = str(tmp / "production-state.json")
            os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
            _write_recording(tmp / "operation-recordings")
            app = _create_app_with_reloaded_runtime()

            with TestClient(app) as client:
                matched = client.post(
                    "/api/v1/tasks/catalog/seed",
                    json={
                        "raw_task_name": "RFT人标_返修评分 7634537456234385161",
                        "task_status_raw": "进行中",
                        "pending_raw": "1",
                    },
                )
                self.assertEqual(matched.status_code, 200, matched.text)
                unmatched = client.post(
                    "/api/v1/tasks/catalog/seed",
                    json={
                        "raw_task_name": "RFT人标_无录制任务 7999999999999999999",
                        "task_status_raw": "进行中",
                        "pending_raw": "0",
                    },
                )
                self.assertEqual(unmatched.status_code, 200, unmatched.text)
                catalog = client.get("/api/v1/tasks/catalog")
                self.assertEqual(catalog.status_code, 200, catalog.text)
                by_task_id = {item["task_id"]: item for item in catalog.json()["items"]}
                self.assertTrue(by_task_id["7634537456234385161"]["capability_available"])
                self.assertGreaterEqual(by_task_id["7634537456234385161"]["capability_recording_count"], 1)
                self.assertFalse(by_task_id["7999999999999999999"]["capability_available"])
                self.assertEqual(by_task_id["7999999999999999999"]["capability_recording_count"], 0)

                capability = client.get(f"/api/v1/tasks/catalog/{unmatched.json()['item']['id']}/capability")
                self.assertEqual(capability.status_code, 404, capability.text)

    def test_capability_card_and_draft_dry_run_are_http_only(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            tmp = Path(tmpdir)
            os.environ["AIDP_DATABASE_URL"] = f"sqlite+pysqlite:///{tmp / 'aidp-test.db'}"
            os.environ["AIDP_OPERATION_RECORDING_ROOT"] = str(tmp / "operation-recordings")
            os.environ["AIDP_PRODUCTION_STATE_PATH"] = str(tmp / "production-state.json")
            os.environ["AIDP_AUTO_CREATE_TABLES"] = "true"
            _write_recording(tmp / "operation-recordings")
            (tmp / "production-state.json").write_text(
                json.dumps(
                    {
                        "accounts": [
                            {"user_id": "account-sample-002", "enabled": True, "cookie": "sessionid=target-cookie"},
                            {"user_id": "other-account", "enabled": True, "cookie": "sessionid=other-cookie"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            service_module = importlib.import_module("app.services.task_capability_service")
            learned = service_module._learn_from_recording(tmp / "operation-recordings" / "opr-test-capability.json")
            self.assertEqual(learned["account_user_id"], "account-sample-002")
            app = _create_app_with_reloaded_runtime()

            with TestClient(app) as client:
                seed = client.post(
                    "/api/v1/tasks/catalog/seed",
                    json={
                        "raw_task_name": "RFT人标_返修评分 7634537456234385161",
                        "task_status_raw": "进行中",
                        "pending_raw": "1",
                    },
                )
                self.assertEqual(seed.status_code, 200, seed.text)
                item_id = seed.json()["item"]["id"]

                capability = client.get(f"/api/v1/tasks/catalog/{item_id}/capability")
                self.assertEqual(capability.status_code, 200, capability.text)
                card = capability.json()
                self.assertEqual(card["state"], "http_draft_verified")
                self.assertEqual(card["capability_level"], "HTTP-only")
                self.assertEqual(card["identity"]["TaskID"], "7634537456234385161")
                self.assertIn("SubmitTempItemAnswer", card["endpoint"])
                self.assertIn("beauty_score", {field["field"] for field in card["field_mappings"]})
                self.assertTrue(card["latest_validation"]["success_response_count"] >= 1)
                self.assertIn("beauty_score", {field["field"] for field in card["ai_output_schema"]})
                self.assertIn("motion_richness_score", {field["field"] for field in card["ai_output_schema"]})
                self.assertIn("reason_rules", card)
                self.assertTrue(any(rule["key"] == "beauty_score" for rule in card["scoring_rules"]))
                self.assertTrue(any("网页" in item for item in card["ai_input_materials"]))
                self.assertIn("ai_input_spec", card)
                input_spec = {item["key"]: item for item in card["ai_input_spec"]}
                self.assertTrue(input_spec["web_page"]["required"])
                self.assertEqual(input_spec["final_screenshot"]["material_type"], "image")
                self.assertEqual(input_spec["motion_media"]["material_type"], "video")
                self.assertIn("美观度", input_spec["score_guideline"]["usage"])

                with patch("app.services.task_capability_service.requests.post", side_effect=AssertionError("http-question-context must not call remote endpoints")):
                    question_context = client.get(f"/api/v1/tasks/catalog/{item_id}/capability/http-question-context")
                self.assertEqual(question_context.status_code, 200, question_context.text)
                context_payload = question_context.json()
                self.assertTrue(context_payload["ok"])
                self.assertEqual(context_payload["mode"], "http_question_context")
                self.assertFalse(context_payload["sends_network"])
                self.assertFalse(context_payload["writes_remote"])
                self.assertEqual(context_payload["source_mode"], "recorded_submit_temp_payload")
                self.assertEqual(context_payload["identity"]["TaskID"], "7634537456234385161")
                self.assertEqual(context_payload["identity"]["ItemID"], "7634878124416814874")
                resources = {resource["key"]: resource for resource in context_payload["material_resources"]}
                self.assertEqual(resources["web_page"]["material_type"], "url")
                self.assertEqual(resources["web_page"]["url"], "https://example.test/item.html")
                self.assertEqual(resources["final_screenshot"]["material_type"], "image")
                self.assertEqual(resources["final_screenshot"]["url"], "https://example.test/final.png")
                self.assertEqual(resources["motion_media_1"]["material_type"], "video")
                self.assertEqual(resources["motion_media_2"]["url"], "https://example.test/after.mp4")
                self.assertEqual(context_payload["current_answer_data"]["beauty_score"], "2")
                self.assertIn("SubmitTempItemAnswer", context_payload["payload_identity"]["allowed_save_endpoint"])
                self.assertIn("禁止打开 AIDP 做题 UI", context_payload["guardrails"])
                self.assertIn("sandbox_web_interaction", {step["key"] for step in context_payload["decision_pipeline"]})
                self.assertIn("http-receive-live-fetch", {item["key"] for item in context_payload["iteration_candidates"]})

                live_content = {
                    "item": {
                        "html": "https://example.test/live-item.html",
                        "image": "https://example.test/live-final.png",
                        "mediaUrls": ["https://example.test/live-before.mp4", "https://example.test/live-after.mp4"],
                        "imageScoreGuide": "左图完好且排版正常给2分，乱码乱版给0分。",
                        "videoGuideline": "点击网页元素判断跳转、交互、动效；视频判断是否复现同样操作。",
                    },
                    "templateID": "7634454580450266889",
                    "type": "neeko",
                    "data": {
                        "beauty_score": "0",
                        "motion_richness_score": "1",
                        "richness_reason": "LIVE_RECORDED",
                        "sceneConsistencyScore": {"product1": "0", "product2": "0"},
                    },
                    "dataMap": {
                        "beauty_score": "0",
                        "motion_richness_score": "1",
                        "richness_reason": "LIVE_RECORDED",
                        "sceneConsistencyScore": {"product1": "0", "product2": "0"},
                    },
                    "itemID": "live-item-001",
                }

                class FakeLiveContextResponse:
                    ok = True
                    status_code = 200

                    def raise_for_status(self) -> None:
                        return None

                    def json(self) -> dict[str, object]:
                        return {
                            "BaseResp": {"StatusCode": 0},
                            "AnswerList": [
                                {
                                    "ItemID": "live-item-001",
                                    "Content": json.dumps(live_content, ensure_ascii=False, separators=(",", ":")),
                                }
                            ],
                        }

                live_calls = []

                def fake_live_post(url: str, **kwargs: object) -> FakeLiveContextResponse:
                    live_calls.append({"url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers")})
                    self.assertIn("MGetAnswerList", url)
                    self.assertNotIn("SubmitTempItemAnswer", url)
                    self.assertNotIn("Receive", url)
                    return FakeLiveContextResponse()

                with patch("app.services.task_capability_service.requests.post", side_effect=fake_live_post):
                    live_question_context = client.get(
                        f"/api/v1/tasks/catalog/{item_id}/capability/http-question-context",
                        params={
                            "prefer_live": "true",
                            "allow_remote_fetch": "true",
                            "account_user_id": "account-sample-002",
                        },
                    )
                self.assertEqual(live_question_context.status_code, 200, live_question_context.text)
                self.assertEqual(len(live_calls), 1)
                live_payload = live_question_context.json()
                self.assertTrue(live_payload["ok"])
                self.assertEqual(live_payload["source_mode"], "live_mget_answer_list")
                self.assertTrue(live_payload["sends_network"])
                self.assertFalse(live_payload["writes_remote"])
                self.assertEqual(live_payload["identity"]["ItemID"], "live-item-001")
                live_resources = {resource["key"]: resource for resource in live_payload["material_resources"]}
                self.assertEqual(live_resources["web_page"]["url"], "https://example.test/live-item.html")
                self.assertEqual(live_resources["motion_media_2"]["url"], "https://example.test/live-after.mp4")
                self.assertEqual(live_payload["current_answer_data"]["richness_reason"], "LIVE_RECORDED")
                self.assertIn("MGetAnswerList", live_payload["evidence_path"])
                self.assertIn("禁止调用 Receive/PreReceive", live_payload["guardrails"])
                self.assertIn("readonly-live-mget-answer-list", {step["key"] for step in live_payload["decision_pipeline"]})

                process_plan = client.get(f"/api/v1/tasks/catalog/{item_id}/capability/operation-process-plan")
                self.assertEqual(process_plan.status_code, 200, process_plan.text)
                process_payload = process_plan.json()
                self.assertTrue(process_payload["ok"])
                self.assertEqual(process_payload["mode"], "operation_process_plan")
                self.assertEqual(process_payload["operation_url"], "https://aidp.juejin.cn/operation")
                self.assertTrue(process_payload["claims_task"])
                self.assertTrue(process_payload["writes_remote"])
                self.assertFalse(process_payload["submits_answer"])
                self.assertIn("点击处理后自动分配题目", process_payload["message"])
                self.assertIn("MGetAnswerList", process_payload["post_claim_read_step"])
                self.assertIn("SubmitTempItemAnswer", process_payload["answer_write_step"])
                self.assertIn("点击“处理”会领题并改变账号任务状态", process_payload["guardrails"])
                self.assertIn("operation-click-process", {step["key"] for step in process_payload["steps"]})

                sandbox_plan = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/sandbox-click-plan",
                    json={
                        "html_url": "https://example.test/live-item.html",
                        "html_snapshot": """
                            <main>
                              <a id="docs" href="/docs">Docs</a>
                              <button data-testid="start">Start</button>
                              <div role="button" onclick="openModal()">Open modal</div>
                              <span>plain text</span>
                            </main>
                        """,
                    },
                )
                self.assertEqual(sandbox_plan.status_code, 200, sandbox_plan.text)
                plan_payload = sandbox_plan.json()
                self.assertTrue(plan_payload["ok"])
                self.assertEqual(plan_payload["mode"], "sandbox_click_plan")
                self.assertFalse(plan_payload["sends_network"])
                self.assertFalse(plan_payload["writes_remote"])
                self.assertFalse(plan_payload["executes_clicks"])
                self.assertEqual(plan_payload["html_url"], "https://example.test/live-item.html")
                candidates = {candidate["selector"]: candidate for candidate in plan_payload["click_candidates"]}
                self.assertIn("#docs", candidates)
                self.assertIn("[data-testid='start']", candidates)
                self.assertTrue(any(candidate["reason"] == "role=button" for candidate in plan_payload["click_candidates"]))
                self.assertFalse(any("plain text" in candidate.get("text", "") for candidate in plan_payload["click_candidates"]))
                self.assertIn("只允许加载题目网页 URL，不允许打开 AIDP UI", plan_payload["guardrails"])
                self.assertIn("sandbox-browser-click-execution", {step["key"] for step in plan_payload["next_steps"]})

                blocked_execution = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/sandbox-click-execution",
                    json={
                        "html_url": "https://example.test/live-item.html",
                        "selectors": ["#docs", "[data-testid='start']"],
                    },
                )
                self.assertEqual(blocked_execution.status_code, 200, blocked_execution.text)
                blocked_execution_payload = blocked_execution.json()
                self.assertFalse(blocked_execution_payload["ok"])
                self.assertFalse(blocked_execution_payload["sends_network"])
                self.assertFalse(blocked_execution_payload["writes_remote"])
                self.assertFalse(blocked_execution_payload["executes_clicks"])
                self.assertIn("missing-allow-execute", blocked_execution_payload["blockers"])

                class FakeSandboxExecutionResponse:
                    ok = True
                    status_code = 200

                    def raise_for_status(self) -> None:
                        return None

                    def json(self) -> dict[str, object]:
                        return {
                            "ok": True,
                            "mode": "host_helper_sandbox_click_execution",
                            "htmlUrl": "https://example.test/live-item.html",
                            "allowedDomains": ["example.test"],
                            "results": [
                                {
                                    "selector": "#docs",
                                    "status": "clicked",
                                    "beforeUrl": "https://example.test/live-item.html",
                                    "afterUrl": "https://example.test/docs",
                                    "urlChanged": True,
                                    "domChanged": False,
                                    "popupDetected": False,
                                    "animationDetected": False,
                                    "interactionDetected": True,
                                    "evidence": "点击后 URL 变化。",
                                    "error": "",
                                },
                                {
                                    "selector": "[data-testid='start']",
                                    "status": "clicked",
                                    "beforeUrl": "https://example.test/live-item.html",
                                    "afterUrl": "https://example.test/live-item.html",
                                    "urlChanged": False,
                                    "domChanged": True,
                                    "popupDetected": False,
                                    "animationDetected": True,
                                    "interactionDetected": True,
                                    "evidence": "点击后 DOM 与动画信号变化。",
                                    "error": "",
                                },
                            ],
                            "summary": {"hasNavigation": True, "hasDomInteraction": True, "hasAnimation": True, "clickedCount": 2},
                            "elapsedMs": 1234,
                        }

                sandbox_execution_calls = []

                def fake_sandbox_execution_post(url: str, **kwargs: object) -> FakeSandboxExecutionResponse:
                    sandbox_execution_calls.append({"url": url, "json": kwargs.get("json")})
                    self.assertTrue(url.endswith("/api/sandbox-click-execute"))
                    payload = kwargs.get("json")
                    self.assertIsInstance(payload, dict)
                    self.assertEqual(payload["html_url"], "https://example.test/live-item.html")
                    self.assertEqual(payload["selectors"], ["#docs", "[data-testid='start']"])
                    self.assertEqual(payload["allowed_domains"], ["example.test"])
                    return FakeSandboxExecutionResponse()

                with patch("app.services.task_capability_service.requests.post", side_effect=fake_sandbox_execution_post):
                    sandbox_execution = client.post(
                        f"/api/v1/tasks/catalog/{item_id}/capability/sandbox-click-execution",
                        json={
                            "html_url": "https://example.test/live-item.html",
                            "selectors": ["#docs", "[data-testid='start']"],
                            "allow_execute": True,
                            "max_clicks": 2,
                            "timeout_ms": 3000,
                        },
                    )
                self.assertEqual(sandbox_execution.status_code, 200, sandbox_execution.text)
                self.assertEqual(len(sandbox_execution_calls), 1)
                execution_payload = sandbox_execution.json()
                self.assertTrue(execution_payload["ok"])
                self.assertEqual(execution_payload["mode"], "sandbox_click_execution")
                self.assertTrue(execution_payload["sends_network"])
                self.assertFalse(execution_payload["writes_remote"])
                self.assertTrue(execution_payload["executes_clicks"])
                self.assertEqual(execution_payload["allowed_domains"], ["example.test"])
                self.assertTrue(execution_payload["interaction_summary"]["has_navigation"])
                self.assertTrue(execution_payload["interaction_summary"]["has_dom_interaction"])
                self.assertTrue(execution_payload["interaction_summary"]["has_animation"])
                results_by_selector = {item["selector"]: item for item in execution_payload["click_results"]}
                self.assertTrue(results_by_selector["#docs"]["url_changed"])
                self.assertTrue(results_by_selector["[data-testid='start']"]["animation_detected"])
                self.assertIn("不打开 AIDP UI", "；".join(execution_payload["guardrails"]))

                sandbox_draft = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/sandbox-click-draft",
                    json={
                        "click_results": execution_payload["click_results"],
                        "interaction_summary": execution_payload["interaction_summary"],
                        "remark_marker": "SANDBOX_CLICK_DRY_RUN_TEST",
                    },
                )
                self.assertEqual(sandbox_draft.status_code, 200, sandbox_draft.text)
                sandbox_draft_payload = sandbox_draft.json()
                self.assertTrue(sandbox_draft_payload["ok"])
                self.assertEqual(sandbox_draft_payload["mode"], "sandbox_click_draft_plan")
                self.assertFalse(sandbox_draft_payload["sends_network"])
                self.assertFalse(sandbox_draft_payload["writes_remote"])
                sandbox_decoded = json.loads(sandbox_draft_payload["payload_preview"]["AuditAnswers"][0]["Content"])
                self.assertEqual(sandbox_decoded["data"]["motion_richness_score"], "2")
                self.assertIn("跳转", sandbox_decoded["data"]["richness_reason"])
                self.assertIn("交互", sandbox_decoded["data"]["richness_reason"])
                self.assertIn("动效", sandbox_decoded["data"]["richness_reason"])
                self.assertIn("有明显的交互跳转", sandbox_decoded["data"]["high_richness_reason"])
                self.assertIn("有明显视觉动效", sandbox_decoded["data"]["high_richness_reason"])
                self.assertEqual(sandbox_decoded["data"]["discard_remark"], "SANDBOX_CLICK_DRY_RUN_TEST")

                media_plan = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/media-inspection-plan",
                    json={
                        "image_url": "https://example.test/live-final.png",
                        "video_urls": ["https://example.test/live-before.mp4", "https://example.test/live-after.mp4"],
                    },
                )
                self.assertEqual(media_plan.status_code, 200, media_plan.text)
                media_payload = media_plan.json()
                self.assertTrue(media_payload["ok"])
                self.assertEqual(media_payload["mode"], "media_inspection_plan")
                self.assertFalse(media_payload["sends_network"])
                self.assertFalse(media_payload["writes_remote"])
                self.assertFalse(media_payload["claims_visual_judgement"])
                resources_by_key = {resource["key"]: resource for resource in media_payload["media_resources"]}
                self.assertEqual(resources_by_key["final_screenshot"]["material_type"], "image")
                self.assertEqual(resources_by_key["motion_media_2"]["url"], "https://example.test/live-after.mp4")
                self.assertIn("multimodal-image-layout-check", {step["key"] for step in media_payload["inspection_steps"]})
                self.assertIn("multimodal-video-action-match", {step["key"] for step in media_payload["inspection_steps"]})
                self.assertIn("无多模态执行结果前不能声明图片/视频已判分", media_payload["guardrails"])

                blocked_media_probe = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/media-inspection-execution",
                    json={
                        "media_resources": media_payload["media_resources"],
                    },
                )
                self.assertEqual(blocked_media_probe.status_code, 200, blocked_media_probe.text)
                blocked_media_payload = blocked_media_probe.json()
                self.assertFalse(blocked_media_payload["ok"])
                self.assertFalse(blocked_media_payload["sends_network"])
                self.assertFalse(blocked_media_payload["writes_remote"])
                self.assertFalse(blocked_media_payload["claims_visual_judgement"])
                self.assertIn("missing-allow-remote-probe", blocked_media_payload["blockers"])

                class FakeMediaProbeResponse:
                    def __init__(self, url: str) -> None:
                        self.url = url
                        self.ok = True
                        self.status_code = 200
                        self.headers = {
                            "content-type": "image/png" if url.endswith(".png") else "video/mp4",
                            "content-length": "67" if url.endswith(".png") else "4096",
                        }
                        self.content = (
                            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x03\x08\x02\x00\x00\x00"
                            if url.endswith(".png")
                            else b"\x00\x00\x00\x18ftypmp42"
                        )

                    def raise_for_status(self) -> None:
                        return None

                media_probe_calls = []

                def fake_media_get(url: str, **kwargs: object) -> FakeMediaProbeResponse:
                    media_probe_calls.append({"url": url, "headers": kwargs.get("headers")})
                    return FakeMediaProbeResponse(url)

                with patch("app.services.task_capability_service.requests.get", side_effect=fake_media_get):
                    media_probe = client.post(
                        f"/api/v1/tasks/catalog/{item_id}/capability/media-inspection-execution",
                        json={
                            "media_resources": media_payload["media_resources"],
                            "allow_remote_probe": True,
                            "max_bytes": 8192,
                        },
                    )
                self.assertEqual(media_probe.status_code, 200, media_probe.text)
                self.assertEqual(len(media_probe_calls), 3)
                media_probe_payload = media_probe.json()
                self.assertTrue(media_probe_payload["ok"])
                self.assertEqual(media_probe_payload["mode"], "media_inspection_execution")
                self.assertTrue(media_probe_payload["sends_network"])
                self.assertFalse(media_probe_payload["writes_remote"])
                self.assertFalse(media_probe_payload["claims_visual_judgement"])
                probe_by_key = {item["key"]: item for item in media_probe_payload["probe_results"]}
                self.assertEqual(probe_by_key["final_screenshot"]["status_code"], 200)
                self.assertEqual(probe_by_key["final_screenshot"]["content_type"], "image/png")
                self.assertEqual(probe_by_key["final_screenshot"]["width"], 2)
                self.assertEqual(probe_by_key["final_screenshot"]["height"], 3)
                self.assertEqual(probe_by_key["motion_media_1"]["content_type"], "video/mp4")
                self.assertIn("multimodal-still-required", media_probe_payload["blockers"])

                blocked_keyframes = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/media-keyframe-extraction",
                    json={"media_resources": media_payload["media_resources"]},
                )
                self.assertEqual(blocked_keyframes.status_code, 200, blocked_keyframes.text)
                blocked_keyframes_payload = blocked_keyframes.json()
                self.assertFalse(blocked_keyframes_payload["ok"])
                self.assertFalse(blocked_keyframes_payload["sends_network"])
                self.assertFalse(blocked_keyframes_payload["writes_remote"])
                self.assertIn("missing-allow-extract", blocked_keyframes_payload["blockers"])

                fake_helper_keyframes = {
                    "ok": True,
                    "mode": "host_helper_video_keyframe_extract",
                    "results": [
                        {
                            "resourceKey": "motion_media_1",
                            "url": "https://example.test/live-before.mp4",
                            "status": "ok",
                            "keyframes": [
                                {
                                    "index": 0,
                                    "timestampSec": 0.5,
                                    "dataUrl": "data:image/jpeg;base64,AAA111",
                                    "width": 960,
                                    "height": 540,
                                    "mimeType": "image/jpeg",
                                },
                                {
                                    "index": 1,
                                    "timestampSec": 1.5,
                                    "dataUrl": "data:image/jpeg;base64,AAA222",
                                    "width": 960,
                                    "height": 540,
                                    "mimeType": "image/jpeg",
                                },
                                {
                                    "index": 2,
                                    "timestampSec": 2.5,
                                    "dataUrl": "data:image/jpeg;base64,AAA333",
                                    "width": 960,
                                    "height": 540,
                                    "mimeType": "image/jpeg",
                                }
                            ],
                            "error": "",
                        },
                        {
                            "resourceKey": "motion_media_2",
                            "url": "https://example.test/live-after.mp4",
                            "status": "ok",
                            "keyframes": [
                                {
                                    "index": 0,
                                    "timestampSec": 1.0,
                                    "dataUrl": "data:image/jpeg;base64,BBB222",
                                    "width": 960,
                                    "height": 540,
                                    "mimeType": "image/jpeg",
                                },
                                {
                                    "index": 1,
                                    "timestampSec": 2.0,
                                    "dataUrl": "data:image/jpeg;base64,BBB333",
                                    "width": 960,
                                    "height": 540,
                                    "mimeType": "image/jpeg",
                                },
                                {
                                    "index": 2,
                                    "timestampSec": 3.0,
                                    "dataUrl": "data:image/jpeg;base64,BBB444",
                                    "width": 960,
                                    "height": 540,
                                    "mimeType": "image/jpeg",
                                }
                            ],
                            "error": "",
                        },
                    ],
                    "elapsedMs": 1200,
                }

                with patch("app.services.task_capability_service.requests.post", return_value=type("FakeKeyframeHelperResponse", (), {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: fake_helper_keyframes,
                })()) as keyframe_post_mock:
                    keyframes = client.post(
                        f"/api/v1/tasks/catalog/{item_id}/capability/media-keyframe-extraction",
                        json={
                            "media_resources": media_payload["media_resources"],
                            "allow_extract": True,
                            "archive_frames": True,
                            "max_frames_per_video": 3,
                            "timeout_ms": 6000,
                        },
                    )
                self.assertEqual(keyframes.status_code, 200, keyframes.text)
                keyframe_post_mock.assert_called_once()
                self.assertTrue(str(keyframe_post_mock.call_args.args[0]).endswith("/api/video-keyframe-extract"))
                keyframes_payload = keyframes.json()
                self.assertTrue(keyframes_payload["ok"])
                self.assertEqual(keyframes_payload["mode"], "media_keyframe_extraction")
                self.assertTrue(keyframes_payload["sends_network"])
                self.assertFalse(keyframes_payload["writes_remote"])
                self.assertFalse(keyframes_payload["claims_visual_judgement"])
                self.assertEqual(keyframes_payload["helper_mode"], "host_helper_video_keyframe_extract")
                self.assertGreater(keyframes_payload["archived_frame_count"], 0)
                self.assertTrue(keyframes_payload["artifact_path"].endswith("manifest.json"))
                self.assertEqual(keyframes_payload["keyframe_results"][0]["resource_key"], "motion_media_1")
                self.assertEqual(keyframes_payload["keyframe_results"][0]["keyframes"][0]["data_url"], "data:image/jpeg;base64,AAA111")
                self.assertIn("keyframes", keyframes_payload["keyframe_results"][0]["keyframes"][0]["artifact_path"])
                self.assertEqual(keyframes_payload["keyframe_results"][0]["keyframes"][0]["preview_url"], "data:image/jpeg;base64,AAA111")
                self.assertIn("multimodal-still-required", keyframes_payload["blockers"])

                with patch("app.services.task_capability_service.requests.post", side_effect=AssertionError("缓存命中时不应调用 helper")):
                    cached_keyframes = client.post(
                        f"/api/v1/tasks/catalog/{item_id}/capability/media-keyframe-extraction",
                        json={
                            "media_resources": media_payload["media_resources"],
                            "allow_extract": True,
                            "reuse_cached_frames": True,
                            "archive_frames": True,
                            "max_frames_per_video": 3,
                            "timeout_ms": 6000,
                        },
                    )
                self.assertEqual(cached_keyframes.status_code, 200, cached_keyframes.text)
                cached_keyframes_payload = cached_keyframes.json()
                self.assertTrue(cached_keyframes_payload["ok"])
                self.assertFalse(cached_keyframes_payload["sends_network"])
                self.assertFalse(cached_keyframes_payload["writes_remote"])
                self.assertEqual(cached_keyframes_payload["helper_mode"], "cached_keyframe_archive")
                self.assertTrue(cached_keyframes_payload["cache_hit"])
                self.assertEqual(cached_keyframes_payload["artifact_path"], keyframes_payload["artifact_path"])
                self.assertEqual(cached_keyframes_payload["archived_frame_count"], 6)
                self.assertEqual(cached_keyframes_payload["keyframe_results"][0]["keyframes"][2]["index"], 2)
                self.assertTrue(cached_keyframes_payload["keyframe_results"][0]["keyframes"][0]["data_url"].startswith("data:image/jpeg;base64,"))
                self.assertIn("cached-keyframes-reused", cached_keyframes_payload["guardrails"])

                media_draft = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/media-inspection-draft",
                    json={
                        "image_judgement": {
                            "layout_normal": True,
                            "mojibake_or_broken_layout": False,
                            "reason": "左图完整，排版正常，无乱码。",
                        },
                        "video_keyframe_judgements": [
                            {
                                "resource_key": "motion_media_1",
                                "action_visible": True,
                                "matches_sandbox_trace": True,
                                "keyframe_summary": "关键帧看到点击后页面跳转。",
                                "reason": "产物一复现了沙箱点击跳转。",
                            },
                            {
                                "resource_key": "motion_media_2",
                                "action_visible": True,
                                "matches_sandbox_trace": True,
                                "keyframe_summary": "关键帧看到同样操作反馈。",
                                "reason": "产物二也复现了点击反馈。",
                            },
                        ],
                        "remark_marker": "MEDIA_INSPECTION_DRY_RUN_TEST",
                    },
                )
                self.assertEqual(media_draft.status_code, 200, media_draft.text)
                media_draft_payload = media_draft.json()
                self.assertEqual(media_draft_payload["mode"], "media_inspection_draft_plan")
                self.assertFalse(media_draft_payload["sends_network"])
                self.assertFalse(media_draft_payload["writes_remote"])
                media_decoded = json.loads(media_draft_payload["payload_preview"]["AuditAnswers"][0]["Content"])
                self.assertEqual(media_decoded["data"]["beauty_score"], "2")
                self.assertEqual(media_decoded["data"]["sceneConsistencyScore"], {"product1": "2", "product2": "1"})
                self.assertEqual(media_decoded["dataMap"]["sceneConsistencyScore"], {"product1": "2", "product2": "1"})
                self.assertIn("左图完整", media_decoded["data"]["richness_reason"])
                self.assertIn("产物一复现", media_decoded["data"]["sceneConsistencyRemarks"]["product1"])
                self.assertIn("产物二也复现", media_decoded["data"]["sceneConsistencyRemarks"]["product2"])
                self.assertEqual(media_decoded["data"]["discard_remark"], "MEDIA_INSPECTION_DRY_RUN_TEST")

                dry_run = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/draft",
                    json={
                        "answer_data": {
                            "beauty_score": "1",
                            "motion_richness_score": "1",
                            "richness_reason": "平台 dry-run 原因",
                        },
                        "remark_marker": "PLATFORM_DRY_RUN",
                    },
                )
                self.assertEqual(dry_run.status_code, 200, dry_run.text)
                draft = dry_run.json()
                self.assertTrue(draft["ok"])
                self.assertFalse(draft["sends_network"])
                self.assertFalse(draft["writes_remote"])
                self.assertEqual(draft["mode"], "temp_draft_plan")
                decoded = json.loads(draft["payload_preview"]["AuditAnswers"][0]["Content"])
                self.assertEqual(decoded["data"]["beauty_score"], "1")
                self.assertEqual(decoded["dataMap"]["beauty_score"], "1")
                self.assertEqual(decoded["data"]["discard_remark"], "PLATFORM_DRY_RUN")

                blocked = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/draft",
                    json={"execute": True, "answer_data": {"beauty_score": "1"}},
                )
                self.assertEqual(blocked.status_code, 400, blocked.text)
                self.assertIn("missing-AllowDraftWrite", blocked.text)

                ai_draft = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/ai-draft",
                    json={
                        "ai_output": {
                            "beauty_score": 1,
                            "motion_richness_score": 2,
                            "richness_reason": "截图一般但视频有明确跳转动效，建议人工复核。",
                            "scene_consistency_score": {"product1": 1, "product2": 1},
                            "scene_consistency_reason": "前后场景主体一致，动效补充了静态截图信息。",
                            "discard": False,
                            "check_remark": "AI草稿：仅暂存，人工复核后再决定。",
                        },
                        "remark_marker": "AI_SCHEMA_DRY_RUN",
                    },
                )
                self.assertEqual(ai_draft.status_code, 200, ai_draft.text)
                ai_payload = ai_draft.json()
                self.assertEqual(ai_payload["mode"], "ai_temp_draft_plan")
                self.assertFalse(ai_payload["sends_network"])
                self.assertFalse(ai_payload["writes_remote"])
                decoded_ai = json.loads(ai_payload["payload_preview"]["AuditAnswers"][0]["Content"])
                self.assertEqual(decoded_ai["data"]["beauty_score"], "1")
                self.assertEqual(decoded_ai["dataMap"]["motion_richness_score"], "2")
                self.assertEqual(decoded_ai["data"]["sceneConsistencyScore"], {"product1": "1", "product2": "1"})
                self.assertEqual(decoded_ai["dataMap"]["sceneConsistencyRemarks"]["product1"], "前后场景主体一致，动效补充了静态截图信息。")
                self.assertEqual(decoded_ai["data"]["discard"], "No")
                self.assertEqual(decoded_ai["data"]["discard_remark"], "AI_SCHEMA_DRY_RUN")

                invalid_ai_draft = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/ai-draft",
                    json={"ai_output": {"beauty_score": 3, "motion_richness_score": 1, "richness_reason": "bad"}},
                )
                self.assertEqual(invalid_ai_draft.status_code, 400, invalid_ai_draft.text)
                self.assertIn("invalid-ai-output", invalid_ai_draft.text)

                local_provider_draft = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/provider-draft",
                    json={"use_provider": False, "operator_prompt": "优先复核动效和前后场景一致性。"},
                )
                self.assertEqual(local_provider_draft.status_code, 200, local_provider_draft.text)
                local_payload = local_provider_draft.json()
                self.assertEqual(local_payload["mode"], "local_ai_temp_draft_plan")
                self.assertFalse(local_payload["sends_network"])
                self.assertFalse(local_payload["writes_remote"])
                decoded_local = json.loads(local_payload["payload_preview"]["AuditAnswers"][0]["Content"])
                self.assertIn(decoded_local["data"]["beauty_score"], {"0", "1", "2"})
                self.assertIn(decoded_local["data"]["motion_richness_score"], {"0", "1", "2"})
                self.assertIn("AI_PROVIDER_LOCAL_DRY_RUN", decoded_local["data"]["discard_remark"])

                config = client.put(
                    "/api/v1/ai/config",
                    json={
                        "system_ai": {"base_url": "", "api_key": "", "model": "gpt-4.1-mini", "timeout_seconds": 30},
                        "task_ai": {
                            "base_url": "https://api.example-task.local/v1",
                            "api_key": "test-task-provider-key",
                            "model": "task-model-test",
                            "timeout_seconds": 25,
                            "pre_prompt": "只输出返修评分 JSON。",
                            "skills": ["rft-score"],
                            "md_files": ["notes/projects/aidp-operation-recording-20260507-analysis.md"],
                        },
                        "task_ai_managed_by_system_ai": True,
                    },
                )
                self.assertEqual(config.status_code, 200, config.text)

                class FakeMediaProviderResponse:
                    ok = True
                    status_code = 200

                    def raise_for_status(self) -> None:
                        return None

                    def json(self) -> dict[str, object]:
                        return {
                            "choices": [
                                {
                                    "message": {
                                        "content": json.dumps(
                                            {
                                                "image_judgement": {
                                                    "layout_normal": True,
                                                    "mojibake_or_broken_layout": False,
                                                    "reason": "多模态模型看到左图完整、排版正常、没有乱码。",
                                                },
                                                "video_keyframe_judgements": [
                                                    {
                                                        "resource_key": "motion_media_1",
                                                        "action_visible": True,
                                                        "matches_sandbox_trace": True,
                                                        "total_frame_count": 3,
                                                        "supporting_frame_count": 3,
                                                        "confidence": "high",
                                                        "review_required": False,
                                                        "keyframe_summary": "关键帧显示点击后页面跳转。",
                                                        "reason": "产物一与沙箱点击跳转一致。",
                                                    },
                                                    {
                                                        "resource_key": "motion_media_2",
                                                        "action_visible": True,
                                                        "matches_sandbox_trace": True,
                                                        "total_frame_count": 3,
                                                        "supporting_frame_count": 1,
                                                        "confidence": "low",
                                                        "review_required": True,
                                                        "review_hint": "只有 1/3 帧能看到操作反馈，需要人工复核。",
                                                        "keyframe_summary": "关键帧显示同样的交互反馈。",
                                                        "reason": "产物二也能看到操作反馈。",
                                                    },
                                                ],
                                            },
                                            ensure_ascii=False,
                                        )
                                    }
                                }
                            ]
                        }

                with patch("app.services.task_capability_service.requests.post", return_value=FakeMediaProviderResponse()) as media_post_mock:
                    media_provider = client.post(
                        f"/api/v1/tasks/catalog/{item_id}/capability/media-inspection-provider",
                        json={
                            "media_resources": media_payload["media_resources"],
                            "sandbox_trace": execution_payload["interaction_summary"],
                            "operator_prompt": "用左图判断排版，用视频关键帧判断是否复现沙箱点击。",
                        "use_provider": True,
                        "video_keyframes": keyframes_payload["keyframe_results"],
                    },
                )
                self.assertEqual(media_provider.status_code, 200, media_provider.text)
                media_post_mock.assert_called_once()
                media_call = media_post_mock.call_args
                self.assertTrue(str(media_call.args[0]).endswith("/chat/completions"))
                self.assertNotIn("SubmitTempItemAnswer", str(media_call.args[0]))
                media_provider_payload = media_provider.json()
                self.assertTrue(media_provider_payload["ok"])
                self.assertEqual(media_provider_payload["mode"], "media_inspection_provider")
                self.assertTrue(media_provider_payload["sends_network"])
                self.assertFalse(media_provider_payload["writes_remote"])
                self.assertTrue(media_provider_payload["claims_visual_judgement"])
                self.assertEqual(media_provider_payload["provider_status"], "provider_ok")
                self.assertEqual(media_provider_payload["provider_call_count"], 1)
                self.assertGreaterEqual(media_provider_payload["provider_elapsed_ms"], 0)
                self.assertGreaterEqual(media_provider_payload["total_elapsed_ms"], media_provider_payload["provider_elapsed_ms"])
                self.assertGreater(media_provider_payload["provider_input_text_chars"], 0)
                self.assertEqual(media_provider_payload["provider_input_image_count"], 7)
                self.assertEqual(media_provider_payload["provider_input_keyframe_count"], 6)
                media_diagnostic_keys = {item["key"] for item in media_provider_payload["provider_diagnostics"]}
                self.assertIn("provider-keyframes-present", media_diagnostic_keys)
                self.assertIn("provider-elapsed-observed", media_diagnostic_keys)
                self.assertIn("多模态模型", media_provider_payload["image_judgement"]["reason"])
                self.assertEqual(len(media_provider_payload["video_keyframe_judgements"]), 2)
                self.assertEqual(media_provider_payload["video_keyframe_judgements"][0]["total_frame_count"], 3)
                self.assertEqual(media_provider_payload["video_keyframe_judgements"][0]["supporting_frame_count"], 3)
                self.assertEqual(media_provider_payload["video_keyframe_judgements"][0]["confidence"], "high")
                self.assertFalse(media_provider_payload["video_keyframe_judgements"][0]["review_required"])
                self.assertEqual(media_provider_payload["video_keyframe_judgements"][1]["supporting_frame_count"], 1)
                self.assertEqual(media_provider_payload["video_keyframe_judgements"][1]["confidence"], "low")
                self.assertTrue(media_provider_payload["video_keyframe_judgements"][1]["review_required"])
                self.assertIn("low-confidence-media-review-required", media_provider_payload["blockers"])
                self.assertEqual(media_provider_payload["draft_preview"]["mode"], "media_inspection_draft_plan")
                self.assertFalse(media_provider_payload["draft_preview"]["sends_network"])
                self.assertFalse(media_provider_payload["draft_preview"]["writes_remote"])
                media_provider_decoded = json.loads(media_provider_payload["draft_preview"]["payload_preview"]["AuditAnswers"][0]["Content"])
                self.assertEqual(media_provider_decoded["data"]["beauty_score"], "2")
                self.assertEqual(media_provider_decoded["data"]["sceneConsistencyScore"], {"product1": "2", "product2": "1"})
                self.assertIn("MEDIA_PROVIDER_DRY_RUN", media_provider_decoded["data"]["discard_remark"])
                media_request_json = media_call.kwargs["json"]
                self.assertEqual(media_request_json["model"], "task-model-test")
                prompt_text = json.dumps(media_request_json["messages"], ensure_ascii=False)
                self.assertIn("https://example.test/live-final.png", prompt_text)
                self.assertIn("https://example.test/live-before.mp4", prompt_text)
                self.assertEqual(prompt_text.count("https://example.test/live-before.mp4"), 1)
                self.assertNotIn("video_accessible", prompt_text)
                self.assertNotIn("scene_consistency_signal", prompt_text)
                self.assertIn("data:image/jpeg;base64,AAA111", prompt_text)
                self.assertIn("关键帧", prompt_text)
                self.assertIn("supporting_frame_count", prompt_text)
                self.assertIn("review_required", prompt_text)
                self.assertIn("低置信", prompt_text)
                self.assertIn("不打开 AIDP UI", prompt_text)
                self.assertIn("优先依据已抽取关键帧", prompt_text)

                class FakeAutoSupplementResponse:
                    ok = True
                    status_code = 200

                    def __init__(self, payload: dict[str, object]) -> None:
                        self._payload = payload

                    def raise_for_status(self) -> None:
                        return None

                    def json(self) -> dict[str, object]:
                        return self._payload

                auto_provider_low = {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "image_judgement": {
                                            "layout_normal": True,
                                            "mojibake_or_broken_layout": False,
                                            "reason": "三帧阶段左图正常。",
                                        },
                                        "video_keyframe_judgements": [
                                            {
                                                "resource_key": "motion_media_1",
                                                "action_visible": True,
                                                "matches_sandbox_trace": True,
                                                "total_frame_count": 3,
                                                "supporting_frame_count": 1,
                                                "confidence": "low",
                                                "review_required": True,
                                                "review_hint": "三帧只有一帧支持，需要补帧。",
                                                "keyframe_summary": "三帧证据不足。",
                                                "reason": "三帧低置信。",
                                            },
                                            {
                                                "resource_key": "motion_media_2",
                                                "action_visible": False,
                                                "matches_sandbox_trace": False,
                                                "total_frame_count": 3,
                                                "supporting_frame_count": 0,
                                                "confidence": "low",
                                                "review_required": True,
                                                "review_hint": "三帧未看到交互。",
                                                "keyframe_summary": "三帧未见变化。",
                                                "reason": "三帧不支持。",
                                            },
                                        ],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }
                auto_provider_high = {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "image_judgement": {
                                            "layout_normal": True,
                                            "mojibake_or_broken_layout": False,
                                            "reason": "五帧阶段左图正常。",
                                        },
                                        "video_keyframe_judgements": [
                                            {
                                                "resource_key": "motion_media_1",
                                                "action_visible": True,
                                                "matches_sandbox_trace": True,
                                                "total_frame_count": 5,
                                                "supporting_frame_count": 4,
                                                "confidence": "high",
                                                "review_required": False,
                                                "keyframe_summary": "五帧多数支持点击反馈。",
                                                "reason": "补帧后多数帧支持操作复现。",
                                            },
                                            {
                                                "resource_key": "motion_media_2",
                                                "action_visible": False,
                                                "matches_sandbox_trace": False,
                                                "total_frame_count": 5,
                                                "supporting_frame_count": 0,
                                                "confidence": "low",
                                                "review_required": True,
                                                "review_hint": "五帧仍未看到交互。",
                                                "keyframe_summary": "五帧仍未见变化。",
                                                "reason": "补帧后仍不支持操作复现。",
                                            },
                                        ],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }
                fake_helper_five_frames = {
                    "ok": True,
                    "mode": "host_helper_video_keyframe_extract",
                    "results": [
                        {
                            "resourceKey": "motion_media_1",
                            "url": "https://example.test/live-before.mp4",
                            "status": "ok",
                            "keyframes": [
                                {"index": index, "timestampSec": float(index + 1), "dataUrl": f"data:image/jpeg;base64,AUTOA{index}", "width": 960, "height": 540, "mimeType": "image/jpeg"}
                                for index in range(5)
                            ],
                            "error": "",
                        },
                        {
                            "resourceKey": "motion_media_2",
                            "url": "https://example.test/live-after.mp4",
                            "status": "ok",
                            "keyframes": [
                                {"index": index, "timestampSec": float(index + 1), "dataUrl": f"data:image/jpeg;base64,AUTOB{index}", "width": 960, "height": 540, "mimeType": "image/jpeg"}
                                for index in range(5)
                            ],
                            "error": "",
                        },
                    ],
                    "elapsedMs": 2200,
                }
                auto_provider_payloads = [auto_provider_low, auto_provider_high]

                def fake_auto_post(url: str, **kwargs: object) -> FakeAutoSupplementResponse:
                    if str(url).endswith("/api/video-keyframe-extract"):
                        return FakeAutoSupplementResponse(fake_helper_five_frames)
                    return FakeAutoSupplementResponse(auto_provider_payloads.pop(0))

                with patch("app.services.task_capability_service.requests.post", side_effect=fake_auto_post) as auto_post_mock:
                    auto_media_provider = client.post(
                        f"/api/v1/tasks/catalog/{item_id}/capability/media-inspection-provider",
                        json={
                            "media_resources": media_payload["media_resources"],
                            "sandbox_trace": execution_payload["interaction_summary"],
                            "operator_prompt": "低置信时补抽 5 帧并二次判断。",
                            "use_provider": True,
                            "video_keyframes": keyframes_payload["keyframe_results"],
                            "auto_supplement_low_confidence": True,
                            "supplement_max_frames_per_video": 5,
                        },
                    )
                self.assertEqual(auto_media_provider.status_code, 200, auto_media_provider.text)
                auto_payload = auto_media_provider.json()
                self.assertTrue(auto_payload["ok"])
                self.assertTrue(auto_payload["supplement_attempted"])
                self.assertEqual(auto_payload["supplement_status"], "supplemented_and_rejudged")
                self.assertEqual(len(auto_payload["initial_video_keyframe_judgements"]), 2)
                self.assertEqual(auto_payload["initial_video_keyframe_judgements"][0]["total_frame_count"], 3)
                self.assertTrue(auto_payload["supplement_keyframes"]["ok"])
                self.assertEqual(auto_payload["supplement_keyframes"]["archived_frame_count"], 10)
                self.assertEqual(auto_payload["video_keyframe_judgements"][0]["total_frame_count"], 5)
                self.assertEqual(auto_payload["video_keyframe_judgements"][0]["supporting_frame_count"], 4)
                self.assertEqual(auto_payload["provider_call_count"], 2)
                self.assertGreaterEqual(auto_payload["provider_elapsed_ms"], 0)
                self.assertGreaterEqual(auto_payload["total_elapsed_ms"], auto_payload["provider_elapsed_ms"])
                self.assertGreater(auto_payload["provider_input_text_chars"], media_provider_payload["provider_input_text_chars"])
                self.assertEqual(auto_payload["provider_input_keyframe_count"], 16)
                auto_diagnostic_keys = {item["key"] for item in auto_payload["provider_diagnostics"]}
                self.assertIn("provider-called-twice", auto_diagnostic_keys)
                self.assertIn("provider-keyframe-input-high", auto_diagnostic_keys)
                self.assertEqual(auto_payload["draft_preview"]["mode"], "media_inspection_draft_plan")
                auto_decoded = json.loads(auto_payload["draft_preview"]["payload_preview"]["AuditAnswers"][0]["Content"])
                self.assertEqual(auto_decoded["data"]["sceneConsistencyScore"], {"product1": "2", "product2": "0"})
                self.assertEqual(auto_post_mock.call_count, 3)

                keyframe_cache_root = tmp / "task-capabilities" / "keyframes" / f"task-{item_id}-cached5"
                keyframe_cache_root.mkdir(parents=True, exist_ok=True)
                cached_manifest_results = []
                for resource_key, url, prefix in [
                    ("motion_media_1", "https://example.test/live-before.mp4", "cached-a"),
                    ("motion_media_2", "https://example.test/live-after.mp4", "cached-b"),
                ]:
                    frames = []
                    for index in range(5):
                        frame_path = keyframe_cache_root / f"{prefix}-{index}.jpg"
                        frame_path.write_bytes(b"\xff\xd8\xff\xd9")
                        frames.append(
                            {
                                "index": index,
                                "timestamp_sec": float(index + 1),
                                "artifact_path": str(frame_path),
                                "width": 960,
                                "height": 540,
                                "mime_type": "image/jpeg",
                            }
                        )
                    cached_manifest_results.append(
                        {
                            "resource_key": resource_key,
                            "url": url,
                            "status": "ok",
                            "frames": frames,
                            "error": "",
                        }
                    )
                (keyframe_cache_root / "manifest.json").write_text(
                    json.dumps(
                        {
                            "generated_at": "2026-05-09T00:00:00+00:00",
                            "mode": "video_keyframe_archive",
                            "task_catalog_item_id": item_id,
                            "writes_remote": False,
                            "claims_visual_judgement": False,
                            "archived_frame_count": 10,
                            "results": cached_manifest_results,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                cached_provider_payload = {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "image_judgement": {
                                            "layout_normal": True,
                                            "mojibake_or_broken_layout": False,
                                            "reason": "直接使用五帧缓存后左图正常。",
                                        },
                                        "video_keyframe_judgements": [
                                            {
                                                "resource_key": "motion_media_1",
                                                "action_visible": True,
                                                "matches_sandbox_trace": True,
                                                "total_frame_count": 5,
                                                "supporting_frame_count": 4,
                                                "confidence": "high",
                                                "review_required": False,
                                                "keyframe_summary": "缓存五帧多数支持点击反馈。",
                                                "reason": "五帧缓存足够判断。",
                                            },
                                            {
                                                "resource_key": "motion_media_2",
                                                "action_visible": False,
                                                "matches_sandbox_trace": False,
                                                "total_frame_count": 5,
                                                "supporting_frame_count": 0,
                                                "confidence": "low",
                                                "review_required": True,
                                                "review_hint": "五帧缓存仍未看到交互。",
                                                "keyframe_summary": "缓存五帧仍未见变化。",
                                                "reason": "五帧缓存不支持。",
                                            },
                                        ],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

                def fake_cached_auto_post(url: str, **kwargs: object) -> FakeAutoSupplementResponse:
                    self.assertFalse(str(url).endswith("/api/video-keyframe-extract"), "已有 5 帧缓存时不应调用 helper 补抽")
                    return FakeAutoSupplementResponse(cached_provider_payload)

                with patch("app.services.task_capability_service.requests.post", side_effect=fake_cached_auto_post) as cached_auto_post_mock:
                    cached_auto_media_provider = client.post(
                        f"/api/v1/tasks/catalog/{item_id}/capability/media-inspection-provider",
                        json={
                            "media_resources": media_payload["media_resources"],
                            "sandbox_trace": execution_payload["interaction_summary"],
                            "operator_prompt": "已有五帧缓存时应直接复用，避免首轮三帧 provider。",
                            "use_provider": True,
                            "video_keyframes": keyframes_payload["keyframe_results"],
                            "auto_supplement_low_confidence": True,
                            "supplement_max_frames_per_video": 5,
                        },
                    )
                self.assertEqual(cached_auto_media_provider.status_code, 200, cached_auto_media_provider.text)
                cached_auto_payload = cached_auto_media_provider.json()
                self.assertTrue(cached_auto_payload["supplement_attempted"])
                self.assertEqual(cached_auto_payload["supplement_status"], "cached_supplement_used")
                self.assertTrue(cached_auto_payload["supplement_keyframes"]["cache_hit"])
                self.assertEqual(cached_auto_payload["supplement_keyframes"]["archived_frame_count"], 10)
                self.assertEqual(cached_auto_payload["initial_video_keyframe_judgements"], [])
                self.assertEqual(cached_auto_payload["video_keyframe_judgements"][0]["total_frame_count"], 5)
                self.assertEqual(cached_auto_payload["provider_call_count"], 1)
                self.assertGreaterEqual(cached_auto_payload["provider_elapsed_ms"], 0)
                self.assertGreaterEqual(cached_auto_payload["total_elapsed_ms"], cached_auto_payload["provider_elapsed_ms"])
                self.assertGreater(cached_auto_payload["provider_input_text_chars"], 0)
                self.assertEqual(cached_auto_payload["provider_input_image_count"], 11)
                self.assertEqual(cached_auto_payload["provider_input_keyframe_count"], 10)
                cached_diagnostic_keys = {item["key"] for item in cached_auto_payload["provider_diagnostics"]}
                self.assertIn("cached-supplement-single-provider", cached_diagnostic_keys)
                self.assertIn("provider-keyframe-input-high", cached_diagnostic_keys)
                self.assertEqual(cached_auto_post_mock.call_count, 1)

                class FakeProviderResponse:
                    ok = True
                    status_code = 200

                    def raise_for_status(self) -> None:
                        return None

                    def json(self) -> dict[str, object]:
                        return {
                            "choices": [
                                {
                                    "message": {
                                        "content": json.dumps(
                                            {
                                                "beauty_score": 2,
                                                "motion_richness_score": 1,
                                                "richness_reason": "截图完整且主体清晰，视频动效一般，建议人工复核。",
                                                "scene_consistency_score": {"product1": 2, "product2": 1},
                                                "scene_consistency_reason": "前后素材主体一致，但第二段状态变化较少。",
                                                "discard": False,
                                                "check_remark": "Provider草稿：仅暂存，人工复核后再决定。",
                                            },
                                            ensure_ascii=False,
                                        )
                                    }
                                }
                            ]
                        }

                with patch("app.services.task_capability_service.requests.post", return_value=FakeProviderResponse()) as post_mock:
                    provider_draft = client.post(
                        f"/api/v1/tasks/catalog/{item_id}/capability/provider-draft",
                        json={"use_provider": True, "operator_prompt": "按返修评分规则输出结构化 JSON。"},
                    )
                self.assertEqual(provider_draft.status_code, 200, provider_draft.text)
                post_mock.assert_called_once()
                provider_payload = provider_draft.json()
                self.assertEqual(provider_payload["mode"], "provider_ai_temp_draft_plan")
                self.assertFalse(provider_payload["sends_network"])
                self.assertFalse(provider_payload["writes_remote"])
                self.assertEqual(provider_payload["ai_review_preview"]["provider_status"], "provider_ok")
                self.assertEqual(provider_payload["ai_review_preview"]["ai_output"]["beauty_score"], 2)
                self.assertEqual(provider_payload["ai_review_preview"]["mapped_answer_data"]["beauty_score"], "2")
                review_items = {item["key"]: item for item in provider_payload["ai_review_preview"]["review_items"]}
                self.assertEqual(review_items["beauty_score"]["value"], "2")
                self.assertIn("截图完整", review_items["richness_reason"]["value"])
                decoded_provider = json.loads(provider_payload["payload_preview"]["AuditAnswers"][0]["Content"])
                self.assertEqual(decoded_provider["data"]["beauty_score"], "2")
                self.assertEqual(decoded_provider["dataMap"]["motion_richness_score"], "1")
                self.assertEqual(decoded_provider["data"]["sceneConsistencyScore"], {"product1": "2", "product2": "1"})
                self.assertEqual(decoded_provider["data"]["discard_remark"], "AI_PROVIDER_DRY_RUN")

                approval = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/review-approval",
                    json={
                        "ai_output": provider_payload["ai_review_preview"]["ai_output"],
                        "reviewer": "tester",
                        "review_note": "人工确认分数和原因可作为草稿暂存起点。",
                    },
                )
                self.assertEqual(approval.status_code, 200, approval.text)
                approval_payload = approval.json()
                self.assertTrue(approval_payload["ok"])
                self.assertEqual(approval_payload["status"], "review_approved")
                self.assertFalse(approval_payload["sends_network"])
                self.assertFalse(approval_payload["writes_remote"])
                self.assertEqual(approval_payload["confirmation_sheet"]["title"], "受控草稿暂存确认单")
                self.assertIn("AIDP_TEMP_DRAFT_ALLOW_WRITE=1", approval_payload["confirmation_sheet"]["required_gates"])
                self.assertEqual(approval_payload["confirmation_sheet"]["mapped_answer_data"]["beauty_score"], "2")
                self.assertEqual(approval_payload["confirmation_sheet"]["reviewer"], "tester")
                self.assertTrue(approval_payload["confirmation_sheet"]["draft_evidence_path"].endswith(".json"))
                self.assertFalse(approval_payload["confirmation_sheet"]["ready_for_gated_write"])
                gate_statuses = {item["key"]: item for item in approval_payload["confirmation_sheet"]["gate_statuses"]}
                self.assertTrue(gate_statuses["execute"]["passed"])
                self.assertTrue(gate_statuses["allow_draft_write"]["passed"])
                self.assertFalse(gate_statuses["env_allow_write"]["passed"])
                self.assertIn("AIDP_TEMP_DRAFT_ALLOW_WRITE", gate_statuses["env_allow_write"]["detail"])
                field_diff = {item["field"]: item for item in approval_payload["confirmation_sheet"]["field_diff"]}
                self.assertEqual(field_diff["beauty_score"]["current_value"], "2")
                self.assertEqual(field_diff["beauty_score"]["next_value"], "2")
                self.assertFalse(field_diff["beauty_score"]["changed"])
                self.assertEqual(field_diff["richness_reason"]["current_value"], "录制原因")
                self.assertIn("截图完整", field_diff["richness_reason"]["next_value"])
                self.assertTrue(field_diff["richness_reason"]["changed"])
                checklist = {item["key"]: item for item in approval_payload["confirmation_sheet"]["rehearsal_checklist"]}
                self.assertEqual(checklist["field_diff_review"]["status"], "ready")
                self.assertEqual(checklist["gate_status_review"]["status"], "blocked")
                self.assertEqual(checklist["identity_confirmed"]["status"], "ready")
                self.assertIn("7634537456234385161", checklist["identity_confirmed"]["detail"])
                self.assertEqual(checklist["allowed_endpoint_only"]["status"], "ready")
                self.assertIn("SubmitTempItemAnswer", checklist["allowed_endpoint_only"]["detail"])
                self.assertEqual(checklist["forbidden_actions_review"]["status"], "ready")
                self.assertIn("提交", checklist["forbidden_actions_review"]["detail"])
                self.assertEqual(checklist["dry_run_evidence"]["status"], "ready")
                self.assertTrue(checklist["dry_run_evidence"]["detail"].endswith(".json"))
                self.assertEqual(checklist["manual_page_review"]["status"], "needs_operator")
                self.assertEqual(checklist["explicit_write_permission"]["status"], "blocked")
                self.assertTrue(checklist["explicit_write_permission"]["required"])

                confirmation_execute = client.post(
                    f"/api/v1/tasks/catalog/{item_id}/capability/draft",
                    json={
                        "answer_data": approval_payload["confirmation_sheet"]["mapped_answer_data"],
                        "remark_marker": "AI_REVIEW_APPROVED_GATED_WRITE",
                        "execute": True,
                        "allow_draft_write": True,
                    },
                )
                self.assertEqual(confirmation_execute.status_code, 400, confirmation_execute.text)
                self.assertIn("missing-env-AIDP_TEMP_DRAFT_ALLOW_WRITE", confirmation_execute.text)
                self.assertNotIn("missing-AllowDraftWrite", confirmation_execute.text)

                class FakeDraftWriteResponse:
                    ok = True
                    status_code = 200
                    text = json.dumps({"BaseResp": {"StatusCode": 0}}, ensure_ascii=False)

                    def json(self) -> dict[str, object]:
                        return {"BaseResp": {"StatusCode": 0}}

                with patch.dict(os.environ, {"AIDP_TEMP_DRAFT_ALLOW_WRITE": "1"}):
                    with patch("app.services.task_capability_service.requests.post", return_value=FakeDraftWriteResponse()) as write_mock:
                        live_execute = client.post(
                            f"/api/v1/tasks/catalog/{item_id}/capability/draft",
                            json={
                                "answer_data": approval_payload["confirmation_sheet"]["mapped_answer_data"],
                                "remark_marker": "AI_REVIEW_APPROVED_GATED_WRITE_TEST",
                                "execute": True,
                                "allow_draft_write": True,
                            },
                        )
                self.assertEqual(live_execute.status_code, 200, live_execute.text)
                live_payload = live_execute.json()
                self.assertTrue(live_payload["ok"])
                self.assertTrue(live_payload["sends_network"])
                self.assertTrue(live_payload["writes_remote"])
                self.assertEqual(live_payload["base_resp_status_code"], 0)
                write_mock.assert_called_once()
                self.assertEqual(write_mock.call_args.kwargs["headers"]["Cookie"], "sessionid=target-cookie")

                with patch.dict(os.environ, {"AIDP_TEMP_DRAFT_ALLOW_WRITE": "1"}):
                    with patch("app.services.task_capability_service.requests.post", return_value=FakeDraftWriteResponse()) as override_write_mock:
                        override_execute = client.post(
                            f"/api/v1/tasks/catalog/{item_id}/capability/draft",
                            json={
                                "answer_data": approval_payload["confirmation_sheet"]["mapped_answer_data"],
                                "remark_marker": "AI_REVIEW_APPROVED_GATED_WRITE_TEST",
                                "execute": True,
                                "allow_draft_write": True,
                                "account_user_id": "other-account",
                            },
                        )
                self.assertEqual(override_execute.status_code, 200, override_execute.text)
                override_payload = override_execute.json()
                self.assertTrue(override_payload["ok"])
                self.assertTrue(override_payload["sends_network"])
                self.assertTrue(override_payload["writes_remote"])
                self.assertEqual(override_payload["base_resp_status_code"], 0)
                override_write_mock.assert_called_once()
                self.assertEqual(override_write_mock.call_args.kwargs["headers"]["Cookie"], "sessionid=other-cookie")


if __name__ == "__main__":
    unittest.main()
