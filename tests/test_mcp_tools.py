"""homelink_mcp 工具面回归：配置解析、错误文案、工具注册。

`mcp` 是可选依赖，未安装时整文件跳过。
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")

from homelink_mcp import config, errors, server

EXPECTED_TOOLS = {
    "home_find_devices",
    "home_get_device_state",
    "home_get_home_snapshot",
    "home_control_device",
    "home_run_scene",
    "home_create_automation",
}


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOMELINK_BASE_URL", raising=False)
    monkeypatch.delenv("HOMELINK_API_TOKEN", raising=False)
    monkeypatch.delenv("HOMELINK_TIMEOUT", raising=False)

    settings = config.load_settings()

    assert settings.base_url == "http://127.0.0.1:8000"
    assert settings.token == ""
    assert settings.timeout == 10.0


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMELINK_BASE_URL", "http://192.168.1.9:8000/")
    monkeypatch.setenv("HOMELINK_API_TOKEN", " secret ")
    monkeypatch.setenv("HOMELINK_TIMEOUT", "2.5")

    settings = config.load_settings()

    assert settings.base_url == "http://192.168.1.9:8000"
    assert settings.token == "secret"
    assert settings.timeout == 2.5


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
