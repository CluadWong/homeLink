"""状态页：网关为核心每 5 秒轮询；传感器未接入不显示读数；unknown 只显示「未知」。"""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from homelink.interface.clients.http_client import AgentClient, ApiError
from homelink.interface.clients.streamlit import widgets


def render_state_page(client: AgentClient, state: dict[str, Any] | None) -> None:
    del state
    st.caption("这一页只讲现状。红外单向：调用后状态一律「未知」，只有你亲自校正才会「已确认」。")
    _render_live(client)


@st.fragment(run_every="5s")
def _render_live(client: AgentClient) -> None:
    # 网关排在最前：这一页以网关为核心。普通轮询不带 refresh，命中后端 15 秒缓存。
    try:
        gateway = client.gateway()
    except ApiError as exc:
        widgets.render_error(exc)
        gateway = {}
    else:
        widgets.render_gateway_status(gateway)

    if gateway and st.button("重新检测", key="gateway_recheck"):
        try:
            client.gateway(refresh=True)
        except ApiError as exc:
            widgets.render_error(exc)
        else:
            st.rerun()

    try:
        state = client.state()
    except ApiError as exc:
        widgets.render_error(exc)
        return

    st.markdown("**传感器**")
    widgets.render_sensor_cards(state)

    st.markdown("**设备状态**")
    devices = list(state.get("devices") or [])
    if not devices:
        st.caption("还没有设备。")
        if st.button("去添加设备", key="state_add_device"):
            widgets.goto("设备")
        return

    for device in devices:
        _render_device_row(device)


def _render_device_row(device: dict[str, Any]) -> None:
    device_id = str(device.get("id") or "")
    name = widgets.device_label(device)
    source = str(device.get("state_source") or "unknown")
    row, action = st.columns([4, 1])
    with row:
        raw_state = str(device.get("state") or "").strip()
        if source == "unknown":
            # 绝不把 unknown 渲染成「关」；灰色文字明确表示不可判定。
            st.markdown(f"- **{name}**　:gray[未知]")
        elif len(raw_state) > 12 and raw_state not in ("on", "off"):
            # 非标准状态原样显示；超过 12 字符截断，hover 显示全文。
            st.markdown(
                f"- **{html.escape(name)}**　"
                f'<span title="{html.escape(raw_state)}">'
                f"{html.escape(raw_state[:12])}…</span> · 来源：已确认",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(f"- **{name}**　{widgets.state_text(device)} · 来源：已确认")
        updated = widgets.format_time(device.get("updated_at"))
        if updated:
            prefix = "上次校正" if source == "confirmed" else "上次更新"
            st.caption(f"{prefix}：{updated}")
    with action:
        if st.button("校正", key=f"state_correct_{device_id}", use_container_width=True):
            st.session_state["correct_device_id"] = device_id
            widgets.goto("校正")

    if source == "unknown":
        st.caption("「未知」= 管家没有可靠依据判断它现在的开关状态，不是「关」。")
