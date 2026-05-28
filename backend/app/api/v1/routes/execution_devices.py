from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.execution_devices import (
    ExecutionDeviceApproveRequest,
    ExecutionDeviceCapacityRequest,
    ExecutionDeviceDeleteResponse,
    DeletedExecutionDeviceRead,
    ExecutionDeviceListResponse,
    ExecutionDeviceRead,
    ExecutionDeviceRenameRequest,
    ExecutionDeviceSummaryResponse,
)
from app.services.execution_device_service import (
    approve_execution_device,
    check_execution_device_updates,
    disable_execution_device,
    delete_execution_device,
    list_deleted_execution_devices,
    list_execution_device_reads,
    pause_execution_device_receiving,
    reject_execution_device,
    rename_execution_device,
    restore_execution_device,
    resume_execution_device_receiving,
    update_execution_device_capacity,
)

router = APIRouter(prefix="/execution-devices", tags=["execution-devices"])


@router.get("", response_model=ExecutionDeviceListResponse)
def read_execution_devices(
    q: str = "",
    status: str = "",
    approval_status: str = "",
    update_status: str = "",
    current_state: str = "",
    usable_for_production: Optional[bool] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ExecutionDeviceListResponse:
    items = list_execution_device_reads(
        db,
        q=q,
        status=status,
        approval_status=approval_status,
        update_status=update_status,
        current_state=current_state,
        usable_for_production=usable_for_production,
    )
    start = (page - 1) * page_size
    return ExecutionDeviceListResponse(items=items[start:start + page_size], total=len(items), page=page, page_size=page_size)


@router.get("/summary", response_model=ExecutionDeviceSummaryResponse)
def read_execution_device_summary(db: Session = Depends(get_db)) -> ExecutionDeviceSummaryResponse:
    items = list_execution_device_reads(db)
    return ExecutionDeviceSummaryResponse(
        total=len(items),
        online=sum(1 for item in items if item.status == "online"),
        running=sum(1 for item in items if item.current_state == "running"),
        pending_approval=sum(1 for item in items if item.approval_status == "pending"),
        abnormal=sum(1 for item in items if item.status in {"degraded", "disabled"} or bool(item.needs_attention)),
        update_needed=sum(1 for item in items if item.update_status == "update_available"),
    )


@router.get("/deleted", response_model=list[DeletedExecutionDeviceRead])
def read_deleted_execution_devices(db: Session = Depends(get_db)) -> list[DeletedExecutionDeviceRead]:
    return list_deleted_execution_devices(db)


@router.post("/{worker_id}/approve", response_model=ExecutionDeviceRead)
def approve_device(worker_id: str, payload: Optional[ExecutionDeviceApproveRequest] = None, db: Session = Depends(get_db)) -> ExecutionDeviceRead:
    item = approve_execution_device(db, worker_id, (payload.manual_slots if payload else 1))
    db.commit()
    return item


@router.post("/{worker_id}/reject", response_model=ExecutionDeviceRead)
def reject_device(worker_id: str, db: Session = Depends(get_db)) -> ExecutionDeviceRead:
    item = reject_execution_device(db, worker_id)
    db.commit()
    return item


@router.post("/{worker_id}/disable", response_model=ExecutionDeviceRead)
def disable_device(worker_id: str, db: Session = Depends(get_db)) -> ExecutionDeviceRead:
    item = disable_execution_device(db, worker_id)
    db.commit()
    return item


@router.delete("/{worker_id}", response_model=ExecutionDeviceDeleteResponse)
def delete_device(worker_id: str, db: Session = Depends(get_db)) -> ExecutionDeviceDeleteResponse:
    try:
        item = delete_execution_device(db, worker_id)
        db.commit()
        return item
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{worker_id}/restore", response_model=ExecutionDeviceRead)
def restore_device(worker_id: str, db: Session = Depends(get_db)) -> ExecutionDeviceRead:
    try:
        item = restore_execution_device(db, worker_id)
        db.commit()
        return item
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{worker_id}/rename", response_model=ExecutionDeviceRead)
def rename_device(worker_id: str, payload: ExecutionDeviceRenameRequest, db: Session = Depends(get_db)) -> ExecutionDeviceRead:
    item = rename_execution_device(db, worker_id, payload.device_name)
    db.commit()
    return item


@router.post("/{worker_id}/capacity", response_model=ExecutionDeviceRead)
def update_device_capacity(worker_id: str, payload: ExecutionDeviceCapacityRequest, db: Session = Depends(get_db)) -> ExecutionDeviceRead:
    item = update_execution_device_capacity(db, worker_id, payload.manual_slots)
    db.commit()
    return item


@router.post("/{worker_id}/pause-receiving", response_model=ExecutionDeviceRead)
def pause_device_receiving(worker_id: str, db: Session = Depends(get_db)) -> ExecutionDeviceRead:
    item = pause_execution_device_receiving(db, worker_id)
    db.commit()
    return item


@router.post("/{worker_id}/resume-receiving", response_model=ExecutionDeviceRead)
def resume_device_receiving(worker_id: str, db: Session = Depends(get_db)) -> ExecutionDeviceRead:
    item = resume_execution_device_receiving(db, worker_id)
    db.commit()
    return item


@router.post("/{worker_id}/check-updates", response_model=ExecutionDeviceRead)
def check_device_updates(worker_id: str, db: Session = Depends(get_db)) -> ExecutionDeviceRead:
    item = check_execution_device_updates(db, worker_id)
    db.commit()
    return item
