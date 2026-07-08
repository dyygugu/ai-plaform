from pathlib import Path
from unittest.mock import patch

from app.services.aidp_3d_http_answer_service import Aidp3DAnswerError
from app.schemas.task_auto_runs import TaskAutoRunStartRequest
from app.services.aidp_3d_http_answer_service import AIDP_3D_RUBRIC_TASK_ID
from app.services.task_auto_run_service import TaskAutoRun3DRubricAdapter, default_task_auto_run_adapters
from app.services.task_auto_run_worker_service import GenericTaskAutoRunWorkerScheduler


class FakeAnswerService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def submit_one(self, *, account, account_user_id: str, task_id: str, node_id: str, run_id: str, submit_remote: bool = True) -> dict:
        self.calls.append(account_user_id)
        return {
            "attempted": True,
            "success": True,
            "submits_remote": submit_remote,
            "readback_ok": True,
            "temp_save_only": not submit_remote,
            "saved_to_task_ui": True,
            "item_id": f"item-{account_user_id}",
            "next_item_id": f"next-{account_user_id}" if submit_remote else "",
            "submitted_at": "2026-07-08T00:00:00+00:00" if submit_remote else "",
            "message": "3D HTTP 提交成功。" if submit_remote else "3D HTTP 暂存成功。",
        }


def _account_loader(account_user_id: str) -> dict:
    return {
        "userId": account_user_id,
        "name": f"账号{account_user_id}",
        "cookie": "session=ok",
        "referer": "https://aidp.juejin.cn/operation/task-v2/7658232870117527347/mark-v3/1?templateID=7658120776411467566",
        "tasks": [{"id": AIDP_3D_RUBRIC_TASK_ID, "processing": 1, "frontendNotSubmitted": 1}],
    }


def test_default_adapters_include_3d_rubric_adapter() -> None:
    assert any(getattr(adapter, "adapter_key", "") == "3d_rubric" for adapter in default_task_auto_run_adapters())


def test_3d_adapter_tick_submits_one_item_per_account(tmp_path: Path) -> None:
    answer_service = FakeAnswerService()
    adapter = TaskAutoRun3DRubricAdapter(
        answer_service=answer_service,
        account_loader=_account_loader,
        state_dir=tmp_path / "states",
        evidence_root=tmp_path / "evidence",
    )
    request = TaskAutoRunStartRequest(
        task_id=AIDP_3D_RUBRIC_TASK_ID,
        node_id="1",
        account_user_ids=["7630778503730253620", "7633857103195918123"],
        run_config={"ability_run_mode": "production", "production_max_items_per_account": 10, "rate_limit_per_minute": 100000},
    )

    with patch("app.services.task_auto_run_service.get_task_ability_run_gate", return_value={"can_start_production": True}):
        snapshot = adapter.start(None, request)
        ticked = adapter.tick(snapshot.adapter_run_id)

    assert set(answer_service.calls) == {"7630778503730253620", "7633857103195918123"}
    assert ticked.status == "running_auto"
    assert all(account.status == "submitted" for account in ticked.accounts)
    assert ticked.raw_adapter_run["submits_remote"] is True
    assert set(ticked.raw_adapter_run["account_evidence"]) == {"7630778503730253620", "7633857103195918123"}


def test_3d_trial_tick_temp_saves_without_formal_submit(tmp_path: Path) -> None:
    answer_service = FakeAnswerService()
    adapter = TaskAutoRun3DRubricAdapter(
        answer_service=answer_service,
        account_loader=_account_loader,
        state_dir=tmp_path / "states",
        evidence_root=tmp_path / "evidence",
    )
    request = TaskAutoRunStartRequest(
        task_id=AIDP_3D_RUBRIC_TASK_ID,
        node_id="1",
        account_user_ids=["7630778503730253620"],
        run_config={"ability_run_mode": "trial"},
    )

    snapshot = adapter.start(None, request)
    ticked = adapter.tick(snapshot.adapter_run_id)
    evidence = ticked.raw_adapter_run["account_evidence"]["7630778503730253620"]

    assert ticked.status == "completed"
    assert ticked.raw_adapter_run["submits_remote"] is False
    assert evidence["success"] is True
    assert evidence["submits_remote"] is False
    assert evidence["temp_save_only"] is True
    assert ticked.accounts[0].status == "temp_saved_waiting_submit"


