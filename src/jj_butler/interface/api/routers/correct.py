"""校正端点：`POST /api/correct` —— 用户校正，来源恒 `confirmed`。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from jj_butler.interface.api.schemas import CorrectRequest
from jj_butler.interface.api.security import require_token

router = APIRouter(prefix="/api", tags=["correct"], dependencies=[Depends(require_token)])


@router.post("/correct")
def correct(request: Request, payload: CorrectRequest) -> dict:
    context = request.app.state.context
    record = context.states.correct(
        payload.device_id,
        payload.state,
        payload.reason,
        trace_id=request.state.trace_id,
    )
    return {
        "success": True,
        "device_id": record["device_id"],
        "state": record["state"],
        "state_source": record["state_source"],
    }
