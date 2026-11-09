"""网关端点：`GET /api/gateway` 只读探测，`PUT /api/gateway` 写本地覆盖层。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from homelink.interface.api.schemas import GatewayBaseUrlRequest
from homelink.interface.api.security import require_token

router = APIRouter(prefix="/api", tags=["gateway"], dependencies=[Depends(require_token)])


@router.get("/gateway")
def gateway_status(request: Request, refresh: bool = Query(default=False)) -> dict:
    context = request.app.state.context
    trace_id = request.state.trace_id
    status = context.probe.status(refresh=refresh)
    effective = context.config.settings.gateway.base_url
    pending = context.pending_base_url
    status["base_url"] = pending if pending is not None else effective
    status["pending_restart"] = pending is not None and pending != effective
    return {
        "success": True,
        **status,
        "trace_id": trace_id,
    }


@router.put("/gateway")
def update_gateway(request: Request, payload: GatewayBaseUrlRequest) -> dict:
    context = request.app.state.context
    trace_id = request.state.trace_id
    context.config.set_local(
        "settings.yaml",
        "gateway.ir.base_url",
        payload.base_url,
        who="user",
        source="api",
        trace_id=trace_id,
    )
    context.pending_base_url = payload.base_url
    return {
        "success": True,
        "mode": context.config.settings.gateway.mode,
        "base_url": payload.base_url,
        "restart_required": True,
        "message": "网关地址已保存到本地覆盖层，重启服务后生效",
        "trace_id": trace_id,
    }
