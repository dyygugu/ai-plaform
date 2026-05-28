import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.services.bon8_payload_service import build_bon8_submit_temp_payload  # noqa: E402


DEFAULT_SCORES = {
    "model1": "1",
    "model2": "1",
    "model3": "2",
    "model4": "1",
    "model5": "1",
    "model6": "0",
    "model7": "1",
    "model8": "1",
}
DEFAULT_SORT_MODELS = ["model3", "model7", "model2", "model1", "model4", "model5", "model8", "model6"]
DEFAULT_REASONS = {
    "model1": "结构和输入图接近，但功能入口和核心内容完整度仍有不足。",
    "model2": "整体还原度接近，但页面功能层级和内容完整度不足。",
    "model3": "结构最完整，健康总览、功能入口和内容层级最接近输入图。",
    "model4": "结构差异不大，但核心内容和功能入口完整度不足。",
    "model5": "主要结构可见，但功能组织和内容还原仍不足。",
    "model6": "接近白屏，核心内容低可见，视觉和功能完整度不足。",
    "model7": "整体结构较接近，但部分核心功能和内容完整度不足。",
    "model8": "页面结构有基础还原，但功能入口和核心内容完整度不足。",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit one authorized bon8 current item with scores, checkboxes, and reasons.")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--node-id", default="1")
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--category-path", required=True)
    parser.add_argument("--run-dir", default="data/production-runs/bon8-20260510")
    parser.add_argument("--screenshots-dir", default="")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    run_dir = _path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = ROOT / "data" / "production-state.json"
    account = _load_account(state_path, args.user_id)
    category = _load_json(_path(args.category_path))
    category_item = _find_category_item(category, args.item_id)
    item_content = json.loads(category_item["Content"])

    judgement_path = run_dir / f"{args.item_id}-new-rule-judgement.json"
    _save_json(
        judgement_path,
        {
            "generatedAt": _now(),
            "ruleVersion": "bon8-20260510-scores-checkboxes-reasons-no-audit-remarks",
            "taskId": args.task_id,
            "nodeId": str(args.node_id),
            "itemId": args.item_id,
            "userId": args.user_id,
            "screenshots": args.screenshots_dir,
            "scores": DEFAULT_SCORES,
            "scoreReasons": DEFAULT_REASONS,
            "bestModel": "model3",
            "sortModels": DEFAULT_SORT_MODELS,
            "operatorSummary": "model3 结构最完整且覆盖健康总览、侧边功能导航、异常/随访/用药/医疗团队入口；model6 接近白屏且核心内容低可见给 0；其余模型结构与还原度差不太多给 1。提交 payload 不写 checkRemark/discard_remark 等审核备注字段，但写每个模型评分理由和 0/1 分 lowScoreReason。",
        },
    )

    started = time.perf_counter()
    payload_started = time.perf_counter()
    payload = build_bon8_submit_temp_payload(
        task_id=args.task_id,
        node_id=args.node_id,
        item_id=args.item_id,
        item_content=item_content,
        scores=DEFAULT_SCORES,
        sort_models=DEFAULT_SORT_MODELS,
        score_reasons=DEFAULT_REASONS,
    )
    payload_elapsed_ms = _elapsed_ms(payload_started)
    payload_path = run_dir / f"{args.item_id}-new-rule-payload.json"
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

    result_path = run_dir / f"{args.item_id}-new-rule-http-submit-result.json"
    result = {
        "generatedAt": _now(),
        "action": "bon8-new-rule-temp-verify-submit-category-readback",
        "userId": args.user_id,
        "accountName": account.get("name"),
        "taskId": str(args.task_id),
        "nodeId": str(args.node_id),
        "itemId": str(args.item_id),
        "payloadPath": str(payload_path),
        "judgementPath": str(judgement_path),
        "scores": DEFAULT_SCORES,
        "scoreReasons": DEFAULT_REASONS,
        "sortModels": DEFAULT_SORT_MODELS,
        "timingsMs": {
            "payloadBuild": payload_elapsed_ms,
            "categoryBefore": before["elapsedMs"],
            "submitTemp": temp["elapsedMs"],
            "verifySubmit": verify["elapsedMs"],
            "submitItem": submit["elapsedMs"],
            "categoryAfter": after["elapsedMs"],
            "total": _elapsed_ms(started),
        },
        "beforeCategory": before,
        "temp": temp,
        "verify": verify,
        "submit": submit,
        "categoryAfter": after,
        "ok": all(_base_ok(item) for item in [before, temp, verify, submit, after]),
        "submitShape": {"TaskID": str(args.task_id), "NodeID": int(args.node_id), "Status": 4, "AnswersCount": len(payload["AuditAnswers"]), "answerKeys": sorted(payload["AuditAnswers"][0].keys())},
    }
    _save_json(result_path, result)
    print(
        json.dumps(
            {
                "ok": result["ok"],
                "itemId": args.item_id,
                "baseCodes": {
                    "before": _base_code(before),
                    "temp": _base_code(temp),
                    "verify": _base_code(verify),
                    "submit": _base_code(submit),
                    "after": _base_code(after),
                },
                "totalMapBefore": before.get("body", {}).get("TotalMap"),
                "totalMapAfter": after.get("body", {}).get("TotalMap"),
                "timingsMs": result["timingsMs"],
                "resultPath": str(result_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


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


def _find_category_item(category: dict[str, Any], item_id: str) -> dict[str, Any]:
    for item in category.get("json", {}).get("Data", []):
        if str(item.get("ItemID")) == str(item_id):
            return item
    raise RuntimeError("target item not found in category file")


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


def _category_body(task_id: str, node_id: str) -> dict[str, Any]:
    return {"TaskID": str(task_id), "NodeID": int(node_id), "ItemCategoryType": 0, "Filter": {}, "PageRequest": {"PageNo": 0, "PageSize": 1}}


def _base_code(result: dict[str, Any]) -> Any:
    body = result.get("body") if isinstance(result.get("body"), dict) else {}
    base = body.get("BaseResp") if isinstance(body.get("BaseResp"), dict) else {}
    return base.get("StatusCode")


def _base_ok(result: dict[str, Any]) -> bool:
    return result.get("statusCode") == 200 and _base_code(result) == 0


if __name__ == "__main__":
    main()
