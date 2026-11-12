"""homelink_agent 纯逻辑回归：配置、同意判定、确认闸门、幂等键注入、工具转换。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

pytest.importorskip("mcp")

from homelink_agent.agent import (  # noqa: E402
    HomeAgent,
    _control_reply,
    _looks_like_consent,
    _Pending,
    _reply_text,
    _tool_name,
    _turn_request_id,
    _unwrap,
)  # noqa: E402
from homelink_agent.audit import AuditLog  # noqa: E402
from homelink_agent.config import DEFAULT_NUM_PREDICT, load_settings  # noqa: E402
from homelink_agent.intent_guard import (  # noqa: E402
    requires_clarification,
    requires_context_followup,
    requires_state_query,
)
from homelink_agent.llm import OllamaError, to_ollama_tool  # noqa: E402
from mcp import ClientSession  # noqa: E402


@pytest.fixture
def agent() -> HomeAgent:
    return HomeAgent(replace(load_settings(), audit_path="off"))


def _pending(target: str, *, age: float = 0.0) -> _Pending:
    return _Pending(target, time.monotonic() - age)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OLLAMA_URL", "OLLAMA_MODEL", "HOMELINK_BASE_URL", "AGENT_MAX_STEPS"):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.ollama_url == "http://127.0.0.1:11434"
    assert settings.model == "qwen3.5:9b-q4_K_M"
    assert settings.homelink_base_url == "http://127.0.0.1:8000"
    assert settings.max_steps == 6
    assert settings.confirm_ttl == 120.0
    assert settings.queue_timeout == 30.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("同意", True),
        ("确认", True),
        ("好的", True),
        ("打开卧室风扇", False),
        ("", False),
        ("同意，顺便把客厅灯也关了看看现在几点了", False),  # 长句夹带确认词
        ("不要确认", False),  # 同意词被拒绝词压制
        ("取消", False),
        ("先不", False),
        ("不做了", False),
    ],
)
def test_consent_detection(text: str, expected: bool) -> None:
    assert _looks_like_consent(text) is expected


def test_guard_blocks_self_confirmation(agent: HomeAgent) -> None:
    arguments = {"device_id": "lock-1", "command": "开锁", "confirm": True}

    blocked, granted = agent._guard_confirm(arguments, pending=None)

    assert blocked["confirm"] is False
    assert granted is False
    assert arguments["confirm"] is True  # 不修改入参


def test_guard_allows_pending_device_with_consent(agent: HomeAgent) -> None:
    agent._user_confirmed = True
    arguments = {"device_id": "lock-1", "command": "开锁", "confirm": True}

    _, granted_same = agent._guard_confirm(arguments, pending=_pending("lock-1"))
    _, granted_other = agent._guard_confirm(arguments, pending=_pending("lock-2"))

    assert granted_same is True
    assert granted_other is False  # 跨设备不串用


def test_guard_requires_consent(agent: HomeAgent) -> None:
    agent._user_confirmed = False
    arguments = {"device_id": "lock-1", "command": "开锁", "confirm": True}

    blocked, granted = agent._guard_confirm(arguments, pending=_pending("lock-1"))

    assert blocked["confirm"] is False
    assert granted is False


def test_guard_blocks_scene_self_confirmation(agent: HomeAgent) -> None:
    arguments = {"scene_id": "sc_1", "confirm": True}

    blocked, granted = agent._guard_scene_confirm(arguments, pending=None)

    assert blocked["confirm"] is False
    assert granted is False
    assert arguments["confirm"] is True  # 不修改入参


def test_guard_scene_allows_pending_scene_with_consent(agent: HomeAgent) -> None:
    agent._user_confirmed = True
    arguments = {"scene_id": "sc_1", "confirm": True}

    _, granted_same = agent._guard_scene_confirm(arguments, pending=_pending("sc_1"))
    _, granted_other = agent._guard_scene_confirm(arguments, pending=_pending("sc_2"))

    assert granted_same is True
    assert granted_other is False


def test_scene_and_device_pending_do_not_share(agent: HomeAgent) -> None:
    """同 ID 也互不串用：场景待确认不解锁设备，设备待确认不解锁场景。"""

    agent._user_confirmed = True
    agent._pending_scene = _pending("same_id")

    device_call = {"device_id": "same_id", "command": "开", "confirm": True}
    _, granted_device = agent._guard_confirm(device_call, pending=None)
    assert granted_device is False

    agent._pending_device = _pending("same_id")
    agent._pending_scene = None
    scene_call = {"scene_id": "same_id", "confirm": True}
    _, granted_scene = agent._guard_scene_confirm(scene_call, pending=None)
    assert granted_scene is False


class _RecordingSession:
    """记录 call_tool 入参的假 MCP 会话。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
        self.calls.append((name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], isError=False)


