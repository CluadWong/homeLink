"""homelink_agent 纯逻辑回归：配置、同意判定、确认闸门、工具转换。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from homelink_agent.agent import HomeAgent, _looks_like_consent, _unwrap
from homelink_agent.audit import AuditLog
from homelink_agent.config import load_settings
from homelink_agent.llm import OllamaError, to_ollama_tool


@pytest.fixture
def agent() -> HomeAgent:
    return HomeAgent(replace(load_settings(), audit_path="off"))


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OLLAMA_URL", "OLLAMA_MODEL", "HOMELINK_BASE_URL", "AGENT_MAX_STEPS"):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.ollama_url == "http://127.0.0.1:11434"
    assert settings.model == "qwen3.5:9b-q4_K_M"
    assert settings.homelink_base_url == "http://127.0.0.1:8000"
    assert settings.max_steps == 6


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("同意", True),
        ("确认", True),
        ("好的", True),
        ("打开卧室风扇", False),
        ("", False),
        ("同意，顺便把客厅灯也关了看看现在几点了", False),
    ],
)
def test_consent_detection(text: str, expected: bool) -> None:
    assert _looks_like_consent(text) is expected


def test_guard_blocks_self_confirmation(agent: HomeAgent) -> None:
    arguments = {"device_id": "lock-1", "command": "开锁", "confirm": True}

    blocked = agent._guard_confirm(arguments, pending=None)

    assert blocked["confirm"] is False
    assert arguments["confirm"] is True  # 不修改入参


def test_guard_allows_pending_device_with_consent(agent: HomeAgent) -> None:
    agent._user_confirmed = True
    arguments = {"device_id": "lock-1", "command": "开锁", "confirm": True}

    assert agent._guard_confirm(arguments, pending="lock-1")["confirm"] is True
    assert agent._guard_confirm(arguments, pending="lock-2")["confirm"] is False


def test_guard_requires_consent(agent: HomeAgent) -> None:
    agent._user_confirmed = False
    arguments = {"device_id": "lock-1", "command": "开锁", "confirm": True}

    assert agent._guard_confirm(arguments, pending="lock-1")["confirm"] is False


def test_to_ollama_tool_flattens_nullable_fields() -> None:
    tool = SimpleNamespace(
        name="home_control_device",
        description="控制设备",
        inputSchema={
            "title": "params",
            "type": "object",
            "properties": {
                "confirm": {"title": "确认", "default": False, "type": "boolean"},
                "room": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            },
            "required": ["confirm"],
        },
    )

    converted = to_ollama_tool(tool)

    function = converted["function"]
    assert function["name"] == "home_control_device"
    assert "title" not in function["parameters"]
    assert function["parameters"]["properties"]["confirm"] == {"type": "boolean"}
    assert function["parameters"]["properties"]["room"] == {"type": "string"}


def test_unwrap_digs_ollama_error_out_of_nested_group() -> None:
    inner = ExceptionGroup("llm", [OllamaError("Ollama 返回 HTTP 500")])
    outer = ExceptionGroup("stdio", [ValueError("先出现的无关错误"), inner])

    unwrapped = _unwrap(outer)

    assert isinstance(unwrapped, OllamaError)


def test_unwrap_falls_back_to_first_leaf() -> None:
    group = ExceptionGroup("stdio", [ValueError("a"), RuntimeError("b")])

    assert isinstance(_unwrap(group), ValueError)


def test_audit_log_appends_jsonl_and_creates_parent(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "audit.jsonl"
    log = AuditLog(str(target))

    log.write("tool_call", tool="home_find_devices", arguments={"room": "bedroom"})
    log.write("turn_end", reply="好")

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "tool_call"
    assert first["tool"] == "home_find_devices"
    assert first["arguments"] == {"room": "bedroom"}
    assert first["ts"]


@pytest.mark.parametrize("value", ["", "off", "OFF", "none"])
def test_audit_log_can_be_disabled(value: str) -> None:
    log = AuditLog(value)

    assert not log.enabled
    log.write("tool_call", tool="home_find_devices")


def test_settings_default_audit_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_AUDIT_PATH", raising=False)

    assert load_settings().audit_path == "logs/agent-audit.jsonl"


def test_confirm_downgrade_is_audited(tmp_path: Path) -> None:
    settings = replace(load_settings(), audit_path=str(tmp_path / "audit.jsonl"))
    agent = HomeAgent(settings)
    agent._pending_device = "dev_1"

    guarded = agent._guard_confirm({"device_id": "dev_1", "command": "开关", "confirm": True}, None)

    assert guarded["confirm"] is False
    record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "confirm_downgraded"
    assert record["device_id"] == "dev_1"
    assert record["reason"] == "本轮无明确用户同意"


def test_confirmed_pending_device_keeps_confirm(tmp_path: Path) -> None:
    settings = replace(load_settings(), audit_path=str(tmp_path / "audit.jsonl"))
    agent = HomeAgent(settings)
    agent._user_confirmed = True

    allowed = agent._guard_confirm(
        {"device_id": "dev_1", "command": "开关", "confirm": True}, "dev_1"
    )

    assert allowed["confirm"] is True
    assert not (tmp_path / "audit.jsonl").exists()
