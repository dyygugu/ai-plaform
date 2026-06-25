import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.router import api_router
from app.core.settings import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models.worker import Worker, WorkerAccountTaskLease, WorkerCommand, WorkerEvent, WorkerEventType, WorkerLeaseStatus, WorkerStatus
from app.schemas.task_auto_runs import TaskAutoRunAccountState
from app.services.task_auto_run_service import TaskAutoRunAdapterSnapshot
from app.services.task_rules import utc_now


class FakeProductionAdapter:
    adapter_key = "fake_task"
    supported_task_ids = {"task-prod"}

    def preflight(self, request):
        from app.schemas.task_auto_runs import TaskAutoRunPreflightResponse

        return TaskAutoRunPreflightResponse(
            generated_at=utc_now(),
            task_id=request.task_id,
            node_id=request.node_id,
            adapter_key=self.adapter_key,
            status="ready",
            can_start=True,
            runnable_account_count=len(request.account_user_ids),
            checks=[],
            message="ready",
        )

    def start(self, _db, request):
        return TaskAutoRunAdapterSnapshot(
            adapter_key=self.adapter_key,
            adapter_run_id="fake-run-1",
            task_id=request.task_id,
            node_id=request.node_id,
            status="running_auto",
            stop_requested=False,
            accounts=[TaskAutoRunAccountState(account_user_id=item, status="running_auto") for item in request.account_user_ids],
            last_error="",
            next_step="",
            message="started",
            raw_adapter_run={"run_config": request.run_config},
        )

    def get(self, _adapter_run_id):
        return TaskAutoRunAdapterSnapshot(
            adapter_key=self.adapter_key,
            adapter_run_id="fake-run-1",
            task_id="task-prod",
            node_id="1",
            status="running_auto",
            stop_requested=False,
            accounts=[TaskAutoRunAccountState(account_user_id="111111111111", status="running_auto")],
            last_error="",
            next_step="",
            message="running",
            raw_adapter_run={},
        )

    def stop(self, _adapter_run_id):
        snapshot = self.get(_adapter_run_id)
        snapshot.status = "stopped"
        snapshot.stop_requested = True
        return snapshot


@contextmanager
def _api_client():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
    app.state.task_auto_run_adapters = [FakeProductionAdapter()]
    with tempfile.TemporaryDirectory() as tmp:
      app.state.task_auto_run_state_dir = Path(tmp)
      app.include_router(api_router, prefix="/api/v1")
      try:
          with TestClient(app) as client:
              yield client, testing_session
      finally:
          app.dependency_overrides.clear()
          engine.dispose()


def _seed_worker(db, worker_id: str, *, status=WorkerStatus.ONLINE, slots=1, platform=False, paused=False):
    worker = Worker(
        worker_id=worker_id,
        display_name=worker_id,
        status=status,
        version="0.9.0",
        is_platform_worker=platform,
        configured_http_account_slots=slots,
        effective_http_account_slots=slots,
        health_status="receiving_paused" if paused else "passed",
    )
    db.add(worker)
    db.flush()
    return worker


def test_execution_devices_filter_and_manual_capacity_rules() -> None:
    with _api_client() as (client, sessions):
        db = sessions()
        try:
            _seed_worker(db, "platform-worker", slots=3, platform=True)
            external = _seed_worker(db, "worker-a", slots=6)
            _seed_worker(db, "worker-paused", slots=2, paused=True)
            db.add(WorkerAccountTaskLease(lease_id="lease-1", worker_id=external.worker_id, account_user_id="111111111111", task_id="task-prod", status=WorkerLeaseStatus.ACTIVE))
            db.add(WorkerAccountTaskLease(lease_id="lease-2", worker_id=external.worker_id, account_user_id="222222222222", task_id="task-prod", status=WorkerLeaseStatus.ACTIVE))
            db.commit()
        finally:
            db.close()

        devices = client.get("/api/v1/execution-devices", params={"usable_for_production": "true"}).json()["items"]
        ids = {item["worker_id"] for item in devices}
        worker_a = next(item for item in devices if item["worker_id"] == "worker-a")

        assert "worker-a" in ids
        assert "worker-paused" not in ids
        assert worker_a["manual_slots"] == 6
        assert worker_a["running_slots"] == 2
        assert worker_a["effective_slots"] == 6
        assert worker_a["available_slots"] == 4

        bad = client.post("/api/v1/execution-devices/worker-a/capacity", json={"manual_slots": 0})
        assert bad.status_code == 422

        updated = client.post("/api/v1/execution-devices/worker-a/capacity", json={"manual_slots": 1})
        assert updated.status_code == 200
        assert updated.json()["manual_slots"] == 1
        assert updated.json()["available_slots"] == 0


def test_new_approved_external_device_defaults_to_one_manual_slot() -> None:
    with _api_client() as (client, sessions):
        db = sessions()
        try:
            _seed_worker(db, "worker-new", status=WorkerStatus.PENDING_APPROVAL, slots=0)
            db.commit()
        finally:
            db.close()

        approved = client.post("/api/v1/execution-devices/worker-new/approve", json={})

        assert approved.status_code == 200
        assert approved.json()["manual_slots"] == 1
        assert approved.json()["effective_slots"] == 1


