"""MCP 服务的运行配置：只读环境变量，不读服务端 settings 文件。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HOMELINK_BASE_URL` | `http://127.0.0.1:8000` | HomeLink 服务地址，不含 `/api` |
| `HOMELINK_API_TOKEN` | 空 | 与服务端 `api.token` 一致；空 = 不鉴权 |
| `HOMELINK_TIMEOUT` | `10` | 单次 HTTP 超时（秒） |
| `HOMELINK_ACTOR` | `butler-agent` | 写入审计的调用方标识（`X-Actor`），超长按 64 字符截断 |
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 10.0
DEFAULT_ACTOR = "butler-agent"
_ACTOR_MAX_LENGTH = 64  # 与后端 X-Actor 约束一致


@dataclass(frozen=True)
class Settings:
    base_url: str
    token: str
    timeout: float
    actor: str = DEFAULT_ACTOR


def _timeout(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def load_settings() -> Settings:
    base_url = (os.environ.get("HOMELINK_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    token = (os.environ.get("HOMELINK_API_TOKEN") or "").strip()
    timeout = _timeout((os.environ.get("HOMELINK_TIMEOUT") or "").strip())
    actor = (os.environ.get("HOMELINK_ACTOR") or DEFAULT_ACTOR).strip()[:_ACTOR_MAX_LENGTH]
    return Settings(base_url=base_url, token=token, timeout=timeout, actor=actor)
