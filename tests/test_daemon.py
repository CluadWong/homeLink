"""常驻 Agent 服务（daemon）的单元测试：假 MCP 连接 + 假 Ollama，不碰真实模型。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from homelink_agent import agent as agent_mod
from homelink_agent.config import AgentSettings, load_settings
from homelink_agent.daemon import (
    McpConnection,
    Metrics,
    SessionStore,
    _schedule_runner,
    create_agent_app,
)
from homelink_agent.llm import OllamaError
from homelink_agent.scheduler import Schedule, ScheduleStore
from mcp import ClientSession


def _settings(**overrides: Any) -> AgentSettings:
    # 审计与 token 默认关闭/清空，调度库默认内存，避免宿主机环境变量与真实文件影响测试
    return replace(
        load_settings(),
        audit_path=overrides.pop("audit_path", "off"),
        daemon_token=overrides.pop("daemon_token", ""),
        agent_db_path=overrides.pop("agent_db_path", ":memory:"),
        **overrides,
    )


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


def _client(
    fake_conn: FakeConnection,
    settings: AgentSettings | None = None,
    scene_lookup: Any = None,
) -> TestClient:
    return TestClient(
        create_agent_app(
            settings or _settings(),
            connection=fake_conn,
            warmup=False,
            start_scheduler=False,
            scene_lookup=scene_lookup,
        )
    )


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


# ---------------------------------------------------------------- Bearer 鉴权


def test_bearer_auth_enforced_when_token_set(
    fake_chat: list[list[dict[str, Any]]],
) -> None:
    settings = _settings(daemon_token="s3cret")

    with _client(FakeConnection(), settings=settings) as client:
        assert client.get("/api/health").status_code == 200  # health 永远免鉴权
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
        assert (
            client.get("/api/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        )
        assert client.get("/api/schedules").status_code == 401
        assert (
            client.post(
                "/api/chat",
                json={"message": "hi"},
                headers={"Authorization": "Bearer s3cret"},
            ).status_code
            == 200
        )


def test_bearer_auth_off_when_token_empty(
    fake_chat: list[list[dict[str, Any]]],
) -> None:
    with _client(FakeConnection(), settings=_settings()) as client:
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 200


# ---------------------------------------------------------------- 队列与背压


def _post_pair(monkeypatch: pytest.MonkeyPatch, queue_timeout: float) -> Any:
    """并发两个 chat 请求：第一个先持锁，第二个排队。"""
    seen: list[list[dict[str, Any]]] = []

    async def recording_slow_chat(
        settings: AgentSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        seen.append(messages)
        await asyncio.sleep(0.5)
        return {"content": "慢"}

    monkeypatch.setattr(agent_mod, "chat", recording_slow_chat)
    app = create_agent_app(
        _settings(queue_timeout=queue_timeout),
        connection=FakeConnection(),
        warmup=False,
        start_scheduler=False,
    )

    async def scenario() -> Any:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            first = asyncio.create_task(ac.post("/api/chat", json={"message": "第一个"}))
            await asyncio.sleep(0.25)  # 第一个已持锁执行中
            second = await ac.post("/api/chat", json={"message": "第二个"})
            return await first, second, len(seen)

    return asyncio.run(scenario())


def test_queue_timeout_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second, executed = _post_pair(monkeypatch, queue_timeout=0.2)

    assert second.status_code == 429
    assert "模型忙" in second.json()["detail"]
    assert first.status_code == 200  # 不丢请求：先到的正常完成
    assert executed == 1  # 被拒的不执行，也不重复执行


def test_queue_waits_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second, executed = _post_pair(monkeypatch, queue_timeout=3.0)

    assert first.status_code == 200
    assert second.status_code == 200  # 排队等到锁，正常执行
    assert second.json()["reply"] == "慢"
    assert executed == 2


# ---------------------------------------------------------------- 优雅退出


def _audit_events(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_shutdown_audits_and_clears_sessions(
    fake_chat: list[list[dict[str, Any]]], tmp_path: Path
) -> None:
    audit_file = tmp_path / "audit.jsonl"

    with _client(FakeConnection(), settings=_settings(audit_path=str(audit_file))) as client:
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 200

    shutdown = [r for r in _audit_events(audit_file) if r["event"] == "shutdown"]
    assert len(shutdown) == 1
    assert shutdown[0]["sessions"] == 1  # 记录清空前的会话数
    assert shutdown[0]["mcp_connected"] is True  # 注入连接由测试自行管理


def test_shutdown_stops_own_mcp_subprocess(tmp_path: Path) -> None:
    """own 连接（真实 McpConnection）：退出时收掉 stdio 子进程，不留孤儿。"""

    audit_file = tmp_path / "audit.jsonl"
    app = create_agent_app(
        _settings(audit_path=str(audit_file)), warmup=False, start_scheduler=False
    )

    with TestClient(app):
        pass  # 启动会拉起真实 homelink_mcp 子进程，退出时必须收掉

    shutdown = [r for r in _audit_events(audit_file) if r["event"] == "shutdown"]
    assert len(shutdown) == 1
    assert shutdown[0]["mcp_connected"] is False  # 子进程已关闭


# ---------------------------------------------------------------- 定时任务（P3）

LOW_RISK_SCENE = {"id": "scene-1", "name": "回家", "contains_high_risk": False}
HIGH_RISK_SCENE = {"id": "scene-2", "name": "睡前", "contains_high_risk": True}


class FakeSceneLookup:
    def __init__(self, scenes: dict[str, dict[str, Any]]) -> None:
        self.scenes = scenes
        self.calls: list[str] = []

    async def __call__(self, scene_id: str) -> dict[str, Any] | None:
        self.calls.append(scene_id)
        return self.scenes.get(scene_id)


class BrokenSceneLookup:
    async def __call__(self, scene_id: str) -> dict[str, Any] | None:
        raise RuntimeError("后端不可用")


def test_schedule_create_list_delete_and_persist(tmp_path: Path) -> None:
    db = tmp_path / "agent.db"
    lookup = FakeSceneLookup({"scene-1": LOW_RISK_SCENE})

    with _client(
        FakeConnection(), settings=_settings(agent_db_path=str(db)), scene_lookup=lookup
    ) as client:
        created = client.post("/api/schedules", json={"scene_id": "scene-1", "cron": "0 7 * * *"})
        assert created.status_code == 200
        row = created.json()
        assert row["scene_id"] == "scene-1"
        assert row["enabled"] is True
        assert row["confirm"] is False
        assert lookup.calls == ["scene-1"]

        listed = client.get("/api/schedules").json()
        assert listed["count"] == 1
        assert listed["schedules"][0]["id"] == row["id"]

        assert client.delete(f"/api/schedules/{row['id']}").json() == {"deleted": True}
        assert client.get("/api/schedules").json() == {"count": 0, "schedules": []}
        assert client.delete(f"/api/schedules/{row['id']}").json() == {"deleted": False}

    # 另一个 store 实例能读到同一文件：重启后计划仍在
    reopened = ScheduleStore(str(db))
    try:
        assert reopened.list_all() == []
    finally:
        reopened.close()


def test_schedule_persists_after_restart(tmp_path: Path) -> None:
    db = tmp_path / "agent.db"
    lookup = FakeSceneLookup({"scene-1": LOW_RISK_SCENE})
    with _client(
        FakeConnection(), settings=_settings(agent_db_path=str(db)), scene_lookup=lookup
    ) as client:
        created = client.post("/api/schedules", json={"scene_id": "scene-1", "cron": "0 7 * * *"})
        assert created.status_code == 200
        schedule_id = created.json()["id"]

    reopened = ScheduleStore(str(db))
    try:
        assert [item.id for item in reopened.list_all()] == [schedule_id]
    finally:
        reopened.close()


def test_high_risk_scene_requires_confirm_at_creation() -> None:
    lookup = FakeSceneLookup({"scene-2": HIGH_RISK_SCENE})
    with _client(FakeConnection(), scene_lookup=lookup) as client:
        denied = client.post("/api/schedules", json={"scene_id": "scene-2", "cron": "0 22 * * *"})
        assert denied.status_code == 409
        assert "confirm" in denied.json()["detail"]

        allowed = client.post(
            "/api/schedules", json={"scene_id": "scene-2", "cron": "0 22 * * *", "confirm": True}
        )
        assert allowed.status_code == 200
        assert allowed.json()["confirm"] is True


def test_schedule_rejects_invalid_cron_unknown_scene_and_backend_error() -> None:
    lookup = FakeSceneLookup({"scene-1": LOW_RISK_SCENE})
    with _client(FakeConnection(), scene_lookup=lookup) as client:
        bad_cron = client.post("/api/schedules", json={"scene_id": "scene-1", "cron": "61 * * * *"})
        assert bad_cron.status_code == 422
        missing = client.post("/api/schedules", json={"scene_id": "nope", "cron": "0 7 * * *"})
        assert missing.status_code == 404
        assert client.get("/api/schedules").json()["count"] == 0

    with _client(FakeConnection(), scene_lookup=BrokenSceneLookup()) as client:
        broken = client.post("/api/schedules", json={"scene_id": "scene-1", "cron": "0 7 * * *"})
        assert broken.status_code == 502


class FakeSession:
    """只实现调度用到的 call_tool，记录参数并回放固定 payload。"""

    def __init__(self, payload: str, *, is_error: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._payload = payload
        self._is_error = is_error

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return SimpleNamespace(
            isError=self._is_error,
            content=[SimpleNamespace(type="text", text=self._payload)],
        )


def _runner_setup(scenes: dict[str, dict[str, Any]], session: FakeSession) -> Any:
    conn = FakeConnection()
    conn.session = cast(ClientSession, session)  # 测试桩只实现 call_tool
    return _schedule_runner(conn, FakeSceneLookup(scenes))


def _schedule(scene_id: str = "scene-1", *, confirm: bool = False) -> Schedule:
    return Schedule(
        id="s1",
        scene_id=scene_id,
        cron="0 7 * * *",
        enabled=True,
        confirm=confirm,
        created_at="2026-11-10T07:00:00",
    )


def test_schedule_runner_runs_scene_with_confirm() -> None:
    session = FakeSession('{"status": "sent"}')
    runner = _runner_setup({"scene-1": LOW_RISK_SCENE}, session)

    asyncio.run(runner(_schedule()))

    assert session.calls == [("home_run_scene", {"scene_id": "scene-1", "confirm": True})]


def test_schedule_runner_blocks_risk_escalated_scene() -> None:
    session = FakeSession('{"status": "sent"}')
    runner = _runner_setup({"scene-2": HIGH_RISK_SCENE}, session)

    with pytest.raises(PermissionError):
        asyncio.run(runner(_schedule("scene-2")))

    assert session.calls == []  # 未授权：一个红外指令都不该发出


def test_schedule_runner_reports_partial_result_as_error() -> None:
    session = FakeSession('{"status": "partial"}')
    runner = _runner_setup({"scene-1": LOW_RISK_SCENE}, session)

    with pytest.raises(RuntimeError, match="partial"):
        asyncio.run(runner(_schedule()))


def test_schedule_runner_fails_when_scene_disappeared() -> None:
    session = FakeSession('{"status": "sent"}')
    runner = _runner_setup({}, session)

    with pytest.raises(RuntimeError, match="场景不存在"):
        asyncio.run(runner(_schedule()))

    assert session.calls == []
