from fastapi import APIRouter, HTTPException, Request

from app.api.v1.routes.task_auto_runs import _adapters, _state_dir
from app.services.task_auto_run_service import get_task_auto_run, pause_task_auto_run, resume_task_auto_run, stop_task_auto_run

router = APIRouter(prefix="/auto-answer-runs", tags=["auto-answer-runs"])


def _pause_run_state_if_present(run_id: str, request: Request) -> None:
    try:
        pause_task_auto_run(run_id, state_dir=_state_dir(request))
    except ValueError:
        # The alias route may be used with an adapter-backed run whose worker was
        # stopped successfully while the generic state file is already gone.
        return


@router.post("/{run_id}/pause")
async def pause_auto_answer_run(run_id: str, request: Request) -> dict:
    try:
        run = get_task_auto_run(run_id, adapters=_adapters(request), state_dir=_state_dir(request))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if run.adapter_key == "bon8":
        from app.api.v1.routes.task_auto_runs import _bon8_worker_registry, _worker_status_response

        status = await _bon8_worker_registry(request).stop(run.adapter_run_id)
        _pause_run_state_if_present(run.run_id, request)
        return {"run_id": run.run_id, "status": "paused", "worker_status": _worker_status_response(run.run_id, run.adapter_run_id, status).model_dump(mode="json")}
    from app.api.v1.routes.task_auto_runs import _generic_worker_registry, _generic_worker_status_response

    status = await _generic_worker_registry(request).stop(run.run_id)
    _pause_run_state_if_present(run.run_id, request)
    return {"run_id": run.run_id, "status": "paused", "worker_status": _generic_worker_status_response(run.run_id, run.adapter_run_id, status).model_dump(mode="json")}


@router.post("/{run_id}/stop")
def stop_auto_answer_run_alias(run_id: str, request: Request) -> dict:
    try:
        run = stop_task_auto_run(run_id, adapters=_adapters(request), state_dir=_state_dir(request))
        return run.model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{run_id}/resume")
def resume_auto_answer_run_alias(run_id: str, request: Request) -> dict:
    try:
        run = resume_task_auto_run(run_id, state_dir=_state_dir(request))
        return run.model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
