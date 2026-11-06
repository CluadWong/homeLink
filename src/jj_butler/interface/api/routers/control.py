"""控制端点：`POST /api/control` —— 只接受 `function_id`。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from jj_butler.interface.api.schemas import ControlRequest
from jj_butler.interface.api.security import require_token

router = APIRouter(prefix="/api", tags=["control"], dependencies=[Depends(require_token)])


@router.post("/control")
def control(request: Request, payload: ControlRequest) -> dict:
    context = request.app.state.context
    trace_id = request.state.trace_id
    result = context.control.execute(payload.function_id, trace_id=trace_id)
    return {"success": True, "result": result, "trace_id": trace_id}
