"""健康检查与进程内指标。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from homelink.core.database import SCHEMA_VERSION
from homelink.interface.api.security import require_token

router = APIRouter(prefix="/api", tags=["health"], dependencies=[Depends(require_token)])


@router.get("/health")
def health(request: Request) -> dict:
    return {
        "success": True,
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "trace_id": request.state.trace_id,
    }


@router.get("/metrics")
def metrics(request: Request) -> dict:
    """后端健康 + 调用量 + 错误率 + 最近耗时，用于运维自检。

    进程内计数，重启清零；只统计 `/api/*`，本端点自身不计。
    """

    context = request.app.state.context
    snapshot = request.app.state.metrics.snapshot()
    return {
        "success": True,
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "gateway": {
            "mode": context.config.settings.gateway.mode,
            "base_url": context.config.settings.gateway.base_url,
        },
        **snapshot,
        "trace_id": request.state.trace_id,
    }
