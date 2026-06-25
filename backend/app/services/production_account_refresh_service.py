import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from app.core.settings import get_settings
from app.core.paths import resolve_runtime_path
from app.schemas.production import ProductionAccountRefreshItem, ProductionAccountRefreshResponse
from app.services.earnings_ledger_service import update_earnings_ledger_from_accounts
from app.services.runtime_account_service import load_runtime_accounts

Transport = Callable[[str, str, dict[str, Any], dict[str, Any]], dict[str, Any]]


def refresh_production_accounts(
    accounts: Optional[list[dict[str, Any]]] = None,
    state_path: Optional[Path] = None,
    earnings_ledger_path: Optional[Path] = None,
    transport: Optional[Transport] = None,
    display_names: Optional[dict[str, str]] = None,
) -> ProductionAccountRefreshResponse:
    started_at = _now_text()
    source_accounts = accounts if accounts is not None else _load_refresh_accounts()
    path = state_path or _production_state_path()
    caller = transport or _request_json
    refresh_inputs: list[tuple[int, dict[str, Any], str, str]] = []
    for index, account in enumerate(source_accounts):
        user_id = str(account.get("userId") or account.get("user_id") or "").strip()
        display_name = _display_name_for_refresh(user_id, account, display_names)
        if not user_id or account.get("enabled", True) is False:
            continue
        refresh_inputs.append((index, account, user_id, display_name))

    results: list[tuple[int, dict[str, Any], ProductionAccountRefreshItem]] = []
    max_workers = _refresh_max_workers(len(refresh_inputs))
    if refresh_inputs:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aidp-refresh") as executor:
            futures = [
                executor.submit(_refresh_account_for_state, index, account, user_id, display_name, caller, display_names)
                for index, account, user_id, display_name in refresh_inputs
            ]
            for future in as_completed(futures):
                results.append(future.result())

    results.sort(key=lambda item: item[0])
    state_accounts = [state_account for _, state_account, _ in results]
    items = [item for _, _, item in results]
    finished_at = _now_text()
    failed_count = sum(1 for item in items if item.status == "error")
    _write_refresh_state(path, started_at, finished_at, state_accounts)
    update_earnings_ledger_from_accounts(state_accounts, observed_at=finished_at, ledger_path=earnings_ledger_path or _earnings_ledger_path_for_state(state_path))
    refreshed_count = len(items) - failed_count
    return ProductionAccountRefreshResponse(
        ok=failed_count == 0,
        status="completed" if failed_count == 0 else "warning",
        refreshed_count=refreshed_count,
        failed_count=failed_count,
        started_at=started_at,
        finished_at=finished_at,
        state_path=str(path),
        items=items,
        message=f"8789 原生并发刷新完成：成功 {refreshed_count} 个，失败 {failed_count} 个。",
    )


def _refresh_account_for_state(
    index: int,
    account: dict[str, Any],
    user_id: str,
    display_name: str,
    caller: Transport,
    display_names: Optional[dict[str, str]] = None,
) -> tuple[int, dict[str, Any], ProductionAccountRefreshItem]:
    try:
        refreshed = _refresh_one_account(account, caller, display_names)
        item = ProductionAccountRefreshItem(user_id=user_id, display_name=display_name, status="ok", task_count=len(refreshed.get("tasks", [])))
        return index, refreshed, item
    except Exception as exc:  # noqa: BLE001 - production refresh must preserve per-account failures.
        fallback = dict(account)
        fallback.update(
            {
                "userId": user_id,
                "name": display_name,
                "loginOk": False,
                "stale": True,
                "refreshStatus": "error",
                "error": str(exc),
                "source": "http-8789-native-error",
                "hasCookie": bool(account.get("cookie")),
            }
        )
        item = ProductionAccountRefreshItem(user_id=user_id, display_name=display_name, status="error", task_count=0, error=str(exc))
        return index, fallback, item


def _refresh_max_workers(account_count: int) -> int:
    if account_count <= 0:
        return 1
    return min(6, account_count)