def test_turn_request_id_is_stable_within_turn() -> None:
    assert _turn_request_id("abcd1234", "dev_1", "开") == _turn_request_id(
        "abcd1234", "dev_1", "开"
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (("abcd1234", "dev_1", "开"), ("efgh5678", "dev_1", "开")),  # 换轮
        (("abcd1234", "dev_1", "开"), ("abcd1234", "dev_2", "开")),  # 换设备
        (("abcd1234", "dev_1", "开"), ("abcd1234", "dev_1", "关")),  # 换命令
    ],
)
def test_turn_request_id_varies_across_turns_devices_commands(
    first: tuple[str, str, str], second: tuple[str, str, str]
) -> None:
    assert _turn_request_id(*first) != _turn_request_id(*second)


def test_turn_request_id_format() -> None:
    request_id = _turn_request_id("abcd1234", "dev_1", "定时")

    assert request_id.startswith("ag-abcd1234-")
    assert 0 < len(request_id) <= 64


def test_call_tool_injects_request_id_and_overrides_model_value(agent: HomeAgent) -> None:
    session = _RecordingSession()
    call = {
        "function": {
            "name": "home_control_device",
            "arguments": {"device_id": "dev_1", "command": "开", "request_id": "模型编造的值"},
        }
    }

    asyncio.run(
        agent._call_tool(
            cast(ClientSession, session), call, previous_pending=None, turn_id="abcd1234"
        )
    )

    assert session.calls[0][1]["request_id"] == _turn_request_id("abcd1234", "dev_1", "开")


def test_call_tool_reuses_request_id_for_retry_within_turn(agent: HomeAgent) -> None:
    session = _RecordingSession()
    call = {
        "function": {
            "name": "home_control_device",
            "arguments": {"device_id": "dev_1", "command": "开"},
        }
    }

    asyncio.run(
        agent._call_tool(
            cast(ClientSession, session), call, previous_pending=None, turn_id="abcd1234"
        )
    )
    asyncio.run(
        agent._call_tool(
            cast(ClientSession, session), call, previous_pending=None, turn_id="abcd1234"
        )
    )

    assert session.calls[0][1]["request_id"] == session.calls[1][1]["request_id"]
    assert session.calls[0][1]["request_id"] != _turn_request_id(
        "ffffffff", "dev_1", "开"
    )  # 换了 turn_id 就不同


class _FailingSession(_RecordingSession):
    """返回指定错误文本的假 MCP 会话。"""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
        self.calls.append((name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], isError=True)


def test_call_tool_scene_blocks_self_confirm_and_sets_pending(agent: HomeAgent) -> None:
    session = _FailingSession("工具执行失败：CONFIRMATION_REQUIRED 场景包含高风险设备")
    call = {
        "function": {
            "name": "home_run_scene",
            "arguments": {"scene_id": "sc_1", "confirm": True},
        }
    }

    asyncio.run(
        agent._call_tool(
            cast(ClientSession, session), call, previous_pending=None, turn_id="abcd1234"
        )
    )

    assert session.calls[0][1]["confirm"] is False  # 模型不能自己补 confirm
    assert agent._pending_scene is not None and agent._pending_scene.target == "sc_1"
    assert agent._pending_device is None  # 与设备待确认互不串用


