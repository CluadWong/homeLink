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
    GET  /api/metrics         进程内计数：请求数 / 错误数 / 工具调用数 / 耗时分位
    DELETE /api/sessions/{id} 主动丢弃会话（含未完成的高风险确认）
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field
from starlette.middleware.base import RequestResponseEndpoint

from homelink_agent.agent import HomeAgent
from homelink_agent.config import AgentSettings, load_settings
from homelink_agent.llm import OllamaError, to_ollama_tool, warm_model
from homelink_mcp.config import DEFAULT_ACTOR
from mcp import ClientSession, StdioServerParameters

_HISTORY_CAP = 20  # 保留最近 10 轮，防止上下文无限膨胀


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


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
        # 审计身份：未显式配置时也固定为 butler-agent，保证后端审计能识别 Agent 来源。
        env.setdefault("HOMELINK_ACTOR", DEFAULT_ACTOR)
        return env


class Metrics:
    """进程内计数（P1-6）：只读输出给 /api/metrics，不引入 Prometheus。

    只记次数与耗时，不记消息内容，天然不泄露用户输入。
    """

    def __init__(self, *, max_latency_samples: int = 1000) -> None:
        self.requests_total = 0
        self.errors_total = 0
        self.tool_calls_total = 0
        self._latencies_ms: list[float] = []
        self._max_samples = max_latency_samples

    def on_tool(self, name: str, arguments: dict[str, Any], text: str) -> None:
        """HomeAgent 的 on_tool 回调签名；只计数，不落内容。"""
        self.tool_calls_total += 1

    def record_request(self, *, error: bool, latency_ms: float) -> None:
        self.requests_total += 1
        if error:
            self.errors_total += 1
        self._latencies_ms.append(round(latency_ms, 1))
        if len(self._latencies_ms) > self._max_samples:  # 只留最近 N 个样本
            del self._latencies_ms[: len(self._latencies_ms) - self._max_samples]

    def percentile(self, pct: float) -> float | None:
        """最近秩百分位；无样本返回 None。"""
        if not self._latencies_ms:
            return None
        ordered = sorted(self._latencies_ms)
        rank = max(1, math.ceil(pct / 100 * len(ordered)))
        return ordered[min(rank, len(ordered)) - 1]

    def snapshot(self) -> dict[str, Any]:
        return {
            "requests_total": self.requests_total,
            "errors_total": self.errors_total,
            "tool_calls_total": self.tool_calls_total,
            "latency_ms": {"p50": self.percentile(50), "p95": self.percentile(95)},
        }


class SessionStore:
    """会话 = 独立的 HomeAgent（确认状态）+ 历史；按 TTL 与容量上限淘汰。"""

    def __init__(self, settings: AgentSettings, on_tool: Any = None) -> None:
        self._settings = settings
        self._on_tool = on_tool
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def get(self, session_id: str) -> dict[str, Any]:
        self.evict_expired()
        item = self._items.get(session_id)
        if item is None:
            item = {
                "agent": HomeAgent(self._settings, on_tool=self._on_tool),
                "history": [],
                "last_used": time.time(),
            }
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
    metrics = Metrics()
    store = SessionStore(settings, on_tool=metrics.on_tool)
    lock = asyncio.Lock()  # 9B 模型一次只能跑一个请求，串行化避免显存争抢

    @app.middleware("http")
    async def bearer_auth(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """AGENTD_TOKEN 非空时，除 /api/health 外全部要求 Bearer；空 = 不校验（本机开发）。"""
        if settings.daemon_token and request.url.path != "/api/health":
            header = request.headers.get("Authorization", "")
            if header != f"Bearer {settings.daemon_token}":
                return JSONResponse(
                    {"detail": "未授权：需要 Authorization: Bearer <AGENTD_TOKEN>"},
                    status_code=401,
                )
        return await call_next(request)

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat_endpoint(payload: ChatRequest) -> ChatResponse:
        await conn.ensure()
        session_id = payload.session_id or uuid.uuid4().hex[:12]
        item = store.get(session_id)
        started = time.perf_counter()
        async with lock:  # 串行执行；等待期间会话不会被淘汰（TTL 按 last_used）
            try:
                reply = await item["agent"].respond(
                    conn.session, conn.tools, payload.message, item["history"]
                )
            except OllamaError as exc:
                metrics.record_request(error=True, latency_ms=_elapsed_ms(started))
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except Exception as exc:  # MCP 掉线等：恢复连接但不重发这一轮，避免红外重复
                metrics.record_request(error=True, latency_ms=_elapsed_ms(started))
                await conn.stop()
                raise HTTPException(
                    status_code=502,
                    detail=f"本轮失败（{exc}）；连接已重置，请重试。上一条命令如已发出不会重复。",
                ) from exc
        metrics.record_request(error=False, latency_ms=_elapsed_ms(started))
        history: list[dict[str, str]] = item["history"]
        history.append({"role": "user", "content": payload.message})
        history.append({"role": "assistant", "content": reply})
        del history[:-_HISTORY_CAP]
        return ChatResponse(session_id=session_id, reply=reply)

    @app.get("/api/metrics")
    async def metrics_endpoint() -> dict[str, Any]:
        store.evict_expired()
        return {
            **metrics.snapshot(),
            "sessions": len(store),
            "mcp_connected": conn.connected,
        }

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
