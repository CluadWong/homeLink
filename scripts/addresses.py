"""登录后显示访问地址：只读配置与健康检查，不启动、不停止服务。

由 `scripts/service/show-addresses.cmd` 在用户登录时调用；服务本身由计划任务
`HomeLinkService` 在开机时后台启动，本脚本只负责把地址打到终端里。

用法（仓库根目录）::

    .\\.venv\\Scripts\\python.exe scripts\\addresses.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Final

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    # 同目录的 start.py 是脚本而非包，直接插入路径复用其配置读取与地址推导。
    sys.path.insert(0, str(SCRIPT_DIR))

import start as launcher  # noqa: E402
import yaml  # noqa: E402

DEFAULT_WAIT_SECONDS: Final = 20.0


def format_addresses(
    *,
    host: str,
    lan_host: str | None,
    api_port: int,
    frontend_enabled: bool,
    dist_ready: bool,
    dist: Path,
    token_configured: bool,
    mode: str,
    ready: bool,
    reason: str,
) -> list[str]:
    """拼出终端展示用的地址行；纯函数，便于测试。"""

    addresses = launcher.derive_addresses(host, lan_host, api_port)
    lines = ["HomeLink已就绪。" if ready else "HomeLink尚未就绪（可能仍在启动）。"]
    if not ready and reason:
        lines.append(f"  原因：{reason}")
    lines.append("")
    lines.append("访问地址：")

    if not frontend_enabled:
        lines.append("  前端静态托管已关闭（frontend.enabled=false）：只提供 /api。")
    elif dist_ready:
        lines.append(f"  前端（同源）：{addresses['local_ui']}")
    else:
        lines.append(f"  前端未构建：缺少 {dist / 'index.html'}")
        lines.append("  构建命令：cd frontend && npm install && npm run build")

    lines.append(f"  健康检查：{addresses['local_health']}")
    if "lan_ui" in addresses:
        if frontend_enabled and dist_ready:
            lines.append(f"  局域网前端：{addresses['lan_ui']}（手机/平板须与电脑同一 Wi-Fi）")
        lines.append(f"  局域网健康：{addresses['lan_health']}")

    lines.append(f"  网关模式：{mode}" + ("（模拟，不连真机）" if mode == "mock" else ""))
    lines.append(f"  鉴权：{'已配置 token' if token_configured else '未设置 token'}")
    lines.append(f"  后端日志：{launcher.BACKEND_LOG}")
    lines.append(f"  服务日志：{launcher.LOG_DIR / 'service.log'}")
    return lines


def main(argv: list[str] | None = None) -> int:
    launcher.ensure_utf8_output()
    parser = argparse.ArgumentParser(description="显示 HomeLink访问地址（不启动服务）")
    parser.add_argument("--host", default=launcher.DEFAULT_HOST)
    parser.add_argument("--api-port", type=int, default=launcher.DEFAULT_API_PORT)
    parser.add_argument("--wait-seconds", type=float, default=DEFAULT_WAIT_SECONDS)
    args = parser.parse_args(argv)

    try:
        settings: dict[str, Any] = launcher.read_effective_settings()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"错误：读取 config/settings.yaml 失败 —— {exc}", flush=True)
        return 2

    token = launcher.api_token(settings)
    mode = launcher.gateway_mode(settings)
    frontend_enabled, dist = launcher.frontend_settings(settings)
    dist_ready = frontend_enabled and launcher.frontend_ready(dist)

    ready, reason = launcher.wait_for_backend(
        args.api_port, token, timeout=max(args.wait_seconds, 0.0)
    )
    for line in format_addresses(
        host=args.host,
        lan_host=launcher.lan_ip(),
        api_port=args.api_port,
        frontend_enabled=frontend_enabled,
        dist_ready=dist_ready,
        dist=dist,
        token_configured=bool(token),
        mode=mode,
        ready=ready,
        reason=reason,
    ):
        print(line, flush=True)

    if not ready:
        print("", flush=True)
        if "鉴权" in reason:
            print("8000 端口上已有服务在跑，但当前配置读到的 token 与之不匹配。", flush=True)
            print(
                "  检查 config/settings.local.yaml 的 api.token；"
                "若端口被旧项目占用，确认旧任务 JJButlerService 已停用。",
                flush=True,
            )
        else:
            print(
                "服务没在运行：开机任务 HomeLinkService 可能还没拉起，或已异常退出。",
                flush=True,
            )
            print("  查看服务日志：logs\\service.log", flush=True)
            print("  手工启动：.\\.venv\\Scripts\\python.exe scripts\\start.py", flush=True)
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
