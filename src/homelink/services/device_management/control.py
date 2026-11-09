"""控制链路：按 `function_id` 回放该功能最新一条码。

固定顺序：取功能与码 → 网关回放 → 追加 `device_states`（unknown）→ 审计。
探测结果不是前置条件；审计失败不回滚已发出的红外。
"""

from __future__ import annotations

import logging
from typing import Any

from homelink.brain.audit import AuditStore
from homelink.core.errors import AppError, ErrorCode
from homelink.gateway.base import GatewayError, IRGateway
from homelink.services.device_management.devices import DeviceStore
from homelink.services.device_management.states import DeviceStateStore

logger = logging.getLogger(__name__)


class ControlService:
    def __init__(
        self,
        devices: DeviceStore,
        gateway: IRGateway,
        audit: AuditStore,
        states: DeviceStateStore,
    ) -> None:
        self._devices = devices
        self._gateway = gateway
        self._audit = audit
        self._states = states

    def execute(self, function_id: str, *, trace_id: str) -> dict[str, Any]:
        function = self._devices.get_function(function_id)  # 功能不存在 → 404
        device_id = function["device_id"]
        raw_code = self._devices.latest_code(function_id)
        if raw_code is None:
            self._audit_best_effort(
                device_id,
                function_id,
                trace_id,
                result="failed",
                reason="该功能还没有可用码",
            )
            raise AppError(ErrorCode.NOT_FOUND, "该功能还没有可用码，请先学码")

        try:
            self._gateway.replay(raw_code)
        except GatewayError as exc:
            self._audit_best_effort(
                device_id,
                function_id,
                trace_id,
                result="failed",
                reason=exc.message,
            )
            raise _as_app_error(exc) from exc

        # 信号已发出：只记「发出过」，状态一律 unknown，不猜测真实开关。
        self._states.append(
            device_id,
            "unknown",
            "unknown",
            reason=f"control:{function_id}",
            trace_id=trace_id,
        )
        self._audit_best_effort(
            device_id,
            function_id,
            trace_id,
            result="success",
            reason=None,
        )
        return {
            "function_id": function_id,
            "device_id": device_id,
            "state": "unknown",
            "message": "信号已发出",
        }

    def _audit_best_effort(
        self,
        device_id: str,
        function_id: str,
        trace_id: str,
        *,
        result: str,
        reason: str | None,
    ) -> None:
        """审计失败只记日志：红外已经出门，不能因为它回滚。"""

        try:
            self._audit.write(
                who="user",
                action=f"{device_id}.control",
                device_id=device_id,
                context={"function_id": function_id},
                result=result,
                reason=reason,
                trace_id=trace_id,
            )
        except Exception:  # 审计落库失败不得影响控制结果
            logger.warning("控制审计写入失败（function_id=%s）", function_id, exc_info=True)


def _as_app_error(exc: GatewayError) -> AppError:
    if exc.kind == "unauthorized":
        return AppError(ErrorCode.DEVICE_UNAUTHORIZED, exc.message)
    return AppError(ErrorCode.DEVICE_OFFLINE, exc.message)
