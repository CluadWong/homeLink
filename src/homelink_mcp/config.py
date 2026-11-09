"""MCP 服务的运行配置：只读环境变量，不读服务端 settings 文件。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HOMELINK_BASE_URL` | `http://127.0.0.1:8000` | HomeLink 服务地址，不含 `/api` |
| `HOMELINK_API_TOKEN` | 空 | 与服务端 `api.token` 一致；空 = 不鉴权 |
| `HOMELINK_TIMEOUT` | `10` | 单次 HTTP 超时（秒） |
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class Settings:
    base_url: str
    token: str
    timeout: float


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
    return Settings(base_url=base_url, token=token, timeout=timeout)
