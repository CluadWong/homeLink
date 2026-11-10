"""Agent 配置：只读环境变量，默认值面向本机部署。"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:9b-q4_K_M"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MAX_STEPS = 6
DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True)
class AgentSettings:
    ollama_url: str
    model: str
    homelink_base_url: str
    homelink_token: str
    max_steps: int
    timeout: float


def _positive_int(raw: str, fallback: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        return fallback
    return value if value > 0 else fallback


def _positive_float(raw: str, fallback: float) -> float:
    try:
        value = float(raw)
    except ValueError:
        return fallback
    return value if value > 0 else fallback


def load_settings() -> AgentSettings:
    return AgentSettings(
        ollama_url=(os.environ.get("OLLAMA_URL") or DEFAULT_OLLAMA_URL).strip().rstrip("/"),
        model=(os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL).strip(),
        homelink_base_url=(os.environ.get("HOMELINK_BASE_URL") or DEFAULT_BASE_URL)
        .strip()
        .rstrip("/"),
        homelink_token=(os.environ.get("HOMELINK_API_TOKEN") or "").strip(),
        max_steps=_positive_int(os.environ.get("AGENT_MAX_STEPS", ""), DEFAULT_MAX_STEPS),
        timeout=_positive_float(os.environ.get("AGENT_TIMEOUT", ""), DEFAULT_TIMEOUT),
    )
