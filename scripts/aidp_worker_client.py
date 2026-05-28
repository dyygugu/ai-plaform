from __future__ import annotations

import argparse
import json
import platform
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable


JsonDict = dict[str, object]
Transport = Callable[[str, str, JsonDict | None], JsonDict]


@dataclass
class WorkerClient:
    base_url: str
    worker_id: str
    display_name: str
    version: str
    estimated_http_account_slots: int
    transport: Transport | None = None

    def run_once(self) -> JsonDict:
        self.register()
        self.heartbeat()
        command = self.claim_command()
        if not command:
            return {"status": "idle", "message": "no_command"}
        command_id = str(command.get("command_id") or "")
        command_type = str(command.get("command_type") or "")
        if not command_id:
            return {"status": "idle", "message": "empty_command"}
        self.renew_command(command_id)
        result = self.execute_command(command)
        self.report_result(command_id, bool(result["success"]), result)
        return {"status": "succeeded" if result["success"] else "failed", **result}

    def register(self) -> JsonDict:
        return self.request(
            "POST",
            "/workers/register",
            {
                "worker_id": self.worker_id,
                "display_name": self.display_name,
                "version": self.version,
                "estimated_http_account_slots": self.estimated_http_account_slots,
            },
        )

    def heartbeat(self) -> JsonDict:
        return self.request(
            "POST",
            "/workers/heartbeat",
            {
                "worker_id": self.worker_id,
                "display_name": self.display_name,
                "version": self.version,
            },
        )

    def claim_command(self) -> JsonDict:
        try:
            return self.request("POST", f"/workers/{self.worker_id}/commands/claim", {})
        except WorkerHttpError as exc:
            if exc.status_code in {403, 404}:
                return {}
            raise

    def renew_command(self, command_id: str) -> JsonDict:
        return self.request("POST", f"/workers/commands/{command_id}/renew", {})

    def report_result(self, command_id: str, success: bool, result: JsonDict) -> JsonDict:
        return self.request(
            "POST",
            f"/workers/commands/{command_id}/result",
            {"success": success, "result": result},
        )

    def check_execution_gate(self, command_id: str) -> JsonDict:
        return self.request(
            "POST",
            f"/workers/commands/{command_id}/execution-gate",
            {"worker_id": self.worker_id},
        )

    def execute_command(self, command: JsonDict) -> JsonDict:
        command_type = str(command.get("command_type") or "")
        if command_type == "health_probe":
            return {
                "success": True,
                "probe": "ok",
                "worker_id": self.worker_id,
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
            }
        if command_type == "produce_account_task":
            return self.execute_produce_account_task(command)
        return {
            "success": False,
            "error": f"unsupported_command:{command_type}",
            "error_code": "UNSUPPORTED_COMMAND",
            "message": "Worker client skeleton only executes health_probe; production commands are not enabled yet.",
        }

    def execute_produce_account_task(self, command: JsonDict) -> JsonDict:
        command_id = str(command.get("command_id") or "")
        payload = command.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        mode = str(payload.get("mode") or "")
        if mode == "execute_once":
            gate = self.check_execution_gate(command_id)
            if not bool(gate.get("can_execute")):
                return {
                    "success": False,
                    "error": "execution_gate_blocked",
                    "error_code": "EXECUTION_GATE_BLOCKED",
                    "writes_remote": False,
                    "submits_remote": False,
                    "starts_run": False,
                    "gate": gate,
                    "message": "真实执行前置闸门未通过，Worker 已停止执行。",
                }
            return {
                "success": False,
                "error": "real_executor_not_enabled",
                "error_code": "REAL_EXECUTOR_NOT_ENABLED",
                "writes_remote": False,
                "submits_remote": False,
                "starts_run": False,
                "gate": gate,
                "message": "真实执行前置闸门已通过，但跨设备真实执行器仍未启用。",
            }
        if mode != "preflight_only":
            return {
                "success": False,
                "error": "unsupported_command:produce_account_task",
                "error_code": "UNSUPPORTED_COMMAND",
                "message": "Worker client only supports produce_account_task in preflight_only mode; it will not start, claim, submit, or temp-save work.",
            }
        account_user_id = str(command.get("account_user_id") or "")
        task_id = str(command.get("task_id") or "")
        node_id = str(payload.get("node_id") or "1")
        preflight = self.request(
            "POST",
            "/task-auto-runs/preflight",
            {
                "task_id": task_id,
                "node_id": node_id,
                "account_user_ids": [account_user_id] if account_user_id else [],
                "write_audit": False,
            },
        )
        return {
            "success": True,
            "error_code": "",
            "mode": "preflight_only",
            "preflight_status": "preflight_ready" if bool(preflight.get("can_start")) else "preflight_blocked",
            "task_id": task_id,
            "account_user_id": account_user_id,
            "writes_remote": False,
            "submits_remote": False,
            "starts_run": False,
            "preflight": preflight,
            "message": "Worker 已完成只读启动前自检；未启动、未暂存、未提交、未领取题目。",
        }

    def request(self, method: str, path: str, payload: JsonDict | None = None) -> JsonDict:
        if self.transport is not None:
            return self.transport(method, path, payload)
        return http_json_request(method, self.base_url.rstrip("/") + path, payload or {})


class WorkerHttpError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body


def http_json_request(method: str, url: str, payload: JsonDict) -> JsonDict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise WorkerHttpError(exc.code, error_body) from exc
    if not response_body:
        return {}
    parsed = json.loads(response_body)
    return parsed if isinstance(parsed, dict) else {}


def build_client(args: argparse.Namespace) -> WorkerClient:
    return WorkerClient(
        base_url=args.base_url,
        worker_id=args.worker_id,
        display_name=args.display_name or args.worker_id,
        version=args.version,
        estimated_http_account_slots=args.estimated_http_account_slots,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIDP Worker client skeleton")
    parser.add_argument("--base-url", required=True, help="Platform API base URL, for example http://127.0.0.1:8789/api/v1")
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--display-name", default="")
    parser.add_argument("--version", default="0.2.0")
    parser.add_argument("--estimated-http-account-slots", type=int, default=1)
    parser.add_argument("--once", action="store_true", help="Run one register/heartbeat/claim/result cycle")
    parser.add_argument("--interval-seconds", type=int, default=10)
    args = parser.parse_args(argv)

    client = build_client(args)
    while True:
        result = client.run_once()
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.once:
            break
        time.sleep(max(1, args.interval_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
