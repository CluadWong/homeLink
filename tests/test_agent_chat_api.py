"""界面文字对话代理：成功、agentd 未启动、模型忙、MCP 掉线与入参校验。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from homelink.interface.api.app import create_app
from homelink.interface.clients.agentd import AgentdResponseError, AgentdUnavailableError


class _StubAgentd:
    def __init__(
        self,
        reply: str = "已经帮你打开卧室风扇。",
        *,
        error: bool = False,
        status: int | None = None,
        detail: str = "",
    ) -> None:
        self._reply = reply
        self._error = error
        self._status = status
        self._detail = detail
        self.calls: list[tuple[str, str | None]] = []

    def chat(self, message: str, session_id: str | None = None) -> dict[str, Any]:
        self.calls.append((message, session_id))
        if self._error:
            raise AgentdUnavailableError("connection refused")
        if self._status is not None:
            raise AgentdResponseError(self._status, self._detail)
        return {"reply": self._reply, "session_id": session_id or "sess-new"}


def test_chat_is_proxied_with_session(context) -> None:
    app = create_app(context)
    app.state.agentd = _StubAgentd()

    with TestClient(app) as client:
        response = client.post(
            "/api/agent/chat",
            json={"message": "打开卧室风扇", "session_id": "sess-1"},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["result"]["reply"] == "已经帮你打开卧室风扇。"
    assert payload["result"]["session_id"] == "sess-1"
    assert payload["trace_id"]
    assert app.state.agentd.calls == [("打开卧室风扇", "sess-1")]


def test_chat_without_session_gets_new_one(context) -> None:
    app = create_app(context)
    app.state.agentd = _StubAgentd()

    with TestClient(app) as client:
        response = client.post("/api/agent/chat", json={"message": "现在几度"})

    assert response.status_code == 200
    assert response.json()["result"]["session_id"] == "sess-new"
    assert app.state.agentd.calls == [("现在几度", None)]


def test_chat_unavailable_maps_to_503(context) -> None:
    app = create_app(context)
    app.state.agentd = _StubAgentd(error=True)

    with TestClient(app) as client:
        response = client.post("/api/agent/chat", json={"message": "打开风扇"})

    payload = response.json()
    assert response.status_code == 503
    assert payload["error"] == "DEVICE_OFFLINE"
    assert "agentd" in payload["message"]


def test_chat_busy_maps_to_503_with_detail(context) -> None:
    app = create_app(context)
    app.state.agentd = _StubAgentd(status=429, detail="模型忙：等待超过 30 秒，请稍后重试")

    with TestClient(app) as client:
        response = client.post("/api/agent/chat", json={"message": "打开风扇"})

    payload = response.json()
    assert response.status_code == 503
    assert payload["error"] == "DEVICE_OFFLINE"
    assert "模型忙" in payload["message"]


def test_chat_mcp_drop_maps_to_502(context) -> None:
    app = create_app(context)
    app.state.agentd = _StubAgentd(status=502, detail="MCP 掉线，连接已重置")

    with TestClient(app) as client:
        response = client.post("/api/agent/chat", json={"message": "打开风扇"})

    payload = response.json()
    assert response.status_code == 502
    assert payload["error"] == "EXECUTION_FAILED"
    assert "MCP" in payload["message"]


def test_chat_rejects_blank_message(context) -> None:
    app = create_app(context)
    app.state.agentd = _StubAgentd()

    with TestClient(app) as client:
        response = client.post("/api/agent/chat", json={"message": "   "})

    assert response.status_code == 400
    assert app.state.agentd.calls == []
