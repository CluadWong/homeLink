"""时间工具：全库统一 ISO 8601 UTC（秒级，带 Z）。"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
