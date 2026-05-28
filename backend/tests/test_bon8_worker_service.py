import asyncio
import unittest

from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.services.bon8_worker_service import Bon8RunWorkerRegistry, Bon8RunWorkerScheduler


class Bon8WorkerServiceTests(unittest.TestCase):
    def test_run_once_records_tick_status_without_concurrent_overlap(self) -> None:
        calls: list[str] = []

        async def run_test() -> None:
            async def slow_tick(run_id: str) -> dict[str, str]:
                calls.append(run_id)
                await asyncio.sleep(0.01)
                return {"run_id": run_id}

            worker = Bon8RunWorkerScheduler("bon8-test-run", tick_func=slow_tick, interval_seconds=1)
            await asyncio.gather(worker.run_once(), worker.run_once())

            self.assertEqual(calls, ["bon8-test-run"])
            self.assertEqual(worker.status.cycle_count, 1)
            self.assertTrue(worker.status.last_ok)
            self.assertFalse(worker.status.running)
            self.assertIn("bon8-test-run", worker.status.run_id)

        asyncio.run(run_test())

    def test_registry_start_stop_keeps_one_worker_per_run(self) -> None:
        calls: list[str] = []

        async def run_test() -> None:
            registry = Bon8RunWorkerRegistry(tick_func=lambda run_id: calls.append(run_id))

            first = registry.start("bon8-test-run", interval_seconds=1)
            second = registry.start("bon8-test-run", interval_seconds=1)
            await asyncio.sleep(0.05)
            stopped = await registry.stop("bon8-test-run")
            call_count_after_stop = len(calls)
            await asyncio.sleep(0.05)

            self.assertTrue(first.active)
            self.assertTrue(second.active)
            self.assertEqual(first.run_id, second.run_id)
            self.assertFalse(stopped.active)
            self.assertGreaterEqual(call_count_after_stop, 1)
            self.assertEqual(len(calls), call_count_after_stop)

        asyncio.run(run_test())

    def test_worker_routes_start_status_and_stop_run_loop(self) -> None:
        app = FastAPI()
        app.state.bon8_run_worker_registry = Bon8RunWorkerRegistry(tick_func=lambda _run_id: None)
        app.include_router(api_router, prefix="/api/v1")
        with TestClient(app) as client:
            started = client.post("/api/v1/bon8-production/runs/bon8-missing-run/worker/start", json={"interval_seconds": 1})
            self.assertEqual(started.status_code, 200, started.text)
            self.assertTrue(started.json()["active"])
            self.assertEqual(started.json()["run_id"], "bon8-missing-run")

            status = client.get("/api/v1/bon8-production/runs/bon8-missing-run/worker/status")
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(status.json()["run_id"], "bon8-missing-run")

            stopped = client.post("/api/v1/bon8-production/runs/bon8-missing-run/worker/stop")
            self.assertEqual(stopped.status_code, 200, stopped.text)
            self.assertFalse(stopped.json()["active"])


if __name__ == "__main__":
    unittest.main()
