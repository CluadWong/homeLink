"""MCP 侧错误：把服务端错误信封转成模型能直接照着行动的一句话。

服务端错误形状见 `docs/api-contract.md`：`{success: false, error, message, trace_id}`。
MCP 工具抛出的异常会变成 `isError` 结果，因此 `__str__` 面向模型书写。
"""

from __future__ import annotations

from typing import Any

# 每个错误码附一句「下一步怎么做」；模型不必记文档。
_HINTS: dict[str, str] = {
    "INVALID_REQUEST": "请检查参数：parameters 必须为空对象，command 必须是 capabilities 中的名字或 command_id。",
    "UNAUTHORIZED": "服务端 token 不匹配，属于配置问题，不要重试。",
    "NOT_FOUND": "没有这个设备/功能/场景；先调用 home_find_devices 确认 device_id 与可用 command。",
    "CONFLICT": "request_id 已被占用或场景重名；换一个新的 request_id 再发。",
    "CONFIRMATION_REQUIRED": "高风险设备需用户明确同意后，带 confirm=true 重试。",
    "DEVICE_OFFLINE": "红外网关不可达，提示用户检查板子电源与网络；最多重试 1 次。",
    "DEVICE_UNAUTHORIZED": "网关鉴权失败，需要修凭据，不要重试。",
    "GATEWAY_TIMEOUT": "信号可能已发出，不要自动重发，请让用户现场确认。",
    "EXECUTION_FAILED": "网关返回异常响应，不要自动重发。",
    "NOT_SUPPORTED": "该能力未开放（当前只有场景，没有自动化/定时）。",
    "INTERNAL_ERROR": "服务内部错误，稍后重试；排障时再展示 trace_id。",
    "UNREACHABLE": "连不上 HomeLink 服务，确认服务已启动且 HOMELINK_BASE_URL 正确。",
    "BAD_RESPONSE": "服务返回了非 JSON 内容，检查地址是否指向 HomeLink 而不是其他服务。",
}


class HomeLinkError(Exception):
    """携带错误码与 trace_id 的可读异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.trace_id = trace_id

    @classmethod
    def from_envelope(cls, payload: dict[str, Any], *, status: int | None = None) -> HomeLinkError:
        return cls(
            str(payload.get("error") or "INTERNAL_ERROR"),
            str(payload.get("message") or "服务返回失败但未提供原因"),
            status=status,
            trace_id=str(payload.get("trace_id") or "") or None,
        )

    def __str__(self) -> str:
        parts = [f"[{self.code}] {self.message}"]
        hint = _HINTS.get(self.code)
        if hint:
            parts.append(hint)
        if self.trace_id:
            parts.append(f"trace_id={self.trace_id}")
        return " ".join(parts)
