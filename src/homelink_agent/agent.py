"""Agent 主循环：MCP 子进程提供工具，Ollama 负责选择工具。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

from mcp.client.stdio import stdio_client

from homelink_agent.audit import AuditLog
from homelink_agent.config import AgentSettings
from homelink_agent.fastpath import match_devices, parse_simple_control, pick_command
from homelink_agent.intent_guard import (
    delegates_authority,
    requires_clarification,
    requires_context_followup,
    requires_scene_check,
    requires_state_query,
)
from homelink_agent.llm import OllamaError, chat, to_ollama_tool
from homelink_agent.prompt import (
    CLARIFY_INSTRUCTION,
    STATE_QUERY_NUDGE,
    SYSTEM_PROMPT,
    history_messages,
)
from homelink_mcp.config import DEFAULT_ACTOR
from mcp import ClientSession, StdioServerParameters

_CONSENT_WORDS = frozenset(
    {"确认", "确定", "同意", "可以", "好的", "好", "是的", "开吧", "执行", "没问题"}
)
_DENY_WORDS = frozenset({"取消", "不要", "不行", "别", "不"})
_DEBUG = os.environ.get("AGENT_DEBUG") == "1"

# 与 llm.chat 同契约：给定 settings / messages / tools，返回含 content 或 tool_calls 的消息
ChatFn = Callable[
    [AgentSettings, list[dict[str, Any]], list[dict[str, Any]]], Awaitable[dict[str, Any]]
]


class _Pending(NamedTuple):
    """待确认项：目标 ID + 创建时刻（monotonic），超过 confirm_ttl 即作废。"""

    target: str
    created: float


def _debug(message: str) -> None:
    if _DEBUG:
        print(message, file=sys.stderr, flush=True)


def _looks_like_consent(text: str) -> bool:
    """短消息、含明确同意词、且不含拒绝词才算同意，避免长句夹带「确认」被误判。"""

    stripped = text.strip()
    if not stripped or len(stripped) > 12:
        return False
    if any(word in stripped for word in _DENY_WORDS):
        return False
    return any(word in stripped for word in _CONSENT_WORDS)


def _unwrap(group: BaseExceptionGroup) -> BaseException:
    """优先返回组里的 OllamaError，否则返回第一个叶子异常。"""

    for inner in group.exceptions:
        found = _unwrap(inner) if isinstance(inner, BaseExceptionGroup) else inner
        if isinstance(found, OllamaError):
            return found
    leaf = group.exceptions[0]
    return _unwrap(leaf) if isinstance(leaf, BaseExceptionGroup) else leaf


def _tool_name(call: dict[str, Any]) -> str:
    """从模型返回的 tool_call 里取工具名，非法结构返回空串。"""

    function = call.get("function") or {}
    return str(function.get("name") or "")


def _reply_text(message: dict[str, Any]) -> str:
    """取模型回复文本；云端/本地返回结构可能省略 content。"""

    return str(message.get("content") or "").strip()


def _turn_request_id(turn_id: str, device_id: str, command: str) -> str:
    """一轮内的幂等键：同设备同命令重试天然复用，跨轮/跨设备/跨命令必然不同。"""

    digest = hashlib.sha256(f"{device_id}\x00{command}".encode()).hexdigest()[:12]
    return f"ag-{turn_id}-{digest}"


def _arguments_of(call: dict[str, Any]) -> dict[str, Any]:
    """取 tool_call 的参数；模型可能返回 JSON 字符串，统一成字典。"""

    arguments = (call.get("function") or {}).get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    return arguments if isinstance(arguments, dict) else {}


def _json_payload(text: str) -> dict[str, Any] | None:
    """工具成功时返回 JSON；报错文本有前缀，解析失败一律当作非成功。"""

    raw = text.strip()
    if not raw.startswith("{"):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


_TOGGLE_COMMANDS = frozenset({"开关", "切换"})
_CLOSE_WORDS = ("关闭", "关", "停止", "停")
_OPEN_WORDS = ("打开", "开启", "启动", "开")

# 确定性护栏模板：由运行时直接回答，不给模型擅自控设备的机会。
SCENE_MISSING_REPLY = (
    "家里目前还没有配置任何场景，暂时没有可运行的场景。"
    "需要我直接帮你控制某台设备吗？比如「打开风扇」或「关闭风扇」。"
)
DELEGATION_REPLY = (
    "你还没有告诉我要做什么。请告诉我具体想控制的设备或想运行的场景，例如「关闭风扇」。"
)


def _control_reply(device_name: str, command: str, user_text: str) -> str:
    """控制成功的一句话回复；翻转码按用户说的方向措辞。"""

    name = device_name or "设备"
    if command in _TOGGLE_COMMANDS:
        if any(word in user_text for word in _CLOSE_WORDS):
            return f"{name}已关闭"
        if any(word in user_text for word in _OPEN_WORDS):
            return f"{name}已打开"
        return f"{name}已切换"
    if any(word in command for word in _CLOSE_WORDS):
        return f"{name}已关闭"
    if any(word in command for word in _OPEN_WORDS):
        return f"{name}已打开"
    return f"{name}已执行「{command}」"


class HomeAgent:
    def __init__(
        self,
        settings: AgentSettings,
        on_tool: Callable[[str, dict[str, Any], str], None] | None = None,
        chat_fn: ChatFn | None = None,
    ) -> None:
        self._settings = settings
        self._on_tool = on_tool
        # 默认走 Ollama；评测可用云端模型替换（契约同 llm.chat）
        self._chat = chat_fn or chat
        self._audit = AuditLog(settings.audit_path)
        self._pending_device: _Pending | None = None
        self._pending_scene: _Pending | None = None
        self._user_confirmed = False
        # 本轮用过的设备名缓存：控制成功后直接拼一句话回复，省掉最后一次模型调用。
        self._device_names: dict[str, str] = {}

    async def run(self, user_text: str, history: list[dict[str, str]] | None = None) -> str:
        """一次性路径（CLI）：每次现起 MCP 子进程，用完即关。"""

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "homelink_mcp"],
            env=self._server_env(),
        )
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            try:
                async with (
                    stdio_client(params, errlog=errlog) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    tools = [to_ollama_tool(tool) for tool in (await session.list_tools()).tools]
                    return await self.respond(session, tools, user_text, history)
            except BaseExceptionGroup as group:
                # 起子进程阶段的 anyio 异常组；respond 内部的已在 respond 处理过
                error = _unwrap(group)
                self._audit.write("turn_error", error=str(error))
                raise error from None

    async def respond(
        self,
        session: ClientSession,
        tools: list[dict[str, Any]],
        user_text: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """完整一轮对话：常驻服务与一次性路径共用；session 由调用方提供并保活。"""

        # 上一轮被拦下的设备/场景只对这一轮有效，且超过 confirm_ttl 即作废，
        # 避免「下次随口说好」被当成授权。
        previous_pending = self._valid_pending(self._pending_device, key="device_id")
        self._pending_device = None
        previous_pending_scene = self._valid_pending(self._pending_scene, key="scene_id")
        self._pending_scene = None
        self._user_confirmed = _looks_like_consent(user_text)
        self._device_names = {}
        turn_id = uuid.uuid4().hex[:8]  # 本轮幂等键前缀，见 _turn_request_id
        self._audit.write(
            "turn_start",
            user=user_text,
            consent=self._user_confirmed,
            pending=previous_pending.target if previous_pending else None,
            pending_scene=previous_pending_scene.target if previous_pending_scene else None,
        )

        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history_messages(history or []))
        messages.append({"role": "user", "content": user_text})

        # 确定性护栏：不依赖模型自觉。命中全局/模糊输入时本轮不进入工具循环。
        clarify_only = requires_clarification(user_text) or (
            not history and requires_context_followup(user_text)
        )
        needs_state_query = requires_state_query(user_text)
        state_queried = False
        state_nudged = False

        try:
            if clarify_only:
                messages.append({"role": "system", "content": CLARIFY_INSTRUCTION})
                message = await self._chat(self._settings, messages, [])
                reply = _reply_text(message) or "你想控制哪台设备？请告诉我设备名称或房间。"
                self._audit.write("clarify_forced", user=user_text, reply=reply[:200])
                self._audit.write("turn_end", reply=reply)
                return reply

            # 代劳授权护栏：用户让 agent 自行决定做什么时，必须先澄清，不能擅作主张控设备。
            # 无条件拦截：同意词（如「执行」）只表达确认，不指定动作内容。
            if delegates_authority(user_text):
                self._audit.write("delegation_clarified", user=user_text)
                self._audit.write("turn_end", reply=DELEGATION_REPLY)
                return DELEGATION_REPLY

            # 场景护栏：先查快照；场景缺失时直接说明，杜绝「场景不存在→擅自控设备」的漂移。
            # 仅当用户正在确认一个待执行场景时跳过（该流程由场景确认闸门接管）。
            if self._pending_scene is None and requires_scene_check(user_text):
                snapshot_call = {"function": {"name": "home_get_home_snapshot", "arguments": {}}}
                snapshot_message = await self._call_tool(session, snapshot_call, None, turn_id)
                payload = _json_payload(str(snapshot_message.get("content") or ""))
                if payload is not None and not (payload.get("scenes") or []):
                    self._audit.write("scene_guard_blocked", user=user_text, reason="no_scenes")
                    self._audit.write("turn_end", reply=SCENE_MISSING_REPLY)
                    return SCENE_MISSING_REPLY
                # 场景存在：按正常流程交给模型运行场景（高风险场景仍需确认）。

            # 确定性快路径：「关闭风扇」这类明确指令直接执行，不进入模型循环。
            if not needs_state_query:
                fast_reply = await self._try_fast_path(session, user_text, turn_id)
                if fast_reply is not None:
                    self._audit.write("turn_end", reply=fast_reply)
                    return fast_reply

            for _ in range(self._settings.max_steps):
                message = await self._chat(self._settings, messages, tools)
                calls = message.get("tool_calls") or []
                if not calls:
                    if needs_state_query and not state_queried:
                        if not state_nudged:
                            # 第一次：提醒模型必须查状态，再给一次机会。
                            state_nudged = True
                            messages.append({"role": "system", "content": STATE_QUERY_NUDGE})
                            self._audit.write("state_query_nudged", user=user_text)
                            continue
                        # 第二次仍不查：运行时兜底拉一次全屋快照，保证回答有数据依据。
                        synthetic = {
                            "function": {
                                "name": "home_get_home_snapshot",
                                "arguments": {},
                            }
                        }
                        messages.append(
                            {
                                "role": "assistant",
                                "content": str(message.get("content") or ""),
                                "tool_calls": [synthetic],
                            }
                        )
                        state_queried = True
                        self._audit.write("state_query_forced", user=user_text)
                        messages.append(
                            await self._call_tool(
                                session,
                                synthetic,
                                previous_pending,
                                turn_id,
                                previous_pending_scene=previous_pending_scene,
                            )
                        )
                        continue
                    reply = _reply_text(message) or "（模型没有返回内容）"
                    self._audit.write("turn_end", reply=reply)
                    return reply

                messages.append(
                    {
                        "role": "assistant",
                        "content": str(message.get("content") or ""),
                        "tool_calls": calls,
                    }
                )
                for call in calls:
                    if _tool_name(call) in {"home_get_device_state", "home_get_home_snapshot"}:
                        state_queried = True
                    messages.append(
                        await self._call_tool(
                            session,
                            call,
                            previous_pending,
                            turn_id,
                            previous_pending_scene=previous_pending_scene,
                        )
                    )
                # 单设备控制成功：运行时直接给一句话回复，不再让模型总结，
                # 既避免啰嗦，也省掉一整次推理。
                # 用户同一轮还问「成功了吗/确定吗」时，改成如实说明信号已发出。
                if len(calls) == 1 and _tool_name(calls[0]) == "home_control_device":
                    payload = _json_payload(str(messages[-1].get("content") or ""))
                    if payload is not None and payload.get("status") == "sent":
                        arguments = _arguments_of(calls[0])
                        name = self._device_names.get(str(arguments.get("device_id") or ""), "")
                        if needs_state_query:
                            reply = (
                                f"{name or '设备'}的操作信号已发出；"
                                "红外是单向的，无法确认设备当前状态。"
                            )
                        else:
                            reply = _control_reply(
                                name, str(arguments.get("command") or ""), user_text
                            )
                        self._audit.write("turn_end", reply=reply)
                        return reply
        except BaseExceptionGroup as group:
            # anyio 任务组会把模型/网络异常包成 ExceptionGroup，剥出来再抛
            error = _unwrap(group)
            self._audit.write("turn_error", error=str(error))
            raise error from None

        reply = "（指令拆得太复杂，已到最大步数；请一次只做一件事）"
        self._audit.write("turn_end", reply=reply)
        return reply

    async def _call_tool(
        self,
        session: ClientSession,
        call: dict[str, Any],
        previous_pending: _Pending | None,
        turn_id: str,
        previous_pending_scene: _Pending | None = None,
    ) -> dict[str, Any]:
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        if name == "home_control_device":
            pending = previous_pending or self._pending_device
            arguments, granted = self._guard_confirm(arguments, pending)
            if granted and pending is not None:
                self._audit.write("confirm_granted", device_id=pending.target)
                # 一次性使用：授权随本轮执行销毁；若服务端再次拦截会重新登记待确认。
                self._pending_device = None
            # 幂等键由运行时注入并覆盖模型编造的值：同轮同设备同命令复用，重试不会重复发红外。
            arguments["request_id"] = _turn_request_id(
                turn_id, str(arguments.get("device_id") or ""), str(arguments.get("command") or "")
            )
        elif name == "home_run_scene":
            # 场景确认与设备确认互不串用：待确认键为 scene:<scene_id> 语义（单独存 _pending_scene）。
            pending = previous_pending_scene or self._pending_scene
            arguments, granted = self._guard_scene_confirm(arguments, pending)
            if granted and pending is not None:
                self._audit.write("confirm_granted", scene_id=pending.target)
                self._pending_scene = None

        result = await session.call_tool(name, arguments)
        text = "\n".join(
            getattr(block, "text", "")
            for block in result.content
            if getattr(block, "type", "") == "text"
        )
        if name in {"home_find_devices", "home_get_home_snapshot"} and not result.isError:
            self._remember_devices(_json_payload(text))
        _debug(f"[tool] {name} {json.dumps(arguments, ensure_ascii=False)} -> {text[:200]}")
        self._audit.write(
            "tool_call",
            tool=name,
            arguments=arguments,
            is_error=bool(result.isError),
            output=text[:500],
        )
        if self._on_tool is not None:
            self._on_tool(name, arguments, text)
        if result.isError:
            text = "工具执行失败：" + text
            if "CONFIRMATION_REQUIRED" in text:
                if name == "home_run_scene":
                    target = str(arguments.get("scene_id") or "")
                    if target:
                        self._pending_scene = _Pending(target, time.monotonic())
                        self._audit.write("confirm_requested", scene_id=target)
                else:
                    target = str(arguments.get("device_id") or "")
                    if target:
                        self._pending_device = _Pending(target, time.monotonic())
                        self._audit.write("confirm_requested", device_id=target)
        elif name == "home_run_scene":
            self._pending_scene = None
        else:
            self._pending_device = None
        return {"role": "tool", "tool_name": name, "content": text or "（无输出）"}

    async def _try_fast_path(
        self, session: ClientSession, user_text: str, turn_id: str
    ) -> str | None:
        """明确的「开/关某设备」直接执行；任何一步不确定都交回模型。"""

        parsed = parse_simple_control(user_text)
        if parsed is None:
            return None
        find_call = {"function": {"name": "home_find_devices", "arguments": {}}}
        message = await self._call_tool(session, find_call, None, turn_id)
        payload = _json_payload(str(message.get("content") or ""))
        if payload is None:
            return None
        devices = [item for item in payload.get("devices") or [] if isinstance(item, dict)]
        hits = match_devices(devices, parsed.target)
        if len(hits) != 1:
            self._audit.write(
                "fastpath_skipped",
                user=user_text,
                reason="ambiguous" if hits else "no_match",
                candidates=len(hits),
            )
            return None
        device = hits[0]
        if str(device.get("risk_level") or "") == "high":
            return None
        command = pick_command(device, parsed.direction)
        if command is None:
            return None
        control_call = {
            "function": {
                "name": "home_control_device",
                "arguments": {
                    "device_id": str(device.get("device_id") or ""),
                    "command": command,
                },
            }
        }
        result_message = await self._call_tool(session, control_call, None, turn_id)
        result = _json_payload(str(result_message.get("content") or ""))
        if result is None or result.get("status") != "sent":
            return None
        self._audit.write(
            "fastpath_matched",
            user=user_text,
            device_id=device.get("device_id"),
            command=command,
        )
        return _control_reply(str(device.get("name") or ""), command, user_text)

    def _remember_devices(self, payload: dict[str, Any] | None) -> None:
        """缓存 device_id → name，供控制成功后的一句话回复使用。"""

        if not payload:
            return
        for item in payload.get("devices") or []:
            if not isinstance(item, dict):
                continue
            device_id = item.get("device_id") or item.get("id")
            name = item.get("name")
            if isinstance(device_id, str) and isinstance(name, str) and name:
                self._device_names[device_id] = name

    def _valid_pending(self, pending: _Pending | None, *, key: str) -> _Pending | None:
        """过期检查：超过 confirm_ttl 的待确认项作废并审计 confirm_expired。"""

        if pending is None:
            return None
        if time.monotonic() - pending.created > self._settings.confirm_ttl:
            self._audit.write("confirm_expired", **{key: pending.target})
            return None
        return pending

    def _guard_confirm(
        self, arguments: dict[str, Any], pending: _Pending | None
    ) -> tuple[dict[str, Any], bool]:
        """模型不能自己补 consent：只有上一轮被拦下的同设备 + 本轮明确同意才放行。"""

        if not arguments.get("confirm"):
            return arguments, False
        device_id = str(arguments.get("device_id") or "")
        # 两种来源算「已问过」：上一轮被拦下的设备，或本轮刚被服务端拦下的设备。
        pending = pending or self._pending_device
        if self._user_confirmed and pending and device_id == pending.target:
            return arguments, True
        return (
            self._downgrade_confirm(
                arguments, target=device_id, pending=pending, key="device_id", label="设备"
            ),
            False,
        )

    def _guard_scene_confirm(
        self, arguments: dict[str, Any], pending: _Pending | None
    ) -> tuple[dict[str, Any], bool]:
        """场景与设备各自维护待确认状态，互不串用。"""

        if not arguments.get("confirm"):
            return arguments, False
        scene_id = str(arguments.get("scene_id") or "")
        pending = pending or self._pending_scene
        if self._user_confirmed and pending and scene_id == pending.target:
            return arguments, True
        return (
            self._downgrade_confirm(
                arguments, target=scene_id, pending=pending, key="scene_id", label="场景"
            ),
            False,
        )

    def _downgrade_confirm(
        self,
        arguments: dict[str, Any],
        *,
        target: str,
        pending: _Pending | None,
        key: str,
        label: str,
    ) -> dict[str, Any]:
        blocked = dict(arguments)
        blocked["confirm"] = False
        reason = (
            f"{label}不在待确认清单"
            if pending and target != pending.target
            else "本轮无明确用户同意"
        )
        self._audit.write(
            "confirm_denied",
            **{key: target},
            pending=pending.target if pending else None,
            reason=reason,
            user_confirmed=self._user_confirmed,
        )
        return blocked

    def _server_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["HOMELINK_BASE_URL"] = self._settings.homelink_base_url
        if self._settings.homelink_token:
            env["HOMELINK_API_TOKEN"] = self._settings.homelink_token
        else:
            env.pop("HOMELINK_API_TOKEN", None)
        # 审计身份：未显式配置时也固定为 butler-agent，保证后端审计能识别 Agent 来源。
        env.setdefault("HOMELINK_ACTOR", DEFAULT_ACTOR)
        return env
