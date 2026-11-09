"""网关抽象：开关学习模式、读最近波形、回放波形、探测。

硬件差异全部收敛在 `IRGateway`；上层只按错误类型区分「离线」与「鉴权被拒」。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

GatewayFailure = Literal["unreachable", "unauthorized", "bad_response", "timeout"]


class GatewayError(Exception):
    """网关侧失败的统一形态；上层按 kind 映射错误码。"""

    def __init__(self, kind: GatewayFailure, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class GatewayOfflineError(GatewayError):
    """连接失败 / 超时 / 设备侧 5xx：板子没有正常响应。"""

    def __init__(self, message: str) -> None:
        super().__init__("unreachable", message)


class GatewayTimeoutError(GatewayOfflineError):
    """设备能连上但请求超时；旧链路仍按离线处理，Agent 链路单独区分。"""

    def __init__(self, message: str) -> None:
        GatewayError.__init__(self, "timeout", message)


class GatewayUnauthorizedError(GatewayError):
    """板子在线但拒绝凭证（401/403）：不能报成离线。"""

    def __init__(self, message: str) -> None:
        super().__init__("unauthorized", message)


@dataclass(frozen=True)
class ProbeOutcome:
    reachable: bool
    latency_ms: int
    error: str | None
    simulated: bool = False


class IRGateway(ABC):
    mode: str
    base_url: str
    simulated: bool

    @abstractmethod
    def start_learn(self) -> None:
        """打开学码接收态；失败抛 GatewayError。"""

    @abstractmethod
    def stop_learn(self) -> None:
        """关闭学码接收态；幂等。"""

    @abstractmethod
    def read_last_code(self) -> str | None:
        """读最近一次波形原文；None 表示空串/无信号。"""

    @abstractmethod
    def replay(self, raw_code: Sequence[int]) -> None:
        """回放一段学码波形；固定走 `ir_proxy_tx`，载波 38000 Hz。"""

    @abstractmethod
    def probe(self) -> ProbeOutcome:
        """只读探测，不产生副作用。"""
