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
    POST /api/schedules       {"scene_id", "cron", "enabled", "confirm"} → 新建定时任务
    GET  /api/schedules       列出全部定时任务
    DELETE /api/schedules/{id} 删除定时任务
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field
from starlette.middleware.base import RequestResponseEndpoint

from homelink_agent.agent import HomeAgent
from homelink_agent.audit import AuditLog
from homelink_agent.config import AgentSettings, load_settings
from homelink_agent.llm import OllamaError, to_ollama_tool, warm_model
from homelink_agent.scheduler import (
    Schedule,
    Scheduler,
    ScheduleStore,
    dump_schedule,
    validate_cron,
)
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


class ScheduleRequest(BaseModel):
    scene_id: str = Field(min_length=1, max_length=64)
    cron: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    confirm: bool = False


SceneLookup = Callable[[str], Awaitable[dict[str, Any] | None]]


def _backend_scene_lookup(settings: AgentSettings) -> SceneLookup:
    """默认从后端读场景清单，用来校验场景是否存在与是否含高风险设备。"""

    async def lookup(scene_id: str) -> dict[str, Any] | None:
        headers = {"X-Actor": DEFAULT_ACTOR}
        if settings.homelink_token:
            headers["Authorization"] = f"Bearer {settings.homelink_token}"
        async with httpx.AsyncClient(base_url=settings.homelink_base_url, timeout=10.0) as client:
            response = await client.get("/api/agent/scenes", headers=headers)
            response.raise_for_status()
            payload = response.json()
        scenes = (payload.get("result") or {}).get("scenes") or []
        for scene in scenes:
            if isinstance(scene, dict) and scene.get("id") == scene_id:
                return scene
        return None

    return lookup


def _tool_text(result: Any) -> str:
    return "\n".join(
        getattr(block, "text", "")
        for block in result.content
        if getattr(block, "type", "") == "text"
    )


def _schedule_runner(
    conn: McpConnection, lookup: SceneLookup
) -> Callable[[Schedule], Awaitable[None]]:
    """到点执行：复查风险后经 MCP 跑场景；失败抛错由 Scheduler 记审计且不重试。"""

    async def run(schedule: Schedule) -> None:
        scene = await lookup(schedule.scene_id)
        if scene is None:
            raise RuntimeError(f"场景不存在：{schedule.scene_id}")
        if scene.get("contains_high_risk") and not schedule.confirm:
            raise PermissionError("场景已升级为高风险且创建时未授权，跳过本次执行")
        await conn.ensure()
        if conn.session is None:
            raise RuntimeError("MCP 未连接")
        result = await conn.session.call_tool(
            "home_run_scene", {"scene_id": schedule.scene_id, "confirm": True}
        )
        text = _tool_text(result)
        if result.isError:
            raise RuntimeError(text or "场景执行失败")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError("场景执行返回不是 JSON") from None
        status = payload.get("status") if isinstance(payload, dict) else None
        if status in {"partial", "failed"}:
            raise RuntimeError(f"场景执行 {status}")

    return run


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

    def clear(self) -> None:
        """退出时清空全部会话（含未完成的高风险确认状态）。"""

        self._items.clear()

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
    *,
    start_scheduler: bool = True,
    scene_lookup: SceneLookup | None = None,
) -> FastAPI:
    """connection/warmup/start_scheduler/scene_lookup 参数供测试注入；生产路径全走默认值。"""

    own_connection = connection is None
    conn = connection if connection is not None else McpConnection(settings)
    metrics = Metrics()
    store = SessionStore(settings, on_tool=metrics.on_tool)
    lock = asyncio.Lock()  # 9B 模型一次只能跑一个请求，串行化避免显存争抢
    audit = AuditLog(settings.audit_path)
    schedules = ScheduleStore(settings.agent_db_path)
    lookup = scene_lookup or _backend_scene_lookup(settings)
    scheduler = Scheduler(schedules, _schedule_runner(conn, lookup), audit=audit)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if own_connection:
            await conn.start()
            if warmup:
                try:
                    await warm_model(settings)
                except OllamaError as exc:  # 预热失败不拦启动，首轮提问会给出同样报错
                    print(f"警告：{exc}", file=sys.stderr, flush=True)
        if start_scheduler:
            task = asyncio.create_task(scheduler.run())
        try:
            yield
        finally:
            # 优雅退出（SIGTERM / Ctrl+C 走 uvicorn 的 lifespan shutdown）：
            # 先停调度（不再发起新的红外指令），再清会话与连接。
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            sessions = len(store)
            store.clear()
            if own_connection:
                await conn.stop()
            schedules.close()
            audit.write("shutdown", sessions=sessions, mcp_connected=conn.connected)

    app = FastAPI(title="HomeLink Agent Daemon", lifespan=lifespan)

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
        # 背压：最多排队 queue_timeout 秒，超时直接 429 拒绝，不丢请求也不重复执行
        try:
            await asyncio.wait_for(lock.acquire(), timeout=settings.queue_timeout)
        except TimeoutError as exc:
            metrics.record_request(error=True, latency_ms=_elapsed_ms(started))
            raise HTTPException(
                status_code=429,
                detail=f"模型忙：等待超过 {settings.queue_timeout:g} 秒，请稍后重试",
            ) from exc
        try:
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
        finally:
            lock.release()
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

    @app.post("/api/schedules")
    async def create_schedule(payload: ScheduleRequest) -> dict[str, Any]:
        try:
            validate_cron(payload.cron)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"cron 表达式不合法：{exc}") from exc
        try:
            scene = await lookup(payload.scene_id)
        except Exception as exc:  # 后端不可用时不落库，避免创建无法校验的计划
            raise HTTPException(status_code=502, detail=f"无法读取场景：{exc}") from exc
        if scene is None:
            raise HTTPException(status_code=404, detail=f"场景不存在：{payload.scene_id}")
        if scene.get("contains_high_risk") and not payload.confirm:
            # 高风险场景必须由用户在创建时明确授权，之后到点执行才允许 confirm=true
            raise HTTPException(
                status_code=409, detail="场景含高风险设备：创建定时任务需要 confirm=true"
            )
        schedule = schedules.add(
            scene_id=payload.scene_id,
            cron=payload.cron,
            enabled=payload.enabled,
            confirm=payload.confirm,
        )
        audit.write(
            "schedule_created",
            schedule_id=schedule.id,
            scene_id=schedule.scene_id,
            cron=schedule.cron,
            enabled=schedule.enabled,
            confirm=schedule.confirm,
        )
        return dump_schedule(schedule)

    @app.get("/api/schedules")
    async def list_schedules() -> dict[str, Any]:
        items = [dump_schedule(schedule) for schedule in schedules.list_all()]
        return {"count": len(items), "schedules": items}

    @app.delete("/api/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: str) -> dict[str, bool]:
        deleted = schedules.delete(schedule_id)
        if deleted:
            audit.write("schedule_deleted", schedule_id=schedule_id)
        return {"deleted": deleted}

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
