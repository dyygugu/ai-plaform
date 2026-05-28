import asyncio

from app.services.production_auto_refresh_service import ProductionAutoRefreshScheduler


def test_scheduler_run_once_records_success() -> None:
    calls = []
    scheduler = ProductionAutoRefreshScheduler(refresh_func=lambda: calls.append("refresh") or {"ok": True}, interval_seconds=60, initial_delay_seconds=0)

    asyncio.run(scheduler.run_once())

    assert calls == ["refresh"]
    assert scheduler.status.running is False
    assert scheduler.status.last_ok is True
    assert scheduler.status.last_error is None
    assert scheduler.status.run_count == 1


def test_scheduler_run_once_records_failure_without_crashing() -> None:
    def fail() -> None:
        raise RuntimeError("cookie expired")

    scheduler = ProductionAutoRefreshScheduler(refresh_func=fail, interval_seconds=60, initial_delay_seconds=0)

    asyncio.run(scheduler.run_once())

    assert scheduler.status.running is False
    assert scheduler.status.last_ok is False
    assert scheduler.status.last_error == "cookie expired"
    assert scheduler.status.run_count == 1