def test_call_tool_scene_success_clears_pending(agent: HomeAgent) -> None:
    agent._pending_scene = _pending("sc_1")
    session = _RecordingSession()
    call = {
        "function": {"name": "home_run_scene", "arguments": {"scene_id": "sc_1"}},
    }

    asyncio.run(
        agent._call_tool(
            cast(ClientSession, session), call, previous_pending=None, turn_id="abcd1234"
        )
    )

    assert session.calls[0][1] == {"scene_id": "sc_1"}
    assert agent._pending_scene is None


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


def test_settings_default_model_speed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_THINK", raising=False)
    monkeypatch.delenv("AGENT_NUM_PREDICT", raising=False)

    settings = load_settings()

    assert settings.think is False  # 默认关闭思考过程
    assert settings.num_predict == DEFAULT_NUM_PREDICT


def test_settings_model_speed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_THINK", "1")
    monkeypatch.setenv("AGENT_NUM_PREDICT", "128")

    settings = load_settings()

    assert settings.think is True
    assert settings.num_predict == 128


def test_confirm_denied_is_audited(tmp_path: Path) -> None:
    settings = replace(load_settings(), audit_path=str(tmp_path / "audit.jsonl"))
    agent = HomeAgent(settings)
    agent._pending_device = _pending("dev_1")

    guarded, granted = agent._guard_confirm(
        {"device_id": "dev_1", "command": "开关", "confirm": True}, None
    )

    assert guarded["confirm"] is False
    assert granted is False
    record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "confirm_denied"
    assert record["device_id"] == "dev_1"
    assert record["reason"] == "本轮无明确用户同意"


def test_confirmed_pending_device_keeps_confirm(tmp_path: Path) -> None:
    settings = replace(load_settings(), audit_path=str(tmp_path / "audit.jsonl"))
    agent = HomeAgent(settings)
    agent._user_confirmed = True

    allowed, granted = agent._guard_confirm(
        {"device_id": "dev_1", "command": "开关", "confirm": True}, _pending("dev_1")
    )

    assert allowed["confirm"] is True
    assert granted is True
    assert not (tmp_path / "audit.jsonl").exists()  # guard 本身不写审计；granted 由 _call_tool 记


def test_scene_confirm_denied_is_audited(tmp_path: Path) -> None:
    settings = replace(load_settings(), audit_path=str(tmp_path / "audit.jsonl"))
    agent = HomeAgent(settings)
    agent._pending_scene = _pending("sc_1")

    guarded, _ = agent._guard_scene_confirm({"scene_id": "sc_2", "confirm": True}, None)

    assert guarded["confirm"] is False
    record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "confirm_denied"
    assert record["scene_id"] == "sc_2"
    assert record["pending"] == "sc_1"
    assert record["reason"] == "场景不在待确认清单"


# ---------------------------------------------------------------- 过期与一次性


def test_stale_pending_expires_and_is_audited(tmp_path: Path) -> None:
    settings = replace(load_settings(), audit_path=str(tmp_path / "audit.jsonl"), confirm_ttl=120.0)
    agent = HomeAgent(settings)
    stale = _pending("dev_1", age=121.0)

    assert agent._valid_pending(stale, key="device_id") is None
    record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "confirm_expired"
    assert record["device_id"] == "dev_1"


def test_fresh_pending_survives_ttl_check(tmp_path: Path) -> None:
    settings = replace(load_settings(), audit_path=str(tmp_path / "audit.jsonl"), confirm_ttl=120.0)
    agent = HomeAgent(settings)
    fresh = _pending("dev_1")

    assert agent._valid_pending(fresh, key="device_id") is fresh
    assert not (tmp_path / "audit.jsonl").exists()