def test_3d_trial_requires_all_accounts_to_temp_save(tmp_path: Path) -> None:
    class PartialAnswerService:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def submit_one(self, *, account, account_user_id: str, task_id: str, node_id: str, run_id: str, submit_remote: bool = True) -> dict:
            self.calls.append(account_user_id)
            if account_user_id == "7633857103195918123":
                return {
                    "attempted": False,
                    "success": False,
                    "submits_remote": False,
                    "readback_ok": False,
                    "no_current_item": True,
                    "error_code": "NO_CURRENT_ITEM",
                    "message": "当前账号没有 3D 当前题。",
                }
            return FakeAnswerService().submit_one(
                account=account,
                account_user_id=account_user_id,
                task_id=task_id,
                node_id=node_id,
                run_id=run_id,
                submit_remote=submit_remote,
            )

    answer_service = PartialAnswerService()
    adapter = TaskAutoRun3DRubricAdapter(
        answer_service=answer_service,
        account_loader=_account_loader,
        state_dir=tmp_path / "states",
        evidence_root=tmp_path / "evidence",
    )
    request = TaskAutoRunStartRequest(
        task_id=AIDP_3D_RUBRIC_TASK_ID,
        node_id="1",
        account_user_ids=["7630778503730253620", "7633857103195918123"],
        run_config={"ability_run_mode": "trial"},
    )

    snapshot = adapter.start(None, request)
    ticked = adapter.tick(snapshot.adapter_run_id)

    assert ticked.status == "completed_no_item"
    assert ticked.raw_adapter_run["submits_remote"] is False


def test_3d_production_gate_blocks_before_writer_call(tmp_path: Path) -> None:
    answer_service = FakeAnswerService()
    adapter = TaskAutoRun3DRubricAdapter(
        answer_service=answer_service,
        account_loader=_account_loader,
        state_dir=tmp_path / "states",
        evidence_root=tmp_path / "evidence",
    )
    request = TaskAutoRunStartRequest(
        task_id=AIDP_3D_RUBRIC_TASK_ID,
        node_id="1",
        account_user_ids=["7630778503730253620"],
        run_config={"ability_run_mode": "production", "production_max_items_per_account": 10},
    )

    with patch("app.services.task_auto_run_service.get_task_ability_run_gate", return_value={"can_start_production": False, "next_step": "请先完成试运行"}):
        snapshot = adapter.start(None, request)
        ticked = adapter.tick(snapshot.adapter_run_id)

    evidence = ticked.raw_adapter_run["account_evidence"]["7630778503730253620"]
    assert answer_service.calls == []
    assert ticked.status == "blocked"
    assert evidence["attempted"] is True
    assert evidence["submits_remote"] is False
    assert "请先完成试运行" in evidence["error"]


def test_3d_production_submit_limit_blocks_second_formal_submit(tmp_path: Path) -> None:
    answer_service = FakeAnswerService()
    adapter = TaskAutoRun3DRubricAdapter(
        answer_service=answer_service,
        account_loader=_account_loader,
        state_dir=tmp_path / "states",
        evidence_root=tmp_path / "evidence",
    )
    request = TaskAutoRunStartRequest(
        task_id=AIDP_3D_RUBRIC_TASK_ID,
        node_id="1",
        account_user_ids=["7630778503730253620"],
        run_config={"ability_run_mode": "production", "production_max_items_per_account": 1},
    )

    with patch("app.services.task_auto_run_service.get_task_ability_run_gate", return_value={"can_start_production": True}):
        snapshot = adapter.start(None, request)
        first = adapter.tick(snapshot.adapter_run_id)
        second = adapter.tick(snapshot.adapter_run_id)

    evidence = second.raw_adapter_run["account_evidence"]["7630778503730253620"]
    assert first.raw_adapter_run["submits_remote"] is True
    assert answer_service.calls == ["7630778503730253620"]
    assert second.status == "completed"
    assert evidence["attempted"] is False
    assert evidence["limit_reached"] is True


