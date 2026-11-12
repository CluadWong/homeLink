"""轻量告警：统一 `[ALERT]` 日志行 + JSONL 文件，供任务计划日志与后续看板消费。

为什么这样做：
- 不加依赖、不加通知通道，常驻任务的 stdout/stderr 落到 `logs\\agent.log` 后，
  直接 grep `[ALERT]` 就能发现「调度连续失败 / Ollama 不可用 / MCP 掉线」等；
- 同时追加 `logs/alerts.jsonl`（可用 `AGENT_ALERT_PATH` 覆盖），结构化字段留给
  以后接本地通知或告警看板；
- 同一 `kind + key` 在冷却期内只记一次，避免每分钟一跑的任务刷屏。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

COOLDOWN_SECONDS = 600.0

# 统一签名：种类、人话、去重键、附加细节；实现方不返回值也不抛错。
AlertFn = Callable[[str, str, str, str | None], None]

_lock = threading.Lock()
_last: dict[tuple[str, str], float] = {}


def alert_path() -> Path:
    raw = os.environ.get("AGENT_ALERT_PATH", "").strip()
    return Path(raw) if raw else Path("logs") / "alerts.jsonl"


def emit(
    kind: str,
    message: str,
    *,
    key: str = "",
    detail: str | None = None,
    now: float | None = None,
    cooldown: float = COOLDOWN_SECONDS,
) -> bool:
    """记录一条告警；冷却期内重复（同 kind+key）返回 False。写文件失败不影响调用方。"""

    moment = time.time() if now is None else now
    dedupe = (kind, key or message)
    with _lock:
        previous = _last.get(dedupe)
        if previous is not None and moment - previous < cooldown:
            return False
        _last[dedupe] = moment

    record: dict[str, Any] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(moment)),
        "kind": kind,
        "key": key,
        "message": message,
    }
    if detail:
        record["detail"] = detail
    print(f"[ALERT] {json.dumps(record, ensure_ascii=False)}", file=sys.stderr, flush=True)
    try:
        path = alert_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:  # 告警自身失败只降级到 stderr，不能打断业务
        print(f"[ALERT] 告警文件写入失败：{exc}", file=sys.stderr, flush=True)
    return True


def reset() -> None:
    """清空去重状态（测试用）。"""

    with _lock:
        _last.clear()
