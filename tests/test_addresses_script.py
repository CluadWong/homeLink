"""登录后展示地址脚本：纯函数输出与就绪 / 未就绪文案。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "addresses.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("jj_addresses_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def addresses() -> Any:
    return _load()


def _lines(addresses: Any, **overrides: Any) -> list[str]:
    params: dict[str, Any] = {
        "host": "0.0.0.0",
        "lan_host": "192.168.1.20",
        "api_port": 8000,
        "frontend_enabled": True,
        "dist_ready": True,
        "dist": Path("frontend/dist"),
        "token_configured": True,
        "mode": "real",
        "ready": True,
        "reason": "",
    }
    params.update(overrides)
    return list(addresses.format_addresses(**params))


def test_ready_prints_local_and_lan_addresses(addresses: Any) -> None:
    text = "\n".join(_lines(addresses))
    assert "HomeLink 已就绪" in text
    assert "前端（同源）：http://127.0.0.1:8000/" in text
    assert "http://192.168.1.20:8000/" in text
    assert "已配置 token" in text
    assert "未设置 token" not in text


def test_not_ready_keeps_reason_and_addresses(addresses: Any) -> None:
    text = "\n".join(_lines(addresses, ready=False, reason="后端暂时连不上：拒绝连接"))
    assert "尚未就绪" in text
    assert "后端暂时连不上" in text
    assert "http://127.0.0.1:8000/api/health" in text


def test_frontend_missing_shows_build_hint_without_ui_url(addresses: Any) -> None:
    text = "\n".join(_lines(addresses, dist_ready=False))
    assert "前端未构建" in text
    assert "npm run build" in text
    assert "前端（同源）" not in text


def test_frontend_disabled_marks_api_only(addresses: Any) -> None:
    text = "\n".join(_lines(addresses, frontend_enabled=False, dist_ready=False))
    assert "只提供 /api" in text
    assert "npm run build" not in text