def test_delete_execution_device_moves_to_recycle_bin_and_restore_keeps_platform_worker() -> None:
    with _api_client() as (client, sessions):
        db = sessions()
        try:
            _seed_worker(db, "platform-worker", slots=2, platform=True)
            _seed_worker(db, "fake-device", slots=1)
            db.add(WorkerAccountTaskLease(lease_id="lease-fake", worker_id="fake-device", account_user_id="111111111111", task_id="task-prod", status=WorkerLeaseStatus.ACTIVE))
            db.add(WorkerCommand(command_id="cmd-fake", worker_id="fake-device", command_type="test"))
            db.add(WorkerEvent(worker_id="fake-device", event_type=WorkerEventType.EVENT_REPORT, severity="info", message="fake", trace_id="trace-fake"))
            db.commit()
        finally:
            db.close()

        protected = client.delete("/api/v1/execution-devices/platform-worker")
        deleted = client.delete("/api/v1/execution-devices/fake-device")
        devices = client.get("/api/v1/execution-devices").json()["items"]
        recycled = client.get("/api/v1/execution-devices/deleted").json()
        restored = client.post("/api/v1/execution-devices/fake-device/restore")
        restored_devices = client.get("/api/v1/execution-devices").json()["items"]
        ids = {item["worker_id"] for item in devices}
        restored_ids = {item["worker_id"] for item in restored_devices}

        assert protected.status_code == 400
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert "fake-device" not in ids
        assert "platform-worker" in ids
        assert recycled[0]["worker_id"] == "fake-device"
        assert restored.status_code == 200
        assert restored.json()["status"] == "offline"
        assert "fake-device" in restored_ids


def test_start_production_limits_and_execution_modes() -> None:
    with _api_client() as (client, sessions):
        db = sessions()
        try:
            _seed_worker(db, "platform-worker", slots=2, platform=True)
            _seed_worker(db, "worker-a", slots=1)
            _seed_worker(db, "worker-full", slots=1)
            db.add(WorkerAccountTaskLease(lease_id="lease-full", worker_id="worker-full", account_user_id="222222222222", task_id="task-prod", status=WorkerLeaseStatus.ACTIVE))
            db.commit()
        finally:
            db.close()

        payload = {
            "account_scope": {"mode": "specified", "account_user_ids": ["111111111111"]},
            "question_scope": {"mode": "pending"},
            "execution_mode": "platform_plus_devices",
            "device_scope": {"mode": "auto", "worker_ids": []},
            "limits": {"max_items_total": None, "failure_threshold": 3},
        }
        started = client.post("/api/v1/tasks/task-prod/auto-production/production/start", json=payload)
        assert started.status_code == 200
        run_config = started.json()["raw_adapter_run"]["run_config"]
        assert run_config["limits"]["max_items_total"] is None
        assert run_config["selected_worker_ids"] == ["platform-worker", "worker-a"]

        payload["limits"]["max_items_total"] = 0
        bad = client.post("/api/v1/tasks/task-prod/auto-production/production/start", json=payload)
        assert bad.status_code == 400
        assert bad.json()["detail"]["code"] == "INVALID_MAX_ITEMS_TOTAL"

        payload["limits"]["max_items_total"] = None
        payload["execution_mode"] = "platform"
        platform = client.post("/api/v1/tasks/task-prod/auto-production/production/start", json=payload)
        assert platform.json()["raw_adapter_run"]["run_config"]["selected_worker_ids"] == ["platform-worker"]

        payload["execution_mode"] = "devices"
        payload["device_scope"] = {"mode": "specified", "worker_ids": ["worker-full"]}
        full = client.post("/api/v1/tasks/task-prod/auto-production/production/start", json=payload)
        assert full.status_code == 400
        assert "可用并发" in full.json()["detail"]


def test_pause_stop_resume_routes() -> None:
    with _api_client() as (client, sessions):
        db = sessions()
        try:
            _seed_worker(db, "platform-worker", slots=2, platform=True)
            db.commit()
        finally:
            db.close()

        started = client.post(
            "/api/v1/tasks/task-prod/auto-production/production/start",
            json={
                "account_scope": {"mode": "specified", "account_user_ids": ["111111111111"]},
                "question_scope": {"mode": "pending"},
                "execution_mode": "platform",
                "device_scope": {"mode": "auto", "worker_ids": []},
                "limits": {"max_items_total": None, "failure_threshold": 3},
            },
        )
        run_id = started.json()["run_id"]

        paused = client.post(f"/api/v1/auto-answer-runs/{run_id}/pause")
        resumed = client.post(f"/api/v1/auto-answer-runs/{run_id}/resume")
        stopped = client.post(f"/api/v1/auto-answer-runs/{run_id}/stop")

        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "running_auto"
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopped"


def test_local_agent_suite_download_returns_zip_file(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        release_root = Path(tmp)
        suite = release_root / "aidp-local-suite-0.9.0.zip"
        suite.write_bytes(b"PK\x03\x04local-agent-suite")
        monkeypatch.setenv("AIDP_LOCAL_AGENT_RELEASE_ROOT", str(release_root))
        get_settings.cache_clear()
        try:
            with _api_client() as (client, _sessions):
                latest = client.get("/api/v1/local-agent/releases/latest")
                downloaded = client.get("/api/v1/local-agent/releases/latest/download-suite")
        finally:
            get_settings.cache_clear()

    assert latest.status_code == 200
    assert latest.json()["suite_name"] == suite.name
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/zip")
    assert "aidp-local-suite-0.9.0.zip" in downloaded.headers["content-disposition"]
    assert downloaded.content.startswith(b"PK")


def test_local_agent_suite_download_returns_404_when_file_missing(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("AIDP_LOCAL_AGENT_RELEASE_ROOT", tmp)
        get_settings.cache_clear()
        try:
            with _api_client() as (client, _sessions):
                response = client.get("/api/v1/local-agent/releases/latest/download-suite")
        finally:
            get_settings.cache_clear()

    assert response.status_code == 404
