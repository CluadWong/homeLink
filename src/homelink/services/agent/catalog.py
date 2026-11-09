"""Agent 能力目录：把设备行整理成稳定、可读的设备模型。"""

from __future__ import annotations

import json
from typing import Any

_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("air_conditioner", ("空调", "冷气", "air conditioner", "aircon")),
    ("heater", ("取暖器", "暖风机", "电暖", "heater")),
    ("fan", ("风扇", "电扇", "fan")),
    ("humidifier", ("加湿器", "humidifier")),
    ("projector", ("投影", "projector")),
    ("speaker", ("音响", "音箱", "speaker")),
    ("tv", ("电视", "tv")),
    ("light", ("灯", "light")),
)

HIGH_RISK_TYPES = {"heater", "oven", "lock", "gas"}
_RISK_LEVELS = {"low", "high"}


def infer_type(name: str) -> str:
    """按设备名推断类型；识别不到时回退 `appliance`。"""

    lowered = name.casefold()
    for device_type, keywords in _TYPE_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return device_type
    return "appliance"


def _extra(device: dict[str, Any]) -> dict[str, Any]:
    raw = device.get("extra") or {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _metadata(device: dict[str, Any]) -> dict[str, Any]:
    extra = _extra(device)
    device_type = extra.get("type")
    if not isinstance(device_type, str) or not device_type.strip():
        device_type = infer_type(str(device.get("name", "")))
    aliases = extra.get("aliases")
    clean_aliases = (
        [alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()]
        if isinstance(aliases, list)
        else []
    )
    risk_level = extra.get("risk_level")
    if risk_level not in _RISK_LEVELS:
        risk_level = "high" if device_type in HIGH_RISK_TYPES else "low"
    return {"type": device_type, "aliases": clean_aliases, "risk_level": risk_level}


def build_device_model(
    device: dict[str, Any],
    state_row: dict[str, Any] | None,
    *,
    online: bool,
) -> dict[str, Any]:
    """设备模型：能力只声明命令，不声明做不到的连续参数。"""

    metadata = _metadata(device)
    capabilities = [
        {"command": function["name"], "command_id": function["id"], "parameters": {}}
        for function in device.get("functions", [])
    ]
    return {
        "device_id": device["id"],
        "type": metadata["type"],
        "room": device["room"],
        "name": device["name"],
        "aliases": metadata["aliases"],
        "risk_level": metadata["risk_level"],
        "online": online,
        "online_scope": "gateway",
        "capabilities": capabilities,
        "state": {"power": state_row["state"] if state_row else "unknown"},
        "state_source": state_row["state_source"] if state_row else "unknown",
        "state_updated_at": state_row["updated_at"] if state_row else None,
        "state_readback": False,
    }


def matches_name(model: dict[str, Any], keyword: str) -> bool:
    needle = keyword.strip().casefold()
    if not needle:
        return True
    candidates = [str(model.get("name", "")), *model.get("aliases", [])]
    return any(needle in str(candidate).casefold() for candidate in candidates)
