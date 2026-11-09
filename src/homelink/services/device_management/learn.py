"""学码会话：四步端点 + 600 秒空闲 TTL。

并发模型：所有会话状态只由 ``threading.RLock`` 保护；TTL 用每会话一个
``threading.Timer``，到期回调在锁内比对「设备 + 代次」，已被 save/cancel/新会话
取代的回调直接退出，避免误停接收。
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

from homelink.brain.audit import AuditStore
from homelink.core.errors import AppError, ErrorCode
from homelink.gateway.base import GatewayError, IRGateway
from homelink.gateway.waveform import WaveformError, decode_waveform
from homelink.services.device_management.devices import DeviceStore

DEFAULT_IDLE_TTL_SECONDS = 600.0

REASON_NO_SIGNAL = "no_signal"
REASON_MALFORMED = "malformed"
REASON_GATEWAY_UNREACHABLE = "gateway_unreachable"

_MISSING_SESSION = "学码会话不存在或已结束"


@dataclass
class _Session:
    device_id: str
    generation: int
    timer: threading.Timer | None = field(default=None)


class LearnService:
    def __init__(
        self,
        devices: DeviceStore,
        gateway: IRGateway,
        audit: AuditStore,
        *,
        idle_ttl_seconds: float = DEFAULT_IDLE_TTL_SECONDS,
    ) -> None:
        self._devices = devices
        self._gateway = gateway
        self._audit = audit
        self._idle_ttl = idle_ttl_seconds
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}
        self._generation = 0

    # ---------------------------------------------------------------- 四步

    def start(self, device_id: str, *, trace_id: str) -> dict[str, Any]:
        self._devices.get(device_id)  # 设备不存在 → 404
        try:
            self._gateway.start_learn()
        except GatewayError as exc:
            self._audit.write(
                who="user",
                action="learn.start",
                device_id=device_id,
                context={"device_id": device_id},
                result="failed",
                reason=exc.message,
                trace_id=trace_id,
            )
            raise _as_app_error(exc) from exc

        with self._lock:
            previous = self._sessions.get(device_id)
            if previous is not None:
                self._disarm(previous)
            session = _Session(device_id=device_id, generation=0)
            self._sessions[device_id] = session
            self._arm(session)

        self._audit.write(
            who="user",
            action="learn.start",
            device_id=device_id,
            context={"device_id": device_id},
            result="success",
            trace_id=trace_id,
        )
        return {"device_id": device_id}

    def capture(self, device_id: str, *, trace_id: str) -> dict[str, Any]:
        with self._lock:
            if self._sessions.get(device_id) is None:
                raise AppError(ErrorCode.NOT_FOUND, _MISSING_SESSION)

        try:
            text = self._gateway.read_last_code()
        except GatewayError as exc:
            if exc.kind == "unauthorized":
                raise _as_app_error(exc) from exc
            return _capture_result(device_id, captured=False, reason=REASON_GATEWAY_UNREACHABLE)

        with self._lock:
            session = self._sessions.get(device_id)
            if session is not None:
                self._arm(session)  # 捕获动作续期，避免长读期间被 TTL 回收

        if text is None or not text.strip():
            return _capture_result(device_id, captured=False, reason=REASON_NO_SIGNAL)
        try:
            raw_code = decode_waveform(text)
        except WaveformError as exc:
            return _capture_result(
                device_id,
                captured=False,
                reason=REASON_MALFORMED,
                message=f"收到的波形无法解析（{exc.label}）：{exc}",
            )
        return _capture_result(device_id, captured=True, raw_code=raw_code)

    def save(
        self, device_id: str, name: str, raw_code: list[int], *, trace_id: str
    ) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(device_id)
            if session is None:
                raise AppError(ErrorCode.NOT_FOUND, _MISSING_SESSION)
            # 先落库（含 3 条保留裁剪），再结束会话；落库失败不回滚会话。
            created = self._devices.create_function_with_code(device_id, name, raw_code)
            self._sessions.pop(device_id, None)
            self._disarm(session)

        receiver_stopped = True
        receiver_error: str | None = None
        try:
            self._gateway.stop_learn()
        except GatewayError as exc:
            receiver_stopped = False
            receiver_error = exc.message

        self._audit.write(
            who="user",
            action="learn.save",
            device_id=device_id,
            context={"device_id": device_id, "function_id": created["function_id"]},
            reason=receiver_error,
            result="success",
            trace_id=trace_id,
        )
        return {
            "function_id": created["function_id"],
            "name": created["name"],
            "receiver_stopped": receiver_stopped,
            "receiver_error": receiver_error,
        }

    def cancel(self, device_id: str, *, trace_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.pop(device_id, None)
            if session is not None:
                self._disarm(session)

        receiver_error: str | None = None
        try:
            self._gateway.stop_learn()
        except GatewayError as exc:
            receiver_error = exc.message

        self._audit.write(
            who="user",
            action="learn.cancel",
            device_id=device_id,
            context={"device_id": device_id},
            reason=receiver_error,
            result="success",
            trace_id=trace_id,
        )
        return {
            "device_id": device_id,
            "message": "已取消学码",
        }

    # ------------------------------------------------------------ TTL 内部

    def _arm(self, session: _Session) -> None:
        """（持锁调用）重置该会话的空闲计时器，并递增代次作废旧回调。"""

        if session.timer is not None:
            session.timer.cancel()
        self._generation += 1
        session.generation = self._generation
        timer = threading.Timer(
            self._idle_ttl, self._expire, args=(session.device_id, session.generation)
        )
        timer.daemon = True
        session.timer = timer
        timer.start()

    def _disarm(self, session: _Session) -> None:
        if session.timer is not None:
            session.timer.cancel()
            session.timer = None

    def _expire(self, device_id: str, generation: int) -> None:
        with self._lock:
            session = self._sessions.get(device_id)
            if session is None or session.generation != generation:
                return  # 已被 save / cancel / 新会话取代
            self._sessions.pop(device_id, None)
            self._disarm(session)

        receiver_error: str | None = None
        try:
            self._gateway.stop_learn()
        except GatewayError as exc:
            receiver_error = exc.message

        reason = "空闲超时"
        if receiver_error:
            reason = f"{reason}；关接收失败：{receiver_error}"
        self._audit.write(
            who="system",
            action="learn.idle_timeout",
            device_id=device_id,
            context={"device_id": device_id},
            reason=reason,
            result="success",
            trace_id=secrets.token_hex(4),  # 系统收尾无请求 trace
        )


def _capture_result(
    device_id: str,
    *,
    captured: bool,
    raw_code: list[int] | None = None,
    reason: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    messages = {
        REASON_NO_SIGNAL: "还没有收到信号，请在设备前按键",
        REASON_MALFORMED: "收到的波形无法解析，请重新按键",
        REASON_GATEWAY_UNREACHABLE: "网关连接失败，请检查网关状态",
        None: "已捕获到红外波形",
    }
    return {
        "captured": captured,
        "device_id": device_id,
        "raw_code": raw_code,
        "reason": reason,
        "message": message or messages[reason],
    }


def _as_app_error(exc: GatewayError) -> AppError:
    if exc.kind == "unauthorized":
        return AppError(ErrorCode.DEVICE_UNAUTHORIZED, exc.message)
    return AppError(ErrorCode.DEVICE_OFFLINE, exc.message)
