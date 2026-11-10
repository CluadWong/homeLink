"""HomeLink MCP 服务：把 `/api/agent` 的能力注册成 MCP 工具。

传输方式为 stdio，供 MCP Inspector 或本地 Agent 以子进程方式拉起：

    python -m homelink_mcp
    homelink-mcp
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from homelink_mcp.client import HomeLinkClient
from homelink_mcp.config import load_settings

INSTRUCTIONS = (
    "家庭红外设备管家。设备支持什么以 capabilities 为准，不要猜；"
    "控制类是整条红外码回放，parameters 必须为空对象；"
    "state 是虚拟状态，控制成功后仍是 unknown，不要声称已确认。"
)

mcp = FastMCP("homelink", instructions=INSTRUCTIONS)


def _client() -> HomeLinkClient:
    return HomeLinkClient(load_settings())


@mcp.tool()
async def home_find_devices(
    room: str | None = None,
    device_type: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """按房间、设备类型或名称（含别名）检索设备。

    Args:
        room: 房间标识，以返回值中的 room 为准，例如 bedroom、living_room。
        device_type: 设备类型，例如 light、fan、air_conditioner；不确定时省略。
        name: 设备名或别名，做包含匹配，例如「客厅灯」「大灯」。

    Returns:
        {"count": int, "devices": [...]}。设备模型含 type、room、aliases、risk_level、
        online、capabilities 与虚拟状态；控制前先用它拿 device_id 与可用 command。
    """
    return await _client().find_devices(room=room, device_type=device_type, name=name)


@mcp.tool()
async def home_get_device_state(device_id: str) -> dict[str, Any]:
    """查询单台设备的虚拟状态。

    Args:
        device_id: 设备 ID，必须来自 home_find_devices 或 home_get_home_snapshot 的结果。

    Returns:
        {"device_id": str, "state": {"power": str}, "state_source": str,
        "state_updated_at": str | None, "state_readback": false}。
        红外无回传：正常为 unknown，只有用户手动校正过才是 confirmed。
    """
    return await _client().get_device_state(device_id)


@mcp.tool()
async def home_get_home_snapshot(room: str | None = None) -> dict[str, Any]:
    """获取全屋快照：网关状态、设备列表与场景列表。

    Args:
        room: 只看某个房间；省略表示全屋。

    Returns:
        {"gateway": {...}, "devices": [...], "scenes": [...], "generated_at": str}。
        适合回答「家里现在什么情况」，也用来发现可运行的 scene_id。
    """
    return await _client().snapshot(room=room)


@mcp.tool()
async def home_control_device(
    device_id: str,
    command: str,
    request_id: str,
    parameters: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """对一台设备执行一个已学码功能（整条红外码回放）。

    Args:
        device_id: 设备 ID，来自 home_find_devices 或快照。
        command: 该设备 capabilities 中的 command 或 command_id；不在列表里就是做不到。
        request_id: 幂等键，由运行时注入，重试同一条指令会自动复用同一值，不要自己编造。
        parameters: 必须为空对象。红外码是整条回放，传亮度/温度等参数会被拒绝。
        confirm: risk_level 为 high 的设备须由用户明确同意后传 true，否则报 CONFIRMATION_REQUIRED。

    Returns:
        {"request_id": str, "status": "sent", "replayed": bool, "device_id": str,
        "function_id": str, "command": str, "state": "unknown", "message": str, "trace_id": str}。
        status 只有 sent（信号已发出）；失败以错误形式返回，不会返回 failed。
    """
    return await _client().control_device(
        device_id=device_id,
        command=command,
        request_id=request_id,
        parameters=parameters or {},
        confirm=confirm,
    )


@mcp.tool()
async def home_run_scene(scene_id: str, confirm: bool = False) -> dict[str, Any]:
    """按顺序执行一个已存在的场景。

    Args:
        scene_id: 场景 ID，来自 home_get_home_snapshot 返回的 scenes 列表。
        confirm: 场景 contains_high_risk 为 true 时须由用户明确同意后传 true，
            否则报 CONFIRMATION_REQUIRED。

    Returns:
        {"scene_id": str, "status": "sent" | "partial" | "failed",
        "results": [{"device_id", "command", "status", "request_id" | "error_code", "message"}]}。
        整体失败不抛错，按 results 逐条向用户汇报失败项。
    """
    return await _client().run_scene(scene_id, confirm=confirm)


@mcp.tool()
async def home_create_automation(rule: dict[str, Any] | None = None) -> dict[str, Any]:
    """创建自动化规则（当前不支持，固定返回 NOT_SUPPORTED）。

    Args:
        rule: 规则草案，服务端不解析，仅用于表达意图。

    Returns:
        失败：not supported。需要定时能力时应告知用户改用手动触发场景或外部调度。
    """
    return await _client().create_automation(rule)


def main() -> None:
    """以 stdio 传输启动 MCP 服务。"""

    mcp.run()
