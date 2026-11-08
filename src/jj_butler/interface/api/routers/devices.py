"""设备端点：GET / POST / GET{id} / PATCH / DELETE /api/devices。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from jj_butler.core.errors import AppError, ErrorCode
from jj_butler.interface.api.schemas import DeviceCreateRequest, DevicePatchRequest
from jj_butler.interface.api.security import require_token

router = APIRouter(prefix="/api/devices", tags=["devices"], dependencies=[Depends(require_token)])


@router.get("")
def list_devices(request: Request, room: str | None = Query(default=None)) -> dict:
    context = request.app.state.context
    return {
        "success": True,
        "devices": context.devices.list(room),
        "trace_id": request.state.trace_id,
    }


@router.post("")
def create_device(request: Request, payload: DeviceCreateRequest) -> dict:
    context = request.app.state.context
    device = context.devices.create(
        name=payload.name,
        room=payload.room,
        notes=payload.notes,
    )
    return {"success": True, **device, "trace_id": request.state.trace_id}


@router.get("/{device_id}")
def get_device(device_id: str, request: Request) -> dict:
    context = request.app.state.context
    device = context.devices.get(device_id)
    return {"success": True, **device, "trace_id": request.state.trace_id}


@router.patch("/{device_id}")
def patch_device(device_id: str, request: Request, payload: DevicePatchRequest) -> dict:
    context = request.app.state.context
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise AppError(ErrorCode.INVALID_REQUEST, "至少需要提供 name、room、notes 之一")
    if ("name" in fields and fields["name"] is None) or (
        "room" in fields and fields["room"] is None
    ):
        raise AppError(ErrorCode.INVALID_REQUEST, "name、room 不能为 null")

    device = context.devices.patch(device_id, fields)
    return {"success": True, **device, "trace_id": request.state.trace_id}


@router.delete("/{device_id}")
def delete_device(device_id: str, request: Request) -> dict:
    context = request.app.state.context
    deleted = context.devices.delete(device_id)
    return {"success": True, "deleted": deleted, "trace_id": request.state.trace_id}


@router.delete("/{device_id}/functions/{function_id}")
def delete_function(device_id: str, function_id: str, request: Request) -> dict:
    context = request.app.state.context
    deleted = context.devices.delete_function(device_id, function_id)
    return {"success": True, "deleted": deleted, "trace_id": request.state.trace_id}
