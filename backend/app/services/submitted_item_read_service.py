import json
from typing import Any, Callable, Optional

import requests

from app.services.runtime_account_service import load_runtime_account


SEARCH_ITEM_CATEGORY_ENDPOINT = "/dispatcher/search_item/category"
MGET_ANSWER_LIST_ENDPOINT = "/api/dispatch/MGetAnswerList"

Transport = Callable[[dict[str, Any], str, str, dict[str, Any]], dict[str, Any]]


def load_account_with_cookie(user_id: str) -> dict[str, Any]:
    account = load_runtime_account(user_id)
    if not account:
        raise FileNotFoundError(f"runtime account not found: {user_id}")
    if not account.get("cookie"):
        raise ValueError(f"runtime account has no cookie: {user_id}")
    return account


def read_submitted_items(
    account: dict[str, Any],
    task_id: str,
    *,
    node_id: int = 1,
    page_size: int = 100,
    transport: Optional[Transport] = None,
) -> dict[str, Any]:
    remote = transport or _request_json
    items: list[dict[str, Any]] = []
    total_map: dict[str, Any] = {}
    submitted_total = 0
    page_no = 0
    while True:
        payload = remote(
            account,
            "agw",
            SEARCH_ITEM_CATEGORY_ENDPOINT,
            {
                "TaskID": str(task_id),
                "NodeID": int(node_id),
                "ItemCategoryType": 1,
                "Filter": {},
                "PageRequest": {"PageNo": page_no, "PageSize": max(1, int(page_size))},
            },
        )
        if _base_status_code(payload) not in {None, 0}:
            raise RuntimeError(f"search_item/category returned BaseResp={_base_status_code(payload)} for task {task_id}")
        if page_no == 0:
            total_map = payload.get("TotalMap") if isinstance(payload.get("TotalMap"), dict) else {}
            submitted_total = _num(payload.get("TabItemCategoryTotal"), total_map.get("1"))
        page_items = payload.get("Data") if isinstance(payload.get("Data"), list) else []
        if not page_items:
            break
        items.extend(page_items)
        if submitted_total and len(items) >= submitted_total:
            break
        page_no += 1
        if page_no > 500:
            raise RuntimeError(f"too many pages while reading submitted items for task {task_id}")
    status_counts = _status_counts(items)
    item_ids = [item_id for item_id in (_item_id(item) for item in items) if item_id]
    return {
        "task_id": str(task_id),
        "node_id": int(node_id),
        "submitted_total": submitted_total,
        "total_map": total_map,
        "status_counts": status_counts,
        "items": items,
        "item_ids": item_ids,
    }


def read_submitted_item_answers(
    account: dict[str, Any],
    task_id: str,
    item_ids: list[str],
    *,
    batch_size: int = 50,
    transport: Optional[Transport] = None,
) -> dict[str, Any]:
    remote = transport or _request_json
    normalized_ids = [str(item_id) for item_id in item_ids if str(item_id)]
    answer_list: dict[str, list[dict[str, Any]]] = {}
    for start in range(0, len(normalized_ids), max(1, int(batch_size))):
        batch = normalized_ids[start : start + max(1, int(batch_size))]
        payload = remote(
            account,
            "api",
            MGET_ANSWER_LIST_ENDPOINT,
            {"TaskID": str(task_id), "ItemIDs": batch},
        )
        if _base_status_code(payload) not in {None, 0}:
            raise RuntimeError(f"MGetAnswerList returned BaseResp={_base_status_code(payload)} for task {task_id}")
        batch_answers = payload.get("AnswerList") if isinstance(payload.get("AnswerList"), dict) else {}
        for item_id in batch:
            answers = batch_answers.get(item_id)
            answer_list[item_id] = answers if isinstance(answers, list) else []
    nonempty = {key: value for key, value in answer_list.items() if value}
    return {
        "task_id": str(task_id),
        "answer_key_count": len(answer_list),
        "nonempty_answer_key_count": len(nonempty),
        "answer_list": answer_list,
    }


def read_all_submitted_task_payloads(
    account: dict[str, Any],
    task_id: str,
    *,
    node_id: int = 1,
    page_size: int = 100,
    batch_size: int = 50,
    transport: Optional[Transport] = None,
) -> dict[str, Any]:
    submitted = read_submitted_items(account, task_id, node_id=node_id, page_size=page_size, transport=transport)
    answers = read_submitted_item_answers(account, task_id, submitted["item_ids"], batch_size=batch_size, transport=transport)
    return {
        "task_id": str(task_id),
        "node_id": int(node_id),
        "submitted": submitted,
        "answers": answers,
        "sample_item_ids": submitted["item_ids"][:10],
    }


def _request_json(account: dict[str, Any], kind: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    headers = _headers(account, kind)
    response = requests.post(f"https://aidp.juejin.cn{path}", headers=headers, json=body, timeout=30)
    response.raise_for_status()
    return response.json()


def _headers(account: dict[str, Any], kind: str) -> dict[str, str]:
    referer = str(account.get("referer") or account.get("operationUrl") or "https://aidp.juejin.cn/operation/task-v2?page=1")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://aidp.juejin.cn",
        "Referer": referer,
        "Cookie": str(account.get("cookie") or ""),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
    }
    if kind == "api":
        headers.update({"x-secsdk-csrf-token": "DOWNGRADE", "x-backend-org-id": "100", "x-web-org-id": "100"})
    else:
        headers.update({"Agw-Js-Conv": "str", "X-JS-REQ": "1", "X-Backend-Side": "4", "X-Backend-Org-Id": "100"})
    return headers


def _base_status_code(payload: Any) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    base = payload.get("BaseResp")
    if not isinstance(base, dict):
        return None
    return _num(base.get("StatusCode"))


def _item_id(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    source = item.get("Item") if isinstance(item.get("Item"), dict) else item
    return str(source.get("ItemID") or "") if isinstance(source, dict) else ""


def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        source = item.get("Item") if isinstance(item.get("Item"), dict) else item
        if not isinstance(source, dict):
            continue
        status = _num(source.get("Status"))
        if status is None:
            continue
        key = str(status)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _num(*values: Any) -> Optional[int]:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None
