import json
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from app.core.settings import get_settings
from app.schemas.earnings import EarningsLedgerAccountItem, EarningsLedgerRunItem, EarningsLedgerTaskItem
from app.services.earnings_service import read_earnings_price_config
from app.services.task_rules import utc_now


UNCHANGED_REFRESHES_TO_CLOSE = 4


class EarningsLedgerSummary:
    def __init__(self, tasks: list[EarningsLedgerTaskItem]) -> None:
        self.tasks = tasks
        self.total_amount = round(sum(item.amount for item in tasks), 2)


def update_earnings_ledger_from_accounts(
    accounts: list[dict[str, Any]],
    *,
    observed_at: Optional[str] = None,
    ledger_path: Optional[Path] = None,
    default_unit_price: Optional[float] = None,
) -> EarningsLedgerSummary:
    path = ledger_path or _ledger_path()
    data = _load_ledger(path)
    observed = observed_at or utc_now().isoformat()
    unit_price = float(default_unit_price if default_unit_price is not None else read_earnings_price_config().unit_price)
    for account in accounts:
        user_id = str(account.get("userId") or account.get("user_id") or "").strip()
        if not user_id:
            continue
        display_name = str(account.get("name") or account.get("displayName") or account.get("customName") or user_id)
        tasks = account.get("tasks") if isinstance(account.get("tasks"), list) else []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("id") or task.get("taskId") or "").strip()
            if not task_id:
                continue
            task_name = str(task.get("name") or task.get("taskName") or task_id)
            current = _task_in_progress(task)
            _update_tracker(data, task_id, task_name, user_id, display_name, current, observed, unit_price)
    _recalculate(data)
    _write_ledger(path, data)
    return build_earnings_ledger_summary(ledger_path=path)


def build_earnings_ledger_summary(*, ledger_path: Optional[Path] = None) -> EarningsLedgerSummary:
    data = _load_ledger(ledger_path or _ledger_path())
    _recalculate(data)
    closed_runs = [run for run in data.get("runs", []) if run.get("status") == "closed" and _int(run.get("completed_count")) > 0]
    tasks: dict[str, EarningsLedgerTaskItem] = {}
    for run in closed_runs:
        task_id = str(run.get("task_id") or "")
        accounts = [
            EarningsLedgerAccountItem(
                account_run_id=str(account.get("account_run_id") or ""),
                account_user_id=str(account.get("account_user_id") or ""),
                display_name=str(account.get("display_name") or ""),
                completed_count=_int(account.get("completed_count")),
                amount=_money(account.get("amount")),
                started_at=account.get("started_at"),
                finished_at=account.get("finished_at"),
                status=str(account.get("status") or "closed"),
            )
            for account in run.get("accounts", [])
            if account.get("status") == "closed" and _int(account.get("completed_count")) > 0
        ]
        item = EarningsLedgerRunItem(
            run_id=str(run.get("run_id") or ""),
            task_id=task_id,
            task_name=str(run.get("task_name") or task_id),
            completed_count=_int(run.get("completed_count")),
            amount=_money(run.get("amount")),
            unit_price=_money(run.get("unit_price")),
            started_at=run.get("started_at"),
            finished_at=run.get("finished_at"),
            status=str(run.get("status") or "closed"),
            accounts=accounts,
        )
        task = tasks.get(task_id)
        if task is None:
            task = EarningsLedgerTaskItem(task_id=task_id, task_name=item.task_name, runs=[])
            tasks[task_id] = task
        task.runs.append(item)
    for task in tasks.values():
        task.runs.sort(key=lambda item: item.started_at or "", reverse=True)
        task.completed_count = sum(run.completed_count for run in task.runs)
        task.amount = round(sum(run.amount for run in task.runs), 2)
        starts = [run.started_at for run in task.runs if run.started_at]
        finishes = [run.finished_at for run in task.runs if run.finished_at]
        task.started_at = min(starts) if starts else None
        task.finished_at = max(finishes) if finishes else None
    return EarningsLedgerSummary(sorted(tasks.values(), key=lambda item: item.finished_at or "", reverse=True))


def update_earnings_ledger_run_price(run_id: str, unit_price: float, *, ledger_path: Optional[Path] = None) -> EarningsLedgerRunItem:
    path = ledger_path or _ledger_path()
    data = _load_ledger(path)
    target = None
    for run in data.get("runs", []):
        if str(run.get("run_id") or "") == run_id:
            target = run
            break
    if target is None:
        raise ValueError("未找到这次任务记账记录。")
    target["unit_price"] = max(0, float(unit_price or 0))
    _recalculate(data)
    _write_ledger(path, data)
    summary = build_earnings_ledger_summary(ledger_path=path)
    for task in summary.tasks:
        for run in task.runs:
            if run.run_id == run_id:
                return run
    raise ValueError("该记录尚未截止，暂不能计入收益汇总。")


