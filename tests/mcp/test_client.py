"""HomeLinkClient HTTP 层测试：路径拼接、鉴权头、错误分支与连接复用。

全部基于注入 MockTransport，不启动真实后端即可覆盖全部错误分支。
`homelink_mcp.client` 不依赖 `mcp` 包，本文件无需 importorskip。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from urllib.parse import quote

import httpx
import pytest

from homelink_mcp import client, config, errors

Handler = Callable[[httpx.Request], httpx.Response]


def _make_client(
    handler: Handler, *, token: str = ""
) -> tuple[client.HomeLinkClient, list[httpx.Request]]:
    """构造带请求记录的客户端，返回 (client, requests)。"""
    requests: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    settings = config.Settings(
        base_url="http://backend.test", token=token, timeout=5.0, actor="butler-agent"
    )
    return client.HomeLinkClient(settings, transport=httpx.MockTransport(wrapped)), requests


def _ok() -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": {"ok": 1}, "trace_id": "t"})


# ---------- 路径拼接 ----------


def test_device_id_and_scene_id_are_percent_encoded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    control, control_requests = _make_client(handler)
    scene, scene_requests = _make_client(handler)

    asyncio.run(
        control.control_device(
            device_id="bedroom light/1",
            command="on",
            request_id="req-1",
            parameters={},
            confirm=False,
        )
    )
    asyncio.run(scene.run_scene("晚安 场景", confirm=False))

    assert str(control_requests[0].url).endswith("/api/agent/control")
    assert b'"device_id":"bedroom light/1"' in control_requests[0].content
    assert str(scene_requests[0].url).endswith(
        f"/api/agent/scenes/{quote('晚安 场景', safe='')}/run"
    )


def test_state_path_quotes_device_id() -> None:
    c, requests = _make_client(lambda request: _ok())

    asyncio.run(c.get_device_state("bedroom light/1"))

    assert str(requests[0].url).endswith(
        f"/api/agent/devices/{quote('bedroom light/1', safe='')}/state"
    )


# ---------- 鉴权头 ----------


def test_authorization_sent_only_with_token() -> None:
    with_token, with_requests = _make_client(lambda request: _ok(), token="secret")
    without_token, without_requests = _make_client(lambda request: _ok())

    asyncio.run(with_token.find_devices())
    asyncio.run(without_token.find_devices())

    assert with_requests[0].headers.get("Authorization") == "Bearer secret"
    assert "Authorization" not in without_requests[0].headers


# ---------- 错误分支 ----------


def test_timeout_maps_to_gateway_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    c, _ = _make_client(handler)

    with pytest.raises(errors.HomeLinkError) as exc_info:
        asyncio.run(c.snapshot())

    assert exc_info.value.code == "GATEWAY_TIMEOUT"


def test_connect_error_maps_to_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    c, _ = _make_client(handler)

    with pytest.raises(errors.HomeLinkError) as exc_info:
        asyncio.run(c.snapshot())

    assert exc_info.value.code == "UNREACHABLE"


def test_non_json_response_maps_to_bad_response() -> None:
    c, _ = _make_client(lambda request: httpx.Response(200, text="<html>oops</html>"))

    with pytest.raises(errors.HomeLinkError) as exc_info:
        asyncio.run(c.snapshot())

    assert exc_info.value.code == "BAD_RESPONSE"


def test_non_object_json_maps_to_bad_response() -> None:
    c, _ = _make_client(lambda request: httpx.Response(200, json=[1, 2]))

    with pytest.raises(errors.HomeLinkError) as exc_info:
        asyncio.run(c.snapshot())

    assert exc_info.value.code == "BAD_RESPONSE"


def test_envelope_failure_raises_error_with_code_and_trace() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": False,
                "error": "NOT_FOUND",
                "message": "没有这个设备",
                "trace_id": "t9",
            },
        )

    c, _ = _make_client(handler)

    with pytest.raises(errors.HomeLinkError) as exc_info:
        asyncio.run(c.snapshot())

    error = exc_info.value
    assert (error.code, error.status, error.trace_id) == ("NOT_FOUND", 200, "t9")
    assert "trace_id=t9" in str(error)


def test_http_500_with_valid_body_keeps_error_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"success": False, "error": "INTERNAL_ERROR", "message": "炸了", "trace_id": "t5"},
        )

    c, _ = _make_client(handler)

    with pytest.raises(errors.HomeLinkError) as exc_info:
        asyncio.run(c.snapshot())

    error = exc_info.value
    assert error.code == "INTERNAL_ERROR"
    assert error.status == 500
    assert error.trace_id == "t5"


# ---------- 连接复用 ----------


def test_ten_calls_reuse_single_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续 10 次调用只创建一个 AsyncClient；aclose 后再次请求才重建。"""
    created: list[int] = []
    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        created.append(1)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(client.httpx, "AsyncClient", factory)
    c, requests = _make_client(lambda request: _ok())

    async def scenario() -> None:
        for _ in range(10):
            await c.find_devices()
        await c.aclose()
        await c.find_devices()  # 关闭后重建
        await c.aclose()

    asyncio.run(scenario())

    assert len(created) == 2  # 复用：10 次请求只有 1 次创建，close 后重建 1 次
    assert len(requests) == 11
