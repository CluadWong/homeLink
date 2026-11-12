"""确定性快路径的解析与匹配规则。"""

from __future__ import annotations

import pytest

from homelink_agent.fastpath import match_devices, parse_simple_control, pick_command


@pytest.mark.parametrize(
    ("text", "direction", "target"),
    [
        ("关闭风扇", "close", "风扇"),
        ("关风扇", "close", "风扇"),
        ("打开卧室风扇", "open", "卧室风扇"),
        ("把风扇关掉", "close", "风扇"),
        ("风扇开一下", "open", "风扇"),
        ("关闭风扇吧。", "close", "风扇"),
    ],
)
def test_parse_simple_control(text: str, direction: str, target: str) -> None:
    parsed = parse_simple_control(text)

    assert parsed is not None
    assert parsed.direction == direction
    assert parsed.target == target


@pytest.mark.parametrize(
    "text",
    [
        "关小一点",
        "太热了",
        "打开空调并关灯",
        "风扇摇头",
        "全部关闭",
        "关掉风扇，成功了吗",
        "打开它",
        "把那个关掉",
        "",
    ],
)
def test_parse_rejects_non_simple_input(text: str) -> None:
    assert parse_simple_control(text) is None


_FAN = {
    "device_id": "dev_fan",
    "name": "风扇",
    "type": "fan",
    "room": "bedroom",
    "aliases": ["电扇"],
    "capabilities": [{"command": "开关"}, {"command": "摇头"}],
}
_FAN2 = {
    "device_id": "dev_fan2",
    "name": "风扇 2",
    "type": "fan",
    "room": "living_room",
    "aliases": [],
    "capabilities": [{"command": "开关"}],
}
_LIGHT = {
    "device_id": "dev_light",
    "name": "吸顶灯",
    "type": "light",
    "room": "bedroom",
    "aliases": [],
    "capabilities": [{"command": "开"}, {"command": "关"}],
}


def test_match_by_name_alias_and_type() -> None:
    devices = [_FAN, _LIGHT]

    assert match_devices(devices, "风扇") == [_FAN]
    assert match_devices(devices, "电扇") == [_FAN]
    assert match_devices(devices, "卧室灯") == [_LIGHT]


def test_match_with_room_narrows_duplicates() -> None:
    assert match_devices([_FAN, _FAN2], "卧室风扇") == [_FAN]


def test_match_returns_multiple_when_ambiguous() -> None:
    assert len(match_devices([_FAN, _FAN2], "风扇")) == 2


def test_pick_command_prefers_exact_direction() -> None:
    assert pick_command(_LIGHT, "open") == "开"
    assert pick_command(_LIGHT, "close") == "关"


def test_pick_command_falls_back_to_toggle() -> None:
    assert pick_command(_FAN, "close") == "开关"


def test_pick_command_none_when_device_has_no_capabilities() -> None:
    assert pick_command({"capabilities": []}, "close") is None
