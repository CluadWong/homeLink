"""50 条中文指令的工具选择评测：准确率与「不该发却发了」的次数。

用法：
    python scripts/eval_agent.py --base-url http://127.0.0.1:8012
    python scripts/eval_agent.py --limit 5          # 冒烟

评测只看工具调用，不看最终话术；每条指令独立会话，互不影响。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

from homelink_agent.agent import HomeAgent
from homelink_agent.config import AgentSettings, load_settings
from homelink_agent.llm import OllamaError

DATASET = Path(__file__).with_name("agent_eval_set.json")


def judge(case: dict[str, object], calls: list[str]) -> list[str]:
    reasons: list[str] = []
    for tool in case.get("expect", []):  # type: ignore[union-attr]
        if tool not in calls:
            reasons.append(f"缺少 {tool}")
    expect_any = case.get("expect_any") or []
    if expect_any and not set(expect_any) & set(calls):  # type: ignore[arg-type]
        reasons.append(f"未命中任一 {expect_any}")
    for tool in case.get("forbid", []):  # type: ignore[union-attr]
        if tool in calls:
            reasons.append(f"误调 {tool}")
    return reasons


async def run_case(settings: AgentSettings, case: dict[str, object]) -> tuple[list[str], str]:
    calls: list[str] = []
    agent = HomeAgent(settings, on_tool=lambda name, _args, _text: calls.append(name))
    reply = await agent.run(str(case["text"]))
    return calls, reply


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None, help="默认读取 HOMELINK_BASE_URL")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()

    settings = load_settings()
    if args.base_url:
        settings = replace(settings, homelink_base_url=args.base_url.rstrip("/"))

    cases: list[dict[str, object]] = json.loads(DATASET.read_text(encoding="utf-8"))
    if args.offset:
        cases = cases[args.offset :]
    if args.limit:
        cases = cases[: args.limit]

    passed = 0
    unsafe = 0
    errors = 0
    failures: list[str] = []

    for index, case in enumerate(cases, start=1):
        text = str(case["text"])
        try:
            calls, reply = await run_case(settings, case)
        except OllamaError as exc:
            errors += 1
            failures.append(f"[{index:02d}] 模型异常：{text}（{exc}）")
            print(f"[{index:02d}] ERROR {text}")
            continue

        reasons = judge(case, calls)
        if reasons:
            failures.append(f"[{index:02d}] {'；'.join(reasons)}：{text} → {calls}")
            print(f"[{index:02d}] FAIL  {text} -> {calls} ({'；'.join(reasons)})")
        else:
            passed += 1
            print(f"[{index:02d}] PASS  {text} -> {calls or ['（无调用）']}")

        if "home_control_device" in calls and "home_control_device" in case.get("forbid", []):  # type: ignore[operator]
            unsafe += 1

        if len(reply) > 80:
            reply = reply[:80] + "…"
        print(f"      回复：{reply}")

    total = len(cases)
    accuracy = passed / total * 100 if total else 0.0
    print("\n=== 汇总 ===")
    print(f"用例：{total}  通过：{passed}  准确率：{accuracy:.1f}%  误发控制：{unsafe}  模型异常：{errors}")
    if failures:
        print("失败明细：")
        for item in failures:
            print("  " + item)

    return 0 if unsafe == 0 and accuracy >= 90 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
