"""常驻 Agent 服务（daemon）的单元测试：假 MCP 连接 + 假 Ollama，不碰真实模型。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from homelink_agent import agent as agent_mod
from homelink_agent.config import AgentSettings, load_settings
from homelink_agent.daemon import McpConnection, Metrics, SessionStore, create_agent_app
from homelink_agent.llm import OllamaError
from mcp import ClientSession


def _settings(**overrides: Any) -> AgentSettings:
    return replace(load_settings(), audit_path="off", **overrides)


class FakeConnection(McpConnection):
    """跳过真实 MCP 子进程，只提供会话占位与工具表。"""

    def __init__(self) -> None:
        super().__init__(_settings())
        self.session = object.__new__(ClientSession)  # 跳过 __init__，仅作占位
        self.tools: list[dict[str, Any]] = [
            {"type": "function", "function": {"name": "home_find_devices", "parameters": {}}}
        ]

    async def start(self) -> None:  # pragma: no cover - 测试不触发生命周期
        raise AssertionError("注入的假连接不应被启动")

    async def stop(self) -> None:
        pass

    async def ensure(self) -> None:
        pass


@pytest.fixture
def fake_chat(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, Any]]]:
    """把 agent 主循环里的 chat 换成记录器；返回每次调用收到的 messages。"""

    seen: list[list[dict[str, Any]]] = []

    async def fake_chat(
        settings: AgentSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        seen.append(messages)
        return {"content": "好的，已处理"}

    monkeypatch.setattr(agent_mod, "chat", fake_chat)
    return seen


def _client(fake_conn: FakeConnection) -> TestClient:
    return TestClient(create_agent_app(_settings(), connection=fake_conn, warmup=False))


def test_chat_replies_and_history_carries_over(
    fake_chat: list[list[dict[str, Any]]],
) -> None:
    with _client(FakeConnection()) as client:
        first = client.post("/api/chat", json={"message": "家里有哪些设备"})
        assert first.status_code == 200
        body = first.json()
        assert body["reply"] == "好的，已处理"
        assert len(body["session_id"]) == 12

        second = client.post(
            "/api/chat", json={"message": "打开它", "session_id": body["session_id"]}
        )
        assert second.status_code == 200
        assert second.json()["session_id"] == body["session_id"]

    assert fake_chat[0][-1] == {"role": "user", "content": "家里有哪些设备"}
    # 第二轮带上了上一轮的 user/assistant 历史
    assert fake_chat[1][-3:] == [
        {"role": "user", "content": "家里有哪些设备"},
        {"role": "assistant", "content": "好的，已处理"},
        {"role": "user", "content": "打开它"},
    ]


def test_chat_ollama_error_maps_to_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_chat(
        settings: AgentSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raise OllamaError("连不上 Ollama")

    monkeypatch.setattr(agent_mod, "chat", broken_chat)

    with _client(FakeConnection()) as client:
        response = client.post("/api/chat", json={"message": "你好"})

    assert response.status_code == 503
    assert "连不上 Ollama" in response.json()["detail"]


def test_health_reports_connection_and_sessions(
    fake_chat: list[list[dict[str, Any]]],
) -> None:
    with _client(FakeConnection()) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        client.post("/api/chat", json={"message": "在吗"})

        health = client.get("/api/health").json()
        assert health["sessions"] == 1
        assert health["tools"] == 1
        assert health["mcp_connected"] is True


def test_max_sessions_eviction_and_drop(
    fake_chat: list[list[dict[str, Any]]],
) -> None:
    app = create_agent_app(_settings(max_sessions=1), connection=FakeConnection(), warmup=False)

    with TestClient(app) as client:
        first = client.post("/api/chat", json={"message": "一", "session_id": "s1"}).json()
        client.post("/api/chat", json={"message": "二", "session_id": "s2"})

        assert client.get("/api/health").json()["sessions"] == 1  # s1 被挤掉
        assert (
            client.post(
                "/api/chat", json={"message": "再来", "session_id": first["session_id"]}
            ).status_code
            == 200
        )  # s1 重新创建也不报错

        assert client.delete("/api/sessions/s1").json()["dropped"] is True
        assert client.delete("/api/sessions/s1").json()["dropped"] is False


def test_history_is_capped() -> None:
    store = SessionStore(_settings())
    item = store.get("s")
    history: list[dict[str, str]] = item["history"]
    for index in range(30):
        history.append({"role": "user", "content": str(index)})
        history.append({"role": "assistant", "content": str(index)})
        del history[:-20]  # 与 daemon 端点同款裁剪

    assert len(history) == 20
    assert history[0]["content"] == "20"  # 只留最近 10 轮（u20..a29）


# ---------------------------------------------------------------- /api/metrics


class _FakeToolResult:
    def __init__(self) -> None:
        block = SimpleNamespace(type="text", text="ok")
        self.content: list[Any] = [block]
        self.isError = False


class ToolSessionStub:
    """记录调用次数的最小 ClientSession 替身。"""

    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _FakeToolResult:
        self.calls += 1
        return _FakeToolResult()


class ToolFakeConnection(FakeConnection):
    """session 换成可记录 call_tool 的替身。"""

    def __init__(self) -> None:
        super().__init__()
        self.session = ToolSessionStub()  # type: ignore[assignment]


def test_metrics_counters_after_three_chats(
    fake_chat: list[list[dict[str, Any]]],
) -> None:
    with _client(FakeConnection()) as client:
        for index in range(3):
            response = client.post("/api/chat", json={"message": f"问题{index}"})
            assert response.status_code == 200

        body = client.get("/api/metrics").json()

    assert body["requests_total"] == 3
    assert body["errors_total"] == 0
    assert body["tool_calls_total"] == 0
    assert body["latency_ms"]["p50"] is not None
    assert body["latency_ms"]["p95"] >= body["latency_ms"]["p50"]
    assert body["sessions"] >= 1
    assert body["mcp_connected"] is True
    assert "问题" not in str(body)  # 不泄露用户输入


def test_metrics_counts_errors_on_503(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken_chat(
        settings: AgentSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raise OllamaError("连不上 Ollama")

    monkeypatch.setattr(agent_mod, "chat", broken_chat)

    with _client(FakeConnection()) as client:
        client.post("/api/chat", json={"message": "你好"})
        body = client.get("/api/metrics").json()

    assert body["requests_total"] == 1
    assert body["errors_total"] == 1


def test_metrics_counts_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """带工具调用的轮次：tool_calls_total 按 on_tool 回调计数。"""

    async def chatting_chat(
        settings: AgentSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if any("tool_calls" in item for item in messages):
            return {"content": "已查完"}
        return {
            "tool_calls": [
                {"function": {"name": "home_find_devices", "arguments": {}}},
            ]
        }

    monkeypatch.setattr(agent_mod, "chat", chatting_chat)

    with _client(ToolFakeConnection()) as client:
        response = client.post("/api/chat", json={"message": "家里有哪些设备"})
        assert response.status_code == 200

        body = client.get("/api/metrics").json()

    assert body["tool_calls_total"] == 1
    assert body["requests_total"] == 1


def test_metrics_percentile_and_sample_cap() -> None:
    metrics = Metrics(max_latency_samples=3)
    for latency in (10.0, 20.0, 30.0, 40.0):  # 40 挤掉 10
        metrics.record_request(error=False, latency_ms=latency)

    assert metrics.percentile(50) == 30.0  # 最近秩：ceil(0.5*3)=2 → 第 2 小
    assert metrics.percentile(95) == 40.0  # ceil(0.95*3)=3 → 最大
    assert metrics.requests_total == 4
    assert metrics.errors_total == 0

    assert Metrics().percentile(50) is None  # 无样本返回 None
