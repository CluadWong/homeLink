"""Agent 主循环：MCP 子进程提供工具，Ollama 负责选择工具。"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from homelink_agent.config import AgentSettings
from homelink_agent.llm import chat, to_ollama_tool
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


class HomeAgent:
    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings
        self._pending_device: str | None = None
        self._user_confirmed = False

    async def run(self, user_text: str, history: list[dict[str, str]] | None = None) -> str:
        # 上一轮被拦下的设备只对这一轮有效，避免「下次随口说好」被当成授权。
        previous_pending = self._pending_device
        self._pending_device = None
        self._user_confirmed = _looks_like_consent(user_text)

        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history_messages(history or []))
        messages.append({"role": "user", "content": user_text})

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "homelink_mcp"],
            env=self._server_env(),
        )
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with (
                stdio_client(params, errlog=errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                tools = [to_ollama_tool(tool) for tool in (await session.list_tools()).tools]

                for _ in range(self._settings.max_steps):
                    message = await chat(self._settings, messages, tools)
                    calls = message.get("tool_calls") or []
                    if not calls:
                        return str(message.get("content") or "").strip() or "（模型没有返回内容）"

                    messages.append(
                        {
                            "role": "assistant",
                            "content": str(message.get("content") or ""),
                            "tool_calls": calls,
                        }
                    )
                    for call in calls:
                        messages.append(await self._call_tool(session, call, previous_pending))

        return "（指令拆得太复杂，已到最大步数；请一次只做一件事）"

    async def _call_tool(
        self,
        session: ClientSession,
        call: dict[str, Any],
        previous_pending: str | None,
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

        result = await session.call_tool(name, arguments)
        text = "\n".join(
            getattr(block, "text", "")
            for block in result.content
            if getattr(block, "type", "") == "text"
        )
        _debug(f"[tool] {name} {json.dumps(arguments, ensure_ascii=False)} -> {text[:200]}")
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
        return blocked

    def _server_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["HOMELINK_BASE_URL"] = self._settings.homelink_base_url
        if self._settings.homelink_token:
            env["HOMELINK_API_TOKEN"] = self._settings.homelink_token
        else:
            env.pop("HOMELINK_API_TOKEN", None)
        return env
