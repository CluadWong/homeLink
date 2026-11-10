"""MCP 结构化日志：只写 stderr 的 JSONL，stdout 保持纯 JSON-RPC。

字段：`ts / level / event / tool / trace_id / duration_ms / ok / error_code`。
不记录 token、完整设备列表或用户原话。
`HOMELINK_LOG_LEVEL` 控制门槛（DEBUG/INFO/WARNING/ERROR，默认 INFO，非法值回落 INFO）。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
_DEFAULT_LEVEL = "INFO"


def _threshold() -> int:
    raw = (os.environ.get("HOMELINK_LOG_LEVEL") or "").strip().upper()
    return _LEVELS.get(raw, _LEVELS[_DEFAULT_LEVEL])


def emit(event: str, *, level: str = "INFO", **fields: Any) -> None:
    """写一行 JSONL 到 stderr；低于门槛的级别直接丢弃。"""
    if _LEVELS.get(level, _LEVELS[_DEFAULT_LEVEL]) < _threshold():
        return
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "level": level,
        "event": event,
        **fields,
    }
    sys.stderr.write(json.dumps(record, ensure_ascii=False) + "\n")
    sys.stderr.flush()
