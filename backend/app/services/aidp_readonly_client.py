from typing import Any

import requests

from app.services.runtime_account_service import load_runtime_account


def _load_runtime_account(source_account_user_id: str) -> dict[str, Any]:
    account = load_runtime_account(source_account_user_id)
    if account:
        return account
    raise FileNotFoundError(f"source account {source_account_user_id} not found in 8789 runtime account store")


def capture_search_task_readonly(source_account_user_id: str) -> dict[str, Any]:
    account = _load_runtime_account(source_account_user_id)
    cookie = str(account.get("cookie") or "")
    if not cookie:
        raise ValueError(f"source account {source_account_user_id} has no cookie")
    referer = str(account.get("referer") or account.get("operationUrl") or "https://aidp.juejin.cn/operation/task-v2?page=1")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://aidp.juejin.cn",
        "Referer": referer,
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
        "x-secsdk-csrf-token": "DOWNGRADE",
        "x-backend-org-id": "100",
        "x-web-org-id": "100",
    }
    body = {"Filter": {}, "PageRequest": {"PageNo": 0, "PageSize": 100}}
    response = requests.post("https://aidp.juejin.cn/api/dispatch/SearchTask", headers=headers, json=body, timeout=20)
    response.raise_for_status()
    payload = response.json()
    return {
        "sourceAccountUserId": source_account_user_id,
        "referer": referer,
        "statusCode": response.status_code,
        "searchTask": payload,
    }
