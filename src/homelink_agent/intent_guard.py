"""意图护栏：在模型调用工具之前，拦下已知会误发红外的高风险输入。

两类规则都是确定性的、可单测的，不依赖模型自觉：

1. 全局动作 / 环境感受且没有明确目标 -> 本轮禁止调用任何工具，只允许追问。
2. 状态类问题 -> 必须至少调用一次状态查询工具，不能只查设备列表就回答。

规则保持窄口径：只覆盖评测与实际使用中确认会误发的模式，避免把正常控制
（例如「把卧室风扇关掉」）误判成需要澄清。
"""

from __future__ import annotations

import re

# 全局量词：命中即要求澄清，即使句子里带了设备名。
_GLOBAL_RE = re.compile(r"全部|全都|所有|一键|整个家|全屋|家里所有|每台|各个|都关|都开")

# 环境感受：没有明确设备/房间时先追问，不直接控制。
_COMPLAINT_RE = re.compile(r"太热|太冷|有点热|有点冷|有点闷|好闷|闷得|来点风|空气不好|不舒服")

# 明确目标：设备类型或房间。用于判断环境感受是否可以落到具体设备。
_TARGET_WORDS: tuple[str, ...] = (
    "风扇",
    "空调",
    "灯",
    "电视",
    "投影",
    "加湿器",
    "取暖器",
    "门锁",
    "烤箱",
    "燃气",
    "窗帘",
    "音响",
    "音箱",
    "扫地",
    "卧室",
    "客厅",
    "厨房",
    "卫生间",
    "书房",
    "阳台",
    "餐厅",
    "主卧",
    "次卧",
)

# 依赖上下文的追问：没有历史时先澄清，有历史时交给模型结合上文判断。
_FOLLOWUP_RE = re.compile(r"^(再|继续|还是)|再.*(一点|一档|一下)|^那个|^那么")

# 状态问题标记。
_STATE_RE = re.compile(
    r"开着吗|关了吗|关着吗|开了没|关了没|开了没有|关了没有|在转|状态|什么模式|几档|多少度|是不是|有没有开|"
    r"确定|保证|成功了吗|发出去|发对|发错|断电|开着|关着"
)


def requires_clarification(text: str) -> bool:
    """本轮是否必须先追问、禁止调用工具。

    - 出现全局量词：一律追问（例如「全部关掉」「帮我把家里所有东西关一下」）。
    - 出现环境感受且没有具体设备/房间：追问（例如「太热了」「来点风」「有点闷」）。
    """

    stripped = text.strip()
    if not stripped:
        return False
    if _GLOBAL_RE.search(stripped):
        return True
    vague_complaint = _COMPLAINT_RE.search(stripped) is not None and not any(
        word in stripped for word in _TARGET_WORDS
    )
    return vague_complaint


def requires_context_followup(text: str) -> bool:
    """是否是「再小一点」「那个模式」这类必须依赖上文的追加指令。"""

    stripped = text.strip()
    if not stripped:
        return False
    return _FOLLOWUP_RE.search(stripped) is not None


def requires_state_query(text: str) -> bool:
    """是否是状态类问题（需要先查状态，而不是只查设备列表）。"""

    stripped = text.strip()
    if not stripped:
        return False
    # 命中状态词即要求查询；纯控制指令（例如「把风扇关掉」）不会命中状态词。
    # 同时含状态词与动作词（例如「刚才那下打开成功了吗」）仍要求查。
    return _STATE_RE.search(stripped) is not None