def refresh_production_account_by_user_id(
    user_id: str,
    accounts: Optional[list[dict[str, Any]]] = None,
    state_path: Optional[Path] = None,
    earnings_ledger_path: Optional[Path] = None,
    transport: Optional[Transport] = None,
    display_names: Optional[dict[str, str]] = None,
) -> ProductionAccountRefreshResponse:
    started_at = _now_text()
    target_user_id = str(user_id or "").strip()
    source_accounts = accounts if accounts is not None else _load_refresh_accounts()
    target = next((account for account in source_accounts if str(account.get("userId") or account.get("user_id") or "").strip() == target_user_id), None)
    if target is None:
        raise ValueError("未找到该账号的 Cookie 运行数据，请先同步登录态。")
    path = state_path or _production_state_path()
    caller = transport or _request_json
    display_name = _display_name_for_refresh(target_user_id, target, display_names)
    existing_accounts = _load_existing_state_accounts(path)
    try:
        refreshed = _refresh_one_account(target, caller, display_names)
        existing_accounts[target_user_id] = refreshed
        item = ProductionAccountRefreshItem(user_id=target_user_id, display_name=display_name, status="ok", task_count=len(refreshed.get("tasks", [])))
    except Exception as exc:  # noqa: BLE001 - return per-account error without dropping other state.
        fallback = dict(target)
        fallback.update(
            {
                "userId": target_user_id,
                "name": display_name,
                "loginOk": False,
                "stale": True,
                "refreshStatus": "error",
                "error": str(exc),
                "source": "http-8789-native-error",
                "hasCookie": bool(target.get("cookie")),
            }
        )
        existing_accounts[target_user_id] = fallback
        item = ProductionAccountRefreshItem(user_id=target_user_id, display_name=display_name, status="error", task_count=0, error=str(exc))
    finished_at = _now_text()
    _write_refresh_state(path, started_at, finished_at, list(existing_accounts.values()))
    update_earnings_ledger_from_accounts(list(existing_accounts.values()), observed_at=finished_at, ledger_path=earnings_ledger_path or _earnings_ledger_path_for_state(state_path))
    failed_count = 1 if item.status == "error" else 0
    return ProductionAccountRefreshResponse(
        ok=failed_count == 0,
        status="completed" if failed_count == 0 else "warning",
        refreshed_count=1 - failed_count,
        failed_count=failed_count,
        started_at=started_at,
        finished_at=finished_at,
        state_path=str(path),
        items=[item],
        message=f"8789 原生刷新账号 {display_name} 完成：成功 {1 - failed_count} 个，失败 {failed_count} 个。",
    )


def _load_refresh_accounts() -> list[dict[str, Any]]:
    return list(load_runtime_accounts().values())


