"""状态端点：`GET /api/state` —— 全部设备最新状态与传感器占位。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from homelink.interface.api.security import require_token

router = APIRouter(prefix="/api", tags=["state"], dependencies=[Depends(require_token)])


@router.get("/state")
def get_state(request: Request) -> dict:
    context = request.app.state.context
    return {
        "devices": context.states.list_with_devices(),
        "sensors": {
            "available": False,
            "temperature": None,
            "humidity": None,
            "presence": None,
        },
    }
