"""统一错误信封：{success, error, message, trace_id}。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from homelink.core.errors import AppError, ErrorCode


def _trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "") or ""


def _envelope(request: Request, status: int, code: ErrorCode, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "error": str(code),
            "message": message,
            "trace_id": _trace_id(request),
        },
    )


def _describe_validation_error(errors: Sequence[Mapping[str, Any]]) -> str:
    first = errors[0] if errors else {}
    location = tuple(first.get("loc", ()))
    field = next(
        (str(part) for part in reversed(location) if isinstance(part, str) and part != "body"), ""
    )
    error_type = str(first.get("type", ""))

    if error_type == "extra_forbidden":
        return f"字段 {field} 不被接受"
    if error_type == "missing":
        return f"缺少字段 {field}"
    if error_type == "json_invalid":
        return "请求体不是合法 JSON"
    if field:
        return f"字段 {field} 不合法"
    return "请求字段不合法"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return _envelope(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _envelope(
            request, 400, ErrorCode.INVALID_REQUEST, _describe_validation_error(exc.errors())
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code in (404, 405):
            return _envelope(request, 404, ErrorCode.NOT_FOUND, "接口不存在")
        code = ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else ErrorCode.INVALID_REQUEST
        return _envelope(request, exc.status_code, code, "请求未被接受")

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        return _envelope(request, 500, ErrorCode.INTERNAL_ERROR, "服务内部错误")
