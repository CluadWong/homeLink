"""命令行入口：一次性提问或交互式对话。"""

from __future__ import annotations

import asyncio
import sys

from homelink_agent.agent import HomeAgent
from homelink_agent.config import load_settings
from homelink_agent.llm import OllamaError


async def _run(prompt: str) -> None:
    settings = load_settings()
    agent = HomeAgent(settings)

    if prompt:
        print(await agent.run(prompt))
        return

    print(f"HomeLink 管家（{settings.model}）；直接回车退出。")
    history: list[dict[str, str]] = []
    while True:
        try:
            text = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            break
        try:
            reply = await agent.run(text, history)
        except OllamaError as exc:
            print(f"管家> 出错了：{exc}")
            continue
        print(f"管家> {reply}")
        history.extend(
            [
                {"role": "user", "content": text},
                {"role": "assistant", "content": reply},
            ]
        )


def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip()
    try:
        asyncio.run(_run(prompt))
    except KeyboardInterrupt:
        pass
    except (OllamaError, RuntimeError) as exc:
        print(f"启动失败：{exc}")
        raise SystemExit(1) from exc
