"""探测缓存：默认 15 秒，refresh=True 绕过。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from jj_butler.core.timeutil import utc_now_iso
from jj_butler.gateway.base import IRGateway


class GatewayProbe:
    def __init__(
        self,
        gateway: IRGateway,
        *,
        ttl_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gateway = gateway
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._last_success_at: str | None = None

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            if not refresh and self._cached is not None and now - self._cached_at < self._ttl:
                return dict(self._cached)

            started = self._clock()
            outcome = self._gateway.probe()
            elapsed_ms = int((self._clock() - started) * 1000)

            checked_at = utc_now_iso()
            if outcome.reachable:
                self._last_success_at = checked_at
            self._cached = {
                "mode": self._gateway.mode,
                "base_url": self._gateway.base_url,
                "reachable": outcome.reachable,
                "checked_at": checked_at,
                "latency_ms": elapsed_ms,
                "error": outcome.error,
                "last_success_at": self._last_success_at,
                "simulated": self._gateway.simulated,
            }
            self._cached_at = self._clock()
            return dict(self._cached)
