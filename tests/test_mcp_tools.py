"""homelink_mcp 工具面回归：配置解析、错误文案、工具注册。

`mcp` 是可选依赖，未安装时整文件跳过。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

pytest.importorskip("mcp")

from homelink_mcp import client, config, errors, server

EXPECTED_TOOLS = {
    "home_find_devices",
    "home_get_device_state",
    "home_get_home_snapshot",
    "home_control_device",
    "home_run_scene",
    "home_create_automation",
}


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HOMELINK_BASE_URL", "HOMELINK_API_TOKEN", "HOMELINK_TIMEOUT", "HOMELINK_ACTOR"):
        monkeypatch.delenv(name, raising=False)

    settings = config.load_settings()

    assert settings.base_url == "http://127.0.0.1:8000"
    assert settings.token == ""
    assert settings.timeout == 10.0
    assert settings.actor == "butler-agent"


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMELINK_BASE_URL", "http://192.168.1.9:8000/")
    monkeypatch.setenv("HOMELINK_API_TOKEN", " secret ")
    monkeypatch.setenv("HOMELINK_TIMEOUT", "2.5")
    monkeypatch.setenv("HOMELINK_ACTOR", " my-agent ")

    settings = config.load_settings()

    assert settings.base_url == "http://192.168.1.9:8000"
    assert settings.token == "secret"
    assert settings.timeout == 2.5
    assert settings.actor == "my-agent"


def test_actor_over_limit_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMELINK_ACTOR", "a" * 100)

    settings = config.load_settings()

    assert len(settings.actor) == 64
    assert settings.actor == "a" * 64


@pytest.mark.parametrize("raw", ["abc", "0", "-3", ""])
def test_timeout_falls_back_to_default(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("HOMELINK_TIMEOUT", raw)

    assert config.load_settings().timeout == 10.0


def test_error_text_carries_code_hint_and_trace() -> None:
    text = str(errors.HomeLinkError("NOT_FOUND", "设备不存在", trace_id="abc"))

    assert "[NOT_FOUND]" in text
    assert "home_find_devices" in text
    assert "trace_id=abc" in text


def test_error_from_envelope() -> None:
    error = errors.HomeLinkError.from_envelope(
        {"error": "CONFLICT", "message": "request_id 已存在", "trace_id": "t1"},
        status=409,
    )

    assert (error.code, error.status, error.trace_id) == ("CONFLICT", 409, "t1")
    assert "新的 request_id" in str(error)


def test_registers_six_tools() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in tools}

    assert names == EXPECTED_TOOLS
    control = next(tool for tool in tools if tool.name == "home_control_device")
    assert set(control.inputSchema["required"]) == {"device_id", "command", "request_id"}
    scene = next(tool for tool in tools if tool.name == "home_run_scene")
    assert set(scene.inputSchema["required"]) == {"scene_id"}
    assert scene.inputSchema["properties"]["confirm"]["type"] == "boolean"
    assert scene.inputSchema["properties"]["confirm"]["default"] is False


def _client_with_transport(handler) -> client.HomeLinkClient:
    """用注入 MockTransport 的客户端，指向假后端。"""

    settings = config.Settings(
        base_url="http://backend.test", token="", timeout=5.0, actor="butler-agent"
    )
    return client.HomeLinkClient(settings, transport=httpx.MockTransport(handler))


def test_request_sends_actor_and_trace_headers() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["actor"] = request.headers.get("X-Actor", "")
        captured["trace_id"] = request.headers.get("X-Trace-Id", "")
        return httpx.Response(200, json={"success": True, "result": {"ok": 1}, "trace_id": "t"})

    client_obj = _client_with_transport(handler)
    asyncio.run(client_obj.find_devices())

    assert captured["actor"] == "butler-agent"
    assert len(captured["trace_id"]) == 8  # secrets.token_hex(4)


def test_transport_error_carries_trace_id() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["trace_id"] = request.headers.get("X-Trace-Id", "")
        raise httpx.ConnectError("boom", request=request)

    client_obj = _client_with_transport(handler)

    with pytest.raises(errors.HomeLinkError) as exc_info:
        asyncio.run(client_obj.snapshot())

    error = exc_info.value
    assert error.code == "UNREACHABLE"
    assert error.trace_id == captured["trace_id"]
    assert f"trace_id={captured['trace_id']}" in str(error)


def test_lifespan_creates_and_closes_client() -> None:
    async def run() -> None:
        async with server._lifespan(server.mcp) as http_client:
            assert isinstance(http_client, client.HomeLinkClient)
            http_client._http()  # 首次请求时惰性创建
            assert http_client._http_client is not None
        # 退出 lifespan 后内部连接池已关闭
        assert http_client._http_client is None

    asyncio.run(run())
