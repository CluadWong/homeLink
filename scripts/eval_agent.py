"""Agent 工具选择评测：准确率、误发控制、澄清率、话术违规与延迟。

用法：
    # 冒烟（本地 Ollama，仅验证链路，不作为评测结论）
    python scripts/eval_agent.py --limit 5

    # 正式评测：云端 OpenAI 兼容模型
    set EVAL_CLOUD_BASE_URL=https://api.example.com/v1
    set EVAL_CLOUD_API_KEY=sk-xxx
    python scripts/eval_agent.py --provider cloud --model qwen-plus --report docs/eval-report.md

    # 只跑某一类
    python scripts/eval_agent.py --provider cloud --model qwen-plus --category risky

判定规则与指标聚合在 homelink_agent.evaluation，本文件只负责驱动会话与落报告。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from mcp.client.stdio import stdio_client

from homelink_agent.agent import ChatFn, HomeAgent
from homelink_agent.config import AgentSettings, load_settings
from homelink_agent.evaluation import Call, Case, Outcome, render_report, summarize
from homelink_agent.llm import to_ollama_tool
from mcp import ClientSession, StdioServerParameters

DATASET = Path(__file__).with_name("agent_eval_set.json")


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 Agent 内部（Ollama 风格）消息翻译成 OpenAI 兼容格式。

    差异只有两处：assistant.tool_calls 的 arguments 要序列化成字符串；
    工具结果要用 role=tool + tool_call_id 对应回上一次 assistant 的调用。
    """

    converted: list[dict[str, Any]] = []
    pending_ids: list[str] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "tool":
            call_id = pending_ids.pop(0) if pending_ids else f"call_{len(converted)}"
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(message.get("content") or ""),
                }
            )
            continue
        if role != "assistant":
            converted.append({"role": role, "content": str(message.get("content") or "")})
            continue
        item: dict[str, Any] = {"role": "assistant", "content": str(message.get("content") or "")}
        calls = message.get("tool_calls") or []
        if calls:
            normalized: list[dict[str, Any]] = []
            for index, call in enumerate(calls):
                function = call.get("function") or {}
                arguments = function.get("arguments")
                normalized.append(
                    {
                        "id": str(call.get("id") or f"call_{len(converted)}_{index}"),
                        "type": "function",
                        "function": {
                            "name": str(function.get("name") or ""),
                            "arguments": (
                                arguments
                                if isinstance(arguments, str)
                                else json.dumps(arguments or {}, ensure_ascii=False)
                            ),
                        },
                    }
                )
            pending_ids.extend(str(call["id"]) for call in normalized)
            item["tool_calls"] = normalized
        converted.append(item)
    return converted


