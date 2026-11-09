"""HomeLink HTTP 客户端：只访问 `/api/agent`，不碰旧的 CRUD 端点。

所有方法返回服务端 `result` 字段；失败统一抛 `HomeLinkError`。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from homelink_mcp.config import Settings
from homelink_mcp.errors import HomeLinkError

_AGENT_PREFIX = "/api/agent"


class HomeLinkClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._settings.base_url}{_AGENT_PREFIX}{path}"
        headers = {"Accept": "application/json"}
        if self._settings.token:
            headers["Authorization"] = f"Bearer {self._settings.token}"

        try:
            async with httpx.AsyncClient(timeout=self._settings.timeout) as client:
                response = await client.request(
                    method, url, params=params, json=body, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise HomeLinkError(
                "GATEWAY_TIMEOUT",
                f"请求 {url} 超时（{self._settings.timeout:g}s）",
            ) from exc
        except httpx.HTTPError as exc:
            raise HomeLinkError(
                "UNREACHABLE",
                f"连不上 HomeLink 服务 {self._settings.base_url}",
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise HomeLinkError(
                "BAD_RESPONSE",
                f"服务返回的不是 JSON（HTTP {response.status_code}）",
                status=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise HomeLinkError(
                "BAD_RESPONSE",
                "服务返回的 JSON 不是对象",
                status=response.status_code,
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
        return await self._request("GET", "/devices", params=params or None)

    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/devices/{quote(device_id, safe='')}/state")

    async def snapshot(self, *, room: str | None = None) -> dict[str, Any]:
        params = {"room": room} if room else None
        return await self._request("GET", "/snapshot", params=params)

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
        return await self._request("POST", "/control", body=body)
