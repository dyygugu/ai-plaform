import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional


RefreshFunc = Callable[[], Any]


@dataclass
class ProductionAutoRefreshStatus:
    enabled: bool
    running: bool = False
    run_count: int = 0
    last_ok: Optional[bool] = None
    last_error: Optional[str] = None
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    interval_seconds: int = 0
    next_run_at: Optional[str] = None


class ProductionAutoRefreshScheduler:
    def __init__(self, refresh_func: RefreshFunc, interval_seconds: int, initial_delay_seconds: int = 0, enabled: bool = True) -> None:
        self.refresh_func = refresh_func
        self.interval_seconds = max(1, int(interval_seconds))
        self.initial_delay_seconds = max(0, int(initial_delay_seconds))
        self.status = ProductionAutoRefreshStatus(enabled=enabled, interval_seconds=self.interval_seconds)
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event: Optional[asyncio.Event] = None

    async def run_once(self) -> None:
        if self.status.running:
            return
        self.status.running = True
        self.status.run_count += 1
        self.status.last_started_at = _now_text()
        self.status.last_error = None
        try:
            await asyncio.to_thread(self.refresh_func)
            self.status.last_ok = True
        except Exception as exc:  # noqa: BLE001 - scheduler must not crash the API process.
            self.status.last_ok = False
            self.status.last_error = str(exc)
        finally:
            self.status.running = False
            self.status.last_finished_at = _now_text()
            self.status.next_run_at = _future_text(self.interval_seconds)

    def start(self) -> None:
        if not self.status.enabled or self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            await self._task
        self._task = None
        self._stop_event = None

    async def _loop(self) -> None:
        if self.initial_delay_seconds:
            await self._wait(self.initial_delay_seconds)
        while self._stop_event is not None and not self._stop_event.is_set():
            await self.run_once()
            await self._wait(self.interval_seconds)

    async def _wait(self, seconds: int) -> None:
        if self._stop_event is None:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return


def _now_text() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _future_text(seconds: int) -> str:
    return datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + seconds, tz=timezone.utc).astimezone().replace(microsecond=0).isoformat()
