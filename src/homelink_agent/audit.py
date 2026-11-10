"""Agent 审计日志：把每轮对话、每次工具调用和确认裁决追加到 JSONL。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_PATH = "logs/agent-audit.jsonl"
_DISABLED_VALUES = {"", "off", "none", "false"}


class AuditLog:
    def __init__(self, path: str) -> None:
        cleaned = path.strip()
        self._path = None if cleaned.lower() in _DISABLED_VALUES else Path(cleaned)

    @property
    def enabled(self) -> bool:
        return self._path is not None

    def write(self, event: str, **fields: Any) -> None:
        """写失败不影响主流程：审计不能成为控制链路的新故障点。"""

        if self._path is None:
            return
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
