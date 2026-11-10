"""MCP 到后端的端到端测试：stdio 子进程 + 真实 FastAPI（uvicorn 随机端口）。

标记 `integration`：需要拉起子进程与本机回环端口；网关为 mock，不依赖 Ollama / 真实板子。
本地一条命令：pytest tests/mcp/test_e2e.py -q
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
import yaml

pytest.importorskip("mcp")

from mcp.client.stdio import get_default_environment, stdio_client  # noqa: E402

from homelink.bootstrap.context import AppContext, build_context  # noqa: E402
from homelink.gateway.mock import MockIRGateway  # noqa: E402
from homelink.interface.api.app import create_app  # noqa: E402
from mcp import ClientSession, StdioServerParameters, types  # noqa: E402

EXPECTED_TOOLS = {
    "home_find_devices",
    "home_get_device_state",
    "home_get_home_snapshot",
    "home_control_device",
    "home_run_scene",
    "home_create_automation",
}

SETTINGS: dict[str, Any] = {
    "gateway": {"mode": "mock", "ir": {"base_url": "", "username": "", "password": ""}},
    "state": {"retention_days": 90},
    "audit": {"retention_days": 365},
    "api": {"token": ""},
    "frontend": {"enabled": False, "dist_path": "frontend/dist"},
}

DEVICES: dict[str, Any] = {"rooms": [{"name": "bedroom", "display_name": "卧室", "sort_order": 1}]}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_server(server: uvicorn.Server, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn 测试服务 15 秒内未启动")
        time.sleep(0.05)


@pytest.fixture()
def backend(tmp_path: Path) -> Any:
    """随机空闲端口启动真实 FastAPI（mock 网关），返回 (base_url, context)。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(SETTINGS, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (config_dir / "devices.yaml").write_text(
        yaml.safe_dump(DEVICES, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    context: AppContext = build_context(config_dir, tmp_path / "data" / "e2e.db")

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(context), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_server(server)
    try:
        yield f"http://127.0.0.1:{port}", context
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class McpHarness:
    """后台事件循环线程里用单个长驻 task 维持 stdio MCP 会话，供同步测试顺序调用。

    anyio 的 cancel scope 要求在进入它的同一 task 内退出，因此连接与关闭
    必须收在同一个 task 里，不能跨 run_coroutine_threadsafe 调用拆开。
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._session: ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._thread.start()

    def start(self) -> None:
        ready: asyncio.Future[None] = self._loop.create_future()

        async def run() -> None:
            env = {
                **get_default_environment(),
                "HOMELINK_BASE_URL": self._base_url,
                "HOMELINK_TIMEOUT": "10",
                "HOMELINK_ACTOR": "butler-agent",
            }
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "homelink_mcp"], env=env
            )
            async with AsyncExitStack() as stack:
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._session = session
                assert self._stop_event is not None
                ready.set_result(None)
                await self._stop_event.wait()
            self._session = None

        self._stop_event = asyncio.Event()

        async def spawn() -> None:
            self._task = asyncio.get_running_loop().create_task(run())

        asyncio.run_coroutine_threadsafe(spawn(), self._loop).result(timeout=60)
        asyncio.run_coroutine_threadsafe(_wait(ready), self._loop).result(timeout=60)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        assert self._session is not None
        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments), self._loop
        )
        return future.result(timeout=60)

    def list_tools(self) -> set[str]:
        assert self._session is not None
        future = asyncio.run_coroutine_threadsafe(self._session.list_tools(), self._loop)
        return {tool.name for tool in future.result(timeout=60).tools}

    def close(self) -> None:
        stop_event = self._stop_event
        task = self._task
        assert stop_event is not None and task is not None

        async def finish() -> None:
            stop_event.set()
            await task

        asyncio.run_coroutine_threadsafe(finish(), self._loop).result(timeout=30)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


async def _wait(ready: asyncio.Future[None]) -> None:
    await ready


@pytest.fixture()
def mcp(backend: Any) -> Any:
    base_url, _ = backend
    harness = McpHarness(base_url)
    harness.start()
    try:
        yield harness
    finally:
        harness.close()


def _text(result: types.CallToolResult) -> str:
    assert result.content, "结果没有内容"
    item = result.content[0]
    assert isinstance(item, types.TextContent), f"非文本内容：{type(item).__name__}"
    return item.text


def _payload(result: types.CallToolResult) -> dict[str, Any]:
    """成功结果的唯一 JSON 对象；失败断言给出原始文本。"""
    assert not result.isError, _text(result)
    return json.loads(_text(result))


def _api(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=10.0)


def _learned_device(base_url: str, context: AppContext, *, name: str) -> str:
    """走真实 API 建设备并学一个「开」功能，返回 device_id。"""
    gateway = context.gateway
    assert isinstance(gateway, MockIRGateway)
    with _api(base_url) as client:
        created = client.post("/api/devices", json={"name": name, "room": "bedroom"}).json()
        device_id = created["id"]
        assert client.post("/api/learn/start", json={"device_id": device_id}).status_code == 200
        gateway.inject_test_waveform()  # 同进程注入，mock 网关实例一致
        captured = client.get("/api/learn/capture", params={"device_id": device_id}).json()
        assert captured["captured"] is True
        saved = client.post(
            "/api/learn/save",
            json={"device_id": device_id, "name": "开", "raw_code": captured["raw_code"]},
        )
        assert saved.status_code == 200, saved.text
    return device_id


# ---------------------------------------------------------------- 用例


@pytest.mark.integration
def test_e2e_tools_list_returns_six(mcp: McpHarness) -> None:
    assert mcp.list_tools() == EXPECTED_TOOLS


@pytest.mark.integration
def test_e2e_find_devices_sees_learned_device(mcp: McpHarness, backend: Any) -> None:
    base_url, context = backend
    device_id = _learned_device(base_url, context, name="E2E 测试灯")

    payload = _payload(mcp.call_tool("home_find_devices", {"name": "E2E 测试灯"}))

    assert payload["count"] == 1
    assert payload["devices"][0]["device_id"] == device_id


@pytest.mark.integration
def test_e2e_control_sent_then_replayed(mcp: McpHarness, backend: Any) -> None:
    base_url, context = backend
    device_id = _learned_device(base_url, context, name="E2E 卧室灯")
    arguments = {"device_id": device_id, "command": "开", "request_id": "e2e-req-1"}

    first = _payload(mcp.call_tool("home_control_device", arguments))
    second = _payload(mcp.call_tool("home_control_device", arguments))

    assert first["status"] == "sent"
    assert first["replayed"] is False
    assert second["status"] == "sent"
    assert second["replayed"] is True  # 同 request_id 幂等回放，不重复发红外


@pytest.mark.integration
def test_e2e_unknown_device_is_error(mcp: McpHarness) -> None:
    result = mcp.call_tool(
        "home_control_device",
        {"device_id": "dev_missing", "command": "开", "request_id": "e2e-req-2"},
    )

    assert result.isError is True
    assert "NOT_FOUND" in _text(result)