def _confirm_attempt_chat(*, device: bool, final: str) -> Any:
    """第一轮返回一次 confirm 工具调用，之后返回最终回复。"""

    async def fake_chat(
        settings: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if any("tool_calls" in item for item in messages):
            return {"content": final}
        call = (
            {
                "function": {
                    "name": "home_control_device",
                    "arguments": {"device_id": "dev_1", "command": "开锁", "confirm": True},
                }
            }
            if device
            else {
                "function": {
                    "name": "home_run_scene",
                    "arguments": {"scene_id": "sc_1", "confirm": True},
                }
            }
        )
        return {"tool_calls": [call]}

    return fake_chat


def test_expired_pending_blocks_grant_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """上一轮待确认超过 TTL 后，本轮即使说「确认」也不放行。"""

    monkeypatch.setattr(
        "homelink_agent.agent.chat", _confirm_attempt_chat(device=True, final="已处理")
    )
    settings = replace(load_settings(), audit_path=str(tmp_path / "audit.jsonl"), confirm_ttl=0.0)
    agent = HomeAgent(settings)
    agent._pending_device = _pending("dev_1", age=1.0)  # TTL=0：一秒前即过期

    reply = asyncio.run(agent.respond(cast(ClientSession, _RecordingSession()), [], "确认", None))

    assert reply == "已处理"
    events = [
        json.loads(line)["event"]
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "confirm_expired" in events
    assert "confirm_granted" not in events  # 过期项不放行


def test_granted_confirm_is_one_shot_and_audited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """待确认 + 明确同意：放行、记 confirm_granted，且授权本轮用后即清。"""

    monkeypatch.setattr(
        "homelink_agent.agent.chat", _confirm_attempt_chat(device=True, final="已开锁")
    )
    settings = replace(load_settings(), audit_path=str(tmp_path / "audit.jsonl"))
    agent = HomeAgent(settings)
    agent._pending_device = _pending("dev_1")

    asyncio.run(agent.respond(cast(ClientSession, _RecordingSession()), [], "确认", None))

    assert agent._pending_device is None  # 一次性使用：执行后清除
    events = [
        json.loads(line)["event"]
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events.count("confirm_granted") == 1


# ---------------------------------------------------------------- 意图护栏


@pytest.mark.parametrize(
    "text",
    [
        "全都打开吧",
        "帮我把家里所有东西关一下",
        "全部关掉",
        "一键关闭全屋",
        "把所有灯和风扇一次关掉",
        "太热了",
        "来点风",
        "有点闷",
        "空气不好",
    ],
)
def test_requires_clarification_blocks_global_and_vague(text: str) -> None:
    assert requires_clarification(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "把卧室风扇关掉",
        "打开卧室风扇",
        "风扇风速调大一档",
        "切换一下风扇模式",
        "同意",
        "确认",
        "执行睡觉场景",
        "太热了，打开卧室空调",
    ],
)
def test_requires_clarification_allows_specific_commands(text: str) -> None:
    assert requires_clarification(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "风扇现在什么模式？",
        "卧室风扇现在开着吗？",
        "你确定风扇现在开着吗",
        "刚才那下打开成功了吗",
        "风扇现在几档",
        "空调关了没",
        "我开的那个码发对了吗",
        "你刚才到底发出去没有",
    ],
)
def test_requires_state_query_detects_questions(text: str) -> None:
    assert requires_state_query(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "把风扇关掉",
        "打开卧室风扇",
        "风扇风速调大一档",
        "切换一下风扇模式",
        "同意",
    ],
)
def test_requires_state_query_ignores_plain_controls(text: str) -> None:
    assert requires_state_query(text) is False


