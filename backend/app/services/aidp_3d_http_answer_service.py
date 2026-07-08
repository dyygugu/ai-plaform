import base64
import copy
import io
import json
import mimetypes
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from app.core.settings import get_settings
from app.services.task_rules import utc_now

try:
    from PIL import Image
except Exception:  # noqa: BLE001 - Pillow is optional in the runtime package.
    Image = None


AIDP_3D_RUBRIC_TASK_ID = "7658232870117527347"
AIDP_3D_RUBRIC_NODE_ID = "1"
AIDP_3D_RUBRIC_TEMPLATE_ID = "7658120776411467566"
AIDP_3D_RUBRIC_MAX_PARALLEL_ACCOUNTS = 5
AIDP_3D_RUBRIC_MODEL = "qwen3-vl-plus"


class Aidp3DAnswerError(RuntimeError):
    def __init__(self, code: str, message: str, *, stage: str = "", retryable: bool = True, evidence: Optional[dict[str, Any]] = None) -> None:
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.evidence = evidence
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class Aidp3DRuntime:
    base_url: str
    api_key: str
    model: str = AIDP_3D_RUBRIC_MODEL
    timeout_seconds: int = 25


class Aidp3DLedger:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def begin(self, task_id: str, account_user_id: str, item_id: str, *, run_id: str) -> None:
        path = self._path(task_id, account_user_id, item_id)
        existing = _load_json_file(path)
        if isinstance(existing, dict):
            status = str(existing.get("status") or "")
            if status == "submitted":
                raise Aidp3DAnswerError("DUPLICATE_SUBMITTED", f"题目 {item_id} 已记录为提交成功，禁止重复提交。", stage="ledger", retryable=False)
            if status == "in_progress":
                blocked = {
                    **existing,
                    "status": "blocked_unknown",
                    "blocked_at": utc_now().isoformat(),
                    "blocked_reason": "发现旧 in_progress，无法确认上次是否已提交，禁止自动重提。",
                }
                _write_json_file(path, blocked)
                raise Aidp3DAnswerError(
                    "LEDGER_IN_PROGRESS_UNKNOWN",
                    f"题目 {item_id} 存在未完成提交记录，已转为 blocked_unknown。",
                    stage="ledger",
                    retryable=False,
                )
        payload = {
            "schema_version": 1,
            "task_id": str(task_id),
            "account_user_id": str(account_user_id),
            "item_id": str(item_id),
            "run_id": str(run_id),
            "status": "in_progress",
            "started_at": utc_now().isoformat(),
        }
        _write_json_file(path, payload)

    def mark_submitted(self, task_id: str, account_user_id: str, item_id: str, *, evidence: dict[str, Any]) -> None:
        self._mark(task_id, account_user_id, item_id, "submitted", evidence=evidence)

    def mark_temp_saved(self, task_id: str, account_user_id: str, item_id: str, *, evidence: dict[str, Any]) -> None:
        self._mark(task_id, account_user_id, item_id, "temp_saved", evidence=evidence)

    def mark_failed(self, task_id: str, account_user_id: str, item_id: str, *, error: str, evidence: Optional[dict[str, Any]] = None) -> None:
        self._mark(task_id, account_user_id, item_id, "failed", error=error, evidence=evidence or {})

    def mark_blocked_unknown(self, task_id: str, account_user_id: str, item_id: str, *, error: str, evidence: Optional[dict[str, Any]] = None) -> None:
        self._mark(task_id, account_user_id, item_id, "blocked_unknown", error=error, evidence=evidence or {})

    def _mark(self, task_id: str, account_user_id: str, item_id: str, status: str, *, error: str = "", evidence: dict[str, Any]) -> None:
        path = self._path(task_id, account_user_id, item_id)
        existing = _load_json_file(path)
        payload = existing if isinstance(existing, dict) else {}
        payload.update(
            {
                "schema_version": 1,
                "task_id": str(task_id),
                "account_user_id": str(account_user_id),
                "item_id": str(item_id),
                "status": status,
                "updated_at": utc_now().isoformat(),
                "error": error,
                "evidence": evidence,
            }
        )
        _write_json_file(path, payload)

    def _path(self, task_id: str, account_user_id: str, item_id: str) -> Path:
        return self.root / _safe_part(task_id) / _safe_part(account_user_id) / f"{_safe_part(item_id)}.json"


