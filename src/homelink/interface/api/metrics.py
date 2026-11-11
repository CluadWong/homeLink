"""进程内请求指标（D4）：只记次数、状态码与耗时，不记请求内容。

输出给 `GET /api/metrics`，不引入 Prometheus；进程重启即清零。
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any

_STATUS_GROUPS = ("2xx", "3xx", "4xx", "5xx")


def _status_group(status_code: int) -> str:
    group = f"{status_code // 100}xx"
    return group if group in _STATUS_GROUPS else "5xx"


class RequestMetrics:
    """线程安全计数；FastAPI 的同步端点跑在线程池里，必须加锁。"""

    def __init__(
        self,
        *,
        max_latency_samples: int = 1000,
        clock: Callable[[], float] = time.perf_counter,
        started_at: float | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._started_at = clock() if started_at is None else started_at
        self._max_samples = max_latency_samples
        self.requests_total = 0
        self.errors_total = 0
        self.status_counts: dict[str, int] = dict.fromkeys(_STATUS_GROUPS, 0)
        self._latencies_ms: list[float] = []

    def record(self, *, status_code: int, latency_ms: float) -> None:
        """错误按 4xx + 5xx 统计，便于看出鉴权/参数错误与真实故障。"""

        with self._lock:
            self.requests_total += 1
            if status_code >= 400:
                self.errors_total += 1
            self.status_counts[_status_group(status_code)] += 1
            self._latencies_ms.append(round(latency_ms, 1))
            if len(self._latencies_ms) > self._max_samples:
                del self._latencies_ms[: len(self._latencies_ms) - self._max_samples]

    def percentile(self, pct: float) -> float | None:
        """最近秩百分位；无样本返回 None。"""

        with self._lock:
            samples = list(self._latencies_ms)
        if not samples:
            return None
        ordered = sorted(samples)
        rank = max(1, math.ceil(pct / 100 * len(ordered)))
        return ordered[min(rank, len(ordered)) - 1]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests_total = self.requests_total
            errors_total = self.errors_total
            status_counts = dict(self.status_counts)
            samples = len(self._latencies_ms)
        return {
            "uptime_seconds": round(self._clock() - self._started_at, 1),
            "requests": {
                "total": requests_total,
                "errors": errors_total,
                "error_rate": round(errors_total / requests_total, 4) if requests_total else 0.0,
                "by_status": status_counts,
            },
            "latency_ms": {
                "p50": self.percentile(50),
                "p95": self.percentile(95),
                "samples": samples,
            },
        }
