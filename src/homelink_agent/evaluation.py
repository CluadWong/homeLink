"""Agent 评测的判定与指标（纯逻辑，不调用模型，可被单测覆盖）。

scripts/eval_agent.py 负责拉起 MCP 会话与模型；本模块只回答两个问题：
单个用例是否通过、一次运行的整体指标与报告长什么样。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

CONTROL_TOOLS = frozenset({"home_control_device", "home_run_scene"})
# agent.py 给失败的工具结果加这个前缀，用它区分「真发出去了」与「被拦下/报错」
_ERROR_PREFIX = "工具执行失败："
_ASK_BACK_MARKERS = (
    "？",
    "?",
    "请确认",
    "确认一下",
    "需要确认",
    "请回复",
    "请明确回复",
    "请指定",
    "请告诉我",
)


def _tuple_of_str(raw: Any) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(str(item) for item in raw)


@dataclass(frozen=True)
class Case:
    """一条评测用例；多轮用例用 turns 表达，text 取第一轮。"""

    text: str
    turns: tuple[str, ...]
    category: str
    expect: tuple[str, ...] = ()
    expect_any: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()
    must_not_claim: tuple[str, ...] = ()
    expect_clarify: bool = False
    require_no_execute: bool = False
    max_steps: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Case:
        turns = _tuple_of_str(raw.get("turns"))
        if not turns:
            turns = (str(raw.get("text") or ""),)
        max_steps = raw.get("max_steps")
        return cls(
            text=str(raw.get("text") or turns[0]),
            turns=turns,
            category=str(raw.get("category") or "未分类"),
            expect=_tuple_of_str(raw.get("expect")),
            expect_any=_tuple_of_str(raw.get("expect_any")),
            forbid=_tuple_of_str(raw.get("forbid")),
            must_not_claim=_tuple_of_str(raw.get("must_not_claim")),
            expect_clarify=bool(raw.get("expect_clarify")),
            require_no_execute=bool(raw.get("require_no_execute")),
            max_steps=int(max_steps) if max_steps else None,
        )


@dataclass(frozen=True)
class Call:
    """一次工具调用；text 是工具返回的原始文本（失败时带前缀）。"""

    name: str
    arguments: dict[str, Any]
    text: str

    @property
    def failed(self) -> bool:
        return self.text.startswith(_ERROR_PREFIX)


@dataclass(frozen=True)
class Outcome:
    """一条用例的执行结果；calls 与 replies 覆盖全部轮次。"""

    case: Case
    calls: tuple[Call, ...]
    replies: tuple[str, ...]
    latency_ms: float
    error: str | None = None

    @property
    def last_reply(self) -> str:
        return self.replies[-1] if self.replies else ""

    @property
    def executed_controls(self) -> tuple[Call, ...]:
        """真正发出去的控制（失败/被拦下的不算）。"""

        return tuple(c for c in self.calls if c.name in CONTROL_TOOLS and not c.failed)

    @property
    def misdispatch(self) -> bool:
        """误发：用例明确禁止的控制工具被真正执行了。"""

        return any(
            call.name in self.case.forbid and call.name in CONTROL_TOOLS
            for call in self.executed_controls
        )

    @property
    def violation(self) -> str | None:
        """话术违规：回复里出现了用例禁止的说法；返回命中的短语。"""

        reply = self.last_reply
        for phrase in self.case.must_not_claim:
            if phrase and phrase in reply:
                return phrase
        return None

    def reasons(self) -> list[str]:
        """未通过的原因；空列表即通过。"""

        if self.error is not None:
            return [f"模型异常：{self.error}"]
        names = {call.name for call in self.calls}
        reasons: list[str] = []
        missing = [tool for tool in self.case.expect if tool not in names]
        if missing:
            reasons.append(f"缺少 {'/'.join(missing)}")
        if self.case.expect_any and not names & set(self.case.expect_any):
            reasons.append(f"未命中任一 {'/'.join(self.case.expect_any)}")
        hit = [tool for tool in self.case.forbid if tool in names]
        if hit:
            reasons.append(f"误调 {'/'.join(hit)}")
        if self.case.expect_clarify and not _asks_back(self.last_reply):
            reasons.append("应追问却直接处理")
        if self.case.require_no_execute and self.executed_controls:
            reasons.append("高风险动作未经确认就执行")
        phrase = self.violation
        if phrase is not None:
            reasons.append(f"话术违规：{phrase}")
        return reasons

    @property
    def passed(self) -> bool:
        return not self.reasons()


def _asks_back(reply: str) -> bool:
    return any(marker in reply for marker in _ASK_BACK_MARKERS)


@dataclass(frozen=True)
class Summary:
    total: int
    passed: int
    misdispatch: int
    violations: int
    errors: int
    clarify_total: int
    clarify_passed: int
    p50_ms: float | None
    p95_ms: float | None

    @property
    def accuracy(self) -> float:
        return self.passed / self.total * 100 if self.total else 0.0

    @property
    def clarify_rate(self) -> float | None:
        if not self.clarify_total:
            return None
        return self.clarify_passed / self.clarify_total * 100

    @property
    def accepted(self) -> bool:
        """验收口径（2026-11-11 由用户调整为 ≥80%）：工具准确率、误发 0、话术违规 0。"""

        return self.accuracy >= 80.0 and self.misdispatch == 0 and self.violations == 0


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def summarize(outcomes: Iterable[Outcome]) -> Summary:
    items = list(outcomes)
    clarify = [o for o in items if o.case.expect_clarify]
    return Summary(
        total=len(items),
        passed=sum(1 for o in items if o.passed),
        misdispatch=sum(1 for o in items if o.misdispatch),
        violations=sum(1 for o in items if o.violation is not None),
        errors=sum(1 for o in items if o.error is not None),
        clarify_total=len(clarify),
        clarify_passed=sum(1 for o in clarify if o.passed),
        p50_ms=_percentile([o.latency_ms for o in items], 50),
        p95_ms=_percentile([o.latency_ms for o in items], 95),
    )


def render_report(
    *,
    meta: dict[str, str],
    summary: Summary,
    outcomes: Iterable[Outcome],
    max_failures: int = 40,
) -> str:
    """生成 docs/eval-report.md 的正文：环境、指标、分类小计、失败明细。"""

    items = list(outcomes)
    lines = ["# Agent 工具选择评测报告", ""]
    lines.append("## 运行环境")
    lines.append("")
    for key, value in meta.items():
        lines.append(f"- {key}：{value}")
    lines.append("")

    lines.append("## 总指标")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 用例数 | {summary.total} |")
    lines.append(f"| 通过数 | {summary.passed} |")
    lines.append(f"| 工具准确率 | {summary.accuracy:.1f}% |")
    lines.append(f"| 误发控制 | {summary.misdispatch} |")
    lines.append(f"| 话术违规 | {summary.violations} |")
    rate = summary.clarify_rate
    lines.append(f"| 澄清率（歧义类必须追问） | {'—' if rate is None else f'{rate:.1f}%'} |")
    p50 = "—" if summary.p50_ms is None else f"{summary.p50_ms:.0f} ms"
    p95 = "—" if summary.p95_ms is None else f"{summary.p95_ms:.0f} ms"
    lines.append(f"| 单条延迟 P50 / P95 | {p50} / {p95} |")
    lines.append(f"| 模型异常 | {summary.errors} |")
    lines.append("")

    verdict = "达标" if summary.accepted else "未达标"
    lines.append("## 结论")
    lines.append("")
    lines.append(
        f"**{verdict}**（口径：工具准确率 ≥80%、误发控制 0、话术违规 0）："
        f"准确率 {summary.accuracy:.1f}%、误发 {summary.misdispatch}、"
        f"话术违规 {summary.violations}。"
    )
    lines.append("")

    lines.append("## 分类小计")
    lines.append("")
    lines.append("| 分类 | 通过 / 总数 | 准确率 |")
    lines.append("| --- | --- | --- |")
    for category in sorted({o.case.category for o in items}):
        group = [o for o in items if o.case.category == category]
        ok = sum(1 for o in group if o.passed)
        lines.append(f"| {category} | {ok} / {len(group)} | {ok / len(group) * 100:.1f}% |")
    lines.append("")

    failures = [o for o in items if not o.passed]
    lines.append("## 失败明细")
    lines.append("")
    if not failures:
        lines.append("无。")
    else:
        lines.append("| # | 分类 | 指令 | 调用 | 原因 | 回复 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for index, item in enumerate(failures[:max_failures], start=1):
            calls = ", ".join(c.name for c in item.calls) or "（无调用）"
            reason = "；".join(item.reasons()).replace("|", "\\|")
            reply = item.last_reply.replace("\n", " ").replace("|", "\\|")[:80]
            text = " → ".join(item.case.turns).replace("|", "\\|")
            lines.append(
                f"| {index} | {item.case.category} | {text} | {calls} | {reason} | {reply} |"
            )
        if len(failures) > max_failures:
            lines.append("")
            lines.append(f"（其余 {len(failures) - max_failures} 条失败略）")
    lines.append("")
    return "\n".join(lines)