class Aidp3DHttpAnswerService:
    def __init__(
        self,
        *,
        transport: Optional[Callable[[dict[str, Any], str, str, dict[str, Any]], dict[str, Any]]] = None,
        qwen_decider: Optional[Callable[[Aidp3DRuntime, dict[str, Any]], dict[str, Any]]] = None,
        runtime_loader: Optional[Callable[[], Aidp3DRuntime]] = None,
        ledger: Optional[Aidp3DLedger] = None,
    ) -> None:
        self.transport = transport or post_aidp_json
        self.qwen_decider = qwen_decider or qwen_decide
        self.runtime_loader = runtime_loader or load_qwen_runtime
        self.ledger = ledger or Aidp3DLedger(default_3d_ledger_root())

    def submit_one(self, *, account: dict[str, Any], account_user_id: str, task_id: str, node_id: str, run_id: str, submit_remote: bool = True) -> dict[str, Any]:
        task_id = str(task_id or AIDP_3D_RUBRIC_TASK_ID)
        node_id = str(node_id or AIDP_3D_RUBRIC_NODE_ID)
        account_context = normalize_account_context(account, task_id=task_id, node_id=node_id)
        before = try_read_current_item_http(account_context, self.transport, task_id=task_id, node_id=node_id)
        if before is None:
            return {
                "attempted": False,
                "success": False,
                "submits_remote": False,
                "readback_ok": False,
                "no_current_item": True,
                "error_code": "NO_CURRENT_ITEM",
                "message": "当前账号没有 3D 当前题，保持后台循环等待新题。",
            }
        item = before["item"]
        content = before["content"]
        item_id = str(item.get("ItemID") or "")
        if not item_id:
            raise Aidp3DAnswerError("TASK_PARSE_FAILED", "当前题缺少 ItemID。", stage="read_current", retryable=True)
        self.ledger.begin(task_id, account_user_id, item_id, run_id=run_id)
        evidence: dict[str, Any] = {
            "attempted": True,
            "success": False,
            "submits_remote": False,
            "readback_ok": False,
            "item_id": item_id,
            "account_user_id": account_user_id,
            "task_id": task_id,
            "node_id": node_id,
            "started_at": utc_now().isoformat(),
        }
        formal_submit_attempted = False

        def fail_with(code: str, message: str, *, stage: str, retryable: bool = False) -> None:
            evidence.update({"error_code": code, "error": message, "message": message})
            raise Aidp3DAnswerError(code, message, stage=stage, retryable=retryable, evidence=evidence)

        try:
            image_requirements = validate_required_images(content)
            runtime = self.runtime_loader()
            decision = self.qwen_decider(runtime, content)
            payload = build_temp_payload(account_context, item_id, content, decision, task_id=task_id, node_id=node_id)
            payload_shape = validate_payload_not_empty(payload)
            temp_result = self.transport(account_context, "api", "/api/dispatch/SubmitTempItemAnswer", payload)
            require_base_ok(temp_result, "SubmitTempItemAnswer")
            if not submit_remote:
                evidence.update(
                    {
                        "success": True,
                        "submits_remote": False,
                        "readback_ok": True,
                        "temp_save_only": True,
                        "saved_to_task_ui": True,
                        "saved_at": utc_now().isoformat(),
                        "qwen_model": runtime.model,
                        "qwen_confidence": decision.get("confidence"),
                        "image_requirements": image_requirements,
                        "payload_shape": payload_shape,
                        "qwen_decision": decision,
                        "temp_result": compact_result(temp_result),
                        "message": "3D HTTP 暂存成功；试运行不执行正式提交。",
                    }
                )
                self.ledger.mark_temp_saved(task_id, account_user_id, item_id, evidence=evidence)
                return evidence
            submit_request = {
                "TaskID": task_id,
                "NodeID": _node_id_value(node_id),
                "Status": 4,
                "Answers": payload["AuditAnswers"],
            }
            receive_request = {"Filter": {"Type": 1, "TaskID": task_id, "NodeID": _node_id_value(node_id), "Count": 1, "StatusList": []}}
            formal_submit_attempted = True
            evidence["submits_remote"] = True
            submit_result = self.transport(
                account_context,
                "api",
                "/api/dispatch/SubmitItemAndReceive",
                {"SubmitItemRequest": submit_request, "ReceiveRequest": receive_request},
            )
            submit_body = submit_result.get("body") if isinstance(submit_result.get("body"), dict) else {}
            submit_code = base_status_code(submit_body.get("SubmitItemResponse"))
            receive_code = base_status_code(submit_body.get("ReceiveResponse"))
            if submit_result.get("statusCode") != 200 or submit_code != 0 or receive_code != 0:
                evidence.update({"temp_result": compact_result(temp_result), "submit_result": compact_result(submit_result)})
                message = f"SubmitItemAndReceive 返回异常：submit={submit_code} receive={receive_code} http={submit_result.get('statusCode')}"
                self.ledger.mark_blocked_unknown(task_id, account_user_id, item_id, error=message, evidence=evidence)
                fail_with("SUBMIT_FAILED", message, stage="submit_answer")
            evidence.update({"temp_result": compact_result(temp_result), "submit_result": compact_result(submit_result)})
            validate_submit_item_response(submit_body, item_id)
            receive_next_item_id = extract_receive_next_item_id(submit_body)
            after = try_read_current_item_http(account_context, self.transport, task_id=task_id, node_id=node_id)
            after_item_id = str(((after or {}).get("item") or {}).get("ItemID") or "")
            readback_result = compact_readback(after)
            if after_item_id and after_item_id == item_id:
                evidence.update({"temp_result": compact_result(temp_result), "submit_result": compact_result(submit_result), "readback_result": readback_result, "after_item_id": after_item_id})
                message = f"提交后回读仍停留在原题 {item_id}。"
                self.ledger.mark_blocked_unknown(task_id, account_user_id, item_id, error=message, evidence=evidence)
                fail_with("READBACK_MISMATCH", message, stage="readback_result")
            if receive_next_item_id and not after_item_id:
                evidence.update({"temp_result": compact_result(temp_result), "submit_result": compact_result(submit_result), "readback_result": readback_result})
                message = f"Receive 已返回下一题 {receive_next_item_id}，但 search 回读未返回当前题。"
                self.ledger.mark_blocked_unknown(task_id, account_user_id, item_id, error=message, evidence=evidence)
                fail_with("READBACK_MISMATCH", message, stage="readback_result")
            if receive_next_item_id and after_item_id and receive_next_item_id != after_item_id:
                evidence.update({"temp_result": compact_result(temp_result), "submit_result": compact_result(submit_result), "readback_result": readback_result, "after_item_id": after_item_id})
                message = f"Receive 下一题与 search 回读不一致：receive={receive_next_item_id} readback={after_item_id}。"
                self.ledger.mark_blocked_unknown(task_id, account_user_id, item_id, error=message, evidence=evidence)
                fail_with("READBACK_MISMATCH", message, stage="readback_result")
            evidence.update(
                {
                    "success": True,
                    "submits_remote": True,
                    "readback_ok": True,
                    "submitted_at": utc_now().isoformat(),
                    "next_item_id": after_item_id or receive_next_item_id,
                    "no_more_items": not bool(after_item_id or receive_next_item_id),
                    "qwen_model": runtime.model,
                    "qwen_confidence": decision.get("confidence"),
                    "image_requirements": image_requirements,
                    "payload_shape": payload_shape,
                    "qwen_decision": decision,
                    "temp_result": compact_result(temp_result),
                    "submit_result": compact_result(submit_result),
                    "readback_result": readback_result,
                    "message": "3D HTTP 提交成功并完成回读。",
                }
            )
            self.ledger.mark_submitted(task_id, account_user_id, item_id, evidence=evidence)
            return evidence
        except Aidp3DAnswerError as exc:
            evidence.update({"error_code": exc.code, "error": str(exc), "message": str(exc)})
            if formal_submit_attempted:
                evidence["submits_remote"] = True
                evidence["readback_ok"] = False
                self.ledger.mark_blocked_unknown(task_id, account_user_id, item_id, error=str(exc), evidence=evidence)
            elif exc.code not in {"LEDGER_IN_PROGRESS_UNKNOWN", "DUPLICATE_SUBMITTED"}:
                self.ledger.mark_failed(task_id, account_user_id, item_id, error=str(exc), evidence=evidence)
            exc.evidence = evidence
            raise
        except Exception as exc:  # noqa: BLE001 - convert unknown failures into explicit worker errors.
            message = str(exc)
            if formal_submit_attempted:
                evidence.update({"submits_remote": True, "readback_ok": False, "error_code": "SUBMIT_FAILED", "error": message, "message": f"正式提交后状态不明：{message}"})
                self.ledger.mark_blocked_unknown(task_id, account_user_id, item_id, error=message, evidence=evidence)
                raise Aidp3DAnswerError("SUBMIT_FAILED", f"正式提交后状态不明：{message}", stage="submit_answer", retryable=False, evidence=evidence) from exc
            self.ledger.mark_failed(task_id, account_user_id, item_id, error=message, evidence=evidence)
            raise Aidp3DAnswerError("UNKNOWN_ERROR", message, stage="unknown", retryable=True, evidence=evidence) from exc