def test_tool_name_survives_bad_tool_call_shape() -> None:
    assert _tool_name({"function": {"name": "home_find_devices"}}) == "home_find_devices"
    assert _tool_name({}) == ""
    assert _tool_name({"function": {}}) == ""


def test_reply_text_handles_missing_content() -> None:
    assert _reply_text({"content": " 好 "}) == "好"
    assert _reply_text({}) == ""


def test_respond_forces_clarification_without_tools() -> None:
    calls: list[list[dict[str, Any]]] = []

    async def fake_chat(
        settings: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        calls.append(tools)
        return {"content": "请问要操作哪台设备？"}

    agent = HomeAgent(replace(load_settings(), audit_path="off"), chat_fn=fake_chat)
    session = _RecordingSession()

    reply = asyncio.run(
        agent.respond(cast(ClientSession, session), [{"type": "function"}], "全都打开吧")
    )

    assert reply == "请问要操作哪台设备？"
    assert calls == [[]]  # 护栏触发时不给模型任何工具
    assert session.calls == []  # 没有真实工具调用


def test_respond_nudges_state_query_after_find_devices() -> None:
    turns: list[list[dict[str, Any]]] = []

    async def fake_chat(
        settings: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        turns.append([dict(item) for item in messages])
        index = len(turns)
        if index == 1:
            return {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "home_find_devices", "arguments": {"room": "bedroom"}}}
                ],
            }
        if index == 2:
            return {"content": "风扇是 unknown"}  # 只查列表就回答：应被拦截
        if index == 3:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "home_get_device_state",
                            "arguments": {"device_id": "dev_1"},
                        }
                    }
                ],
            }
        return {"content": "状态是 unknown"}

    agent = HomeAgent(replace(load_settings(), audit_path="off"), chat_fn=fake_chat)
    session = _RecordingSession()

    reply = asyncio.run(
        agent.respond(cast(ClientSession, session), [{"type": "function"}], "风扇现在什么模式？")
    )

    assert reply == "状态是 unknown"
    assert [name for name, _ in session.calls] == ["home_find_devices", "home_get_device_state"]
    assert any(
        item.get("role") == "system" and "home_get_device_state" in str(item.get("content"))
        for item in turns[2]
    )


@pytest.mark.parametrize(
    "text",
    ["再小一点", "再关小一点", "再大一档", "那个模式", "继续"],
)
def test_requires_context_followup_detects_anaphora(text: str) -> None:
    assert requires_context_followup(text) is True


@pytest.mark.parametrize(
    "text",
    ["把卧室风扇关掉", "打开卧室风扇", "风扇风速调大一档", "同意"],
)
def test_requires_context_followup_ignores_full_commands(text: str) -> None:
    assert requires_context_followup(text) is False