def _update_tracker(
    data: dict[str, Any],
    task_id: str,
    task_name: str,
    user_id: str,
    display_name: str,
    current: int,
    observed_at: str,
    default_unit_price: float,
) -> None:
    trackers = data.setdefault("trackers", {})
    key = f"{task_id}::{user_id}"
    tracker = trackers.get(key)
    if tracker is None:
        trackers[key] = {"last_value": current, "last_seen_at": observed_at, "active_account_run_id": ""}
        return
    last_value = _int(tracker.get("last_value"))
    active_account_run_id = str(tracker.get("active_account_run_id") or "")
    if not active_account_run_id:
        if current > last_value:
            run = _get_or_create_task_run(data, task_id, task_name, tracker.get("last_seen_at") or observed_at, default_unit_price)
            account_run = {
                "account_run_id": uuid4().hex,
                "account_user_id": user_id,
                "display_name": display_name,
                "baseline_count": last_value,
                "end_count": current,
                "completed_count": current - last_value,
                "started_at": tracker.get("last_seen_at") or observed_at,
                "finished_at": None,
                "status": "active",
                "unchanged_count": 0,
                "last_changed_at": observed_at,
            }
            run.setdefault("accounts", []).append(account_run)
            tracker["active_account_run_id"] = account_run["account_run_id"]
        tracker["last_value"] = current
        tracker["last_seen_at"] = observed_at
        return
    account_run = _find_account_run(data, active_account_run_id)
    if account_run is None:
        tracker["active_account_run_id"] = ""
        tracker["last_value"] = current
        tracker["last_seen_at"] = observed_at
        return
    baseline = _int(account_run.get("baseline_count"))
    if current > last_value:
        account_run["end_count"] = current
        account_run["completed_count"] = max(0, current - baseline)
        account_run["unchanged_count"] = 0
        account_run["last_changed_at"] = observed_at
    elif current == last_value:
        account_run["unchanged_count"] = _int(account_run.get("unchanged_count")) + 1
        if account_run["unchanged_count"] >= UNCHANGED_REFRESHES_TO_CLOSE and _int(account_run.get("completed_count")) > 0:
            _close_account_run(data, account_run, observed_at)
            tracker["active_account_run_id"] = ""
    else:
        if _int(account_run.get("completed_count")) > 0:
            _close_account_run(data, account_run, tracker.get("last_seen_at") or observed_at)
        tracker["active_account_run_id"] = ""
    tracker["last_value"] = current
    tracker["last_seen_at"] = observed_at


def _get_or_create_task_run(data: dict[str, Any], task_id: str, task_name: str, started_at: str, unit_price: float) -> dict[str, Any]:
    active = data.setdefault("active_task_runs", {})
    run_id = str(active.get(task_id) or "")
    for run in data.setdefault("runs", []):
        if run.get("run_id") == run_id and run.get("status") == "active":
            return run
    run = {
        "run_id": uuid4().hex,
        "task_id": task_id,
        "task_name": task_name,
        "unit_price": max(0, float(unit_price or 0)),
        "started_at": started_at,
        "finished_at": None,
        "status": "active",
        "accounts": [],
    }
    data["runs"].append(run)
    active[task_id] = run["run_id"]
    return run


def _close_account_run(data: dict[str, Any], account_run: dict[str, Any], finished_at: str) -> None:
    account_run["status"] = "closed"
    account_run["finished_at"] = finished_at
    for run in data.get("runs", []):
        accounts = run.get("accounts") if isinstance(run.get("accounts"), list) else []
        if account_run not in accounts:
            continue
        if all(account.get("status") == "closed" for account in accounts):
            run["status"] = "closed"
            finishes = [account.get("finished_at") for account in accounts if account.get("finished_at")]
            starts = [account.get("started_at") for account in accounts if account.get("started_at")]
            run["finished_at"] = max(finishes) if finishes else finished_at
            run["started_at"] = min(starts) if starts else run.get("started_at")
            data.setdefault("active_task_runs", {}).pop(str(run.get("task_id") or ""), None)
        break


def _find_account_run(data: dict[str, Any], account_run_id: str) -> Optional[dict[str, Any]]:
    for run in data.get("runs", []):
        for account in run.get("accounts", []):
            if str(account.get("account_run_id") or "") == account_run_id:
                return account
    return None


def _recalculate(data: dict[str, Any]) -> None:
    for run in data.get("runs", []):
        unit_price = _money(run.get("unit_price"))
        for account in run.get("accounts", []):
            completed = max(0, _int(account.get("end_count")) - _int(account.get("baseline_count")))
            account["completed_count"] = completed
            account["amount"] = round(completed * unit_price, 2)
        run["completed_count"] = sum(_int(account.get("completed_count")) for account in run.get("accounts", []) if account.get("status") == "closed")
        run["amount"] = round(sum(_money(account.get("amount")) for account in run.get("accounts", []) if account.get("status") == "closed"), 2)


def _task_in_progress(task: dict[str, Any]) -> int:
    progress = task.get("frontendProgress") if isinstance(task.get("frontendProgress"), dict) else {}
    for value in (progress.get("inProgressCount"), task.get("aiInProgress"), task.get("inProgress"), task.get("in_progress")):
        if value is not None:
            return _int(value)
    return 0


def _ledger_path() -> Path:
    path = Path(get_settings().earnings_ledger_path)
    return path if path.is_absolute() else Path.cwd() / path


def _load_ledger(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("trackers", {})
    data.setdefault("active_task_runs", {})
    data.setdefault("runs", [])
    return data


def _write_ledger(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0
