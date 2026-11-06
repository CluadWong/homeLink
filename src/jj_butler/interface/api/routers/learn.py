"""学码端点：start / capture / save / cancel。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from jj_butler.interface.api.schemas import (
    LearnCancelRequest,
    LearnSaveRequest,
    LearnStartRequest,
)
from jj_butler.interface.api.security import require_token

router = APIRouter(prefix="/api/learn", tags=["learn"], dependencies=[Depends(require_token)])


@router.post("/start")
def start_learn(request: Request, payload: LearnStartRequest) -> dict:
    context = request.app.state.context
    result = context.learn.start(payload.device_id, trace_id=request.state.trace_id)
    return {
        "success": True,
        "device_id": result["device_id"],
        "message": "已开始接收红外信号",
        "trace_id": request.state.trace_id,
    }


@router.get("/capture")
def capture(request: Request, device_id: str = Query(...)) -> dict:
    context = request.app.state.context
    result = context.learn.capture(device_id, trace_id=request.state.trace_id)
    return {"success": True, **result, "trace_id": request.state.trace_id}


@router.post("/save")
def save_learned_code(request: Request, payload: LearnSaveRequest) -> dict:
    context = request.app.state.context
    result = context.learn.save(
        payload.device_id,
        payload.name,
        payload.raw_code,
        trace_id=request.state.trace_id,
    )
    return {"success": True, **result, "trace_id": request.state.trace_id}


@router.post("/cancel")
def cancel_learn(request: Request, payload: LearnCancelRequest) -> dict:
    context = request.app.state.context
    result = context.learn.cancel(payload.device_id, trace_id=request.state.trace_id)
    return {"success": True, **result, "trace_id": request.state.trace_id}