def normalize_account_context(account: dict[str, Any], *, task_id: str, node_id: str) -> dict[str, Any]:
    result = dict(account or {})
    result["template_id"] = resolve_template_id(result, task_id=task_id)
    referer = str(result.get("referer") or result.get("operationUrl") or result.get("operation_url") or "")
    if "/mark-v3/" not in referer:
        result["referer"] = (
            f"https://aidp.juejin.cn/operation/task-v2/{task_id}/mark-v3/{_node_id_value(node_id)}"
            f"?templateID={urllib.parse.quote(str(result['template_id']))}&templateType=1000"
        )
    return result


def resolve_template_id(account: dict[str, Any], *, task_id: str) -> str:
    direct = str(account.get("template_id") or account.get("templateID") or account.get("templateId") or "").strip()
    if direct:
        return direct
    for key in ["referer", "operationUrl", "operation_url", "task_open_url", "taskPageUrl"]:
        raw = str(account.get(key) or "")
        if not raw:
            continue
        parsed = urllib.parse.urlparse(raw)
        template_id = urllib.parse.parse_qs(parsed.query).get("templateID", [""])[0]
        if template_id:
            return str(template_id)
    if str(task_id) == AIDP_3D_RUBRIC_TASK_ID:
        return AIDP_3D_RUBRIC_TEMPLATE_ID
    raise Aidp3DAnswerError("TASK_PARSE_FAILED", "缺少 templateID，不能构造 3D 提交 payload。", stage="prepare_context", retryable=False)