def _load_existing_state_accounts(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    except Exception:
        data = {}
    accounts = data.get("accounts") if isinstance(data, dict) else []
    result: dict[str, dict[str, Any]] = {}
    if isinstance(accounts, list):
        for account in accounts:
            if not isinstance(account, dict):
                continue
            user_id = str(account.get("userId") or account.get("user_id") or "").strip()
            if user_id:
                result[user_id] = account
    return result


def _write_refresh_state(path: Path, started_at: str, finished_at: str, accounts: list[dict[str, Any]]) -> None:
    interval_minutes = int(get_settings().production_auto_refresh_interval_minutes or 15)
    state = {
        "startedAt": started_at,
        "lastRefreshStartedAt": started_at,
        "lastRefreshFinishedAt": finished_at,
        "nextRefreshAt": _add_minutes_text(finished_at, interval_minutes),
        "refreshIntervalMinutes": interval_minutes,
        "isRefreshing": False,
        "accounts": accounts,
        "source": "aidp-monitor-next-native",
        "savedAt": finished_at,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _refresh_one_account(account: dict[str, Any], transport: Transport, display_names: Optional[dict[str, str]] = None) -> dict[str, Any]:
    cookie = str(account.get("cookie") or "").strip()
    if not cookie:
        raise ValueError("该账号没有可用 Cookie，请先重新登录并同步。")
    user_id = str(account.get("userId") or account.get("user_id") or "").strip()
    referer = str(account.get("referer") or account.get("operationUrl") or "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1")
    task_sync, visible_tasks = _discover_tasks(account, transport)
    income = _get_income(account, transport)
    tasks = [_refresh_task(account, task, transport) for task in visible_tasks]
    result = dict(account)
    result.update(
        {
            "userId": user_id,
            "name": _display_name_for_refresh(user_id, account, display_names),
            "referer": referer,
            "operationUrl": referer,
            "loginOk": True,
            "needsRelogin": False,
            "stale": False,
            "refreshStatus": "ok",
            "error": None,
            "source": "http-8789-native-category-progress",
            "hasCookie": True,
            "authMode": str(account.get("authMode") or "client-cookie"),
            "income": income,
            "tasks": tasks,
            "taskSync": task_sync,
            "lastRefreshFinishedAt": _now_text(),
        }
    )
    return result


def _display_name_for_refresh(user_id: str, account: dict[str, Any], display_names: Optional[dict[str, str]] = None) -> str:
    preferred = str((display_names or {}).get(user_id) or "").strip()
    if preferred and not _is_placeholder_display_name(preferred):
        return preferred
    for key in ("authoritativeName", "name", "displayName", "customName"):
        value = str(account.get(key) or "").strip()
        if value and not _is_placeholder_display_name(value):
            return value
    return user_id


def _is_placeholder_display_name(value: str) -> bool:
    text = value.strip()
    lower = text.lower()
    return (
        not text
        or lower.startswith(("account-", "new account", "pending-"))
        or text.startswith(("账号-", "主账号", "新账号待登录"))
        or text == "未命名账号"
    )


def _discover_tasks(account: dict[str, Any], transport: Transport) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    remote_tasks = _search_tasks(account, transport)
    configured = [task for task in account.get("tasks", []) if isinstance(task, dict)]
    configured_by_id = {str(task.get("id") or task.get("taskId") or ""): task for task in configured}
    visible: list[dict[str, Any]] = []
    for remote in remote_tasks:
        task_id = str(remote.get("id") or "")
        if not task_id:
            continue
        base = dict(configured_by_id.get(task_id, {}))
        base.update(remote)
        base["hidden"] = False
        visible.append(base)
    return {"changed": True, "visibleTaskIds": [task["id"] for task in visible], "remoteCount": len(visible), "added": 0, "hidden": 0, "reactivated": 0}, visible


def _search_tasks(account: dict[str, Any], transport: Transport) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_size = 100
    for page_no in range(50):
        payload = transport("api", "/api/dispatch/SearchTask", {"Filter": {}, "PageRequest": {"PageNo": page_no, "PageSize": page_size}}, account)
        items = payload.get("Tasks") if isinstance(payload.get("Tasks"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            task = item.get("Task") if isinstance(item.get("Task"), dict) else {}
            task_id = str(task.get("TaskID") or task.get("taskId") or "").strip()
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            nodes = item.get("Nodes")
            node_entry = _select_node_entry(nodes)
            node = node_entry.get("Node") if isinstance(node_entry.get("Node"), dict) else {"NodeID": 4}
            pending = _task_page_pending_value(nodes, node_entry)
            task_data = {"id": task_id, "name": str(task.get("Title") or task_id), "nodeId": int(node.get("NodeID") or 4) if node else 4}
            if pending is not None:
                task_data["poolPendingSubmit"] = pending
            tasks.append(task_data)
        total = _num(payload.get("Total"))
        if len(items) < page_size or (total and len(tasks) >= total):
            break
    return tasks


def _select_node_entry(nodes: Any) -> dict[str, Any]:
    candidates = _node_candidates(nodes)
    if not candidates:
        return {"Node": {"NodeID": 4}}
    permission_candidates = [item for item in candidates if isinstance(item.get("Permission"), list) and item.get("Permission")]
    if permission_candidates:
        return (
            next((item for item in permission_candidates if _node_pending_positive(item)), None)
            or next((item for item in permission_candidates if _node_pending_value(item) is not None), None)
            or permission_candidates[0]
        )
    return (
        next((item for item in candidates if _node_pending_positive(item)), None)
        or next((item for item in candidates if _node_pending_value(item) is not None), None)
        or next((item for item in candidates if _num(item["Node"].get("NodeID")) == 4), None)
        or candidates[0]
    )


def _node_candidates(nodes: Any) -> list[dict[str, Any]]:
    candidates = []
    if isinstance(nodes, list):
        for item in nodes:
            if isinstance(item, dict) and isinstance(item.get("Node"), dict):
                candidates.append(item)
    return candidates


def _task_page_pending_value(nodes: Any, selected_node: dict[str, Any]) -> Optional[int]:
    candidates = _node_candidates(nodes)
    permission_candidates = [item for item in candidates if isinstance(item.get("Permission"), list) and item.get("Permission")]
    if permission_candidates:
        values = [_node_pending_value(item) for item in permission_candidates]
        visible_values = [value for value in values if value is not None]
        if visible_values:
            return sum(visible_values)
        return None
    return _node_pending_value(selected_node)


def _node_pending_positive(node_entry: dict[str, Any]) -> bool:
    value = _node_pending_value(node_entry)
    return value is not None and value > 0


def _node_pending_value(node_entry: dict[str, Any]) -> Optional[int]:
    operator_stat = node_entry.get("OperatorStat") if isinstance(node_entry.get("OperatorStat"), dict) else {}
    node_stat = node_entry.get("NodeStat") if isinstance(node_entry.get("NodeStat"), dict) else {}
    for value in (operator_stat.get("ToDo"), node_stat.get("ToDo")):
        if value is None or value == "":
            continue
        try:
            return int(float(str(value).replace(",", "").strip()))
        except ValueError:
            continue
    return None


def _get_income(account: dict[str, Any], transport: Transport) -> dict[str, Any]:
    try:
        payload = transport("api", "/api/crowdsourcingSettle/SummaryIncome", {}, account)
        return {
            "totalIncome": _decimal_text(payload.get("CumulativeIncome")),
            "currentMonthIncome": _decimal_text(payload.get("CurMonthIncome")),
            "lastMonthIncome": _decimal_text(payload.get("PreMonthIncome")),
            "withdrawableAmount": _decimal_text(payload.get("CashableIncome")),
            "afterTaxWithdrawableAmount": _decimal_text(payload.get("AfterTaxCashableIncome")),
            "tax": payload.get("TaxDetail"),
            "status": "ok",
            "source": "http-summaryincome-cookie",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - income failure should not block task refresh.
        return {"totalIncome": "0.00", "currentMonthIncome": "0.00", "lastMonthIncome": "0.00", "withdrawableAmount": "0.00", "afterTaxWithdrawableAmount": "0.00", "tax": None, "status": "error", "source": "http-summaryincome-cookie", "error": str(exc)}


def _refresh_task(account: dict[str, Any], task: dict[str, Any], transport: Transport) -> dict[str, Any]:
    task_id = str(task.get("id") or task.get("taskId") or "")
    frontend_node_id = 1
    backend_node_id = _num(task.get("nodeId")) or 4
    category = _get_category(account, task_id, frontend_node_id, transport)
    progress = _get_progress(account, task_id, frontend_node_id, transport)
    error = None
    if category.get("error") or progress.get("error"):
        error = "部分HTTP口径失败，详见详情错误字段"
    return {
        "id": task_id,
        "name": str(task.get("name") or task_id),
        "nodeId": backend_node_id,
        "nodeName": "配置后台节点",
        "frontendNodeId": frontend_node_id,
        "frontendNodeName": "前台标注节点",
        "frontendNotSubmitted": _num((category.get("totalMap") or {}).get("0")),
        "frontendSubmittedAi": _num((category.get("totalMap") or {}).get("1")),
        "frontendRepairCount": _num(category.get("repairCount")),
        "frontendCategoryTotalMap": category.get("totalMap") or {},
        "frontendSubmittedCategory": category,
        "frontendProgress": progress,
        "aiDelivery": _num(progress.get("submittedCount")),
        "aiAbandoned": _num(progress.get("abandonedCount")),
        "aiInProgress": _num(progress.get("inProgressCount")),
        "personalTotal": 0,
        "personalProcessing": 0,
        "delivery": 0,
        "abandoned": 0,
        "processing": 0,
        "aiSubmitted": 0,
        "poolTotal": 0,
        "poolPendingSubmit": task.get("poolPendingSubmit"),
        "poolStatus3": 0,
        "poolStatus6": 0,
        "poolStatus7": 0,
        "status": "configured-http",
        "error": error,
        "httpCounts": {"searchItemPersonal": {}, "searchItemAll": {}, "searchItemData": {}, "frontendCategory": category.get("totalMap") or {}, "fullStatusSkipped": True, "fullStatusTaskLimit": 0},
        "nodeStat": {"todo": 0, "doing": 0, "modify": 0, "defer": 0, "pkgReady": 0},
        "operatorStat": {"todo": 0, "doing": 0, "modify": 0, "defer": 0, "pkgReady": 0},
        "source": "http-8789-native-category-progress",
    }


def _get_category(account: dict[str, Any], task_id: str, node_id: int, transport: Transport) -> dict[str, Any]:
    try:
        page_size = 99
        payload = transport("agw", "/dispatcher/search_item/category", {"TaskID": task_id, "NodeID": node_id, "ItemCategoryType": 0, "Filter": {}, "PageRequest": {"PageNo": 0, "PageSize": page_size}}, account)
        total_map = {str(key): _num(value) for key, value in (payload.get("TotalMap") or {}).items()}
        data = list(payload.get("Data") or [])
        tab_total = _num(payload.get("TabItemCategoryTotal"))
        page_no = 1
        while tab_total is not None and len(data) < tab_total:
            next_payload = transport("agw", "/dispatcher/search_item/category", {"TaskID": task_id, "NodeID": node_id, "ItemCategoryType": 0, "Filter": {}, "PageRequest": {"PageNo": page_no, "PageSize": page_size}}, account)
            next_data = next_payload.get("Data") or []
            if not next_data:
                break
            data.extend(next_data)
            page_no += 1
        status_counts = _category_status_counts(data)
        repair_count = status_counts.get("9", 0)
        return {"tabTotal": tab_total, "totalMap": total_map, "dataCount": len(data), "statusCounts": status_counts, "repairCount": repair_count, "receiveEnable": bool(payload.get("ReceiveEnable")), "source": "http-agw-cookie-category", "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"tabTotal": None, "totalMap": {}, "dataCount": 0, "statusCounts": {}, "repairCount": 0, "receiveEnable": False, "source": "http-agw-cookie-category", "error": str(exc)}


def _category_status_counts(data: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(data, list):
        return counts
    for item in data:
        if not isinstance(item, dict):
            continue
        source = item.get("Item") if isinstance(item.get("Item"), dict) else item
        status = _num(source.get("Status")) if isinstance(source, dict) else None
        if status is None:
            continue
        key = str(status)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _get_progress(account: dict[str, Any], task_id: str, node_id: int, transport: Transport) -> dict[str, Any]:
    try:
        end_time = int(datetime.now(timezone.utc).timestamp())
        payload = transport("agw", "/llm/insights/get_progress_stat", {"task_id": task_id, "node_id": node_id, "start_time": "0", "end_time": str(end_time), "CommonRequest": {}}, account)
        return {"submittedCount": _num(payload.get("submitted_count")), "abandonedCount": _num(payload.get("abandoned_count")), "inProgressCount": _num(payload.get("in_progress_count")), "startTime": "0", "endTime": str(end_time), "source": "http-agw-cookie-progress", "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"submittedCount": None, "abandonedCount": None, "inProgressCount": None, "startTime": None, "endTime": None, "source": "http-agw-cookie-progress", "error": str(exc)}


def _request_json(kind: str, path: str, body: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    cookie = str(account.get("cookie") or "")
    referer = str(account.get("referer") or account.get("operationUrl") or "https://aidp.juejin.cn/operation/task-v2?org=AIDP%20Coding&page=1")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://aidp.juejin.cn",
        "Referer": referer,
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
    }
    if kind == "api":
        headers.update({"x-secsdk-csrf-token": "DOWNGRADE", "x-backend-org-id": "100", "x-web-org-id": "100"})
    else:
        headers.update({"Agw-Js-Conv": "str", "X-JS-REQ": "1", "X-Backend-Side": "4", "X-Backend-Org-Id": "100"})
    response = requests.post(f"https://aidp.juejin.cn{path}", headers=headers, json=body, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return payload
    raise ValueError(f"AIDP 接口返回非对象 JSON：{path}")


def _production_state_path() -> Path:
    return resolve_runtime_path(get_settings().production_state_path)


def _earnings_ledger_path_for_state(state_path: Optional[Path]) -> Optional[Path]:
    if state_path is None:
        return None
    return Path(state_path).with_name("earnings-ledger.json")


def _now_text() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _add_minutes_text(value: str, minutes: int) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc).astimezone()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def _num(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "").strip()))
    except ValueError:
        return 0


def _decimal_text(value: Any) -> str:
    try:
        return f"{float(str(value or '0').replace(',', '').strip()):.2f}"
    except ValueError:
        return "0.00"
