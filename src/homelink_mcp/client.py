"""HomeLink HTTP 客户端：只访问 `/api/agent`，不碰旧的 CRUD 端点。

所有方法返回服务端 `result` 字段；失败统一抛 `HomeLinkError`。
"""

from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import quote

import httpx

from homelink_mcp import logging as mcp_logging
from homelink_mcp.config import Settings
from homelink_mcp.errors import HomeLinkError

_AGENT_PREFIX = "/api/agent"


class HomeLinkClient:
    """长生命周期客户端：内部持有一个 httpx.AsyncClient 并跨请求复用连接池。

    `transport` 仅供测试注入（MockTransport）；生产路径不传，走默认连接池。
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._http_client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        """惰性创建 AsyncClient：首次请求建连，之后复用，避免每请求一次 TCP 握手。"""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=self._settings.timeout, transport=self._transport
            )
        return self._http_client

    async def aclose(self) -> None:
        """关闭内部连接池；关闭后再次请求会重建客户端。"""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        tool: str = "",
    ) -> Any:
        """发起一次请求并写一条 stderr JSONL 日志（ok / error_code / duration_ms）。"""
        trace_id = secrets.token_hex(4)
        started = time.perf_counter()
        try:
            result = await self._send(method, path, trace_id, params=params, body=body)
        except HomeLinkError as exc:
            mcp_logging.emit(
                "request",
                tool=tool,
                ok=False,
                error_code=exc.code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                trace_id=exc.trace_id or trace_id,
            )
            raise
        mcp_logging.emit(
            "request",
            tool=tool,
            ok=True,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            trace_id=trace_id,
        )
        return result

    async def _send(
        self,
        method: str,
        path: str,
        trace_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._settings.base_url}{_AGENT_PREFIX}{path}"
        # trace_id 由 _request 生成：后端中间件会采纳传入的 X-Trace-Id，据此串起审计链。
        headers = {
            "Accept": "application/json",
            "X-Actor": self._settings.actor,
            "X-Trace-Id": trace_id,
        }
        if self._settings.token:
            headers["Authorization"] = f"Bearer {self._settings.token}"

        try:
            response = await self._http().request(
                method, url, params=params, json=body, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise HomeLinkError(
                "GATEWAY_TIMEOUT",
                f"请求 {url} 超时（{self._settings.timeout:g}s）",
                trace_id=trace_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise HomeLinkError(
                "UNREACHABLE",
                f"连不上 HomeLink 服务 {self._settings.base_url}",
                trace_id=trace_id,
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise HomeLinkError(
                "BAD_RESPONSE",
                f"服务返回的不是 JSON（HTTP {response.status_code}）",
                status=response.status_code,
                trace_id=trace_id,
            ) from exc
        if not isinstance(payload, dict):
            raise HomeLinkError(
                "BAD_RESPONSE",
                "服务返回的 JSON 不是对象",
                status=response.status_code,
                trace_id=trace_id,
            )
        if payload.get("success") is True:
            return payload.get("result")
        raise HomeLinkError.from_envelope(payload, status=response.status_code)

    async def find_devices(
        self,
        *,
        room: str | None = None,
        device_type: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if room:
            params["room"] = room
        if device_type:
            params["type"] = device_type
        if name:
            params["name"] = name
        return await self._request(
            "GET", "/devices", params=params or None, tool="home_find_devices"
        )

    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/devices/{quote(device_id, safe='')}/state",
            tool="home_get_device_state",
        )

    async def snapshot(self, *, room: str | None = None) -> dict[str, Any]:
        params = {"room": room} if room else None
        return await self._request("GET", "/snapshot", params=params, tool="home_get_home_snapshot")

    async def control_device(
        self,
        *,
        device_id: str,
        command: str,
        request_id: str,
        parameters: dict[str, Any],
        confirm: bool,
    ) -> dict[str, Any]:
        body = {
            "device_id": device_id,
            "command": command,
            "parameters": parameters,
            "request_id": request_id,
            "confirm": confirm,
        }
        return await self._request("POST", "/control", body=body, tool="home_control_device")

    async def run_scene(self, scene_id: str, *, confirm: bool) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/scenes/{quote(scene_id, safe='')}/run",
            body={"confirm": confirm},
            tool="home_run_scene",
        )

    async def create_automation(self, rule: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request(
            "POST", "/automations", body={"rule": rule or {}}, tool="home_create_automation"
        )
