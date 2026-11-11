"""D3 评测沙箱：临时 mock 后端 + 本地 Ollama 跑 130 条。**不发任何红外**。

用法（仓库根）：

    python scripts/eval_sandbox.py                 # 全量 130 条，写 docs/eval-report.md
    python scripts/eval_sandbox.py --limit 5       # 冒烟
    python scripts/eval_sandbox.py --report docs/eval-report-local.md

沙箱会自建临时配置（mock 网关）与临时数据库，按评测集假设预置「卧室风扇」6 个按键与
「卧室空调」2 个按键；结束后自动停掉临时后端。
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "scripts" / "eval_agent.py"

_SETTINGS = {
    "gateway": {
        "mode": "mock",
        "ir": {"base_url": "", "username": "", "password": ""},
        "probe_timeout_seconds": 3,
    },
    "state": {"retention_days": 90},
    "audit": {"retention_days": 365},
    "api": {"token": ""},
    "frontend": {"enabled": False, "dist_path": "frontend/dist"},
}
_DEVICES = {
    "rooms": [
        {"name": "bedroom", "display_name": "卧室"},
        {"name": "living_room", "display_name": "客厅"},
    ]
}
_SEED = {
    "卧室风扇": ["开关", "+", "-", "模式", "定时", "摇头"],
    "卧室空调": ["开关", "模式"],
}
_WAVEFORM = [900, 450, 900, 450]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_config(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(_SETTINGS, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (config_dir / "devices.yaml").write_text(
        yaml.safe_dump(_DEVICES, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return config_dir


def _wait_health(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/api/health", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise SystemExit(f"临时后端未在 {timeout} 秒内就绪：{base_url}")


def _seed_devices(base_url: str) -> None:
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        for name, functions in _SEED.items():
            device = client.post("/api/devices", json={"name": name, "room": "bedroom"})
            device.raise_for_status()
            device_id = device.json()["id"]
            for function in functions:
                started = client.post("/api/learn/start", json={"device_id": device_id})
                started.raise_for_status()
                saved = client.post(
                    "/api/learn/save",
                    json={"device_id": device_id, "name": function, "raw_code": _WAVEFORM},
                )
                saved.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description="HomeLink 评测沙箱（mock 后端，不发红外）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟用）")
    parser.add_argument("--report", default="docs/eval-report.md", help="报告输出路径")
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="homelink-eval-"))
    config_dir = _write_config(root)
    port = _free_port()
    env = {
        **os.environ,
        "HOMELINK_CONFIG_DIR": str(config_dir),
        "HOMELINK_DB_PATH": str(root / "eval.db"),
    }
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "homelink.interface.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_health(base_url)
        _seed_devices(base_url)
        command = [
            sys.executable,
            str(EVAL),
            "--provider",
            "ollama",
            "--base-url",
            base_url,
            "--report",
            args.report,
        ]
        if args.limit:
            command.extend(["--limit", str(args.limit)])
        print(f"评测后端：{base_url}（mock）→ 报告 {args.report}", flush=True)
        return subprocess.call(command, cwd=str(ROOT))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