def test_respond_requires_context_for_followup_without_history() -> None:
    calls: list[list[dict[str, Any]]] = []

    async def fake_chat(
        settings: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        calls.append(tools)
        return {"content": "请问要把哪台设备调小一点？"}

    agent = HomeAgent(replace(load_settings(), audit_path="off"), chat_fn=fake_chat)
    session = _RecordingSession()

    reply = asyncio.run(
        agent.respond(cast(ClientSession, session), [{"type": "function"}], "再小一点")
    )

    assert reply == "请问要把哪台设备调小一点？"
    assert calls == [[]]
    assert session.calls == []


def test_respond_allows_followup_when_history_exists() -> None:
    seen_tools: list[list[dict[str, Any]]] = []

    async def fake_chat(
        settings: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        seen_tools.append(tools)
        return {"content": "好的"}

    agent = HomeAgent(replace(load_settings(), audit_path="off"), chat_fn=fake_chat)
    session = _RecordingSession()
    history = [
        {"role": "user", "content": "打开卧室风扇"},
        {"role": "assistant", "content": "信号已发出"},
    ]

    reply = asyncio.run(
        agent.respond(cast(ClientSession, session), [{"type": "function"}], "再大一档", history)
    )

    assert reply == "好的"
    assert seen_tools == [[{"type": "function"}]]  # 有历史时不强制澄清，正常给工具
    assert session.calls == []


def test_respond_forces_snapshot_when_model_ignores_state_nudge() -> None:
    turns: list[int] = []

    async def fake_chat(
        settings: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        turns.append(len(messages))
        if len(turns) <= 2:
            return {"content": "请问你指哪台设备？"}
        return {"content": "根据全屋快照，风扇状态是 unknown。"}

    agent = HomeAgent(replace(load_settings(), audit_path="off"), chat_fn=fake_chat)
    session = _RecordingSession()

    reply = asyncio.run(
        agent.respond(cast(ClientSession, session), [{"type": "function"}], "我开的那个码发对了吗")
    )

    assert reply == "根据全屋快照，风扇状态是 unknown。"
    assert [name for name, _ in session.calls] == ["home_get_home_snapshot"]
    assert session.calls[0][1] == {}


class _JsonSession:
    """按工具名返回固定 JSON 文本的假会话。"""

    def __init__(self, responses: dict[str, tuple[str, bool]]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses = responses

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
        self.calls.append((name, arguments))
        text, is_error = self.responses[name]
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=is_error)


_DEVICES_JSON = json.dumps(
    {
        "count": 1,
        "devices": [
            {
                "device_id": "dev_fan",
                "name": "风扇",
                "type": "fan",
                "room": "bedroom",
                "risk_level": "low",
                "capabilities": [{"command": "开关", "command_id": "fn_c4a71e4a"}],
            }
        ],
    },
    ensure_ascii=False,
)
_TWO_FANS_JSON = json.dumps(
    {
        "count": 2,
        "devices": [
            {
                "device_id": "dev_fan",
                "name": "风扇",
                "type": "fan",
                "room": "bedroom",
                "risk_level": "low",
                "capabilities": [{"command": "开关", "command_id": "fn_c4a71e4a"}],
            },
            {
                "device_id": "dev_fan2",
                "name": "风扇 2",
                "type": "fan",
                "room": "living_room",
                "risk_level": "low",
                "capabilities": [{"command": "开关", "command_id": "fn_c4a71e4b"}],
            },
        ],
    },
    ensure_ascii=False,
)
_CONTROL_SENT_JSON = json.dumps(
    {"request_id": "ag-1-2", "status": "sent", "device_id": "dev_fan", "command": "开关"},
    ensure_ascii=False,
)


@pytest.mark.parametrize(
    ("device_name", "command", "user_text", "expected"),
    [
        ("风扇", "开关", "关闭风扇", "风扇已关闭"),
        ("风扇", "开关", "打开风扇", "风扇已打开"),
        ("风扇", "开关", "切换一下", "风扇已切换"),
        ("风扇", "关", "关风扇", "风扇已关闭"),
        ("风扇", "摇头", "风扇摇头", "风扇已执行「摇头」"),
        ("", "开关", "关闭", "设备已关闭"),
    ],
)
def test_control_reply_is_one_short_sentence(
    device_name: str, command: str, user_text: str, expected: str
) -> None:
    assert _control_reply(device_name, command, user_text) == expected


def test_control_success_returns_one_line_without_final_model_call() -> None:
    rounds: list[int] = []

    async def fake_chat(
        settings: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        rounds.append(len(messages))
        if len(rounds) == 1:
            return {"tool_calls": [{"function": {"name": "home_find_devices", "arguments": {}}}]}
        if len(rounds) == 2:
            return {
                "tool_calls": [
                    {
                        "function": {
                            "name": "home_control_device",
                            "arguments": {"device_id": "dev_fan", "command": "开关"},
                        }
                    }
                ]
            }
        return {"content": "风扇已经关闭啦，红外信号已发出，但无法确认设备真实状态……"}

    agent = HomeAgent(replace(load_settings(), audit_path="off"), chat_fn=fake_chat)
    session = _JsonSession(
        {
            "home_find_devices": (_DEVICES_JSON, False),
            "home_control_device": (_CONTROL_SENT_JSON, False),
        }
    )

    reply = asyncio.run(
        agent.respond(cast(ClientSession, session), [{"type": "function"}], "关闭风扇")
    )

    assert reply == "风扇已关闭"
    assert rounds == []  # 快路径全程不调用模型
    assert [name for name, _ in session.calls] == ["home_find_devices", "home_control_device"]


def test_ambiguous_target_falls_back_to_model_template() -> None:
    rounds: list[int] = []

    async def fake_chat(
        settings: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        rounds.append(len(messages))
        return {
            "tool_calls": [
                {
                    "function": {
                        "name": "home_control_device",
                        "arguments": {"device_id": "dev_fan", "command": "开关"},
                    }
                }
            ]
        }

    agent = HomeAgent(replace(load_settings(), audit_path="off"), chat_fn=fake_chat)
    session = _JsonSession(
        {
            "home_find_devices": (_TWO_FANS_JSON, False),
            "home_control_device": (_CONTROL_SENT_JSON, False),
        }
    )

    reply = asyncio.run(
        agent.respond(cast(ClientSession, session), [{"type": "function"}], "关闭风扇")
    )

    assert reply == "风扇已关闭"
    assert len(rounds) == 1  # 两台风扇无法唯一命中，交回模型
    assert [name for name, _ in session.calls] == ["home_find_devices", "home_control_device"]


def test_control_failure_keeps_model_explanation() -> None:
    rounds: list[int] = []

    async def fake_chat(
        settings: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        rounds.append(len(messages))
        if len(rounds) == 1:
            return {"tool_calls": [{"function": {"name": "home_find_devices", "arguments": {}}}]}
        if len(rounds) == 2:
            return {
                "tool_calls": [
                    {
                        "function": {
                            "name": "home_control_device",
                            "arguments": {"device_id": "dev_fan", "command": "摇头"},
                        }
                    }
                ]
            }
        return {"content": "风扇没有「摇头」这个功能。"}

    agent = HomeAgent(replace(load_settings(), audit_path="off"), chat_fn=fake_chat)
    session = _JsonSession(
        {
            "home_find_devices": (_DEVICES_JSON, False),
            "home_control_device": ("[NOT_FOUND] 设备不支持该功能", True),
        }
    )

    reply = asyncio.run(
        agent.respond(cast(ClientSession, session), [{"type": "function"}], "风扇摇头")
    )

    assert reply == "风扇没有「摇头」这个功能。"
    assert len(rounds) == 3  # 失败时仍交给模型解释


def test_control_success_with_state_question_keeps_honest_reply() -> None:
    rounds: list[int] = []

    async def fake_chat(
        settings: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        rounds.append(len(messages))
        if len(rounds) == 1:
            return {"tool_calls": [{"function": {"name": "home_find_devices", "arguments": {}}}]}
        if len(rounds) == 2:
            return {
                "tool_calls": [
                    {
                        "function": {
                            "name": "home_control_device",
                            "arguments": {"device_id": "dev_fan", "command": "开关"},
                        }
                    }
                ]
            }
        return {"content": "模型不应该被调用第三次"}

    agent = HomeAgent(replace(load_settings(), audit_path="off"), chat_fn=fake_chat)
    session = _JsonSession(
        {
            "home_find_devices": (_DEVICES_JSON, False),
            "home_control_device": (_CONTROL_SENT_JSON, False),
        }
    )

    reply = asyncio.run(
        agent.respond(cast(ClientSession, session), [{"type": "function"}], "关掉风扇，成功了吗")
    )

    assert reply == "风扇的操作信号已发出；红外是单向的，无法确认设备当前状态。"
    assert len(rounds) == 2  # 用户问状态时用如实模板，不再额外推理
