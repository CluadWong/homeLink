"""管家服务（agentd）客户端：后端代理对话与计划状态，浏览器不直连 8100。

agentd 与后端是两个进程（计划表在 `agent.db`）。浏览器直连 8100 会跨源、还要
暴露 `AGENTD_TOKEN`，所以统一由后端代发。连不上抛 `AgentdUnavailableError`；
agentd 明确返回 4xx/5xx 时抛 `AgentdResponseError`，由路由层映射成对外错误码。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8100"
DEFAULT_TIMEOUT = 3.0
# 一轮对话要等模型推理（本机实测 P95 约 39 秒），代理超时必须明显长于 3 秒。
DEFAULT_CHAT_TIMEOUT = 120.0


class AgentdUnavailableError(RuntimeError):
    """管家服务不可达或返回格式异常。"""


class AgentdResponseError(RuntimeError):
    """管家服务返回了 HTTP 错误，带上状态码与 agentd 的 detail。"""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class AgentdClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        chat_timeout: float = DEFAULT_CHAT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.chat_timeout = chat_timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def chat(self, message: str, session_id: str | None = None) -> dict[str, Any]:
        """`POST /api/chat`；返回 `{"reply", "session_id"}`，不落库、不改本机数据。"""

        body: dict[str, Any] = {"message": message}
        if session_id:
            body["session_id"] = session_id
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=max(self.timeout, self.chat_timeout),
            ) as client:
                response = client.post("/api/chat", json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise AgentdUnavailableError(str(exc)) from exc
        if response.status_code >= 400:
            raise AgentdResponseError(response.status_code, _detail(response))
        try:
            payload = response.json()
        except ValueError as exc:
            raise AgentdUnavailableError("管家服务返回格式不对") from exc
        reply = payload.get("reply") if isinstance(payload, dict) else None
        session = payload.get("session_id") if isinstance(payload, dict) else None
        if not isinstance(reply, str) or not isinstance(session, str):
            raise AgentdUnavailableError("管家服务返回格式不对")
        return {"reply": reply, "session_id": session}

    def list_schedules(self) -> list[dict[str, Any]]:
        """`GET /api/schedules` 的 `schedules` 列表；字段见 docs/api-contract.md。"""

        headers = self._headers()
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


def _detail(response: httpx.Response) -> str:
    """取 agentd 的 `detail`；读不出来就退回状态码，避免把 HTML 直接透给浏览器。"""

    try:
        payload = response.json()
    except ValueError:
        return f"管家服务返回 {response.status_code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    text = str(detail).strip() if detail else ""
    return text[:200] or f"管家服务返回 {response.status_code}"


def client_from_env() -> AgentdClient:
    return AgentdClient(
        os.environ.get("HOMELINK_AGENTD_URL", DEFAULT_BASE_URL).strip().rstrip("/"),
        os.environ.get("HOMELINK_AGENTD_TOKEN", "").strip(),
    )
