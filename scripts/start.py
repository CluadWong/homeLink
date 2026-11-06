"""一键启动：单进程 uvicorn，前端由 FastAPI 同源托管 `frontend/dist`。

从 `config/settings.yaml` 与 `config/settings.local.yaml` 读出 `api.token` 与
`frontend` 开关，自动注入子进程环境变量。脚本**只读配置**，不修改 settings 文件，
也不动 `data/` 下已有的库文件。日志写 `logs/backend.log`，Ctrl+C 会终止后端且不留孤儿进程。

用法（仓库根目录）::

    .\\.venv\\Scripts\\python.exe scripts\\start.py

前端构建：``cd frontend && npm install && npm run build``；生产不使用 Vite dev server。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO, Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"
LOG_DIR = REPO_ROOT / "logs"
BACKEND_LOG = LOG_DIR / "backend.log"

DEFAULT_HOST = "0.0.0.0"
DEFAULT_API_PORT = 8000
BACKEND_READY_TIMEOUT = 30.0


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 的顶层必须是对象")
    return {str(key): value for key, value in data.items()}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_effective_settings(config_dir: str | Path = CONFIG_DIR) -> dict[str, Any]:
    """与后端一致的读法：`settings.local.yaml` 覆盖 `settings.yaml`。"""

    directory = Path(config_dir)
    base = _read_yaml(directory / "settings.yaml")
    overlay = _read_yaml(directory / "settings.local.yaml")
    return _deep_merge(base, overlay)


def api_token(settings: dict[str, Any]) -> str:
    api = settings.get("api")
    if not isinstance(api, dict):
        return ""
    return str(api.get("token") or "").strip()


def gateway_mode(settings: dict[str, Any]) -> str:
    gateway = settings.get("gateway")
    if not isinstance(gateway, dict):
        return "mock"
    return str(gateway.get("mode") or "mock")


def frontend_settings(
    settings: dict[str, Any], repo_root: str | Path = REPO_ROOT
) -> tuple[bool, Path]:
    """返回（静态托管是否开启, dist 绝对路径）；默认开启，路径相对仓库根。"""

    block = settings.get("frontend")
    if not isinstance(block, dict):
        block = {}
    enabled = bool(block.get("enabled", True))
    raw = str(block.get("dist_path") or "frontend/dist").strip() or "frontend/dist"
    dist = Path(raw)
    if not dist.is_absolute():
        dist = Path(repo_root) / dist
    return enabled, dist


def frontend_ready(dist: Path) -> bool:
    return (dist / "index.html").is_file()


def frontend_hint(dist: Path) -> str:
    return (
        f"提示：前端未构建（缺少 {dist / 'index.html'}）。\n"
        "      先构建前端：cd frontend && npm install && npm run build\n"
        "      构建完成后重新运行本脚本，即可在同一端口打开页面（生产不使用 Vite dev server）。"
    )


def build_child_env(
    base: dict[str, str],
    *,
    token: str,
    agent_url: str,
    config_dir: str | Path,
    src_path: str | Path = REPO_ROOT / "src",
) -> dict[str, str]:
    """向后端注入 token、配置目录与地址；不修改调用方传入的环境。

    把 `src/` 放进 `PYTHONPATH`：即使当前解释器的可编辑安装不完整，子进程也能从
    仓库源码导入 `jj_butler`（启动脚本的硬要求是工作目录为仓库根）。
    """

    env = dict(base)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(src_path) if not existing else f"{src_path}{os.pathsep}{existing}"
    env["JJ_API_TOKEN"] = token
    env["JJ_AGENT_URL"] = agent_url
    env["JJ_BUTLER_CONFIG_DIR"] = str(config_dir)
    return env


def derive_addresses(host: str, lan_host: str | None, api_port: int) -> dict[str, str]:
    """推导本机与局域网访问地址；`0.0.0.0` 对外展示时换成本机回环地址。"""

    local_host = "127.0.0.1" if host in ("", "0.0.0.0") else host
    addresses = {
        "local_ui": f"http://{local_host}:{api_port}/",
        "local_health": f"http://{local_host}:{api_port}/api/health",
    }
    if lan_host:
        addresses["lan_ui"] = f"http://{lan_host}:{api_port}/"
        addresses["lan_health"] = f"http://{lan_host}:{api_port}/api/health"
    return addresses


def lan_ip() -> str | None:
    """拿本机在局域网里的 IPv4；拿不到返回 None（不编造地址）。"""

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        address = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return None if address.startswith("127.") else str(address)


def port_in_use(host: str, port: int, *, timeout: float = 0.3) -> bool:
    """端口是否已被监听（只探测，不杀进程）。"""

    target = "127.0.0.1" if host in ("", "0.0.0.0") else host
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_backend(
    api_port: int,
    token: str,
    *,
    timeout: float = BACKEND_READY_TIMEOUT,
) -> tuple[bool, str]:
    """轮询 `/api/health` 直到就绪；返回（是否就绪, 最近一次原因）。"""

    deadline = time.monotonic() + timeout
    reason = "还没有收到健康检查响应"
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{api_port}/api/health",
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )
            with urllib.request.urlopen(request, timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if isinstance(payload, dict) and payload.get("success") is not False:
                    return True, ""
                reason = "健康检查返回失败"
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return False, "后端要求鉴权，但启动脚本注入的 token 不匹配"
            reason = f"健康检查 HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = f"后端暂时连不上：{exc}"
        except ValueError:
            reason = "健康检查返回的不是有效 JSON"
        time.sleep(0.4)
    return False, reason


def ensure_utf8_output() -> None:
    """Windows 控制台 / 重定向时的 UTF-8 兜底，避免中文乱码。"""

    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            # 某些包装过的 stdout 不支持 reconfigure，退回默认行为即可。
            continue


def _say(message: str) -> None:
    print(message, flush=True)


def _spawn(command: list[str], *, env: dict[str, str], log_file: IO[bytes]) -> subprocess.Popen[bytes]:
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )


def _stop(process: subprocess.Popen[bytes], name: str) -> None:
    if process.poll() is not None:
        return
    _say(f"正在停止{name}…")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _print_addresses(host: str, api_port: int, token: str, *, dist_ready: bool, dist: Path) -> None:
    addresses = derive_addresses(host, lan_ip(), api_port)
    _say("")
    _say("访问地址：")
    if dist_ready:
        _say(f"  前端（同源）：{addresses['local_ui']}")
        _say(f"  健康检查：{addresses['local_health']}")
        if "lan_ui" in addresses:
            _say(f"  局域网前端：{addresses['lan_ui']}（手机/平板须与电脑同一 Wi-Fi）")
            _say(f"  局域网健康：{addresses['lan_health']}")
    else:
        _say(f"  纯 API：{addresses['local_health']}")
        if "lan_health" in addresses:
            _say(f"  局域网健康：{addresses['lan_health']}")
        _say(frontend_hint(dist))
    _say(f"  鉴权：{'已注入 token（不显示内容）' if token else '后端未设置 token'}")
    _say(f"  后端日志：{BACKEND_LOG}")
    _say("按 Ctrl+C 停止后端。")


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_output()
    parser = argparse.ArgumentParser(description="启动 JJ 管家后端（前端同源托管）")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 0.0.0.0（允许局域网）")
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=BACKEND_READY_TIMEOUT,
        help="等待后端就绪的秒数",
    )
    args = parser.parse_args(argv)

    config_dir = Path(os.environ.get("JJ_BUTLER_CONFIG_DIR", CONFIG_DIR))
    try:
        settings = read_effective_settings(config_dir)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        _say(f"错误：读取 config/settings.yaml 失败 —— {exc}")
        return 2

    token = api_token(settings)
    mode = gateway_mode(settings)
    frontend_enabled, dist = frontend_settings(settings)
    dist_ready = frontend_enabled and frontend_ready(dist)

    if port_in_use(args.host, args.api_port):
        _say(f"错误：端口 {args.api_port} 已被占用。可能已有一个实例在运行，请先关掉再启动。")
        return 2

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    agent_url = f"http://127.0.0.1:{args.api_port}"
    env = build_child_env(
        dict(os.environ),
        token=token,
        agent_url=agent_url,
        config_dir=config_dir,
    )
    env.setdefault("PYTHONUTF8", "1")

    _say(f"配置：{config_dir}")
    _say(f"网关模式：{mode}" + ("（模拟，不连真机）" if mode == "mock" else ""))
    if not frontend_enabled:
        _say("前端静态托管已关闭（frontend.enabled=false）：只提供 /api。")
    elif not dist_ready:
        _say(frontend_hint(dist))
    _say(f"启动后端：uvicorn jj_butler.interface.api.app:app --host {args.host} --port {args.api_port}")
    _say(f"后端日志：{BACKEND_LOG}")

    log_file = BACKEND_LOG.open("ab")
    backend = _spawn(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "jj_butler.interface.api.app:app",
            "--host",
            args.host,
            "--port",
            str(args.api_port),
        ],
        env=env,
        log_file=log_file,
    )

    try:
        ready, reason = wait_for_backend(args.api_port, token, timeout=args.wait_seconds)
        if not ready:
            code = backend.poll()
            if code is not None:
                reason = f"后端进程已退出（退出码 {code}），日志见 {BACKEND_LOG}"
            _say(f"错误：后端在 {args.wait_seconds:.0f} 秒内没有就绪 —— {reason}")
            return 1

        _say("后端已就绪。")
        _print_addresses(args.host, args.api_port, token, dist_ready=dist_ready, dist=dist)
        return _monitor(backend)
    except KeyboardInterrupt:
        _say("")
        _say("收到 Ctrl+C，正在收尾…")
        return 0
    finally:
        _stop(backend, "后端")
        log_file.close()
        _say("已停止。")


def _monitor(backend: subprocess.Popen[bytes]) -> int:
    while True:
        code = backend.poll()
        if code is not None:
            _say(f"后端已退出（退出码 {code}），日志见 {BACKEND_LOG}")
            return int(code)
        time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
