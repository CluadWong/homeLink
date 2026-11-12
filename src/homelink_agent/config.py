"""Agent 配置：只读环境变量，默认值面向本机部署。"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:9b-q4_K_M"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MAX_STEPS = 6
DEFAULT_TIMEOUT = 120.0
DEFAULT_AUDIT_PATH = "logs/agent-audit.jsonl"
DEFAULT_KEEP_ALIVE = "30m"
DEFAULT_DAEMON_HOST = "127.0.0.1"
DEFAULT_DAEMON_PORT = 8100
DEFAULT_SESSION_TTL_MIN = 120
DEFAULT_MAX_SESSIONS = 20
DEFAULT_QUEUE_TIMEOUT = 30.0
DEFAULT_CONFIRM_TTL = 120.0
DEFAULT_AGENT_DB_PATH = "data/agent.db"
DEFAULT_NUM_PREDICT = 0  # 0 = 不限制，避免思考过程被截断


@dataclass(frozen=True)
class AgentSettings:
    ollama_url: str
    model: str
    homelink_base_url: str
    homelink_token: str
    max_steps: int
    timeout: float
    audit_path: str
    keep_alive: str
    daemon_host: str
    daemon_port: int
    session_ttl_min: int
    max_sessions: int
    daemon_token: str = ""
    queue_timeout: float = DEFAULT_QUEUE_TIMEOUT
    confirm_ttl: float = DEFAULT_CONFIRM_TTL
    agent_db_path: str = DEFAULT_AGENT_DB_PATH
    # 简单指令由快路径处理（0 次推理）；回落到模型的都是复杂指令，默认保留思考过程。
    think: bool = True
    num_predict: int = DEFAULT_NUM_PREDICT


def _env_flag(raw: str, fallback: bool) -> bool:
    value = raw.strip().lower()
    if not value:
        return fallback
    return value in {"1", "true", "yes", "on"}


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
        audit_path=(os.environ.get("AGENT_AUDIT_PATH") or DEFAULT_AUDIT_PATH).strip(),
        keep_alive=(os.environ.get("AGENT_KEEP_ALIVE") or DEFAULT_KEEP_ALIVE).strip(),
        daemon_host=(os.environ.get("AGENTD_HOST") or DEFAULT_DAEMON_HOST).strip(),
        daemon_port=_positive_int(os.environ.get("AGENTD_PORT", ""), DEFAULT_DAEMON_PORT),
        session_ttl_min=_positive_int(
            os.environ.get("AGENTD_SESSION_TTL", ""), DEFAULT_SESSION_TTL_MIN
        ),
        max_sessions=_positive_int(os.environ.get("AGENTD_MAX_SESSIONS", ""), DEFAULT_MAX_SESSIONS),
        daemon_token=(os.environ.get("AGENTD_TOKEN") or "").strip(),
        queue_timeout=_positive_float(
            os.environ.get("AGENTD_QUEUE_TIMEOUT", ""), DEFAULT_QUEUE_TIMEOUT
        ),
        confirm_ttl=_positive_float(os.environ.get("AGENT_CONFIRM_TTL", ""), DEFAULT_CONFIRM_TTL),
        agent_db_path=(os.environ.get("AGENTD_DB_PATH") or DEFAULT_AGENT_DB_PATH).strip(),
        think=_env_flag(os.environ.get("AGENT_THINK", ""), True),
        num_predict=_positive_int(os.environ.get("AGENT_NUM_PREDICT", ""), DEFAULT_NUM_PREDICT),
    )
