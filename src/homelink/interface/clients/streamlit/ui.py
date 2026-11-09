"""Streamlit 过渡前端的入口（六页）。只经 HTTP 客户端访问后端。"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import streamlit as st

from homelink.interface.clients.http_client import DEFAULT_BASE_URL, AgentClient, shared_client
from homelink.interface.clients.streamlit import widgets
from homelink.interface.clients.streamlit.pages import (
    audit,
    call,
    correct,
    devices,
    learn,
)
from homelink.interface.clients.streamlit.pages import (
    state as state_page,
)

NAV_KEY = "nav_page"
ACTIVE_KEY = "active_page"
PAGES: tuple[str, ...] = ("调用", "设备", "学码", "状态", "校正", "审计")


def main() -> None:
    st.set_page_config(page_title="HomeLink", page_icon="◉", layout="centered")

    base_url = os.environ.get("JJ_AGENT_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    token = os.environ.get("JJ_API_TOKEN", "").strip()
    client = shared_client(base_url, token)

    state = widgets.render_summary_bar(client)
    widgets.render_flash()

    page = _render_nav()
    _cancel_learn_on_leave(client, page)

    renderers: dict[str, Callable[[AgentClient, dict[str, Any] | None], None]] = {
        "调用": call.render_call_page,
        "设备": devices.render_devices_page,
        "学码": learn.render_learn_page,
        "状态": state_page.render_state_page,
        "校正": correct.render_correct_page,
        "审计": audit.render_audit_page,
    }
    renderers[page](client, state)

    _render_settings(base_url, token)


def _render_nav() -> str:
    requested = st.session_state.pop(widgets.GOTO_KEY, None)
    if requested in PAGES:
        st.session_state[NAV_KEY] = requested
    if NAV_KEY not in st.session_state:
        st.session_state[NAV_KEY] = PAGES[0]

    page = st.radio(
        "页面",
        PAGES,
        horizontal=True,
        key=NAV_KEY,
        label_visibility="collapsed",
    )
    return str(page)


def _cancel_learn_on_leave(client: AgentClient, page: str) -> None:
    previous = str(st.session_state.get(ACTIVE_KEY) or "")
    if previous == "学码" and page != "学码":
        if learn.cancel_active_session(client):
            widgets.flash("warning", "已离开学码页，未完成的学码已取消。")
        st.session_state[ACTIVE_KEY] = page
        st.rerun()
    st.session_state[ACTIVE_KEY] = page


def _render_settings(base_url: str, token: str) -> None:
    with st.expander("设置", expanded=False):
        st.caption(f"服务地址：{base_url}")
        # 正常路径不展示 token 内容，也不让用户手输。
        st.caption("鉴权：已由启动脚本注入" if token else "鉴权：后端未设置 token（本机可直连）")
