import asyncio
import inspect
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional

TickFunc = Callable[[], Any]


@dataclass
class GenericTaskAutoRunWorkerStatus:
    run_id: str
    active: bool = False
    running: bool = False
    cycle_count: int = 0
    last_ok: Optional[bool] = None
    last_error: Optional[str] = None
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    interval_seconds: int = 0
    next_run_at: Optional[str] = None


class GenericTaskAutoRunWorkerScheduler:
    def __init__(self, run_id: str, *, tick_func: TickFunc, interval_seconds: int = 5) -> None:
        self.run_id = str(run_id)
        self.tick_func = tick_func
        self.interval_seconds = max(1, int(interval_seconds or 1))
        self.status = GenericTaskAutoRunWorkerStatus(run_id=self.run_id, interval_seconds=self.interval_seconds)
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event: Optional[asyncio.Event] = None

    async def run_once(self) -> Any:
        if self.status.running:
            return None
        self.status.running = True
        self.status.cycle_count += 1
        self.status.last_started_at = _now_text()
        self.status.last_error = None
        try:
            result = await self._call_tick()
            self.status.last_ok = True
            return result
        except Exception as exc:  # noqa: BLE001
            self.status.last_ok = False
            self.status.last_error = str(exc)
            return None
        finally:
            self.status.running = False
            self.status.last_finished_at = _now_text()
            self.status.next_run_at = _future_text(self.interval_seconds) if self.status.active else None

    def start(self) -> GenericTaskAutoRunWorkerStatus:
        if self._task is not None:
            return self.snapshot()
        self.status.active = True
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())
        self.status.next_run_at = _future_text(self.interval_seconds)
        return self.snapshot()

    async def stop(self) -> GenericTaskAutoRunWorkerStatus:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            await self._task
        self._task = None
        self._stop_event = None
        self.status.active = False
        self.status.running = False
        self.status.next_run_at = None
        return self.snapshot()

    def snapshot(self) -> GenericTaskAutoRunWorkerStatus:
        return replace(self.status)

    async def _loop(self) -> None:
        while self._stop_event is not None and not self._stop_event.is_set():
            await self._wait(self.interval_seconds)
            if self._stop_event is None or self._stop_event.is_set():
                break
            await self.run_once()

    async def _wait(self, seconds: int) -> None:
        if self._stop_event is None:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return

    async def _call_tick(self) -> Any:
        if inspect.iscoroutinefunction(self.tick_func):
            return await self.tick_func()
        return await asyncio.to_thread(self.tick_func)


class GenericTaskAutoRunWorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, GenericTaskAutoRunWorkerScheduler] = {}

    def ensure(self, run_id: str, *, tick_func: TickFunc, interval_seconds: int = 5) -> GenericTaskAutoRunWorkerScheduler:
        worker = self._workers.get(str(run_id))
        if worker is None:
            worker = GenericTaskAutoRunWorkerScheduler(str(run_id), tick_func=tick_func, interval_seconds=interval_seconds)
            self._workers[str(run_id)] = worker
        else:
            worker.tick_func = tick_func
            worker.interval_seconds = max(1, int(interval_seconds or 1))
            worker.status.interval_seconds = worker.interval_seconds
        return worker

    def status(self, run_id: str) -> GenericTaskAutoRunWorkerStatus:
        worker = self._workers.get(str(run_id))
        if worker is None:
            return GenericTaskAutoRunWorkerStatus(run_id=str(run_id), active=False)
        return worker.snapshot()

    async def stop(self, run_id: str) -> GenericTaskAutoRunWorkerStatus:
        worker = self._workers.get(str(run_id))
        if worker is None:
            return GenericTaskAutoRunWorkerStatus(run_id=str(run_id), active=False)
        status = await worker.stop()
        self._workers.pop(str(run_id), None)
        return status

    async def stop_all(self) -> None:
        for run_id in list(self._workers.keys()):
            await self.stop(run_id)


def _now_text() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _future_text(seconds: int) -> str:
    return datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + seconds, tz=timezone.utc).astimezone().replace(microsecond=0).isoformat()
