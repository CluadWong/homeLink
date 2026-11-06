"""学码页：先建设备草稿，再开会话；每秒轮询捕获，保存后给「去调用」出口。"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import streamlit as st

from jj_butler.interface.clients.http_client import AgentClient, ApiError
from jj_butler.interface.clients.streamlit import errors, widgets

DRAFT_KEY = "learn_draft_device"
DRAFT_NAME_KEY = "learn_draft_name"
ACTIVE_KEY = "learn_active_device"
STARTED_KEY = "learn_started_at"
CAPTURED_KEY = "learn_captured"
TIMEOUT_KEY = "learn_timeout_device"
SESSION_ENDED_KEY = "learn_session_ended"
POLL_ERROR_KEY = "learn_poll_error"
SAVED_KEY = "learn_saved_result"
SAVED_WARNING_KEY = "learn_saved_warning"

# 客户端 10 秒无信号判本次失败（服务端另有 600 秒空闲 TTL 回收资源）
TIMEOUT_SECONDS = 10
POLL_INTERVAL = "1s"


def render_learn_page(client: AgentClient, state: dict[str, Any] | None) -> None:
    del state
    saved = st.session_state.get(SAVED_KEY)
    if isinstance(saved, tuple) and len(saved) >= 3:
        _render_saved(saved)
        return

    captured = st.session_state.get(CAPTURED_KEY)
    if isinstance(captured, dict) and captured.get("device_id"):
        _render_save_form(client, captured)
        return

    active = st.session_state.get(ACTIVE_KEY)
    if active:
        _render_active(client, str(active))
        return

    _render_create_form(client)


def _render_saved(saved: tuple[Any, ...]) -> None:
    device_id, function_name, device_name = str(saved[0]), str(saved[1]), str(saved[2])
    st.success(f"「{device_name} · {function_name}」已保存，可以去「调用」页使用。")
    warning = str(st.session_state.pop(SAVED_WARNING_KEY, "") or "")
    if warning:
        st.warning(warning)
    go_col, again_col = st.columns(2)
    if go_col.button(
        "去调用",
        key=f"learn_goto_call_{device_id}",
        type="primary",
        use_container_width=True,
    ):
        widgets.goto("调用")
    if again_col.button("再学一个", key="learn_again", use_container_width=True):
        st.session_state.pop(SAVED_KEY, None)
        st.rerun()


def _render_create_form(client: AgentClient) -> None:
    st.markdown("**学一个按键 = 先建一台草稿设备，再录一段码。**")
    st.caption("保存成功后草稿转为普通设备；取消时后端只会撤掉还没有任何数据的空壳。")

    try:
        rooms_payload = client.rooms()
    except ApiError as exc:
        widgets.render_error(exc)
        return
    rooms = list(rooms_payload.get("rooms") or [])
    if not rooms:
        st.info("还没有房间。先去「设备」页新建房间，再回来学码。")
        if st.button("去设备页", key="learn_goto_devices", type="primary"):
            widgets.goto("设备")
        return

    room_map = {
        str(item.get("name")): str(item.get("display_name") or item.get("name")) for item in rooms
    }
    with st.form("learn_create_draft"):
        name = st.text_input("设备名称", placeholder="卧室空调", key="learn_device_name")
        room = st.selectbox(
            "房间",
            [str(item.get("name")) for item in rooms],
            format_func=lambda value: room_map.get(value, value),
            key="learn_device_room",
        )
        notes = st.text_input("备注（可选）", key="learn_device_notes")
        submitted = st.form_submit_button(
            "建立草稿并开始接收",
            type="primary",
            use_container_width=True,
        )
        if not submitted:
            return

        text = name.strip()
        if not text:
            st.warning("设备名称不能为空。")
            return

        try:
            created = client.create_device(text, room, notes=notes.strip(), learn_draft=True)
        except ApiError as exc:
            widgets.render_error(exc)
            return

        device_id = str(created.get("id") or "")
        st.session_state[DRAFT_KEY] = device_id
        st.session_state[DRAFT_NAME_KEY] = text
        try:
            client.learn_start(device_id)
        except ApiError as exc:
            # 开会话失败：按契约撤掉草稿空壳，不留半成品。
            _safe_cancel(client, device_id)
            _clear_session()
            widgets.render_error(exc)
            st.caption("草稿设备已撤回，可以修正后重新开始。")
            return

        st.session_state[ACTIVE_KEY] = device_id
        st.session_state[STARTED_KEY] = (device_id, time.monotonic())
        st.session_state.pop(POLL_ERROR_KEY, None)
        widgets.flash("success", "已打开接收 —— 请对准接收头按下遥控器。")
        st.rerun()


def _render_active(client: AgentClient, device_id: str) -> None:
    name = str(st.session_state.get(DRAFT_NAME_KEY) or "草稿设备")
    st.markdown(f"**正在学码：{name}**")
    st.caption("请对准接收头按下遥控器（距离 1–3 米）。")

    if st.session_state.get(SESSION_ENDED_KEY):
        _render_session_ended(client, device_id)
    elif st.session_state.get(TIMEOUT_KEY) == device_id:
        _render_timeout(client, device_id)
    elif isinstance(st.session_state.get(POLL_ERROR_KEY), str):
        _render_poll_error(client, device_id)
    else:
        _render_capture_poll(client, device_id)
        st.caption("超过 10 秒没收到信号会提示重试；服务端另有 10 分钟空闲回收。")
        if st.button("取消学码（不保存）", key="learn_cancel_active", use_container_width=True):
            _cancel_and_report(client, device_id)


@st.fragment(run_every=POLL_INTERVAL)
def _render_capture_poll(client: AgentClient, device_id: str) -> None:
    """每秒轮询；单次请求失败只提示，不让整页崩。"""

    elapsed = _elapsed_seconds(device_id)
    try:
        captured = client.learn_capture(device_id)
    except ApiError as exc:
        if exc.code == "NOT_FOUND":
            st.session_state[SESSION_ENDED_KEY] = "学码会话已结束（可能已保存、取消或空闲超时）。"
        elif exc.code == "AGENT_UNREACHABLE":
            st.session_state[POLL_ERROR_KEY] = errors.error_text(exc)
        else:
            st.session_state[POLL_ERROR_KEY] = errors.error_text(exc)
        st.rerun(scope="app")
        return
    except Exception:  # noqa: BLE001 - 轮询偶发异常不得中断整页
        st.warning("这一次读取没有成功，正在继续等待…")
        return

    if captured.get("captured"):
        raw_code = [int(value) for value in (captured.get("raw_code") or [])]
        st.session_state[CAPTURED_KEY] = {"device_id": device_id, "raw_code": raw_code}
        st.rerun(scope="app")
        return

    reason = str(captured.get("reason") or "")
    waited = f"　已等待 {elapsed} 秒。" if elapsed is not None else ""
    st.info(f"还在等信号…{waited}　请再按一次遥控器。")

    if reason == "malformed":
        st.warning("收到了信号，但这次没认出来 —— 靠近一点（1–3 米）再按一次。")
    elif reason == "gateway_unreachable":
        st.warning("暂时连不上网关 —— 先去「状态」页确认网关，再继续按键。")
    elif reason == "no_signal" and elapsed is not None and elapsed >= TIMEOUT_SECONDS:
        st.session_state[TIMEOUT_KEY] = device_id
        st.rerun(scope="app")
    else:
        st.caption("还没收到，请再按一次。")


def _render_timeout(client: AgentClient, device_id: str) -> None:
    st.error("本次学码已结束：10 秒内没有收到信号，这次没有保存任何功能。")
    st.caption("请确认遥控器对准接收头、电量正常，然后重试。")
    retry_col, cancel_col = st.columns(2)
    if retry_col.button(
        "重新开始接收",
        key=f"learn_retry_{device_id}",
        type="primary",
        use_container_width=True,
    ):
        _restart_session(client, device_id)
    if cancel_col.button(
        "取消学码",
        key=f"learn_timeout_cancel_{device_id}",
        use_container_width=True,
    ):
        _cancel_and_report(client, device_id)


def _render_poll_error(client: AgentClient, device_id: str) -> None:
    message = str(st.session_state.get(POLL_ERROR_KEY) or "读取学码状态失败。")
    st.error(message)
    st.caption("接收窗口仍在服务端保留；恢复连接后可以继续等待。")
    retry_col, cancel_col = st.columns(2)
    if retry_col.button(
        "继续等待",
        key=f"learn_resume_{device_id}",
        type="primary",
        use_container_width=True,
    ):
        st.session_state.pop(POLL_ERROR_KEY, None)
        st.rerun()
    if cancel_col.button(
        "取消学码",
        key=f"learn_poll_cancel_{device_id}",
        use_container_width=True,
    ):
        _cancel_and_report(client, device_id)


def _render_session_ended(client: AgentClient, device_id: str) -> None:
    message = str(st.session_state.get(SESSION_ENDED_KEY) or "学码会话已结束。")
    st.error(message)
    st.caption("本次没有保存新功能。可以重新开始接收，或取消并清理草稿设备。")
    retry_col, reset_col = st.columns(2)
    if retry_col.button(
        "重新开始接收",
        key=f"learn_restart_{device_id}",
        type="primary",
        use_container_width=True,
    ):
        _restart_session(client, device_id)
    if reset_col.button(
        "取消并清理",
        key=f"learn_reset_{device_id}",
        use_container_width=True,
    ):
        _cancel_and_report(client, device_id)


def _restart_session(client: AgentClient, device_id: str) -> None:
    """重新打开接收；失败时按人话提示，不清掉可重试的状态。"""

    try:
        client.learn_start(device_id)
    except ApiError as exc:
        if exc.code == "NOT_FOUND":
            _clear_session()
            widgets.flash("error", "草稿设备已不存在，请重新建立学码草稿。", exc.code)
        else:
            widgets.flash("error", errors.error_text(exc), exc.code)
        st.rerun()
        return
    st.session_state[STARTED_KEY] = (device_id, time.monotonic())
    st.session_state.pop(TIMEOUT_KEY, None)
    st.session_state.pop(SESSION_ENDED_KEY, None)
    st.session_state.pop(POLL_ERROR_KEY, None)
    widgets.flash("success", "已重新打开接收 —— 请再按一次遥控器。")
    st.rerun()


def _render_save_form(client: AgentClient, captured: dict[str, Any]) -> None:
    device_id = str(captured.get("device_id"))
    raw_code = [int(value) for value in (captured.get("raw_code") or [])]
    st.success(f"已捕获 {len(raw_code)} 段波形，请命名保存。")

    with st.form("learn_save_form"):
        name = st.text_input("给这个键起个名字", placeholder="开机", key="learn_function_name")
        submitted = st.form_submit_button(
            "保存为功能",
            type="primary",
            use_container_width=True,
        )
        if not submitted:
            return
        text = name.strip()
        if not text:
            st.warning("名字不能为空，不然以后分不清这个键是做什么的。")
            return
        if not raw_code:
            st.warning("这次没有收到有效信号，没法保存，请重新学一次。")
            return
        try:
            result = client.learn_save(device_id, text, raw_code)
        except ApiError as exc:
            widgets.render_error(exc)
            if exc.code == "NOT_FOUND":
                st.session_state[SESSION_ENDED_KEY] = "学码会话已结束，无法保存。"
                st.session_state.pop(CAPTURED_KEY, None)
                st.rerun()
            return

        warning = ""
        if not result.get("receiver_stopped", True):
            warning = (
                "码已保存，但接收态没有关闭："
                f"{result.get('receiver_error') or '网关没有确认关闭'}。功能与码都已落库，可直接调用。"
            )
        device_name = str(st.session_state.get(DRAFT_NAME_KEY) or device_id)
        _clear_session()
        st.session_state[SAVED_KEY] = (device_id, text, device_name)
        if warning:
            st.session_state[SAVED_WARNING_KEY] = warning
        widgets.flash("success", f"「{text}」已保存。")
        st.rerun()

    if st.button(
        "取消学码（不保存这次结果）", key="learn_cancel_captured", use_container_width=True
    ):
        _cancel_and_report(client, device_id)


def _elapsed_seconds(device_id: str) -> int | None:
    record = st.session_state.get(STARTED_KEY)
    if not isinstance(record, tuple) or len(record) != 2:
        return None
    if str(record[0]) != device_id:
        return None
    return max(0, int(time.monotonic() - float(record[1])))


def _safe_cancel(client: AgentClient, device_id: str) -> None:
    # 撤壳失败不阻断当前流程；服务端空闲 TTL 会兜底清理。
    with contextlib.suppress(ApiError):
        client.learn_cancel(device_id)


def _cancel_and_report(client: AgentClient, device_id: str) -> None:
    try:
        result = client.learn_cancel(device_id)
    except ApiError as exc:
        widgets.flash("error", f"取消请求失败 —— {errors.error_text(exc)}", exc.code)
    else:
        message = str(result.get("message") or "已取消学码。")
        widgets.flash("warning", message)
    _clear_session()
    st.rerun()


def cancel_active_session(client: AgentClient) -> bool:
    """离开学码页时调用一次 cancel；返回是否发起了取消。"""

    device_id = st.session_state.pop(ACTIVE_KEY, None)
    if not device_id:
        return False
    _safe_cancel(client, str(device_id))
    _clear_session()
    return True


def _clear_session() -> None:
    for key in (
        DRAFT_KEY,
        DRAFT_NAME_KEY,
        ACTIVE_KEY,
        STARTED_KEY,
        CAPTURED_KEY,
        TIMEOUT_KEY,
        SESSION_ENDED_KEY,
        POLL_ERROR_KEY,
    ):
        st.session_state.pop(key, None)
