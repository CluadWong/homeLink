"""健康检查（文档未定义响应形状，见交付说明）。"""

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