def _cloud_chat_factory(base_url: str, api_key: str, model: str) -> ChatFn:
    async def cloud_chat(
        _settings: AgentSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": _to_openai_messages(messages),
            "temperature": 0,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            data = response.json()
        message: dict[str, Any] = data["choices"][0]["message"]
        return {
            "content": str(message.get("content") or ""),
            "tool_calls": message.get("tool_calls") or [],
        }

    return cloud_chat


async def _preflight(settings: AgentSettings) -> dict[str, Any]:
    """确认后端可达并记录设备清单，让报告能对应到当时的家庭配置。"""

    headers = {"X-Actor": "butler-agent"}
    if settings.homelink_token:
        headers["Authorization"] = f"Bearer {settings.homelink_token}"
    async with httpx.AsyncClient(base_url=settings.homelink_base_url, timeout=10.0) as client:
        health = (await client.get("/api/health", headers=headers)).json()
        devices = (await client.get("/api/devices", headers=headers)).json()
    return {"health": health, "devices": devices}


def _server_params(settings: AgentSettings) -> StdioServerParameters:
    env = dict(os.environ)
    env["HOMELINK_BASE_URL"] = settings.homelink_base_url
    if settings.homelink_token:
        env["HOMELINK_API_TOKEN"] = settings.homelink_token
    env.setdefault("HOMELINK_ACTOR", "butler-agent")
    return StdioServerParameters(command=sys.executable, args=["-m", "homelink_mcp"], env=env)


async def _run_case(
    settings: AgentSettings,
    case: Case,
    session: ClientSession,
    tools: list[dict[str, Any]],
    chat_fn: ChatFn | None,
) -> Outcome:
    calls: list[Call] = []

    def on_tool(name: str, arguments: dict[str, Any], text: str) -> None:
        calls.append(Call(name=name, arguments=dict(arguments), text=text))

    case_settings = replace(settings, max_steps=case.max_steps) if case.max_steps else settings
    agent = HomeAgent(case_settings, on_tool=on_tool, chat_fn=chat_fn)
    history: list[dict[str, str]] = []
    replies: list[str] = []
    started = time.perf_counter()
    error: str | None = None
    for turn in case.turns:
        try:
            reply = await agent.respond(session, tools, turn, history)
        except Exception as exc:  # 单条失败不打断整轮评测
            error = f"{type(exc).__name__}: {exc}"
            break
        replies.append(reply)
        history.append({"role": "user", "content": turn})
        history.append({"role": "assistant", "content": reply})
    latency = (time.perf_counter() - started) * 1000
    return Outcome(
        case=case,
        calls=tuple(calls),
        replies=tuple(replies),
        latency_ms=latency,
        error=error,
    )


async def _run(cases: list[Case], settings: AgentSettings, chat_fn: ChatFn | None) -> list[Outcome]:
    outcomes: list[Outcome] = []
    # MCP 子进程的 stderr（FastMCP 请求日志 + 服务器 JSONL 日志）不该混进评测输出
    with open(os.devnull, "w", encoding="utf-8") as server_stderr:
        async with AsyncExitStack() as stack:
            read, write = await stack.enter_async_context(
                stdio_client(_server_params(settings), errlog=server_stderr)
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools = [to_ollama_tool(tool) for tool in (await session.list_tools()).tools]
            for index, case in enumerate(cases, start=1):
                outcome = await _run_case(settings, case, session, tools, chat_fn)
                outcomes.append(outcome)
                flag = "PASS" if outcome.passed else "FAIL"
                names = [call.name for call in outcome.calls] or ["（无调用）"]
                print(f"[{index:03d}] {flag} [{case.category}] {' → '.join(case.turns)} -> {names}")
                detail = "；".join(outcome.reasons())
                if detail:
                    print(f"      {detail}")
                if outcome.last_reply:
                    print(f"      回复：{outcome.last_reply[:80]}")
    return outcomes


def _build_chat(args: argparse.Namespace) -> ChatFn | None:
    if args.provider == "ollama":
        return None
    base_url = (os.environ.get("EVAL_CLOUD_BASE_URL") or "").rstrip("/")
    api_key = os.environ.get("EVAL_CLOUD_API_KEY") or ""
    if not base_url or not api_key:
        raise SystemExit(
            "云端评测需要 EVAL_CLOUD_BASE_URL 与 EVAL_CLOUD_API_KEY（OpenAI 兼容接口）"
        )
    if not args.model:
        raise SystemExit("云端评测需要 --model 指定模型名")
    return _cloud_chat_factory(base_url, api_key, args.model)


def _load_cases(args: argparse.Namespace) -> list[Case]:
    raw_cases: list[dict[str, Any]] = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = [Case.from_dict(item) for item in raw_cases]
    if args.category:
        cases = [case for case in cases if case.category == args.category]
    if args.offset:
        cases = cases[args.offset :]
    if args.limit:
        cases = cases[: args.limit]
    return cases


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None, help="默认读取 HOMELINK_BASE_URL")
    parser.add_argument("--provider", choices=["ollama", "cloud"], default="ollama")
    parser.add_argument("--model", default="", help="云端模型名，如 qwen-plus")
    parser.add_argument("--category", default="", help="只跑某一分类")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--report", default="", help="写 Markdown 报告到这个路径")
    args = parser.parse_args()

    settings = replace(load_settings(), audit_path="off")
    if args.base_url:
        settings = replace(settings, homelink_base_url=args.base_url.rstrip("/"))
    chat_fn = _build_chat(args)
    cases = _load_cases(args)
    if not cases:
        print("没有可跑的用例")
        return 1

    inventory = await _preflight(settings)
    devices = inventory["devices"].get("devices", [])
    print(f"后端：{settings.homelink_base_url}  设备 {len(devices)} 台  用例 {len(cases)} 条")
    print(f"模型：{'云端 ' + args.model if chat_fn else settings.model}")

    outcomes = await _run(cases, settings, chat_fn)
    summary = summarize(outcomes)

    print("\n=== 汇总 ===")
    print(
        f"用例：{summary.total}  通过：{summary.passed}  准确率：{summary.accuracy:.1f}%  "
        f"误发控制：{summary.misdispatch}  话术违规：{summary.violations}  模型异常：{summary.errors}"
    )
    rate = summary.clarify_rate
    print(f"澄清率：{'—' if rate is None else f'{rate:.1f}%'}")
    print(
        f"延迟 P50/P95：{'—' if summary.p50_ms is None else f'{summary.p50_ms:.0f}ms'}"
        f" / {'—' if summary.p95_ms is None else f'{summary.p95_ms:.0f}ms'}"
    )
    print(f"结论：{'达标' if summary.accepted else '未达标'}")

    if args.report:
        meta = {
            "运行时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "模型": f"云端 {args.model}" if chat_fn else f"Ollama {settings.model}",
            "后端地址": settings.homelink_base_url,
            "设备数": str(len(devices)),
            "用例数": str(len(cases)),
            "筛选": args.category or "全部",
            "Python": platform.python_version(),
            "命令": " ".join([Path(sys.argv[0]).name, *sys.argv[1:]]),
        }
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            render_report(meta=meta, summary=summary, outcomes=outcomes), encoding="utf-8"
        )
        print(f"报告已写入：{report}")

    return 0 if summary.accepted else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
