"""启动脚本纯函数：token 覆盖读取、环境注入、地址推导、端口占用。"""

from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "start.py"


def _load_start() -> Any:
    spec = importlib.util.spec_from_file_location("jj_start_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def start() -> Any:
    return _load_start()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_local_settings_override_token(start: Any, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write(
        config_dir / "settings.yaml", {"api": {"token": "base-token"}, "gateway": {"mode": "mock"}}
    )
    _write(config_dir / "settings.local.yaml", {"api": {"token": "local-token"}})

    settings = start.read_effective_settings(config_dir)
    assert start.api_token(settings) == "local-token"
    assert start.gateway_mode(settings) == "mock"


def test_missing_local_file_uses_base_token(start: Any, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write(config_dir / "settings.yaml", {"api": {"token": "base-token"}})

    assert start.api_token(start.read_effective_settings(config_dir)) == "base-token"


def test_empty_config_has_no_token(start: Any, tmp_path: Path) -> None:
    settings = start.read_effective_settings(tmp_path / "missing")
    assert settings == {}
    assert start.api_token(settings) == ""


def test_build_child_env_injects_without_mutating_base(start: Any, tmp_path: Path) -> None:
    base = {"PATH": "C:/Windows", "JJ_API_TOKEN": "stale"}
    env = start.build_child_env(
        base,
        token="fresh-token",
        agent_url="http://127.0.0.1:8000",
        config_dir=tmp_path / "config",
        src_path=tmp_path / "src",
    )
    assert env["JJ_API_TOKEN"] == "fresh-token"
    assert env["JJ_AGENT_URL"] == "http://127.0.0.1:8000"
    assert env["JJ_BUTLER_CONFIG_DIR"] == str(tmp_path / "config")
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(tmp_path / "src")
    assert base["JJ_API_TOKEN"] == "stale"
    assert "PYTHONPATH" not in base


def test_derive_addresses_for_wildcard_host(start: Any) -> None:
    addresses = start.derive_addresses("0.0.0.0", "192.168.1.20", 8000)
    assert addresses["local_ui"] == "http://127.0.0.1:8000/"
    assert addresses["local_health"] == "http://127.0.0.1:8000/api/health"
    assert addresses["lan_ui"] == "http://192.168.1.20:8000/"
    assert addresses["lan_health"] == "http://192.168.1.20:8000/api/health"


def test_derive_addresses_without_lan_ip(start: Any) -> None:
    addresses = start.derive_addresses("127.0.0.1", None, 8000)
    assert addresses["local_ui"] == "http://127.0.0.1:8000/"
    assert "lan_ui" not in addresses


def test_frontend_settings_defaults_and_relative_path(start: Any, tmp_path: Path) -> None:
    enabled, dist = start.frontend_settings({}, repo_root=tmp_path)
    assert enabled is True
    assert dist == tmp_path / "frontend" / "dist"


def test_frontend_settings_switch_off_and_absolute_path(start: Any, tmp_path: Path) -> None:
    absolute = tmp_path / "build"
    enabled, dist = start.frontend_settings(
        {"frontend": {"enabled": False, "dist_path": str(absolute)}}, repo_root=tmp_path
    )
    assert enabled is False
    assert dist == absolute


def test_frontend_ready_and_hint(start: Any, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    assert start.frontend_ready(dist) is False
    dist.mkdir()
    (dist / "index.html").write_text("ok", encoding="utf-8")
    assert start.frontend_ready(dist) is True

    hint = start.frontend_hint(dist)
    assert "npm run build" in hint
    assert "Vite dev server" in hint


def test_stop_terminates_child_process(start: Any) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    started = time.monotonic()
    start._stop(child, "测试子进程")
    assert child.poll() is not None
    assert time.monotonic() - started < 10


def test_port_in_use_detects_listener(start: Any) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = int(sock.getsockname()[1])
    try:
        assert start.port_in_use("127.0.0.1", port) is True
    finally:
        sock.close()
    assert start.port_in_use("127.0.0.1", port) is False


def test_port_in_use_treats_wildcard_as_loopback(start: Any) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = int(sock.getsockname()[1])
    try:
        assert start.port_in_use("0.0.0.0", port) is True
    finally:
        sock.close()
