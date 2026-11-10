"""Agent 主循环：MCP 子进程提供工具，Ollama 负责选择工具。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from collections.abc import Callable
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from homelink_agent.audit import AuditLog
from homelink_agent.config import AgentSettings
from homelink_agent.llm import OllamaError, chat, to_ollama_tool
from homelink_agent.prompt import SYSTEM_PROMPT, history_messages

_CONSENT_RE = re.compile(r"确认|确定|同意|可以|好的|好|是的|开吧|执行|没问题")
_DEBUG = os.environ.get("AGENT_DEBUG") == "1"


def _debug(message: str) -> None:
    if _DEBUG:
        print(message, file=sys.stderr, flush=True)


def _looks_like_consent(text: str) -> bool:
    """短消息里出现明确同意词才算同意，避免长句里夹带「确认」被误判。"""

    stripped = text.strip()
    return len(stripped) <= 12 and _CONSENT_RE.search(stripped) is not None


def _unwrap(group: BaseExceptionGroup) -> BaseException:
    """优先返回组里的 OllamaError，否则返回第一个叶子异常。"""

    for inner in group.exceptions:
        found = _unwrap(inner) if isinstance(inner, BaseExceptionGroup) else inner
        if isinstance(found, OllamaError):
            return found
    leaf = group.exceptions[0]
    return _unwrap(leaf) if isinstance(leaf, BaseExceptionGroup) else leaf


def _turn_request_id(turn_id: str, device_id: str, command: str) -> str:
    """一轮内的幂等键：同设备同命令重试天然复用，跨轮/跨设备/跨命令必然不同。"""

    digest = hashlib.sha256(f"{device_id}\x00{command}".encode()).hexdigest()[:12]
    return f"ag-{turn_id}-{digest}"


class HomeAgent:
    def __init__(
        self,
        settings: AgentSettings,
        on_tool: Callable[[str, dict[str, Any], str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._on_tool = on_tool
        self._audit = AuditLog(settings.audit_path)
        self._pending_device: str | None = None
        self._user_confirmed = False

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

        # 上一轮被拦下的设备只对这一轮有效，避免「下次随口说好」被当成授权。
        previous_pending = self._pending_device
        self._pending_device = None
        self._user_confirmed = _looks_like_consent(user_text)
        turn_id = uuid.uuid4().hex[:8]  # 本轮幂等键前缀，见 _turn_request_id
        self._audit.write(
            "turn_start", user=user_text, consent=self._user_confirmed, pending=previous_pending
        )

        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history_messages(history or []))
        messages.append({"role": "user", "content": user_text})

        try:
            for _ in range(self._settings.max_steps):
                message = await chat(self._settings, messages, tools)
                calls = message.get("tool_calls") or []
                if not calls:
                    reply = str(message.get("content") or "").strip() or "（模型没有返回内容）"
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
                    messages.append(await self._call_tool(session, call, previous_pending, turn_id))
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
        previous_pending: str | None,
        turn_id: str,
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
            arguments = self._guard_confirm(arguments, previous_pending)
            # 幂等键由运行时注入并覆盖模型编造的值：同轮同设备同命令复用，重试不会重复发红外。
            arguments["request_id"] = _turn_request_id(
                turn_id, str(arguments.get("device_id") or ""), str(arguments.get("command") or "")
            )

        result = await session.call_tool(name, arguments)
        text = "\n".join(
            getattr(block, "text", "")
            for block in result.content
            if getattr(block, "type", "") == "text"
        )
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
                self._pending_device = str(arguments.get("device_id") or "") or None
        else:
            self._pending_device = None
        return {"role": "tool", "tool_name": name, "content": text or "（无输出）"}

    def _guard_confirm(self, arguments: dict[str, Any], pending: str | None) -> dict[str, Any]:
        """模型不能自己补 consent：只有上一轮被拦下的同设备 + 本轮明确同意才放行。"""

        if not arguments.get("confirm"):
            return arguments
        device_id = str(arguments.get("device_id") or "")
        # 两种来源算「已问过」：上一轮被拦下的设备，或本轮刚被服务端拦下的设备。
        pending = pending or self._pending_device
        if self._user_confirmed and pending and device_id == pending:
            return arguments
        blocked = dict(arguments)
        blocked["confirm"] = False
        reason = "设备不在待确认清单" if pending and device_id != pending else "本轮无明确用户同意"
        self._audit.write(
            "confirm_downgraded",
            device_id=device_id,
            pending=pending,
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
        return env
