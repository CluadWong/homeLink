"""MCP 结构化日志测试：stderr JSONL、stdout 干净、级别门槛、client 埋点。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from homelink_mcp import client, config, errors
from homelink_mcp import logging as mcp_logging

ENV_KEYS = ("HOMELINK_LOG_LEVEL",)


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    err = capsys.readouterr().err
    return [json.loads(line) for line in err.splitlines() if line.strip()]


# ---------- emit 本体 ----------


def test_emit_writes_jsonl_to_stderr_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("HOMELINK_LOG_LEVEL", raising=False)

    mcp_logging.emit("request", tool="home_find_devices", ok=True, duration_ms=1.2, trace_id="ab")

    captured = capsys.readouterr()
    assert captured.out == ""  # stdout 必须保持纯 JSON-RPC
    records = [json.loads(line) for line in captured.err.splitlines() if line.strip()]
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "request"
    assert record["level"] == "INFO"
    assert record["tool"] == "home_find_devices"
    assert record["ok"] is True
    assert record["duration_ms"] == 1.2
    assert record["trace_id"] == "ab"
    assert record["ts"]  # ISO 时间戳存在


def test_emit_respects_level_threshold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOMELINK_LOG_LEVEL", "ERROR")

    mcp_logging.emit("request", tool="t", ok=True)  # INFO 被过滤
    assert _lines(capsys) == []

    mcp_logging.emit("request", level="ERROR", tool="t", ok=False, error_code="X")
    records = _lines(capsys)
    assert len(records) == 1
    assert records[0]["level"] == "ERROR"


@pytest.mark.parametrize("raw", ["bogus", "  ", "Verbose"])
def test_invalid_level_falls_back_to_info(
    monkeypatch: pytest.MonkeyPatch, raw: str, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOMELINK_LOG_LEVEL", raw)

    mcp_logging.emit("request", tool="t", ok=True)  # 非法值回落 INFO，INFO 应当通过

    assert len(_lines(capsys)) == 1


def test_debug_level_allows_info_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOMELINK_LOG_LEVEL", "DEBUG")

    mcp_logging.emit("request", tool="t", ok=True)

    assert len(_lines(capsys)) == 1


# ---------- client 埋点 ----------


def _client_with(handler, *, token: str = "") -> client.HomeLinkClient:
    settings = config.Settings(
        base_url="http://backend.test", token=token, timeout=5.0, actor="butler-agent"
    )
    return client.HomeLinkClient(settings, transport=httpx.MockTransport(handler))


def test_successful_request_logs_tool_and_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    c = _client_with(
        lambda request: httpx.Response(200, json={"success": True, "result": {}, "trace_id": "t"})
    )
    asyncio.run(c.find_devices())

    record = _lines(capsys)[0]
    assert record["tool"] == "home_find_devices"
    assert record["ok"] is True
    assert isinstance(record["duration_ms"], (int, float))
    assert len(str(record["trace_id"])) == 8


def test_failed_request_logs_error_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"success": False, "error": "NOT_FOUND", "message": "没有", "trace_id": "t1"}
        )

    c = _client_with(handler)
    with pytest.raises(errors.HomeLinkError):
        asyncio.run(c.snapshot())

    record = _lines(capsys)[0]
    assert record["tool"] == "home_get_home_snapshot"
    assert record["ok"] is False
    assert record["error_code"] == "NOT_FOUND"


def test_token_never_logged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    c = _client_with(
        lambda request: httpx.Response(200, json={"success": True, "result": {}}), token="secret123"
    )
    asyncio.run(c.find_devices())

    captured = capsys.readouterr()
    assert captured.out == ""
    err_lines = [line for line in captured.err.splitlines() if line.strip()]
    assert len(err_lines) == 1
    assert "secret123" not in captured.err
