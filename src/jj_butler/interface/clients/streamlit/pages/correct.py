"""校正页：只改状态与来源，不发红外；两步式提交防手滑。"""

from __future__ import annotations

from typing import Any

import streamlit as st

from jj_butler.interface.clients.http_client import AgentClient, ApiError
from jj_butler.interface.clients.streamlit import widgets

PENDING_KEY = "correct_pending"
DEVICE_KEY = "correct_device_id"

_OPTIONS: tuple[tuple[str, str], ...] = (("开着", "on"), ("关着", "off"), ("未知", "unknown"))
_REASON_CHIPS = ("用遥控器了", "停电了", "顺手关了", "别人动过")


def render_correct_page(client: AgentClient, state: dict[str, Any] | None) -> None:
    del state
    st.caption("校正不会发红外，只修改管家记录的状态与来源；提交后来源变为「已确认」。")
    st.caption("这是红外单向通信下唯一能把状态拉回真实的手段。")

    try:
        current = client.state()
    except ApiError as exc:
        widgets.render_error(exc)
        return

    devices = list(current.get("devices") or [])
    if not devices:
        st.info("还没有设备可以校正。")
        if st.button("去添加设备", key="correct_add_device"):
            widgets.goto("设备")
        return

    device = _pick_device(devices)
    device_id = str(device.get("id"))
    st.markdown(f"**当前记录：{widgets.device_label(device)}　{widgets.badge_text(device)}**")
    st.caption("提交前请再确认一次：下面会显示「当前记录 → 将改为」。")

    picked_label = st.radio(
        "实际现在是",
        [label for label, _ in _OPTIONS],
        horizontal=True,
        key="correct_actual_state",
    )
    picked_state = next(value for label, value in _OPTIONS if label == picked_label)
    reason = _pick_reason()

    if st.button("下一步确认", key="correct_next", type="primary", use_container_width=True):
        st.session_state[PENDING_KEY] = {
            "device_id": device_id,
            "state": picked_state,
            "reason": reason,
        }
        st.rerun()

    _render_confirm(client, devices)


def _pick_device(devices: list[dict[str, Any]]) -> dict[str, Any]:
    if len(devices) == 1:
        return devices[0]

    ids = [str(item.get("id")) for item in devices]
    preset = str(st.session_state.get(DEVICE_KEY) or "")
    if preset not in ids:
        st.session_state.pop(DEVICE_KEY, None)
    picked = st.selectbox(
        "设备",
        ids,
        index=ids.index(preset) if preset in ids else 0,
        format_func=lambda value: widgets.device_label(
            next(item for item in devices if str(item.get("id")) == value)
        ),
        key=DEVICE_KEY,
    )
    return next(item for item in devices if str(item.get("id")) == picked)


def _pick_reason() -> str:
    st.caption("原因（可选）")
    chip = st.radio(
        "原因",
        _REASON_CHIPS,
        horizontal=True,
        key="correct_reason_chip",
        label_visibility="collapsed",
    )
    typed = st.text_input("补充说明", key="correct_reason_text", label_visibility="collapsed")
    return typed.strip() or str(chip)


def _render_confirm(client: AgentClient, devices: list[dict[str, Any]]) -> None:
    pending = st.session_state.get(PENDING_KEY)
    if not isinstance(pending, dict):
        return

    device_id = str(pending.get("device_id") or "")
    device = next((item for item in devices if str(item.get("id")) == device_id), None)
    if device is None:
        st.session_state.pop(PENDING_KEY, None)
        return

    state = str(pending.get("state") or "")
    reason = str(pending.get("reason") or "")
    label = next((text for text, value in _OPTIONS if value == state), state)
    st.info(
        f"当前记录：{widgets.badge_text(device)} → 将改为「{label}」"
        f"（原因：{reason or '未填'}）"
    )

    confirm_col, cancel_col = st.columns(2)
    if confirm_col.button(
        "确认提交校正",
        key="correct_confirm",
        type="primary",
        use_container_width=True,
    ):
        try:
            client.correct(device_id, state, reason)
        except ApiError as exc:
            widgets.render_error(exc)
            return
        st.session_state.pop(PENDING_KEY, None)
        widgets.flash(
            "success",
            f"记下了：{widgets.device_label(device)} 现在是「{label}」，来源为已确认。",
        )
        st.rerun()
    if cancel_col.button("取消", key="correct_cancel", use_container_width=True):
        st.session_state.pop(PENDING_KEY, None)
        st.rerun()
