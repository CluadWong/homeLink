"""调用页（默认页）：设备下的功能列表 + 一次「执行」。"""

from __future__ import annotations

from typing import Any

import streamlit as st

from jj_butler.interface.clients.http_client import AgentClient, ApiError
from jj_butler.interface.clients.streamlit import errors, widgets


def render_call_page(client: AgentClient, state: dict[str, Any] | None) -> None:
    del state  # 摘要条已展示状态，本页自己拉设备与功能
    st.caption("红外是单向的：执行成功只代表「信号已发送」，管家无法确认设备真的照做。")

    try:
        listing = client.devices()
    except ApiError as exc:
        widgets.render_error(exc)
        return

    devices = list(listing.get("devices") or [])
    if not devices:
        st.info("还没有设备。先添加一台设备，再去学码。")
        if st.button("去添加设备", key="call_add_device", type="primary"):
            widgets.goto("设备")
        return

    for device in devices:
        name = widgets.device_label(device)
        functions = list(device.get("functions") or [])
        st.markdown(f"**{name}**")
        if not functions:
            st.caption("这台设备还没有可调用的功能。")
            if st.button("去学码", key=f"call_learn_{device.get('id')}"):
                widgets.goto("学码")
            st.divider()
            continue

        for function in functions:
            function_id = str(function.get("id") or "")
            function_name = str(function.get("name") or "未命名功能")
            row, action = st.columns([4, 1])
            with row:
                st.markdown(f"- {function_name}")
            with action:
                if st.button(
                    "执行",
                    key=f"call_{function_id}",
                    type="primary",
                    use_container_width=True,
                ):
                    _execute(client, function_id, function_name)
        st.divider()


def _execute(client: AgentClient, function_id: str, function_name: str) -> None:
    try:
        client.control(function_id)
    except ApiError as exc:
        widgets.flash("error", errors.error_text(exc), exc.code)
        st.rerun()
        return

    # 成功只说「已发送」；不得暗示已经开/关。
    widgets.flash("success", f"已发送「{function_name}」")
    try:
        # 执行后重拉状态：摘要条下一轮也会重新读取，这里先拉一次让结果立即生效。
        client.state()
    except ApiError as exc:
        widgets.flash("warning", f"已发送，但状态刷新失败 —— {errors.error_text(exc)}", exc.code)
    st.rerun()
