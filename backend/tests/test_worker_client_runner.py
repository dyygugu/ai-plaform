import importlib.util
from pathlib import Path
import sys
import unittest


def _load_worker_client_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "scripts" / "aidp_worker_client.py"
    spec = importlib.util.spec_from_file_location("aidp_worker_client", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["aidp_worker_client"] = module
    spec.loader.exec_module(module)
    return module


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.responses: list[dict] = []

    def queue(self, response: dict) -> None:
        self.responses.append(response)

    def __call__(self, method: str, path: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path, payload or {}))
        if not self.responses:
            return {}
        return self.responses.pop(0)


class WorkerClientRunnerTests(unittest.TestCase):
    def test_register_heartbeat_claim_renew_and_success_result_for_health_probe(self) -> None:
        module = _load_worker_client_module()
        transport = FakeTransport()
        transport.queue({"status": "pending_approval", "worker_id": "worker-test"})
        transport.queue({"status": "pending_approval", "worker_id": "worker-test"})
        transport.queue({"command_id": "cmd-1", "command_type": "health_probe", "status": "running"})
        transport.queue({"command_id": "cmd-1", "status": "running"})
        transport.queue({"disposition": "accepted", "command": {"command_id": "cmd-1"}})

        client = module.WorkerClient(
            base_url="http://example.local/api/v1",
            worker_id="worker-test",
            display_name="测试 Worker",
            version="0.2.0",
            estimated_http_account_slots=4,
            transport=transport,
        )

        result = client.run_once()

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(
            [call[1] for call in transport.calls],
            [
                "/workers/register",
                "/workers/heartbeat",
                "/workers/worker-test/commands/claim",
                "/workers/commands/cmd-1/renew",
                "/workers/commands/cmd-1/result",
            ],
        )
        self.assertEqual(transport.calls[-1][2]["success"], True)
        self.assertEqual(transport.calls[-1][2]["result"]["probe"], "ok")

    def test_unknown_command_reports_failure_without_running_production_work(self) -> None:
        module = _load_worker_client_module()
        transport = FakeTransport()
        transport.queue({"status": "online", "worker_id": "worker-test"})
        transport.queue({"status": "online", "worker_id": "worker-test"})
        transport.queue({"command_id": "cmd-2", "command_type": "produce_account_task", "status": "running"})
        transport.queue({"command_id": "cmd-2", "status": "running"})
        transport.queue({"disposition": "accepted", "command": {"command_id": "cmd-2"}})

        client = module.WorkerClient(
            base_url="http://example.local/api/v1",
            worker_id="worker-test",
            display_name="测试 Worker",
            version="0.2.0",
            estimated_http_account_slots=4,
            transport=transport,
        )

        result = client.run_once()

        self.assertEqual(result["status"], "failed")
        self.assertIn("unsupported_command", result["error"])
        self.assertEqual(transport.calls[-1][2]["success"], False)
        self.assertEqual(transport.calls[-1][2]["result"]["error_code"], "UNSUPPORTED_COMMAND")

    def test_produce_account_task_preflight_only_checks_platform_without_starting_work(self) -> None:
        module = _load_worker_client_module()
        transport = FakeTransport()
        transport.queue({"status": "online", "worker_id": "worker-test"})
        transport.queue({"status": "online", "worker_id": "worker-test"})
        transport.queue(
            {
                "command_id": "cmd-3",
                "command_type": "produce_account_task",
                "status": "running",
                "account_user_id": "account-1",
                "task_id": "task-1",
                "payload": {"mode": "preflight_only", "node_id": "1"},
            }
        )
        transport.queue({"command_id": "cmd-3", "status": "running"})
        transport.queue(
            {
                "status": "ready",
                "can_start": True,
                "checks": [{"key": "adapter_ready", "status": "passed"}],
                "message": "自检通过；该检查不会提交、暂存或领取题目。",
            }
        )
        transport.queue({"disposition": "accepted", "command": {"command_id": "cmd-3"}})

        client = module.WorkerClient(
            base_url="http://example.local/api/v1",
            worker_id="worker-test",
            display_name="测试 Worker",
            version="0.2.0",
            estimated_http_account_slots=4,
            transport=transport,
        )

        result = client.run_once()

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(
            [call[1] for call in transport.calls],
            [
                "/workers/register",
                "/workers/heartbeat",
                "/workers/worker-test/commands/claim",
                "/workers/commands/cmd-3/renew",
                "/task-auto-runs/preflight",
                "/workers/commands/cmd-3/result",
            ],
        )
        self.assertEqual(
            transport.calls[4][2],
            {
                "task_id": "task-1",
                "node_id": "1",
                "account_user_ids": ["account-1"],
                "write_audit": False,
            },
        )
        self.assertEqual(transport.calls[-1][2]["success"], True)
        self.assertEqual(transport.calls[-1][2]["result"]["error_code"], "")
        self.assertEqual(transport.calls[-1][2]["result"]["writes_remote"], False)
        self.assertEqual(transport.calls[-1][2]["result"]["submits_remote"], False)
        self.assertEqual(transport.calls[-1][2]["result"]["preflight"]["can_start"], True)

    def test_produce_account_task_execute_once_stops_after_execution_gate_until_real_executor_exists(self) -> None:
        module = _load_worker_client_module()
        transport = FakeTransport()
        transport.queue({"status": "online", "worker_id": "worker-test"})
        transport.queue({"status": "online", "worker_id": "worker-test"})
        transport.queue(
            {
                "command_id": "cmd-4",
                "command_type": "produce_account_task",
                "status": "running",
                "account_user_id": "account-1",
                "task_id": "task-1",
                "payload": {"mode": "execute_once"},
            }
        )
        transport.queue({"command_id": "cmd-4", "status": "running"})
        transport.queue(
            {
                "status": "ready",
                "can_execute": True,
                "checks": [{"key": "active_lease", "status": "passed"}],
                "writes_remote": False,
                "submits_remote": False,
                "message": "真实执行前置闸门通过。",
            }
        )
        transport.queue({"disposition": "accepted", "command": {"command_id": "cmd-4"}})

        client = module.WorkerClient(
            base_url="http://example.local/api/v1",
            worker_id="worker-test",
            display_name="测试 Worker",
            version="0.2.0",
            estimated_http_account_slots=4,
            transport=transport,
        )

        result = client.run_once()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            [call[1] for call in transport.calls],
            [
                "/workers/register",
                "/workers/heartbeat",
                "/workers/worker-test/commands/claim",
                "/workers/commands/cmd-4/renew",
                "/workers/commands/cmd-4/execution-gate",
                "/workers/commands/cmd-4/result",
            ],
        )
        self.assertEqual(transport.calls[4][2], {"worker_id": "worker-test"})
        self.assertEqual(transport.calls[-1][2]["success"], False)
        self.assertEqual(transport.calls[-1][2]["result"]["error_code"], "REAL_EXECUTOR_NOT_ENABLED")
        self.assertEqual(transport.calls[-1][2]["result"]["gate"]["can_execute"], True)
        self.assertEqual(transport.calls[-1][2]["result"]["writes_remote"], False)
        self.assertEqual(transport.calls[-1][2]["result"]["submits_remote"], False)


if __name__ == "__main__":
    unittest.main()