def load_qwen_runtime() -> Aidp3DRuntime:
    path = _resolve_path(get_settings().ai_runtime_config_path)
    data = _load_json_file(path)
    task_ai = data.get("task_ai") if isinstance(data, dict) and isinstance(data.get("task_ai"), dict) else {}
    api_key = str(task_ai.get("api_key") or "").strip()
    base_url = str(task_ai.get("base_url") or "").strip().rstrip("/")
    if not api_key or not base_url:
        raise Aidp3DAnswerError("AI_PROVIDER_502", "task_ai provider 未配置 base_url 或 api_key。", stage="call_provider", retryable=False)
    timeout = int(task_ai.get("timeout_seconds") or 25)
    return Aidp3DRuntime(base_url=base_url, api_key=api_key, model=AIDP_3D_RUBRIC_MODEL, timeout_seconds=timeout)


def qwen_decide(runtime: Aidp3DRuntime, content: dict[str, Any]) -> dict[str, Any]:
    rubrics_obj = content.get("rubrics") if isinstance(content.get("rubrics"), dict) else {}
    rubrics = rubrics_obj.get("rubrics") if isinstance(rubrics_obj.get("rubrics"), list) else []
    if not rubrics:
        raise Aidp3DAnswerError("TASK_PARSE_FAILED", "当前 3D 题面缺少 rubrics。", stage="prepare_context", retryable=True)
    images = build_image_set(content)
    text_payload = {
        "task": "3D一致性人工标注",
        "case_id": content.get("id"),
        "category": content.get("category"),
        "target_summary": rubrics_obj.get("target_summary"),
        "rubric_design_note": rubrics_obj.get("rubric_design_note"),
        "rubrics": rubrics,
        "output_schema": {
            "rubrics_reasonable": True,
            "rubrics_reasonable_reason": "合理",
            "rubric_items": [{"rubric_id": "S1-B1", "verdict": "satisfied|unsatisfied", "reason": "不满足时必填，满足可空"}],
            "dimension_scores": {
                "S1": {"score": 1, "reason": "形体评分理由"},
                "S2": {"score": 1, "reason": "结构召回评分理由"},
                "A": {"score": 1, "reason": "材质与颜色评分理由"},
            },
            "discard": {"selected": False, "reason": ""},
            "evidence_summary": "一句话总结关键证据",
            "confidence": "high|medium|low",
        },
        "hard_rules": [
            "必须逐条输出所有 rubrics，rubric_id 必须与输入完全一致。",
            "verdict 只能是 satisfied 或 unsatisfied。",
            "unsatisfied 必须写具体可见原因；满足项 reason 可空。",
            "Rubrics 合理时 rubrics_reasonable=true 且 reason 固定为“合理”。",
            "Rubrics 不合理时 rubrics_reasonable=false 且必须写简短不合理原因，不能写“合理”。",
            "S1/S2/A 分数必须是 1-5 整数，理由简短具体。",
            "只根据图片与 rubrics 判断，不要输出 Markdown。",
        ],
        "image_order": [img["label"] for img in images],
    }
    content_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": "按以下 JSON 输入和图片完成 3D 标注，只输出严格 JSON：\n" + json.dumps(text_payload, ensure_ascii=False)}
    ]
    for image in images:
        content_blocks.append({"type": "text", "text": image["label"]})
        content_blocks.append({"type": "image_url", "image_url": {"url": image["data_url"]}})
    payload = {
        "model": runtime.model,
        "messages": [
            {"role": "system", "content": "你是严谨的 AIDP 3D Rubric 标注员，只输出 JSON。"},
            {"role": "user", "content": content_blocks},
        ],
        "temperature": 0,
        "max_tokens": 2200,
        "response_format": {"type": "json_object"},
    }
    try:
        response = requests.post(
            runtime.base_url + "/chat/completions",
            headers={"Authorization": "Bearer " + runtime.api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=max(90, runtime.timeout_seconds * 4),
        )
    except requests.Timeout as exc:
        raise Aidp3DAnswerError("AI_PROVIDER_TIMEOUT", "qwen3-vl-plus 请求超时。", stage="call_provider", retryable=True) from exc
    except requests.RequestException as exc:
        raise Aidp3DAnswerError("AI_PROVIDER_502", f"qwen3-vl-plus 请求失败：{exc}", stage="call_provider", retryable=True) from exc
    if not response.ok:
        raise Aidp3DAnswerError("AI_PROVIDER_502", f"qwen3-vl-plus HTTP {response.status_code}。", stage="call_provider", retryable=True)
    try:
        data = response.json()
        raw = str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))
        decision = parse_json_object(raw)
    except Exception as exc:  # noqa: BLE001 - normalize provider parse failures.
        raise Aidp3DAnswerError("AI_RESPONSE_INVALID", f"qwen 输出不是合法 JSON：{exc}", stage="parse_answer", retryable=True) from exc
    validate_decision_against_content(decision, content)
    decision["_image_count"] = len(images)
    decision["_image_bytes"] = sum(int(img.get("bytes") or 0) for img in images)
    return decision


