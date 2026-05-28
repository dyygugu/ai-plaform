import json
from pathlib import Path

from app.services.earnings_ledger_service import build_earnings_ledger_summary, update_earnings_ledger_from_accounts


def _task(task_id: str, name: str, in_progress: int) -> dict:
    return {"id": task_id, "name": name, "frontendProgress": {"inProgressCount": in_progress}}


def _account(user_id: str, name: str, tasks: list[dict]) -> dict:
    return {"userId": user_id, "name": name, "tasks": tasks}


def test_ledger_closes_positive_in_progress_delta_after_four_unchanged_refreshes(tmp_path: Path) -> None:
    ledger_path = tmp_path / "earnings-ledger.json"
    task_id = "task-bon8"
    snapshots = [0, 2, 4, 4, 4, 4, 4]
    for index, value in enumerate(snapshots):
        update_earnings_ledger_from_accounts(
            [_account("account-1", "用户1", [_task(task_id, "bon8", value)])],
            observed_at=f"2026-05-12T00:{index:02d}:00+00:00",
            ledger_path=ledger_path,
            default_unit_price=0.5,
        )

    summary = build_earnings_ledger_summary(ledger_path=ledger_path)
    task = summary.tasks[0]
    run = task.runs[0]
    account = run.accounts[0]
    assert task.completed_count == 4
    assert task.amount == 2.0
    assert task.started_at == "2026-05-12T00:00:00+00:00"
    assert task.finished_at == "2026-05-12T00:06:00+00:00"
    assert run.completed_count == 4
    assert run.unit_price == 0.5
    assert account.completed_count == 4
    assert account.amount == 2.0


def test_ledger_groups_multiple_accounts_into_same_task_run(tmp_path: Path) -> None:
    ledger_path = tmp_path / "earnings-ledger.json"
    frames = [
        (0, 0),
        (3, 0),
        (5, 2),
        (5, 4),
        (5, 4),
        (5, 4),
        (5, 4),
        (5, 4),
    ]
    for index, (left, right) in enumerate(frames):
        update_earnings_ledger_from_accounts(
            [
                _account("account-1", "用户1", [_task("task-shared", "共享任务", left)]),
                _account("account-2", "用户2", [_task("task-shared", "共享任务", right)]),
            ],
            observed_at=f"2026-05-12T01:{index:02d}:00+00:00",
            ledger_path=ledger_path,
            default_unit_price=1.2,
        )

    summary = build_earnings_ledger_summary(ledger_path=ledger_path)
    assert len(summary.tasks) == 1
    assert len(summary.tasks[0].runs) == 1
    run = summary.tasks[0].runs[0]
    assert run.completed_count == 9
    assert run.amount == 10.8
    assert {account.account_user_id: account.completed_count for account in run.accounts} == {"account-1": 5, "account-2": 4}


def test_update_run_unit_price_recalculates_run_task_and_account_amounts(tmp_path: Path) -> None:
    ledger_path = tmp_path / "earnings-ledger.json"
    for index, value in enumerate([0, 3, 3, 3, 3, 3]):
        update_earnings_ledger_from_accounts(
            [_account("account-1", "用户1", [_task("task-price", "计价任务", value)])],
            observed_at=f"2026-05-12T02:{index:02d}:00+00:00",
            ledger_path=ledger_path,
            default_unit_price=1,
        )
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    run_id = data["runs"][0]["run_id"]

    from app.services.earnings_ledger_service import update_earnings_ledger_run_price

    update_earnings_ledger_run_price(run_id, 2.5, ledger_path=ledger_path)

    summary = build_earnings_ledger_summary(ledger_path=ledger_path)
    assert summary.tasks[0].amount == 7.5
    assert summary.tasks[0].runs[0].amount == 7.5
    assert summary.tasks[0].runs[0].accounts[0].amount == 7.5
