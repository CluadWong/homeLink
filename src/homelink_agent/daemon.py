"""Agent 常驻服务：保活 MCP 连接与模型，HTTP 提供 /api/chat。

与一次性 CLI 的差别：
- MCP 子进程只起一次，掉线自动重连（只恢复连接，不重发失败那轮，避免红外重复发送）；
- 模型用 keep_alive 驻留显存，启动时预热，后续提问免去几十秒的模型加载；
- 每个会话独立保留历史与高风险确认状态，客户端只需带 session_id。

用法：
    homelink-agentd                      # 127.0.0.1:8100
    python -m homelink_agent.daemon --host 0.0.0.0 --port 8100

接口：
    POST /api/chat            {"message": "...", "session_id": "..."} → {"reply", "session_id"}
    GET  /api/health          连接、模型、会话数
    DELETE /api/sessions/{id} 主动丢弃会话（含未完成的高风险确认）
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

from homelink_agent.agent import HomeAgent
from homelink_agent.config import AgentSettings, load_settings
from homelink_agent.llm import OllamaError, to_ollama_tool, warm_model

_HISTORY_CAP = 20  # 保留最近 10 轮，防止上下文无限膨胀


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    session_id: str
    reply: str


class McpConnection:
    """长连的 homelink_mcp 子进程；掉线后由 ensure() 在下一轮请求前重连。"""

    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings
        self._stack: AsyncExitStack | None = None
        self._errlog: Any = None
        self.session: ClientSession | None = None
        self.tools: list[dict[str, Any]] = []

    @property
    def connected(self) -> bool:
        return self.session is not None

    async def start(self) -> None:
        await self.stop()
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "homelink_mcp"],
            env=self._server_env(),
        )
        self._errlog = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(
                stdio_client(params, errlog=self._errlog)
            )
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools = await session.list_tools()
            self.session = session
            self.tools = [to_ollama_tool(tool) for tool in tools.tools]
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            finally:
                self._stack = None
                self.session = None
                self.tools = []
        if self._errlog is not None:
            self._errlog.close()
            self._errlog = None

    async def ensure(self) -> None:
        if self.session is None:
            await self.start()

    def _server_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["HOMELINK_BASE_URL"] = self._settings.homelink_base_url
        if self._settings.homelink_token:
            env["HOMELINK_API_TOKEN"] = self._settings.homelink_token
        else:
            env.pop("HOMELINK_API_TOKEN", None)
        return env


class SessionStore:
    """会话 = 独立的 HomeAgent（确认状态）+ 历史；按 TTL 与容量上限淘汰。"""

    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def get(self, session_id: str) -> dict[str, Any]:
        self.evict_expired()
        item = self._items.get(session_id)
        if item is None:
            item = {"agent": HomeAgent(self._settings), "history": [], "last_used": time.time()}
            self._items[session_id] = item
            while len(self._items) > self._settings.max_sessions:  # 淘汰最久未用的
                self._items.popitem(last=False)
        item["last_used"] = time.time()
        self._items.move_to_end(session_id)
        return item

    def drop(self, session_id: str) -> bool:
        return self._items.pop(session_id, None) is not None

    def evict_expired(self) -> None:
        deadline = time.time() - self._settings.session_ttl_min * 60
        while self._items:
            oldest_id, oldest = next(iter(self._items.items()))
            if oldest["last_used"] > deadline:
                break
            del self._items[oldest_id]


def create_agent_app(
    settings: AgentSettings,
    connection: McpConnection | None = None,
    warmup: bool = True,
) -> FastAPI:
    """connection/warmup 参数供测试注入；生产路径全部走默认值。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if own_connection:
            await conn.start()
            if warmup:
                try:
                    await warm_model(settings)
                except OllamaError as exc:  # 预热失败不拦启动，首轮提问会给出同样报错
                    print(f"警告：{exc}", file=sys.stderr, flush=True)
        yield
        if own_connection:
            await conn.stop()

    app = FastAPI(title="HomeLink Agent Daemon", lifespan=lifespan)
    own_connection = connection is None
    conn = connection if connection is not None else McpConnection(settings)
    store = SessionStore(settings)
    lock = asyncio.Lock()  # 9B 模型一次只能跑一个请求，串行化避免显存争抢

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat_endpoint(payload: ChatRequest) -> ChatResponse:
        await conn.ensure()
        session_id = payload.session_id or uuid.uuid4().hex[:12]
        item = store.get(session_id)
        async with lock:  # 串行执行；等待期间会话不会被淘汰（TTL 按 last_used）
            try:
                reply = await item["agent"].respond(
                    conn.session, conn.tools, payload.message, item["history"]
                )
            except OllamaError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except Exception as exc:  # MCP 掉线等：恢复连接但不重发这一轮，避免红外重复
                await conn.stop()
                raise HTTPException(
                    status_code=502,
                    detail=f"本轮失败（{exc}）；连接已重置，请重试。上一条命令如已发出不会重复。",
                ) from exc
        history: list[dict[str, str]] = item["history"]
        history.append({"role": "user", "content": payload.message})
        history.append({"role": "assistant", "content": reply})
        del history[:-_HISTORY_CAP]
        return ChatResponse(session_id=session_id, reply=reply)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        store.evict_expired()
        return {
            "status": "ok" if conn.connected else "degraded",
            "model": settings.model,
            "mcp_connected": conn.connected,
            "tools": len(conn.tools),
            "sessions": len(store),
            "keep_alive": settings.keep_alive,
        }

    @app.delete("/api/sessions/{session_id}")
    async def drop_session(session_id: str) -> dict[str, bool]:
        return {"dropped": store.drop(session_id)}

    return app


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="HomeLink Agent 常驻服务")
    parser.add_argument("--host", default=settings.daemon_host)
    parser.add_argument("--port", type=int, default=settings.daemon_port)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(create_agent_app(settings), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
