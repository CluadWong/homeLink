"""HTTP 客户端错误映射与契约参数（MockTransport，不连真实服务）。"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from homelink.interface.clients.http_client import (
    AGENT_UNREACHABLE,
    AgentClient,
    ApiError,
    shared_client,
)
from homelink.interface.clients.streamlit import errors

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler, *, token: str = "secret-token") -> AgentClient:
    return AgentClient(
        base_url="http://test.local",
        token=token,
        transport=httpx.MockTransport(handler),
    )


def test_401_maps_unauthorized_and_keeps_trace() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization", "")
        return httpx.Response(
            401,
            json={
                "success": False,
                "error": "UNAUTHORIZED",
                "message": "缺少或无效的 token",
                "trace_id": "trace-401",
            },
        )

    with pytest.raises(ApiError) as caught:
        _client(handler).devices()

    assert caught.value.code == "UNAUTHORIZED"
    assert caught.value.message == "缺少或无效的 token"
    assert caught.value.trace_id == "trace-401"
    assert seen["authorization"] == "Bearer secret-token"


def test_503_device_offline_is_not_confused_with_backend_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "success": False,
                "error": "DEVICE_OFFLINE",
                "message": "红外网关连不上",
                "trace_id": "trace-503",
            },
        )

    with pytest.raises(ApiError) as caught:
        _client(handler).control("fn-1")

    assert caught.value.code == "DEVICE_OFFLINE"
    text = errors.error_text(caught.value)
    assert "设备没有响应" in text
    assert "管家服务连不上" not in text
    assert "503" not in text
    assert "DEVICE_OFFLINE" not in text


def test_409_conflict_keeps_backend_original_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "success": False,
                "error": "CONFLICT",
                "message": "房间 卧室 下还有 2 台设备，不能删除",
                "trace_id": "trace-409",
            },
        )

    with pytest.raises(ApiError) as caught:
        _client(handler).delete_room("bedroom")

    assert caught.value.code == "CONFLICT"
    text = errors.error_text(caught.value)
    assert "房间 卧室 下还有 2 台设备，不能删除" in text
    assert "状态冲突" in text
    assert "刷新后重试" in text
    assert "409" not in text
    assert "CONFLICT" not in text


def test_connection_error_maps_to_agent_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ApiError) as caught:
        _client(handler).state()

    assert caught.value.code == AGENT_UNREACHABLE
    assert "管家服务连不上" in errors.error_text(caught.value)
    assert "设备" not in errors.error_text(caught.value)


def test_timeout_maps_to_agent_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(ApiError) as caught:
        _client(handler).gateway()

    assert caught.value.code == AGENT_UNREACHABLE
    assert "没有及时返回" in caught.value.message
    assert errors.error_text(caught.value).startswith("管家服务响应超时")


def test_gateway_200_with_probe_error_is_still_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "mode": "real",
                "base_url": "http://192.168.1.9",
                "reachable": False,
                "error": "探测超时",
                "trace_id": "trace-gw",
            },
        )

    payload = _client(handler).gateway()
    assert payload["success"] is True
    assert payload["reachable"] is False


def test_gateway_refresh_param_only_when_requested() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.query.decode())
        return httpx.Response(200, json={"success": True})

    client = _client(handler)
    client.gateway()
    client.gateway(refresh=True)
    assert seen == ["", "refresh=true"]


def test_non_json_response_becomes_internal_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>boom</html>")

    with pytest.raises(ApiError) as caught:
        _client(handler).state()

    assert caught.value.code == "INTERNAL_ERROR"
    assert "无法识别" in caught.value.message


def test_audit_omits_all_filter_and_defaults_to_50() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.query.decode())
        return httpx.Response(200, json={"success": True, "records": []})

    client = _client(handler)
    client.audit()
    client.audit(result="failed", device_id="dev-1")
    assert seen[0] == "limit=50"
    assert "result=failed" in seen[1]
    assert "device_id=dev-1" in seen[1]


def test_client_disables_environment_proxy() -> None:
    client = _client(lambda request: httpx.Response(200, json={"success": True}))
    assert client._client.trust_env is False  # noqa: SLF001 - 验证硬性配置


def test_shared_client_is_process_singleton() -> None:
    first = shared_client("http://127.0.0.1:8000", "token-a")
    second = shared_client("http://127.0.0.1:8000", "token-a")
    assert first is second