def build_image_set(content: dict[str, Any]) -> list[dict[str, Any]]:
    validate_required_images(content)
    images: list[tuple[str, str]] = []
    ref_url = ((content.get("ref_img") or {}) if isinstance(content.get("ref_img"), dict) else {}).get("tos_url")
    latest_url = ((content.get("latest_screenshot") or {}) if isinstance(content.get("latest_screenshot"), dict) else {}).get("tos_url")
    images.append(("参考图", str(ref_url)))
    images.append(("候选最新截图", str(latest_url)))
    views = content.get("artifact_views") if isinstance(content.get("artifact_views"), dict) else {}
    for key, label in [
        ("front", "候选正视角"),
        ("three_quarter", "候选三分之四视角"),
        ("right", "候选右视角"),
        ("back", "候选背视角"),
        ("top", "候选顶视角"),
    ]:
        value = views.get(key) if isinstance(views.get(key), dict) else {}
        url = value.get("tos_url")
        if url:
            images.append((label, str(url)))
    result = []
    seen = set()
    for label, url in images:
        if url in seen:
            continue
        seen.add(url)
        result.append(compact_image(url, label))
        if len(result) >= 7:
            break
    return result


def compact_image(url: str, label: str, max_side: int = 960) -> dict[str, Any]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    raw = response.content
    mime = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if Image is not None:
        try:
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            image.thumbnail((max_side, max_side))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82, optimize=True)
            raw = buffer.getvalue()
            mime = "image/jpeg"
        except Exception:
            pass
    if not mime:
        mime = mimetypes.guess_type(url)[0] or "image/png"
    return {"label": label, "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}", "bytes": len(raw)}


def read_current_item_http(account: dict[str, Any], transport: Callable[[dict[str, Any], str, str, dict[str, Any]], dict[str, Any]], *, task_id: str, node_id: str) -> dict[str, Any]:
    result = transport(account, "agw", "/dispatcher/search_item/category", category_body(task_id, node_id))
    require_base_ok(result, "search_item/category")
    body = result["body"]
    data = body.get("Data") if isinstance(body.get("Data"), list) else []
    if not data:
        raise Aidp3DAnswerError("NO_CURRENT_ITEM", "search_item/category 未返回当前题。", stage="read_current", retryable=True)
    item = data[0]
    content = json.loads(item.get("Content") or "{}")
    return {"raw_result": result, "category": body, "item": item, "content": content}


def try_read_current_item_http(account: dict[str, Any], transport: Callable[[dict[str, Any], str, str, dict[str, Any]], dict[str, Any]], *, task_id: str, node_id: str) -> Optional[dict[str, Any]]:
    try:
        return read_current_item_http(account, transport, task_id=task_id, node_id=node_id)
    except Aidp3DAnswerError as exc:
        if exc.code == "NO_CURRENT_ITEM":
            return None
        raise


def category_body(task_id: str, node_id: str) -> dict[str, Any]:
    return {
        "TaskID": str(task_id),
        "NodeID": _node_id_value(node_id),
        "ItemCategoryType": 0,
        "Filter": {},
        "PageRequest": {"PageNo": 0, "PageSize": 1},
    }


