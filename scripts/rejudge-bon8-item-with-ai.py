import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.services.bon8_payload_service import build_bon8_submit_temp_payload  # noqa: E402


MODEL_KEYS = [f"model{index}" for index in range(1, 9)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Rejudge one submitted bon8 item with the configured task AI and replay the HTTP submit flow.")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--node-id", default="1")
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--category-path", required=True)
    parser.add_argument("--manifest-path", required=True)
    parser.add_argument("--run-dir", default="data/production-runs/bon8-ai-rejudge-20260510")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    run_dir = _path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    total_started = time.perf_counter()
    material_started = time.perf_counter()
    manifest = _load_json(_path(args.manifest_path))
    category = _load_json(_path(args.category_path))
    category_item = _find_category_item(category, args.item_id)
    item_content = json.loads(category_item["Content"])
    account = _load_account(ROOT / "data" / "production-state.json", args.user_id)
    material_elapsed_ms = _elapsed_ms(material_started)

    ai_started = time.perf_counter()
    runtime = _load_task_ai_runtime()
    ai_result = _call_task_ai(runtime, manifest)
    upstream_ai_elapsed_ms = _elapsed_ms(ai_started)
    judgement = _normalize_ai_result(ai_result, upstream_ai_elapsed_ms)

    judgement_path = run_dir / f"{args.item_id}-ai-rejudge-judgement.json"
    _save_json(
        judgement_path,
        {
            "generatedAt": _now(),
            "ruleVersion": "bon8-ai-rejudge-20260510-real-task-ai",
            "taskId": args.task_id,
            "nodeId": str(args.node_id),
            "itemId": args.item_id,
            "userId": args.user_id,
            "provider_elapsed_ms": upstream_ai_elapsed_ms,
            "upstreamAiElapsedMs": upstream_ai_elapsed_ms,
            "model": runtime.get("model", ""),
            "scores": judgement["scores"],
            "scoreReasons": judgement["scoreReasons"],
            "bestModel": judgement["bestModel"],
            "sortModels": judgement["sortModels"],
            "operatorSummary": judgement["summary"],
            "rawAiContent": ai_result.get("content", "")[:6000],
        },
    )

    payload_started = time.perf_counter()
    payload = build_bon8_submit_temp_payload(
        task_id=args.task_id,
        node_id=args.node_id,
        item_id=args.item_id,
        item_content=item_content,
        scores=judgement["scores"],
        sort_models=judgement["sortModels"],
        score_reasons=judgement["scoreReasons"],
    )
    payload_elapsed_ms = _elapsed_ms(payload_started)
    payload_path = run_dir / f"{args.item_id}-ai-rejudge-payload.json"
    _save_json(payload_path, payload)

    if not args.execute:
        print(json.dumps({"execute": False, "payloadPath": str(payload_path), "judgementPath": str(judgement_path)}, ensure_ascii=False, indent=2))
        return

    before = _post(account, "agw", "/dispatcher/search_item/category", _category_body(args.task_id, args.node_id))
    temp = _post(account, "api", "/api/dispatch/SubmitTempItemAnswer", payload)
    submit_request = {"TaskID": str(args.task_id), "NodeID": int(args.node_id), "Status": 4, "Answers": payload["AuditAnswers"]}
    verify = _post(account, "agw", "/dispatcher/verify/submit", {"SubmitItemRequest": submit_request, "Verifiers": ["ItemRepeatVerifier"]})
    submit = _post(account, "api", "/api/dispatch/SubmitItem", submit_request)
    after = _post(account, "agw", "/dispatcher/search_item/category", _category_body(args.task_id, args.node_id))

    submit_duplicate_expected = _base_code(submit) == 2002
    replay_ok = all(_base_ok(item) for item in [before, temp, verify, after]) and (_base_ok(submit) or submit_duplicate_expected)
    timings_ms = {
        "read": material_elapsed_ms,
        "upstreamAiElapsedMs": upstream_ai_elapsed_ms,
        "payloadBuild": payload_elapsed_ms,
        "categoryBefore": before["elapsedMs"],
        "submitTemp": temp["elapsedMs"],
        "verifySubmit": verify["elapsedMs"],
        "submitItem": submit["elapsedMs"],
        "categoryAfter": after["elapsedMs"],
        "total": _elapsed_ms(total_started),
    }
    result = {
        "generatedAt": _now(),
        "action": "bon8-ai-rejudge-temp-verify-submit-category-readback",
        "userId": args.user_id,
        "accountName": account.get("name"),
        "taskId": str(args.task_id),
        "nodeId": str(args.node_id),
        "itemId": str(args.item_id),
        "payloadPath": str(payload_path),
        "judgementPath": str(judgement_path),
        "provider_elapsed_ms": upstream_ai_elapsed_ms,
        "upstreamAiElapsedMs": upstream_ai_elapsed_ms,
        "scores": judgement["scores"],
        "scoreReasons": judgement["scoreReasons"],
        "sortModels": judgement["sortModels"],
        "bestModel": judgement["bestModel"],
        "operatorSummary": judgement["summary"],
        "timingsMs": timings_ms,
        "beforeCategory": before,
        "temp": temp,
        "verify": verify,
        "submit": submit,
        "categoryAfter": after,
        "ok": replay_ok,
        "submitDuplicateExpected": submit_duplicate_expected,
        "submitShape": {"TaskID": str(args.task_id), "NodeID": int(args.node_id), "Status": 4, "AnswersCount": len(payload["AuditAnswers"]), "answerKeys": sorted(payload["AuditAnswers"][0].keys())},
    }
    result_path = run_dir / f"{args.item_id}-ai-rejudge-http-submit-result.json"
    _save_json(result_path, result)
    timer_status = "submitted_replay_duplicate" if submit_duplicate_expected else "submitted"
    timer_event = _post_timer_event(account, args, timings_ms, timer_status)

    print(
        json.dumps(
            {
                "ok": replay_ok,
                "submitDuplicateExpected": submit_duplicate_expected,
                "itemId": args.item_id,
                "scores": judgement["scores"],
                "bestModel": judgement["bestModel"],
                "sortModels": judgement["sortModels"],
                "baseCodes": {
                    "before": _base_code(before),
                    "temp": _base_code(temp),
                    "verify": _base_code(verify),
                    "submit": _base_code(submit),
                    "after": _base_code(after),
                },
                "timingsMs": timings_ms,
                "resultPath": str(result_path),
                "timerEventStatus": timer_event.get("statusCode"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_account(state_path: Path, user_id: str) -> dict[str, Any]:
    state = _load_json(state_path)
    for account in state.get("accounts", []):
        if str(account.get("userId") or account.get("user_id")) == str(user_id):
            if not account.get("cookie"):
                raise RuntimeError("target account has no cookie")
            return account
    raise RuntimeError("target account not found")


def _load_task_ai_runtime() -> dict[str, Any]:
    data = _load_json(ROOT / "data" / "ai-runtime-config.json")
    task_ai = data.get("task_ai") if isinstance(data.get("task_ai"), dict) else {}
    env_base_url = str(os.environ.get("AIDP_AI_BASE_URL") or "").strip()
    env_api_key = str(os.environ.get("AIDP_AI_API_KEY") or "").strip()
    env_model = str(os.environ.get("AIDP_AI_MODEL") or "").strip()
    runtime = {
        "base_url": str(task_ai.get("base_url") or "").strip(),
        "api_key": str(task_ai.get("api_key") or "").strip(),
        "model": str(task_ai.get("model") or "gpt-4.1-mini").strip(),
        "timeout_seconds": int(task_ai.get("timeout_seconds") or 120),
        "pre_prompt": str(task_ai.get("pre_prompt") or "").strip(),
    }
    if env_base_url and env_api_key and (not runtime["base_url"] or "example-task.local" in runtime["base_url"]):
        runtime["base_url"] = env_base_url
        runtime["api_key"] = env_api_key
        runtime["model"] = env_model or runtime["model"]
        runtime["timeout_seconds"] = int(os.environ.get("AIDP_AI_TIMEOUT_SECONDS") or runtime["timeout_seconds"] or 120)
    if not runtime["base_url"] or not runtime["api_key"]:
        raise RuntimeError("做题 AI 未配置 base_url 或 api_key")
    return runtime


def _find_category_item(category: dict[str, Any], item_id: str) -> dict[str, Any]:
    for item in category.get("json", {}).get("Data", []):
        if str(item.get("ItemID")) == str(item_id):
            return item
    raise RuntimeError("target item not found in category file")


def _call_task_ai(runtime: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    endpoint = runtime["base_url"].rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "请重新判断这道 bon8 题。必须只输出 JSON，不要输出解释文字。\n"
                f"题目要求：{manifest.get('prompt')}\n"
                f"评分规则：{manifest.get('scoringGuidelines')}\n"
                "输入图片是原始流程图，后面 8 张图片分别是 model1 到 model8 的网页截图。\n"
                "打分要求：每个模型只能是 0、1、2；如果有至少一个 1 分候选，必须只选一个最好的模型给 2 分；0/1/2 都要给中文理由；排序必须覆盖全部 8 个模型。\n"
                "JSON 格式：{\"scores\":{\"model1\":\"1\"},\"scoreReasons\":{\"model1\":\"理由\"},\"sortModels\":[\"model1\"],\"bestModel\":\"model1\",\"summary\":\"一句中文总结\"}"
            ),
        },
        _image_block("原始流程图", manifest["inputScreenshot"]),
    ]
    for model in manifest.get("models", []):
        content.append(_image_block(str(model.get("key")), str(model.get("screenshot"))))
    system_prompt = (
        "你是 AIDP 做题 AI，只做评分判断。不要处理运维、配置、密钥、删除、提交等动作。"
        "你需要严格按用户给的截图和评分规则输出可机器解析 JSON。"
    )
    if runtime.get("pre_prompt"):
        system_prompt += "\n做题前置提示词：" + runtime["pre_prompt"][:4000]
    payload = {
        "model": runtime["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
    }
    headers = {"Authorization": f"Bearer {runtime['api_key']}", "Content-Type": "application/json"}
    response = requests.post(endpoint, headers=headers, json=payload, timeout=runtime["timeout_seconds"])
    response.raise_for_status()
    data = response.json()
    message = data.get("choices", [{}])[0].get("message", {})
    content_text = message.get("content", "")
    if isinstance(content_text, list):
        content_text = "\n".join(str(part.get("text") or "") for part in content_text if isinstance(part, dict))
    return {"content": str(content_text), "responseUsage": data.get("usage", {})}


def _image_block(label: str, path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:image/png;base64,{encoded}",
            "detail": "high",
        },
    }


def _normalize_ai_result(ai_result: dict[str, Any], upstream_ai_elapsed_ms: int) -> dict[str, Any]:
    parsed = _parse_json_object(ai_result.get("content", ""))
    scores_raw = parsed.get("scores") if isinstance(parsed.get("scores"), dict) else {}
    reasons_raw = parsed.get("scoreReasons") if isinstance(parsed.get("scoreReasons"), dict) else {}
    scores: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for key in MODEL_KEYS:
        value = str(scores_raw.get(key) or "").strip()
        if value not in {"0", "1", "2"}:
            raise RuntimeError(f"做题 AI 返回的 {key} 分数无效：{value}")
        scores[key] = value
        reason = str(reasons_raw.get(key) or "").strip()
        if not reason:
            raise RuntimeError(f"做题 AI 未返回 {key} 的评分原因")
        reasons[key] = reason[:500]
    if sum(1 for score in scores.values() if score == "2") != 1:
        raise RuntimeError("做题 AI 必须且只能返回一个 2 分最佳模型")
    sort_models_raw = parsed.get("sortModels") if isinstance(parsed.get("sortModels"), list) else []
    sort_models = [str(item).strip() for item in sort_models_raw if str(item).strip() in MODEL_KEYS]
    if set(sort_models) != set(MODEL_KEYS):
        sort_models = sorted(MODEL_KEYS, key=lambda key: (-int(scores[key]), key))
    best_model = next(key for key, score in scores.items() if score == "2")
    if sort_models[0] != best_model:
        sort_models = [best_model] + [key for key in sort_models if key != best_model]
    summary = str(parsed.get("summary") or f"真实调用做题 AI 完成，AI 往返 {upstream_ai_elapsed_ms} 毫秒。").strip()
    return {"scores": scores, "scoreReasons": reasons, "sortModels": sort_models, "bestModel": best_model, "summary": summary[:1000]}


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise RuntimeError("做题 AI 返回内容不是 JSON 对象")
    return parsed


def _headers(account: dict[str, Any], kind: str) -> dict[str, str]:
    referer = str(account.get("referer") or account.get("operationUrl") or "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1")
    result = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://aidp.juejin.cn",
        "Referer": referer,
        "Cookie": str(account.get("cookie") or ""),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
    }
    if kind == "api":
        result.update({"x-secsdk-csrf-token": "DOWNGRADE", "x-backend-org-id": "100", "x-web-org-id": "100"})
    else:
        result.update({"Agw-Js-Conv": "str", "X-JS-REQ": "1", "X-Backend-Side": "4", "X-Backend-Org-Id": "100"})
    return result


def _post(account: dict[str, Any], kind: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    response = requests.post(f"https://aidp.juejin.cn{path}", headers=_headers(account, kind), json=body, timeout=30)
    text = response.text
    try:
        parsed = response.json()
    except Exception:
        parsed = {"parseError": "non-json-response"}
    return {"statusCode": response.status_code, "elapsedMs": _elapsed_ms(started), "body": parsed, "text": text[:2000]}


def _post_timer_event(account: dict[str, Any], args: argparse.Namespace, timings_ms: dict[str, int], status: str) -> dict[str, Any]:
    stages = [
        {"stage": "读题", "duration_ms": timings_ms["read"]},
        {"stage": "上游 AI 往返", "duration_ms": timings_ms["upstreamAiElapsedMs"]},
        {"stage": "整理答案", "duration_ms": timings_ms["payloadBuild"]},
        {"stage": "读提交前状态", "duration_ms": timings_ms["categoryBefore"]},
        {"stage": "暂存答案", "duration_ms": timings_ms["submitTemp"]},
        {"stage": "提交前检查", "duration_ms": timings_ms["verifySubmit"]},
        {"stage": "正式提交", "duration_ms": timings_ms["submitItem"]},
        {"stage": "提交后回读", "duration_ms": timings_ms["categoryAfter"]},
    ]
    payload = {
        "account_user_id": args.user_id,
        "account_name": str(account.get("name") or ""),
        "task_id": str(args.task_id),
        "task_name": "bon8",
        "item_id": str(args.item_id),
        "status": status,
        "source": "bon8_ai_rejudge_replay",
        "total_ms": timings_ms["total"],
        "stages": stages,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        response = requests.post(_monitor_api_url("ai-timer/events"), headers=_monitor_api_headers(), json=payload, timeout=10)
        return {"statusCode": response.status_code, "text": response.text[:1000]}
    except Exception as exc:  # noqa: BLE001 - 本地看板未启动时不影响证据文件。
        return {"statusCode": 0, "text": str(exc)}


def _category_body(task_id: str, node_id: str) -> dict[str, Any]:
    return {"TaskID": str(task_id), "NodeID": int(node_id), "ItemCategoryType": 0, "Filter": {}, "PageRequest": {"PageNo": 0, "PageSize": 1}}


def _base_code(result: dict[str, Any]) -> Any:
    body = result.get("body") if isinstance(result.get("body"), dict) else {}
    base = body.get("BaseResp") if isinstance(body.get("BaseResp"), dict) else {}
    return base.get("StatusCode")


def _base_ok(result: dict[str, Any]) -> bool:
    return result.get("statusCode") == 200 and _base_code(result) == 0


def _monitor_api_prefix() -> str:
    raw = str(os.environ.get("AIDP_API_PREFIX") or "/api/v1").strip()
    prefixed = raw if raw.startswith("/") else f"/{raw}"
    normalized = re.sub(r"/+", "/", prefixed).rstrip("/")
    return normalized if normalized and normalized != "/" else "/api/v1"


def _monitor_api_url(suffix: str) -> str:
    base_url = str(os.environ.get("AIDP_MONITOR_BASE_URL") or os.environ.get("AIDP_PLATFORM_BASE_URL") or "http://127.0.0.1:8789").strip()
    normalized_base = base_url.rstrip().rstrip("/")
    prefix = _monitor_api_prefix()
    if normalized_base.endswith(prefix):
        return f"{normalized_base}/{str(suffix or '').lstrip('/')}"
    return f"{normalized_base}{prefix}/{str(suffix or '').lstrip('/')}"


def _monitor_api_headers() -> dict[str, str]:
    token = str(os.environ.get("AIDP_MONITOR_API_TOKEN") or os.environ.get("AIDP_ADMIN_API_TOKEN") or os.environ.get("AIDP_API_TOKEN") or "").strip()
    return {"X-AIDP-API-Token": token} if token else {}


if __name__ == "__main__":
    main()
