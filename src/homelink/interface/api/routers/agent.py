"""Agent 粗粒度端点：少量稳定动作，避免把 21 个 CRUD 端点直接暴露给模型。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from homelink.core.errors import AppError, ErrorCode
from homelink.interface.api.schemas import (
    AgentChatRequest,
    AgentControlRequest,
    AgentMetadataRequest,
    AgentSceneActionGroup,
    AgentSceneCreateRequest,
    AgentScenePatchRequest,
    AgentSceneRunRequest,
)
from homelink.interface.api.security import require_token
from homelink.interface.clients.agentd import AgentdResponseError, AgentdUnavailableError

router = APIRouter(prefix="/api/agent", tags=["agent"], dependencies=[Depends(require_token)])


def _actor(request: Request) -> str:
    raw = request.headers.get("X-Actor", "").strip()
    if len(raw) > 64:
        raise AppError(ErrorCode.INVALID_REQUEST, "X-Actor 不能超过 64 个字符")
    return raw or "user"


def _envelope(request: Request, result: object) -> dict:
    return {"success": True, "result": result, "trace_id": request.state.trace_id}


def _groups_payload(groups: list[AgentSceneActionGroup]) -> list[dict]:
    return [
        {
            "name": group.name,
            "capability": group.capability,
            "targets": [target.device_id for target in group.targets],
        }
        for group in groups
    ]


@router.get("/devices")
def find_devices(
    request: Request,
    room: str | None = None,
    type: str | None = None,
    name: str | None = None,
) -> dict:
    context = request.app.state.context
    devices = context.agent.find_devices(room=room, device_type=type, name=name)
    return _envelope(request, {"count": len(devices), "devices": devices})


@router.get("/devices/{device_id}")
def get_device(request: Request, device_id: str) -> dict:
    context = request.app.state.context
    return _envelope(request, context.agent.get_device(device_id))


@router.put("/devices/{device_id}/metadata")
def update_metadata(request: Request, device_id: str, payload: AgentMetadataRequest) -> dict:
    context = request.app.state.context
    result = context.agent.update_metadata(
        device_id,
        device_type=payload.type,
        aliases=payload.aliases,
        risk_level=payload.risk_level,
    )
    return _envelope(request, result)


@router.get("/devices/{device_id}/state")
def get_device_state(request: Request, device_id: str) -> dict:
    context = request.app.state.context
    return _envelope(request, context.agent.get_state(device_id))


@router.get("/snapshot")
def get_snapshot(request: Request, room: str | None = None) -> dict:
    context = request.app.state.context
    return _envelope(request, context.agent.snapshot(room=room))


@router.post("/control")
def control(request: Request, payload: AgentControlRequest) -> dict:
    context = request.app.state.context
    result = context.agent.control(
        device_id=payload.device_id,
        command=payload.command,
        parameters=payload.parameters,
        request_id=payload.request_id,
        confirm=payload.confirm,
        actor=_actor(request),
        trace_id=request.state.trace_id,
    )
    return _envelope(request, result)


@router.get("/commands/{request_id}")
def command_status(request: Request, request_id: str) -> dict:
    context = request.app.state.context
    return _envelope(request, context.agent.command_status(request_id))


@router.get("/capabilities")
def list_capabilities(request: Request) -> dict:
    """能力目录：能力名 → 支持该能力的设备，供场景编辑时过滤设备列表。"""

    context = request.app.state.context
    return _envelope(request, context.agent.list_capabilities())


@router.get("/scenes")
def list_scenes(request: Request) -> dict:
    context = request.app.state.context
    scenes = context.agent.list_scenes()
    return _envelope(request, {"count": len(scenes), "scenes": scenes})


@router.get("/scenes/{scene_id}")
def get_scene(request: Request, scene_id: str) -> dict:
    context = request.app.state.context
    return _envelope(request, context.agent.get_scene(scene_id))


@router.post("/scenes")
def create_scene(request: Request, payload: AgentSceneCreateRequest) -> dict:
    context = request.app.state.context
    items = (
        [{"device_id": item.device_id, "command": item.command} for item in payload.items]
        if payload.items is not None
        else None
    )
    groups = _groups_payload(payload.action_groups) if payload.action_groups is not None else None
    triggers = (
        [trigger.model_dump() for trigger in payload.triggers]
        if payload.triggers is not None
        else None
    )
    return _envelope(
        request,
        context.agent.create_scene(
            payload.name,
            items=items,
            action_groups=groups,
            triggers=triggers,
            enabled=payload.enabled,
        ),
    )


@router.patch("/scenes/{scene_id}")
def update_scene(request: Request, scene_id: str, payload: AgentScenePatchRequest) -> dict:
    context = request.app.state.context
    groups = _groups_payload(payload.action_groups) if payload.action_groups is not None else None
    triggers = (
        [trigger.model_dump() for trigger in payload.triggers]
        if payload.triggers is not None
        else None
    )
    return _envelope(
        request,
        context.agent.update_scene(
            scene_id,
            name=payload.name,
            enabled=payload.enabled,
            action_groups=groups,
            triggers=triggers,
        ),
    )


@router.delete("/scenes/{scene_id}")
def delete_scene(request: Request, scene_id: str) -> dict:
    context = request.app.state.context
    return _envelope(request, context.agent.delete_scene(scene_id))


@router.post("/scenes/{scene_id}/run")
def run_scene(request: Request, scene_id: str, payload: AgentSceneRunRequest | None = None) -> dict:
    context = request.app.state.context
    result = context.agent.run_scene(
        scene_id,
        confirm=payload.confirm if payload is not None else False,
        actor=_actor(request),
        trace_id=request.state.trace_id,
    )
    return _envelope(request, result)


@router.get("/scenes/{scene_id}/runs")
def list_scene_runs(request: Request, scene_id: str, limit: int = 10) -> dict:
    """场景最近执行记录：只读，按一次执行（trace_id）聚合。"""

    if not 1 <= limit <= 50:
        raise AppError(ErrorCode.INVALID_REQUEST, "limit 需在 1–50 之间")
    context = request.app.state.context
    return _envelope(request, context.agent.scene_runs(scene_id, limit=limit))


@router.post("/chat")
def chat(request: Request, payload: AgentChatRequest) -> dict:
    """界面文字对话（代理 agentd 的 `/api/chat`）：浏览器不直连 8100。"""

    try:
        result = request.app.state.agentd.chat(payload.message, payload.session_id)
    except AgentdUnavailableError:
        raise AppError(
            ErrorCode.DEVICE_OFFLINE,
            "管家服务（agentd）未启动或不可达；请确认 homelink-agentd 正在运行。",
        ) from None
    except AgentdResponseError as exc:
        # 429 是模型排队超时，502 是 MCP 掉线；两者都原样把人话带回前端。
        code = ErrorCode.EXECUTION_FAILED if exc.status == 502 else ErrorCode.DEVICE_OFFLINE
        raise AppError(code, exc.detail or "管家服务暂时不可用") from None
    return _envelope(request, result)


@router.get("/schedules")
def list_schedules(request: Request) -> dict:
    """定时任务状态（代理 agentd）：前端据此展示上次执行结果与失败原因。"""

    try:
        items = request.app.state.agentd.list_schedules()
    except AgentdUnavailableError:
        raise AppError(
            ErrorCode.DEVICE_OFFLINE,
            "调度服务（agentd）不可达，定时任务状态暂不可用；请确认 agentd 已启动后重试。",
        ) from None
    return _envelope(request, {"count": len(items), "schedules": items})


@router.post("/automations")
def create_automation(request: Request) -> dict:
    raise AppError(
        ErrorCode.NOT_SUPPORTED,
        "自动化规则暂不支持；当前只提供场景（一组功能的顺序执行）",
    )
