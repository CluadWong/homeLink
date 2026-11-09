"""对外错误码（见 docs/api-contract.md）。"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"
    DEVICE_UNAUTHORIZED = "DEVICE_UNAUTHORIZED"
    GATEWAY_TIMEOUT = "GATEWAY_TIMEOUT"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.DEVICE_OFFLINE: 503,
    ErrorCode.DEVICE_UNAUTHORIZED: 401,
    ErrorCode.GATEWAY_TIMEOUT: 504,
    ErrorCode.EXECUTION_FAILED: 502,
    ErrorCode.CONFIRMATION_REQUIRED: 409,
    ErrorCode.NOT_SUPPORTED: 501,
    ErrorCode.INTERNAL_ERROR: 500,
}


class AppError(Exception):
    """可安全外露的业务错误；message 直接给人看，不含内部模块名。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = STATUS_BY_CODE[code]
