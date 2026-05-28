from datetime import timedelta
from contextlib import contextmanager
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.router import api_router
from app.db.base import Base
from app.db.session import get_db
from app.models.worker import WorkerAccountTaskLease, WorkerCommandStatus, WorkerLeaseStatus, WorkerStatus
from app.services.task_rules import utc_now
from app.services.worker_dispatch_service import (
    assign_unbound_worker_commands,
    check_worker_command_execution_gate,
    claim_next_worker_command,
    create_worker_command,
    disable_worker_and_reclaim,
    ensure_platform_worker,
    handle_worker_command_result,
    list_account_task_leases,
    manually_recover_account_task_lease,
    renew_worker_command,
    recover_cooldown_account_task_leases,
    start_account_task_lease,
    timeout_and_requeue_worker_commands,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@contextmanager
def _api_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_db] = override_get_db
    app.include_router(api_router, prefix="/api/v1")
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


class WorkerDispatchServiceTests(unittest.TestCase):
    def test_platform_worker_inherits_existing_platform_capacity(self) -> None:
        db = _session()
        try:
            worker = ensure_platform_worker(db, inherited_http_account_slots=12)
            db.commit()

            self.assertEqual(worker.worker_id, "platform-worker")
            self.assertTrue(worker.is_platform_worker)
            self.assertEqual(worker.configured_http_account_slots, 12)
            self.assertEqual(worker.effective_http_account_slots, 12)
            self.assertEqual(worker.status, WorkerStatus.ONLINE)
            self.assertEqual(worker.health_status, "passed")
        finally:
            db.close()

    def test_command_claim_renew_timeout_requeue_and_late_success_is_audit_only(self) -> None:
        db = _session()
        try:
            ensure_platform_worker(db, inherited_http_account_slots=4)
            command = create_worker_command(
                db,
                worker_id="platform-worker",
                command_type="produce_account_task",
                account_user_id="7630778503730253600",
                task_id="task-1",
            )
            claimed = claim_next_worker_command(db, "platform-worker")
            self.assertIsNotNone(claimed)
            assert claimed is not None
            self.assertEqual(claimed.command_id, command.command_id)
            self.assertEqual(claimed.status, WorkerCommandStatus.RUNNING)

            renewed = renew_worker_command(db, claimed.command_id)
            stale_cutoff = renewed.last_renewed_at + timedelta(seconds=181)
            requeued = timeout_and_requeue_worker_commands(db, now=stale_cutoff)

            self.assertEqual(len(requeued), 1)
            self.assertEqual(requeued[0].retry_of_command_id, command.command_id)
            self.assertEqual(requeued[0].status, WorkerCommandStatus.QUEUED)
            self.assertEqual(command.status, WorkerCommandStatus.TIMED_OUT)

            result = handle_worker_command_result(db, command.command_id, success=True, result={"submitted": True})

            self.assertEqual(result["disposition"], "late_success_audit_only")
            self.assertEqual(command.status, WorkerCommandStatus.TIMED_OUT)
            self.assertEqual(command.result_json, "")
        finally:
            db.close()

    def test_disabling_worker_immediately_reclaims_leases_and_requeues_unfinished_work(self) -> None:
        db = _session()
        try:
            ensure_platform_worker(db, inherited_http_account_slots=2)
            task_lease = start_account_task_lease(
                db,
                worker_id="platform-worker",
                account_user_id="7630778503730253600",
                task_id="task-1",
            )
            command = create_worker_command(
                db,
                worker_id="platform-worker",
                command_type="produce_account_task",
                account_user_id=task_lease.account_user_id,
                task_id=task_lease.task_id,
            )
            _claimed = claim_next_worker_command(db, "platform-worker")

            summary = disable_worker_and_reclaim(db, "platform-worker", reason="人工禁用")

            self.assertEqual(summary["worker_status"], "disabled")
            self.assertEqual(summary["reclaimed_task_leases"], 1)
            self.assertEqual(summary["requeued_commands"], 1)
            self.assertEqual(task_lease.status, WorkerLeaseStatus.RECLAIMED)
            self.assertEqual(command.status, WorkerCommandStatus.TIMED_OUT)
            self.assertTrue(any(item.retry_of_command_id == command.command_id for item in summary["new_commands"]))
        finally:
            db.close()

    def test_assigns_unbound_queued_commands_to_available_platform_worker(self) -> None:
        db = _session()
        try:
            ensure_platform_worker(db, inherited_http_account_slots=2)
            command = create_worker_command(
                db,
                worker_id="",
                command_type="produce_account_task",
                account_user_id="7630778503730253600",
                task_id="task-assign",
                payload={"mode": "preflight_only"},
            )

            assigned = assign_unbound_worker_commands(db)

            self.assertEqual([item.command_id for item in assigned], [command.command_id])
            self.assertEqual(command.worker_id, "platform-worker")
            self.assertEqual(command.status, WorkerCommandStatus.QUEUED)
            claimed = claim_next_worker_command(db, "platform-worker")
            self.assertIsNotNone(claimed)
            assert claimed is not None
            self.assertEqual(claimed.command_id, command.command_id)
        finally:
            db.close()

    def test_execution_gate_requires_fresh_command_owner_and_active_lease(self) -> None:
        db = _session()
        try:
            ensure_platform_worker(db, inherited_http_account_slots=2)
            command = create_worker_command(
                db,
                worker_id="platform-worker",
                command_type="produce_account_task",
                account_user_id="7630778503730253600",
                task_id="task-gate",
                payload={"mode": "execute_once"},
            )
            _claimed = claim_next_worker_command(db, "platform-worker")

            blocked = check_worker_command_execution_gate(db, command.command_id, worker_id="platform-worker")

            self.assertFalse(blocked["can_execute"])
            self.assertEqual(blocked["status"], "blocked")
            self.assertIn("active_lease", {item["key"] for item in blocked["checks"] if item["status"] == "failed"})
            self.assertFalse(blocked["writes_remote"])
            self.assertFalse(blocked["submits_remote"])

            lease = start_account_task_lease(
                db,
                worker_id="platform-worker",
                account_user_id=command.account_user_id,
                task_id=command.task_id,
            )

            ready = check_worker_command_execution_gate(db, command.command_id, worker_id="platform-worker")

            self.assertTrue(ready["can_execute"])
            self.assertEqual(ready["status"], "ready")
            self.assertEqual(ready["lease_id"], lease.lease_id)
            self.assertTrue(all(item["status"] == "passed" for item in ready["checks"]))
            self.assertFalse(ready["writes_remote"])
            self.assertFalse(ready["submits_remote"])
        finally:
            db.close()

    def test_three_failed_command_results_suspend_account_task_lease(self) -> None:
        db = _session()
        try:
            ensure_platform_worker(db, inherited_http_account_slots=2)
            lease = start_account_task_lease(
                db,
                worker_id="platform-worker",
                account_user_id="7630778503730253600",
                task_id="task-fail",
            )

            for index in range(3):
                command = create_worker_command(
                    db,
                    worker_id="platform-worker",
                    command_type="produce_account_task",
                    account_user_id=lease.account_user_id,
                    task_id=lease.task_id,
                )
                _claimed = claim_next_worker_command(db, "platform-worker")
                result = handle_worker_command_result(
                    db,
                    command.command_id,
                    success=False,
                    result={"error_code": "WORKER_EXCEPTION", "attempt": index + 1},
                )
                self.assertEqual(result["disposition"], "accepted")

            self.assertEqual(lease.failure_count, 3)
            self.assertEqual(lease.status, WorkerLeaseStatus.SUSPENDED)
            self.assertIn("连续失败 3 次", lease.stop_reason)
        finally:
            db.close()

    def test_temporary_error_suspension_recovers_after_fifteen_minute_cooldown(self) -> None:
        db = _session()
        try:
            ensure_platform_worker(db, inherited_http_account_slots=2)
            lease = start_account_task_lease(
                db,
                worker_id="platform-worker",
                account_user_id="7630778503730253600",
                task_id="task-timeout-recover",
            )

            for _index in range(3):
                command = create_worker_command(
                    db,
                    worker_id="platform-worker",
                    command_type="produce_account_task",
                    account_user_id=lease.account_user_id,
                    task_id=lease.task_id,
                )
                _claimed = claim_next_worker_command(db, "platform-worker")
                handle_worker_command_result(
                    db,
                    command.command_id,
                    success=False,
                    result={"error_code": "AI_PROVIDER_TIMEOUT"},
                )

            self.assertEqual(lease.status, WorkerLeaseStatus.SUSPENDED)
            self.assertEqual(lease.last_error_code, "AI_PROVIDER_TIMEOUT")
            self.assertEqual(lease.recovery_type, "auto_recoverable")
            self.assertIsNotNone(lease.cooldown_until)

            early = recover_cooldown_account_task_leases(db, now=lease.cooldown_until - timedelta(seconds=1))
            self.assertEqual(early, [])
            self.assertEqual(lease.status, WorkerLeaseStatus.SUSPENDED)

            recovered = recover_cooldown_account_task_leases(db, now=lease.cooldown_until + timedelta(seconds=1))

            self.assertEqual([item.lease_id for item in recovered], [lease.lease_id])
            self.assertEqual(lease.status, WorkerLeaseStatus.ACTIVE)
            self.assertEqual(lease.failure_count, 0)
            self.assertEqual(lease.recovery_type, "")
            self.assertIsNone(lease.cooldown_until)
            self.assertIsNotNone(lease.recovered_at)
        finally:
            db.close()

    def test_severe_error_suspension_requires_manual_recovery(self) -> None:
        db = _session()
        try:
            ensure_platform_worker(db, inherited_http_account_slots=2)
            lease = start_account_task_lease(
                db,
                worker_id="platform-worker",
                account_user_id="7630778503730253600",
                task_id="task-auth-expired",
            )

            for _index in range(3):
                command = create_worker_command(
                    db,
                    worker_id="platform-worker",
                    command_type="produce_account_task",
                    account_user_id=lease.account_user_id,
                    task_id=lease.task_id,
                )
                _claimed = claim_next_worker_command(db, "platform-worker")
                handle_worker_command_result(
                    db,
                    command.command_id,
                    success=False,
                    result={"error_code": "TASK_PAGE_AUTH_EXPIRED"},
                )

            self.assertEqual(lease.status, WorkerLeaseStatus.SUSPENDED)
            self.assertEqual(lease.last_error_code, "TASK_PAGE_AUTH_EXPIRED")
            self.assertEqual(lease.recovery_type, "manual_recovery_required")
            self.assertIsNone(lease.cooldown_until)

            scan = recover_cooldown_account_task_leases(db, now=utc_now() + timedelta(hours=1))
            self.assertEqual(scan, [])
            self.assertEqual(lease.status, WorkerLeaseStatus.SUSPENDED)

            recovered = manually_recover_account_task_lease(db, lease.lease_id, reason="账号已重新登录")

            self.assertEqual(recovered.lease_id, lease.lease_id)
            self.assertEqual(lease.status, WorkerLeaseStatus.ACTIVE)
            self.assertEqual(lease.failure_count, 0)
            self.assertEqual(lease.recovery_type, "")
            self.assertIn("账号已重新登录", lease.stop_reason)
        finally:
            db.close()

    def test_lists_account_task_leases_with_recovery_metadata(self) -> None:
        db = _session()
        try:
            ensure_platform_worker(db, inherited_http_account_slots=2)
            lease = start_account_task_lease(
                db,
                worker_id="platform-worker",
                account_user_id="7630778503730253600",
                task_id="task-listed",
            )
            lease.status = WorkerLeaseStatus.SUSPENDED
            lease.failure_count = 3
            lease.last_error_code = "AI_PROVIDER_TIMEOUT"
            lease.recovery_type = "auto_recoverable"
            lease.cooldown_until = utc_now() + timedelta(minutes=15)

            leases = list_account_task_leases(db)

            self.assertEqual([item.lease_id for item in leases], [lease.lease_id])
            self.assertEqual(leases[0].last_error_code, "AI_PROVIDER_TIMEOUT")
            self.assertEqual(leases[0].recovery_type, "auto_recoverable")
        finally:
            db.close()

    def test_dispatch_routes_expose_platform_worker_leases_commands_and_reclaim_flow(self) -> None:
        with _api_client() as client:
            platform_worker = client.post(
                "/api/v1/workers/platform-worker/ensure",
                json={"inherited_http_account_slots": 6},
            )
            self.assertEqual(platform_worker.status_code, 200, platform_worker.text)
            self.assertEqual(platform_worker.json()["worker_id"], "platform-worker")
            self.assertEqual(platform_worker.json()["effective_http_account_slots"], 6)
            self.assertTrue(platform_worker.json()["is_platform_worker"])

            lease = client.post(
                "/api/v1/workers/leases/account-task",
                json={
                    "worker_id": "platform-worker",
                    "account_user_id": "7630778503730253600",
                    "task_id": "task-1",
                },
            )
            self.assertEqual(lease.status_code, 200, lease.text)
            self.assertEqual(lease.json()["status"], "active")

            command = client.post(
                "/api/v1/workers/commands",
                json={
                    "worker_id": "platform-worker",
                    "command_type": "produce_account_task",
                    "account_user_id": "7630778503730253600",
                    "task_id": "task-1",
                    "payload": {"source": "route-test"},
                },
            )
            self.assertEqual(command.status_code, 200, command.text)
            command_id = command.json()["command_id"]
            self.assertEqual(command.json()["status"], "queued")

            claimed = client.post("/api/v1/workers/platform-worker/commands/claim")
            self.assertEqual(claimed.status_code, 200, claimed.text)
            self.assertEqual(claimed.json()["command_id"], command_id)
            self.assertEqual(claimed.json()["status"], "running")

            renewed = client.post(f"/api/v1/workers/commands/{command_id}/renew")
            self.assertEqual(renewed.status_code, 200, renewed.text)
            self.assertEqual(renewed.json()["status"], "running")

            reclaim = client.post(
                "/api/v1/workers/platform-worker/disable-reclaim",
                json={"reason": "route disable"},
            )
            self.assertEqual(reclaim.status_code, 200, reclaim.text)
            self.assertEqual(reclaim.json()["worker_status"], "disabled")
            self.assertEqual(reclaim.json()["reclaimed_task_leases"], 1)
            self.assertEqual(reclaim.json()["requeued_commands"], 1)
            self.assertEqual(len(reclaim.json()["new_commands"]), 1)

    def test_dispatch_routes_timeout_scan_requeues_and_late_result_is_audit_only(self) -> None:
        with _api_client() as client:
            platform_worker = client.post(
                "/api/v1/workers/platform-worker/ensure",
                json={"inherited_http_account_slots": 3},
            )
            self.assertEqual(platform_worker.status_code, 200, platform_worker.text)
            command = client.post(
                "/api/v1/workers/commands",
                json={
                    "worker_id": "platform-worker",
                    "command_type": "produce_account_task",
                    "account_user_id": "7630778503730253600",
                    "task_id": "task-timeout",
                },
            )
            self.assertEqual(command.status_code, 200, command.text)
            command_id = command.json()["command_id"]
            claimed = client.post("/api/v1/workers/platform-worker/commands/claim")
            self.assertEqual(claimed.status_code, 200, claimed.text)
            self.assertEqual(claimed.json()["status"], "running")

            # Force timeout through the route by aging the command via the service API in the same test DB.
            # This keeps the public route focused on scanning, not test-only time travel parameters.
            db = next(client.app.dependency_overrides[get_db]())
            try:
                renewed = renew_worker_command(db, command_id)
                renewed.last_renewed_at = renewed.last_renewed_at - timedelta(seconds=181)
                db.commit()
            finally:
                db.close()

            timeout_scan = client.post("/api/v1/workers/commands/timeout-scan")
            self.assertEqual(timeout_scan.status_code, 200, timeout_scan.text)
            self.assertEqual(timeout_scan.json()["requeued_commands"], 1)
            self.assertEqual(timeout_scan.json()["new_commands"][0]["retry_of_command_id"], command_id)

            late_result = client.post(
                f"/api/v1/workers/commands/{command_id}/result",
                json={"success": True, "result": {"submitted": True}},
            )
            self.assertEqual(late_result.status_code, 200, late_result.text)
            self.assertEqual(late_result.json()["disposition"], "late_success_audit_only")
            self.assertEqual(late_result.json()["command"]["status"], "timed_out")
            self.assertEqual(late_result.json()["command"]["result"], {})

    def test_dispatch_routes_assign_unbound_commands_to_available_worker(self) -> None:
        with _api_client() as client:
            platform_worker = client.post(
                "/api/v1/workers/platform-worker/ensure",
                json={"inherited_http_account_slots": 2},
            )
            self.assertEqual(platform_worker.status_code, 200, platform_worker.text)
            command = client.post(
                "/api/v1/workers/commands",
                json={
                    "worker_id": "",
                    "command_type": "produce_account_task",
                    "account_user_id": "7630778503730253600",
                    "task_id": "task-assign-route",
                    "payload": {"mode": "preflight_only"},
                },
            )
            self.assertEqual(command.status_code, 200, command.text)
            command_id = command.json()["command_id"]
            self.assertEqual(command.json()["worker_id"], "")

            assigned = client.post("/api/v1/workers/commands/assign-scan")

            self.assertEqual(assigned.status_code, 200, assigned.text)
            self.assertEqual(assigned.json()["assigned_commands"], 1)
            self.assertEqual(assigned.json()["commands"][0]["command_id"], command_id)
            self.assertEqual(assigned.json()["commands"][0]["worker_id"], "platform-worker")
            claimed = client.post("/api/v1/workers/platform-worker/commands/claim")
            self.assertEqual(claimed.status_code, 200, claimed.text)
            self.assertEqual(claimed.json()["command_id"], command_id)

    def test_dispatch_routes_expose_execution_gate_for_claimed_command(self) -> None:
        with _api_client() as client:
            platform_worker = client.post(
                "/api/v1/workers/platform-worker/ensure",
                json={"inherited_http_account_slots": 2},
            )
            self.assertEqual(platform_worker.status_code, 200, platform_worker.text)
            lease = client.post(
                "/api/v1/workers/leases/account-task",
                json={
                    "worker_id": "platform-worker",
                    "account_user_id": "7630778503730253600",
                    "task_id": "task-gate-route",
                },
            )
            self.assertEqual(lease.status_code, 200, lease.text)
            command = client.post(
                "/api/v1/workers/commands",
                json={
                    "worker_id": "platform-worker",
                    "command_type": "produce_account_task",
                    "account_user_id": "7630778503730253600",
                    "task_id": "task-gate-route",
                    "payload": {"mode": "execute_once"},
                },
            )
            self.assertEqual(command.status_code, 200, command.text)
            command_id = command.json()["command_id"]
            claimed = client.post("/api/v1/workers/platform-worker/commands/claim")
            self.assertEqual(claimed.status_code, 200, claimed.text)

            gate = client.post(
                f"/api/v1/workers/commands/{command_id}/execution-gate",
                json={"worker_id": "platform-worker"},
            )

            self.assertEqual(gate.status_code, 200, gate.text)
            self.assertTrue(gate.json()["can_execute"])
            self.assertEqual(gate.json()["status"], "ready")
            self.assertEqual(gate.json()["lease_id"], lease.json()["lease_id"])
            self.assertFalse(gate.json()["writes_remote"])
            self.assertFalse(gate.json()["submits_remote"])

    def test_worker_registers_as_pending_until_approved_before_claiming_commands(self) -> None:
        with _api_client() as client:
            registered = client.post(
                "/api/v1/workers/register",
                json={
                    "worker_id": "worker-new-001",
                    "display_name": "新 Worker 001",
                    "version": "0.2.0",
                    "estimated_http_account_slots": 8,
                },
            )
            self.assertEqual(registered.status_code, 200, registered.text)
            self.assertEqual(registered.json()["status"], "pending_approval")
            self.assertEqual(registered.json()["estimated_http_account_slots"], 8)
            self.assertEqual(registered.json()["effective_http_account_slots"], 0)

            heartbeat = client.post(
                "/api/v1/workers/heartbeat",
                json={"worker_id": "worker-new-001", "version": "0.2.0"},
            )
            self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
            self.assertEqual(heartbeat.json()["status"], "pending_approval")

            command = client.post(
                "/api/v1/workers/commands",
                json={"worker_id": "worker-new-001", "command_type": "health_probe"},
            )
            self.assertEqual(command.status_code, 200, command.text)
            blocked_claim = client.post("/api/v1/workers/worker-new-001/commands/claim")
            self.assertEqual(blocked_claim.status_code, 403, blocked_claim.text)

            approved = client.post(
                "/api/v1/workers/worker-new-001/approve",
                json={"configured_http_account_slots": 6},
            )
            self.assertEqual(approved.status_code, 200, approved.text)
            self.assertEqual(approved.json()["status"], "online")
            self.assertEqual(approved.json()["effective_http_account_slots"], 6)

            claimed = client.post("/api/v1/workers/worker-new-001/commands/claim")
            self.assertEqual(claimed.status_code, 200, claimed.text)
            self.assertEqual(claimed.json()["command_type"], "health_probe")

    def test_recovery_routes_scan_auto_cooldown_and_allow_manual_recovery(self) -> None:
        with _api_client() as client:
            platform_worker = client.post(
                "/api/v1/workers/platform-worker/ensure",
                json={"inherited_http_account_slots": 3},
            )
            self.assertEqual(platform_worker.status_code, 200, platform_worker.text)
            lease = client.post(
                "/api/v1/workers/leases/account-task",
                json={
                    "worker_id": "platform-worker",
                    "account_user_id": "7630778503730253600",
                    "task_id": "task-route-recover",
                },
            )
            self.assertEqual(lease.status_code, 200, lease.text)
            lease_id = lease.json()["lease_id"]

            db = next(client.app.dependency_overrides[get_db]())
            try:
                manual_lease = start_account_task_lease(
                    db,
                    worker_id="platform-worker",
                    account_user_id="7630778503730253601",
                    task_id="task-route-manual",
                )
                manual_lease.status = WorkerLeaseStatus.SUSPENDED
                manual_lease.failure_count = 3
                manual_lease.recovery_type = "manual_recovery_required"
                manual_lease.last_error_code = "SUBMIT_FAILED"
                manual_lease.stop_reason = "连续失败 3 次，账号任务组停派"
                manual_lease_id = manual_lease.lease_id
                db.commit()
            finally:
                db.close()

            db = next(client.app.dependency_overrides[get_db]())
            try:
                auto_lease = db.query(WorkerAccountTaskLease).filter_by(lease_id=lease_id).one()
                auto_lease.status = WorkerLeaseStatus.SUSPENDED
                auto_lease.failure_count = 3
                auto_lease.recovery_type = "auto_recoverable"
                auto_lease.last_error_code = "AI_PROVIDER_TIMEOUT"
                auto_lease.cooldown_until = utc_now() - timedelta(seconds=1)
                db.commit()
            finally:
                db.close()

            scan = client.post("/api/v1/workers/leases/recovery-scan")
            self.assertEqual(scan.status_code, 200, scan.text)
            self.assertEqual(scan.json()["recovered_leases"], 1)
            self.assertEqual(scan.json()["leases"][0]["lease_id"], lease_id)
            self.assertEqual(scan.json()["leases"][0]["status"], "active")

            manual = client.post(
                f"/api/v1/workers/leases/{manual_lease_id}/recover",
                json={"reason": "人工确认可恢复"},
            )
            self.assertEqual(manual.status_code, 200, manual.text)
            self.assertEqual(manual.json()["status"], "active")
            self.assertEqual(manual.json()["failure_count"], 0)

            leases = client.get("/api/v1/workers/leases/account-task")
            self.assertEqual(leases.status_code, 200, leases.text)
            self.assertTrue(any(item["lease_id"] == lease_id for item in leases.json()))
            self.assertTrue(any(item["lease_id"] == manual_lease_id for item in leases.json()))


if __name__ == "__main__":
    unittest.main()
