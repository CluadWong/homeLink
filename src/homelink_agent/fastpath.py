"""确定性快路径：常见的「开 / 关某台设备」不经过模型，直接查设备并下发控制。

只有同时满足以下条件才走快路径，其余一律交回模型：

1. 输入能严格解析成「动作 + 目标」，例如「关闭风扇」「把卧室风扇关掉」；
2. 目标在家里的设备中唯一命中；
3. 设备不是高风险，且存在与动作匹配的能力（没有时允许退回唯一的「开关」翻转码）。

这样既不改变现有模型路径的行为，也避免模糊指令被确定性代码误判。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_OPEN_VERBS = ("打开", "开启", "启动", "开")
_CLOSE_VERBS = ("关闭", "关掉", "关上", "停止", "关")

_TARGET = r"(?P<target>[\u4e00-\u9fa5A-Za-z0-9]{1,12}?)"
_SUFFIX = r"(?:一下|下|吧|呢|谢谢)?"

# 「关闭风扇」「请打开卧室风扇」
_PREFIX_RE = re.compile(
    rf"^(?:请|麻烦)?(?:帮我)?(?:把)?(?P<verb>{'|'.join(_OPEN_VERBS + _CLOSE_VERBS)})"
    rf"{_TARGET}{_SUFFIX}$"
)
# 「把卧室风扇关掉」「风扇关一下」
_SUFFIX_RE = re.compile(
    rf"^(?:请|麻烦)?(?:帮我)?(?:把)?{_TARGET}(?P<verb>{'|'.join(_OPEN_VERBS + _CLOSE_VERBS)})"
    rf"{_SUFFIX}$"
)

_TYPE_WORDS: dict[str, tuple[str, ...]] = {
    "fan": ("风扇",),
    "air_conditioner": ("空调",),
    "light": ("灯", "灯光"),
    "tv": ("电视",),
    "projector": ("投影", "投影仪"),
    "humidifier": ("加湿器",),
    "heater": ("取暖器", "电暖器"),
    "lock": ("门锁", "锁"),
    "oven": ("烤箱",),
    "gas": ("燃气", "燃气灶"),
    "speaker": ("音响", "音箱"),
    "appliance": (),
}

_ROOM_WORDS: dict[str, tuple[str, ...]] = {
    "bedroom": ("卧室", "主卧", "次卧"),
    "living_room": ("客厅",),
    "kitchen": ("厨房",),
    "bathroom": ("卫生间", "浴室"),
    "study": ("书房",),
    "balcony": ("阳台",),
    "dining_room": ("餐厅",),
}

_REJECT_WORDS = (
    "全部",
    "所有",
    "都",
    "一键",
    "并",
    "一点",
    "大点",
    "小点",
    # 依赖上文的代词/指代：快路径不看历史，必须交回模型。
    "它",
    "这个",
    "那个",
    "这台",
    "那台",
    "刚才",
)

_CLOSE_COMMANDS = frozenset({"关", "关闭", "关掉", "关上", "停止", "停"})
_OPEN_COMMANDS = frozenset({"开", "打开", "开启", "启动"})
_TOGGLE_COMMANDS = frozenset({"开关", "切换"})


@dataclass(frozen=True)
class SimpleControl:
    """解析结果：方向 + 目标短语（原样保留，用于匹配设备名/别名）。"""

    direction: str  # "open" | "close"
    target: str


def parse_simple_control(text: str) -> SimpleControl | None:
    """严格解析「动词 + 目标」；任何多余成分都返回 None，交回模型。"""

    stripped = text.strip().rstrip("。！!？?")
    if not stripped or len(stripped) > 24:
        return None
    match = _PREFIX_RE.match(stripped) or _SUFFIX_RE.match(stripped)
    if not match:
        return None
    target = match.group("target").strip()
    # 全局动作、多设备连说、程度调整都不是「单设备开关」，交回模型处理。
    if any(word in target for word in _REJECT_WORDS):
        return None
    verb = match.group("verb")
    direction = "open" if verb in _OPEN_VERBS else "close"
    return SimpleControl(direction=direction, target=target)


def _mentioned_rooms(target: str) -> set[str]:
    return {room for room, words in _ROOM_WORDS.items() if any(word in target for word in words)}


def match_devices(devices: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    """按设备名、别名、类型词匹配；目标里带房间词时再按房间收敛。"""

    rooms = _mentioned_rooms(target)
    hits: list[dict[str, Any]] = []
    for device in devices:
        if rooms and str(device.get("room") or "") not in rooms:
            continue
        names = [str(device.get("name") or "")]
        names.extend(str(alias) for alias in device.get("aliases") or [])
        names.extend(_TYPE_WORDS.get(str(device.get("type") or ""), ()))
        if any(name and name in target for name in names):
            hits.append(device)
    return hits


def pick_command(device: dict[str, Any], direction: str) -> str | None:
    """挑一个与方向匹配的能力；没有时退回唯一的翻转码「开关」。"""

    commands = [
        str(capability.get("command"))
        for capability in device.get("capabilities") or []
        if isinstance(capability, dict) and capability.get("command")
    ]
    wanted = _OPEN_COMMANDS if direction == "open" else _CLOSE_COMMANDS
    for command in commands:
        if command in wanted:
            return command
    if direction == "close":
        for command in commands:
            if "关闭" in command or "关掉" in command:
                return command
    else:
        for command in commands:
            if "打开" in command or "开启" in command:
                return command
    toggles = [command for command in commands if command in _TOGGLE_COMMANDS]
    # 翻转码兜底只对风扇生效：真机风扇只有「开关」码且已验证；
    # 空调等设备没有独立开/关码时应交回模型说明限制。
    if len(toggles) == 1 and str(device.get("type") or "") == "fan":
        return toggles[0]
    return None