def post_aidp_json(account: dict[str, Any], kind: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    response = requests.post(f"https://aidp.juejin.cn{path}", headers=aidp_headers(account, kind), json=body, timeout=60)
    text = response.text
    try:
        parsed: Any = response.json()
    except Exception:
        parsed = {"parseError": "non-json-response"}
    return {"statusCode": response.status_code, "elapsedMs": round((time.perf_counter() - started) * 1000), "body": parsed, "text": text[:1200]}


def aidp_headers(account: dict[str, Any], kind: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://aidp.juejin.cn",
        "Referer": str(account.get("referer") or ""),
        "Cookie": str(account.get("cookie") or ""),
        "User-Agent": str(account.get("userAgent") or account.get("user_agent") or "")
        or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
    }
    if kind == "api":
        headers.update({"x-secsdk-csrf-token": "DOWNGRADE", "x-backend-org-id": "100", "x-web-org-id": "100"})
    else:
        headers.update({"Agw-Js-Conv": "str", "X-JS-REQ": "1", "X-Backend-Side": "4", "X-Backend-Org-Id": "100"})
    return headers


def build_temp_payload(account: dict[str, Any], item_id: str, content: dict[str, Any], decision: dict[str, Any], *, task_id: str, node_id: str) -> dict[str, Any]:
    validate_decision_against_content(decision, content)
    data = build_answer_data(decision, content)
    template_id = resolve_template_id(account, task_id=task_id)
    answer_content = {
        "item": content,
        "templateID": template_id,
        "type": "neeko",
        "data": data,
        "dataMap": copy.deepcopy(data),
        "itemID": item_id,
        "isAbandoned": False,
    }
    answer = {
        "ItemID": item_id,
        "Content": json.dumps(answer_content, ensure_ascii=False, separators=(",", ":")),
        "ControlData": json.dumps({"Discard": False, "extraAnswer": [], "discard": False}, ensure_ascii=False, separators=(",", ":")),
    }
    return {"AuditAnswers": [answer], "NodeID": str(node_id), "StagingTime": "604800", "TaskID": str(task_id)}


def build_answer_data(decision: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    rubrics_obj = content.get("rubrics") if isinstance(content.get("rubrics"), dict) else {}
    rubrics = rubrics_obj.get("rubrics") if isinstance(rubrics_obj.get("rubrics"), list) else []
    decision_items = decision.get("rubric_items") if isinstance(decision.get("rubric_items"), list) else []
    rubric_results = []
    for index, rubric in enumerate(rubrics):
        item = decision_items[index]
        passed = 1 if item.get("verdict") == "satisfied" else 0
        row = {"rubricId": str(rubric.get("id")), "rubricPass": passed}
        if not passed:
            reason = str(item.get("reason") or "").strip()
            if not reason:
                raise Aidp3DAnswerError("AI_RESPONSE_INVALID", f"{rubric.get('id')} 缺少不满足原因。", stage="parse_answer", retryable=True)
            row["failReason"] = reason
        rubric_results.append(row)
    dims = decision["dimension_scores"]
    reasonable = decision.get("rubrics_reasonable")
    rubrics_reason = "合理" if reasonable is not False else str(decision.get("rubrics_reasonable_reason") or "").strip()
    return {
        "discard": False,
        "discard_remark": "",
        "__internalData__": {"discard": False},
        "caseId": str(content.get("id") or ""),
        "rubricResults": rubric_results,
        "qualityCheckAdviceHistory": [],
        "rubricsReasonable": 0 if reasonable is False else 1,
        "rubricsReason": rubrics_reason,
        "s1Score": int(dims["S1"]["score"]),
        "s1Reason": str(dims["S1"]["reason"]).strip(),
        "s2Score": int(dims["S2"]["score"]),
        "s2Reason": str(dims["S2"]["reason"]).strip(),
        "aScore": int(dims["A"]["score"]),
        "aReason": str(dims["A"]["reason"]).strip(),
    }


def validate_decision_against_content(decision: dict[str, Any], content: dict[str, Any]) -> None:
    rubrics_obj = content.get("rubrics") if isinstance(content.get("rubrics"), dict) else {}
    rubrics = rubrics_obj.get("rubrics") if isinstance(rubrics_obj.get("rubrics"), list) else []
    if not rubrics:
        raise Aidp3DAnswerError("TASK_PARSE_FAILED", "题面缺少 rubrics。", stage="prepare_context", retryable=True)
    items = decision.get("rubric_items")
    if not isinstance(items, list):
        raise Aidp3DAnswerError("AI_RESPONSE_INVALID", "qwen 输出缺少 rubric_items。", stage="parse_answer", retryable=True)
    expected_ids = [str(rubric.get("id") or "") for rubric in rubrics]
    got_ids = [str(item.get("rubric_id") or "") for item in items if isinstance(item, dict)]
    if got_ids != expected_ids:
        raise Aidp3DAnswerError("AI_RESPONSE_INVALID", f"rubric id 顺序不一致：expected={expected_ids}, got={got_ids}", stage="parse_answer", retryable=True)
    for item in items:
        verdict = str(item.get("verdict") or "").strip()
        if verdict not in {"satisfied", "unsatisfied"}:
            raise Aidp3DAnswerError("AI_RESPONSE_INVALID", f"{item.get('rubric_id')} verdict 非法：{verdict}", stage="parse_answer", retryable=True)
        if verdict == "unsatisfied" and not str(item.get("reason") or "").strip():
            raise Aidp3DAnswerError("AI_RESPONSE_INVALID", f"{item.get('rubric_id')} 缺少不满足原因。", stage="parse_answer", retryable=True)
    confidence = str(decision.get("confidence") or "").strip().lower()
    if confidence != "high":
        raise Aidp3DAnswerError("LOW_CONFIDENCE", f"qwen confidence 必须为 high，当前为 {confidence or '<missing>'}。", stage="parse_answer", retryable=True)
    reasonable = decision.get("rubrics_reasonable")
    if reasonable not in (True, False):
        raise Aidp3DAnswerError("AI_RESPONSE_INVALID", "rubrics_reasonable 必须是布尔值。", stage="parse_answer", retryable=True)
    reason = str(decision.get("rubrics_reasonable_reason") or "").strip()
    if reasonable is False and (not reason or reason == "合理"):
        raise Aidp3DAnswerError("AI_RESPONSE_INVALID", "rubrics_reasonable=false 时必须给出非“合理”的原因。", stage="parse_answer", retryable=True)
    dims = decision.get("dimension_scores")
    if not isinstance(dims, dict):
        raise Aidp3DAnswerError("AI_RESPONSE_INVALID", "qwen 输出缺少 dimension_scores。", stage="parse_answer", retryable=True)
    for key in ["S1", "S2", "A"]:
        part = dims.get(key)
        if not isinstance(part, dict):
            raise Aidp3DAnswerError("AI_RESPONSE_INVALID", f"qwen 输出缺少 {key} 评分。", stage="parse_answer", retryable=True)
        try:
            score = int(part.get("score"))
        except (TypeError, ValueError) as exc:
            raise Aidp3DAnswerError("AI_RESPONSE_INVALID", f"{key} score 非整数。", stage="parse_answer", retryable=True) from exc
        if score < 1 or score > 5:
            raise Aidp3DAnswerError("AI_RESPONSE_INVALID", f"{key} score 越界：{score}。", stage="parse_answer", retryable=True)
        if not str(part.get("reason") or "").strip():
            raise Aidp3DAnswerError("AI_RESPONSE_INVALID", f"{key} 缺少评分理由。", stage="parse_answer", retryable=True)


def validate_required_images(content: dict[str, Any]) -> dict[str, Any]:
    ref_url = ((content.get("ref_img") or {}) if isinstance(content.get("ref_img"), dict) else {}).get("tos_url")
    latest_url = ((content.get("latest_screenshot") or {}) if isinstance(content.get("latest_screenshot"), dict) else {}).get("tos_url")
    views = content.get("artifact_views") if isinstance(content.get("artifact_views"), dict) else {}
    view_urls = {
        key: str(value.get("tos_url") or "")
        for key, value in views.items()
        if isinstance(value, dict) and value.get("tos_url")
    }
    if not ref_url:
        raise Aidp3DAnswerError("MISSING_REQUIRED_IMAGE", "缺少 ref_img.tos_url。", stage="prepare_context", retryable=True)
    if not latest_url:
        raise Aidp3DAnswerError("MISSING_REQUIRED_IMAGE", "缺少 latest_screenshot.tos_url。", stage="prepare_context", retryable=True)
    if len(view_urls) < 3:
        raise Aidp3DAnswerError("MISSING_REQUIRED_IMAGE", f"artifact_views 至少需要 3 个视角，当前 {len(view_urls)}。", stage="prepare_context", retryable=True)
    return {"hasRef": True, "hasLatestScreenshot": True, "artifactViewCount": len(view_urls), "artifactViewKeys": sorted(view_urls.keys())}


def validate_payload_not_empty(payload: dict[str, Any]) -> dict[str, Any]:
    answers = payload.get("AuditAnswers") if isinstance(payload.get("AuditAnswers"), list) else []
    if len(answers) != 1:
        raise Aidp3DAnswerError("SUBMIT_FAILED", "payload AuditAnswers 必须且只能有 1 条。", stage="submit_answer", retryable=False)
    content = json.loads(answers[0].get("Content") or "{}")
    data = content.get("data") if isinstance(content.get("data"), dict) else {}
    data_map = content.get("dataMap") if isinstance(content.get("dataMap"), dict) else {}
    if data_map != data:
        raise Aidp3DAnswerError("SUBMIT_FAILED", "payload dataMap 必须等于 data。", stage="submit_answer", retryable=False)
    rubric_results = data.get("rubricResults") if isinstance(data.get("rubricResults"), list) else []
    if not rubric_results:
        raise Aidp3DAnswerError("SUBMIT_FAILED", "payload rubricResults 为空。", stage="submit_answer", retryable=False)
    for key in ["rubricsReason", "s1Reason", "s2Reason", "aReason"]:
        if not str(data.get(key) or "").strip():
            raise Aidp3DAnswerError("SUBMIT_FAILED", f"payload 缺少 {key}。", stage="submit_answer", retryable=False)
    for key in ["s1Score", "s2Score", "aScore"]:
        score = int(data.get(key))
        if score < 1 or score > 5:
            raise Aidp3DAnswerError("SUBMIT_FAILED", f"payload {key} 越界：{score}。", stage="submit_answer", retryable=False)
    return {
        "rubricResultsCount": len(rubric_results),
        "scores": {"s1": data["s1Score"], "s2": data["s2Score"], "a": data["aScore"]},
        "answerKeys": sorted(data.keys()),
    }


def validate_submit_item_response(submit_body: dict[str, Any], item_id: str) -> None:
    submit_response = submit_body.get("SubmitItemResponse") if isinstance(submit_body.get("SubmitItemResponse"), dict) else {}
    errors = submit_response.get("Errors")
    if isinstance(errors, list) and errors:
        raise Aidp3DAnswerError("SUBMIT_FAILED", f"SubmitItemResponse.Errors 非空：{errors[:3]}", stage="submit_answer", retryable=False)
    ans_versions = submit_response.get("AnsVersions")
    if not isinstance(ans_versions, list) or not ans_versions:
        raise Aidp3DAnswerError("READBACK_MISMATCH", "SubmitItemResponse 缺少 AnsVersions。", stage="readback_result", retryable=False)
    matched = None
    for item in ans_versions:
        if not isinstance(item, dict):
            continue
        if str(item.get("ItemID") or item.get("ItemId") or item.get("item_id") or "") == item_id:
            matched = item
            break
    if matched is None:
        raise Aidp3DAnswerError("READBACK_MISMATCH", f"AnsVersions 不包含当前题 {item_id}。", stage="readback_result", retryable=False)
    if matched.get("AnsModified") is False:
        raise Aidp3DAnswerError("READBACK_MISMATCH", f"AnsVersions 标记当前题未修改：{item_id}。", stage="readback_result", retryable=False)


def extract_receive_next_item_id(submit_body: dict[str, Any]) -> str:
    receive = submit_body.get("ReceiveResponse") if isinstance(submit_body.get("ReceiveResponse"), dict) else {}
    items = receive.get("Items") if isinstance(receive.get("Items"), list) else []
    if not items:
        return ""
    first = items[0] if isinstance(items[0], dict) else {}
    item = first.get("Item") if isinstance(first.get("Item"), dict) else first
    return str(item.get("ItemID") or "")


def require_base_ok(result: dict[str, Any], label: str) -> None:
    if result.get("statusCode") != 200 or base_status_code(result.get("body")) != 0:
        if label == "SubmitTempItemAnswer":
            raise Aidp3DAnswerError("SUBMIT_FAILED", f"{label} 返回异常：{compact_result(result)}", stage="temp_save", retryable=False)
        raise Aidp3DAnswerError("TASK_PAGE_AUTH_EXPIRED", f"{label} 返回异常：{compact_result(result)}", stage="read_current", retryable=True)


def base_status_code(body: Any) -> Optional[int]:
    if not isinstance(body, dict):
        return None
    base = body.get("BaseResp") if isinstance(body.get("BaseResp"), dict) else {}
    try:
        return int(base.get("StatusCode"))
    except (TypeError, ValueError):
        return None


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body") if isinstance(result.get("body"), dict) else {}
    compact_body: dict[str, Any] = {}
    for key in ["BaseResp", "SubmitItemResponse", "ReceiveResponse"]:
        value = body.get(key)
        if key == "ReceiveResponse" and isinstance(value, dict):
            compact_body[key] = {
                "BaseResp": value.get("BaseResp"),
                "ItemsCount": len(value.get("Items") or []) if isinstance(value.get("Items"), list) else 0,
                "FirstItemID": extract_receive_next_item_id({"ReceiveResponse": value}),
            }
        elif key == "SubmitItemResponse" and isinstance(value, dict):
            ans_versions = value.get("AnsVersions") if isinstance(value.get("AnsVersions"), list) else []
            compact_body[key] = {
                "BaseResp": value.get("BaseResp"),
                "ErrorsCount": len(value.get("Errors") or []) if isinstance(value.get("Errors"), list) else 0,
                "AnsVersions": [
                    {"ItemID": item.get("ItemID") or item.get("ItemId") or item.get("item_id"), "AnsModified": item.get("AnsModified")}
                    for item in ans_versions[:5]
                    if isinstance(item, dict)
                ],
            }
        elif key == "BaseResp" and isinstance(value, dict):
            compact_body[key] = value
    if not compact_body and body.get("parseError"):
        compact_body["parseError"] = body.get("parseError")
    return {"statusCode": result.get("statusCode"), "elapsedMs": result.get("elapsedMs"), "body": compact_body, "text": result.get("text")}


def compact_readback(readback: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not readback:
        return {"no_current_item": True, "item_id": "", "raw_result": {}}
    item = readback.get("item") if isinstance(readback.get("item"), dict) else {}
    return {
        "no_current_item": False,
        "item_id": str(item.get("ItemID") or ""),
        "raw_result": compact_result(readback.get("raw_result") if isinstance(readback.get("raw_result"), dict) else {}),
    }


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise Aidp3DAnswerError("AI_RESPONSE_INVALID", "qwen 输出不是 JSON object。", stage="parse_answer", retryable=True)
    return parsed


def default_3d_ledger_root() -> Path:
    return _data_root() / "production-runs" / "3d-rubric-ledger"


def _data_root() -> Path:
    production_state = _resolve_path(get_settings().production_state_path)
    if production_state.name:
        return production_state.parent
    return Path.cwd() / "data"


def _node_id_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(AIDP_3D_RUBRIC_NODE_ID)


def _load_json_file(path: Path) -> Any:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _safe_part(value: str) -> str:
    safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})
    return safe or "unknown"
