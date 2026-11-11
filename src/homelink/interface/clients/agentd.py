"""调度服务（agentd）客户端：只读计划状态，供后端代理给前端展示。

agentd 与后端是两个进程，计划表在 `agent.db`；浏览器不直连 8100（无 CORS、
token 也不同源），所以由后端代读。连不上就抛 `AgentdUnavailableError`，上层转成
503 人话，前端按「暂不可用」展示，不影响场景列表本身。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8100"
DEFAULT_TIMEOUT = 3.0


class AgentdUnavailableError(RuntimeError):
    """调度服务不可达或返回格式异常。"""


class AgentdClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def list_schedules(self) -> list[dict[str, Any]]:
        """`GET /api/schedules` 的 `schedules` 列表；字段见 docs/api-contract.md。"""

        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get("/api/schedules", headers=headers)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AgentdUnavailableError(str(exc)) from exc
        items = payload.get("schedules") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise AgentdUnavailableError("调度服务返回格式不对")
        return [item for item in items if isinstance(item, dict)]


def client_from_env() -> AgentdClient:
    return AgentdClient(
        os.environ.get("HOMELINK_AGENTD_URL", DEFAULT_BASE_URL).strip().rstrip("/"),
        os.environ.get("HOMELINK_AGENTD_TOKEN", "").strip(),
    )
