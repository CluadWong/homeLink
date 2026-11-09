"""房间端点：GET / POST / PATCH / DELETE /api/rooms。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from homelink.core.errors import AppError, ErrorCode
from homelink.interface.api.schemas import RoomCreateRequest, RoomPatchRequest
from homelink.interface.api.security import require_token

router = APIRouter(prefix="/api/rooms", tags=["rooms"], dependencies=[Depends(require_token)])


@router.get("")
def list_rooms(request: Request) -> dict:
    context = request.app.state.context
    return {
        "success": True,
        "rooms": context.rooms.list(),
        "trace_id": request.state.trace_id,
    }


@router.post("")
def create_room(request: Request, payload: RoomCreateRequest) -> dict:
    context = request.app.state.context
    room = context.rooms.create(
        name=payload.name,
        display_name=payload.display_name,
        sort_order=payload.sort_order,
    )
    return {"success": True, "room": room, "trace_id": request.state.trace_id}


@router.patch("/{name}")
def patch_room(name: str, request: Request, payload: RoomPatchRequest) -> dict:
    context = request.app.state.context
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise AppError(
            ErrorCode.INVALID_REQUEST, "至少需要提供 name、display_name、sort_order 之一"
        )
    if any(value is None for value in fields.values()):
        raise AppError(ErrorCode.INVALID_REQUEST, "name、display_name、sort_order 不能为 null")

    room = context.rooms.patch(name, fields)
    return {"success": True, "room": room, "trace_id": request.state.trace_id}


@router.delete("/{name}")
def delete_room(name: str, request: Request) -> dict:
    context = request.app.state.context
    deleted = context.rooms.delete(name)
    return {"success": True, "deleted": deleted, "trace_id": request.state.trace_id}