def test_3d_production_rate_limit_blocks_second_formal_submit(tmp_path: Path) -> None:
    answer_service = FakeAnswerService()
    adapter = TaskAutoRun3DRubricAdapter(
        answer_service=answer_service,
        account_loader=_account_loader,
        state_dir=tmp_path / "states",
        evidence_root=tmp_path / "evidence",
    )
    request = TaskAutoRunStartRequest(
        task_id=AIDP_3D_RUBRIC_TASK_ID,
        node_id="1",
        account_user_ids=["7630778503730253620"],
        run_config={"ability_run_mode": "production", "production_max_items_per_account": 10, "rate_limit_per_minute": 1},
    )

    with patch("app.services.task_auto_run_service.get_task_ability_run_gate", return_value={"can_start_production": True}):
        snapshot = adapter.start(None, request)
        first = adapter.tick(snapshot.adapter_run_id)
        second = adapter.tick(snapshot.adapter_run_id)

    evidence = second.raw_adapter_run["account_evidence"]["7630778503730253620"]
    assert first.raw_adapter_run["submits_remote"] is True
    assert answer_service.calls == ["7630778503730253620"]
    assert second.status == "running_auto"
    assert evidence["attempted"] is False
    assert evidence["rate_limited"] is True


def test_3d_adapter_preserves_post_submit_failure_evidence(tmp_path: Path) -> None:
    class FailingAnswerService:
        def submit_one(self, **_kwargs) -> dict:
            raise Aidp3DAnswerError(
                "READBACK_MISMATCH",
                "提交后回读异常",
                stage="readback_result",
                retryable=False,
                evidence={
                    "attempted": True,
                    "success": False,
                    "submits_remote": True,
                    "readback_ok": False,
                    "item_id": "item-1",
                    "temp_result": {"statusCode": 200},
                    "submit_result": {"statusCode": 200},
                },
            )

    adapter = TaskAutoRun3DRubricAdapter(
        answer_service=FailingAnswerService(),
        account_loader=_account_loader,
        state_dir=tmp_path / "states",
        evidence_root=tmp_path / "evidence",
    )
    request = TaskAutoRunStartRequest(
        task_id=AIDP_3D_RUBRIC_TASK_ID,
        node_id="1",
        account_user_ids=["7630778503730253620"],
        run_config={"ability_run_mode": "production", "production_max_items_per_account": 10},
    )

    snapshot = adapter.start(None, request)
    with patch("app.services.task_auto_run_service.get_task_ability_run_gate", return_value={"can_start_production": True}):
        ticked = adapter.tick(snapshot.adapter_run_id)
    evidence = ticked.raw_adapter_run["account_evidence"]["7630778503730253620"]

    assert ticked.status == "blocked"
    assert evidence["attempted"] is True
    assert evidence["submits_remote"] is True
    assert evidence["error_code"] == "READBACK_MISMATCH"


def test_generic_worker_stops_after_tick_exception() -> None:
    def tick() -> None:
        raise ValueError("3D 所有账号本轮 tick 均失败。")

    worker = GenericTaskAutoRunWorkerScheduler("run-1", tick_func=tick, interval_seconds=1)
    worker.status.active = True

    import asyncio

    asyncio.run(worker.run_once())
    status = worker.snapshot()

    assert status.last_ok is False
    assert status.active is False
    assert status.last_error == "3D 所有账号本轮 tick 均失败。"


def test_generic_worker_stops_after_final_tick_result() -> None:
    def tick() -> dict:
        return {"status": "blocked", "last_error": "all failed"}

    worker = GenericTaskAutoRunWorkerScheduler("run-1", tick_func=tick, interval_seconds=1)
    worker.status.active = True

    import asyncio

    asyncio.run(worker.run_once())
    status = worker.snapshot()

    assert status.last_ok is True
    assert status.active is False
