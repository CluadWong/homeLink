"""FastAPI 应用：trace 中间件 + 错误信封 + 路由注册。

模块级 app 供 `uvicorn homelink.interface.api.app:app` 使用；测试用 create_app(context)。
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from homelink.bootstrap.context import AppContext, build_context
from homelink.core.config import ConfigError
from homelink.core.errors import AppError, ErrorCode
from homelink.interface.api.errors import register_exception_handlers
from homelink.interface.api.routers import (
    agent,
    audit,
    control,
    correct,
    devices,
    gateway,
    health,
    learn,
    rooms,
    state,
)

logger = logging.getLogger(__name__)

TRACE_HEADER = "X-Trace-Id"
_TRACE_MAX_LENGTH = 64


def create_app(context: AppContext) -> FastAPI:
    app = FastAPI(title="HomeLink", version="0.1.0")
    app.state.context = context

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        trace_id = request.headers.get(TRACE_HEADER, "").strip()
        if not trace_id or len(trace_id) > _TRACE_MAX_LENGTH:
            trace_id = secrets.token_hex(4)
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers[TRACE_HEADER] = trace_id
        return response

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(state.router)
    app.include_router(rooms.router)
    app.include_router(devices.router)
    app.include_router(learn.router)
    app.include_router(control.router)
    app.include_router(correct.router)
    app.include_router(gateway.router)
    app.include_router(audit.router)
    app.include_router(agent.router)
    _register_frontend(app, context)
    return app


def frontend_dist(context: AppContext) -> Path:
    """前端构建产物目录；相对路径按配置目录的上一级（仓库根）解析。"""

    raw = Path(context.config.settings.frontend.dist_path)
    if raw.is_absolute():
        return raw
    return (context.config.config_dir.parent / raw).resolve()


def _register_frontend(app: FastAPI, context: AppContext) -> None:
    """同源托管 `dist`；`/api/*` 永远不落到 index.html。

    - 开关关闭：什么都不挂，服务是纯 API（未匹配路径仍是 JSON 404）。
    - 开启但缺 `dist/index.html`：打警告，非 API 路径给 503 人话提示，API 不受影响。
    """

    settings = context.config.settings.frontend
    if not settings.enabled:
        return

    dist = frontend_dist(context)
    index = dist / "index.html"
    if not index.is_file():
        logger.warning(
            "前端静态托管已开启，但缺少 %s；请先在 frontend/ 运行 npm run build。当前只提供 API。",
            index,
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _frontend_missing(full_path: str) -> Response:
            if full_path == "api" or full_path.startswith("api/"):
                raise AppError(ErrorCode.NOT_FOUND, "接口不存在")
            return PlainTextResponse(
                f"前端未构建：缺少 {index}。请先在 frontend/ 目录运行 npm install && npm run build，"
                "然后重启服务；现在 /api 仍可正常使用。",
                status_code=503,
            )

        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_frontend(full_path: str) -> Response:
        if full_path == "api" or full_path.startswith("api/"):
            raise AppError(ErrorCode.NOT_FOUND, "接口不存在")
        if full_path:
            target = dist / full_path
            try:
                target.relative_to(dist)
            except ValueError:
                raise AppError(ErrorCode.NOT_FOUND, "页面不存在") from None
            if target.is_file():
                return FileResponse(target)
        return FileResponse(index)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def create_app_from_env(
    config_dir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> FastAPI:
    """按环境变量 / 默认路径装配应用。

    配置缺失或不可解析时抛 `ConfigError`，消息与命令行启动打印的完全一致。
    """

    root = _repo_root()
    if config_dir is None:
        config_dir = os.environ.get("HOMELINK_CONFIG_DIR", root / "config")
    if db_path is None:
        db_path = os.environ.get("HOMELINK_DB_PATH", root / "data" / "homelink.db")
    return create_app(build_context(Path(config_dir), Path(db_path)))


try:
    app = create_app_from_env()
except ConfigError as exc:  # 配置问题：只打一段人话并以非 0 退出，不甩裸堆栈
    print(str(exc), file=sys.stderr)
    raise SystemExit(1) from None
