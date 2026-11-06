"""错误码 → 人话 + 下一步（`docs/frontend-contract.md` §4）。

**界面不出现裸错误码与堆栈**；`trace_id` 默认折叠，只有排查类错误才展示。
本模块只做纯映射，不 import streamlit，便于单测。
"""

from __future__ import annotations

from dataclasses import dataclass

DEVICE_SIDE = frozenset({"DEVICE_OFFLINE", "DEVICE_UNAUTHORIZED"})


@dataclass(frozen=True)
class Friendly:
    """一条人话表达：说什么 + 下一步做什么。"""

    text: str
    next_step: str = ""
    show_trace: bool = False


MATRIX: dict[str, Friendly] = {
    "INVALID_REQUEST": Friendly(
        "请求字段有问题",
        "检查是否缺字段，或带了不认识的字段",
    ),
    "UNAUTHORIZED": Friendly(
        "鉴权失败",
        "检查部署时注入的 token（启动脚本从 settings.local.yaml 读取），不要手输",
    ),
    "NOT_FOUND": Friendly(
        "找不到设备或功能",
        "刷新设备列表，确认它是否已被删除",
    ),
    "CONFLICT": Friendly(
        "状态冲突",
        "刷新后重试",
    ),
    "DEVICE_OFFLINE": Friendly(
        "设备没有响应",
        "去「状态」页看网关：确认板子已上电、连到 Wi-Fi、网关地址正确",
        show_trace=True,
    ),
    "DEVICE_UNAUTHORIZED": Friendly(
        "板子拒绝访问",
        "板子在线但鉴权没通过：核对网关用户名密码与板子配置",
        show_trace=True,
    ),
    "INTERNAL_ERROR": Friendly(
        "管家内部出错",
        "请把这一刻的排查编号报告给我们",
        show_trace=True,
    ),
    # 前端自造：请求没有到达管家后端 —— 下一步是启动服务，不是查板子
    "AGENT_UNREACHABLE": Friendly(
        "管家服务连不上",
        "确认后端已启动、服务地址正确（scripts/start.py），再刷新页面",
        show_trace=True,
    ),
}

_UNKNOWN = Friendly("出了点问题", "稍后重试；若反复出现请报告", show_trace=True)


def friendly(code: str, message: str = "") -> Friendly:
    """把错误码翻成人话；服务端的具体原因原样附上，比通用话术更有用。"""

    if code == "AGENT_UNREACHABLE" and ("超时" in message or "及时返回" in message):
        return Friendly(
            text="管家服务响应超时",
            next_step="服务可能在忙或网络很慢；稍后重试，持续超时请检查后端日志",
            show_trace=True,
        )
    base = MATRIX.get(code, _UNKNOWN)
    if not message or message in base.text:
        return base
    return Friendly(
        text=f"{base.text}：{message}",
        next_step=base.next_step,
        show_trace=base.show_trace,
    )


def is_device_side(code: str) -> bool:
    return code in DEVICE_SIDE


def error_text(exc: Exception) -> str:
    """异常的完整人话（含下一步），供页面与 flash 复用。"""

    code = str(getattr(exc, "code", "INTERNAL_ERROR"))
    message = str(getattr(exc, "message", "") or "")
    info = friendly(code, message)
    if not info.next_step:
        return info.text
    return f"{info.text} —— {info.next_step}"
