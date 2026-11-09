"""页面共用渲染与动作：统一错误、flash、状态文字、网关与摘要条。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import streamlit as st

from homelink.interface.clients.http_client import ApiError
from homelink.interface.clients.streamlit import errors

FLASH_KEY = "flash_message"
GOTO_KEY = "goto_page"

STATE_LABELS: dict[str, str] = {"on": "开", "off": "关", "unknown": "未知"}


def goto(page: str) -> None:
    """请求切页（页面内按钮用）。"""

    st.session_state[GOTO_KEY] = page
    st.rerun()


def flash(kind: str, message: str, code: str = "") -> None:
    """记一条结果，下一轮渲染显示（显示即清）。"""

    st.session_state[FLASH_KEY] = (kind, message, code)


def render_flash() -> None:
    item = st.session_state.pop(FLASH_KEY, None)
    if not item:
        return
    kind, message = item[0], item[1]
    code = item[2] if len(item) > 2 else ""
    {"success": st.success, "warning": st.warning, "error": st.error}[kind](message)
    if code and errors.is_device_side(code):
        st.button(
            "去状态页看网关",
            key="flash_goto_state",
            on_click=_request_state_page,
        )


def _request_state_page() -> None:
    st.session_state[GOTO_KEY] = "状态"


def call_or_flash(fn: Any, *args: Any, ok: str = "", **kwargs: Any) -> Any:
    """调一次客户端方法；失败转人话并返回 None。"""

    try:
        result = fn(*args, **kwargs)
    except ApiError as exc:
        flash("error", errors.error_text(exc), exc.code)
        return None
    if ok:
        flash("success", ok)
    return result


def render_error(exc: Exception) -> None:
    """错误的人话 + 下一步；只在值得排查时折叠 trace。"""

    code = str(getattr(exc, "code", "INTERNAL_ERROR"))
    info = errors.friendly(code, str(getattr(exc, "message", "") or ""))
    text = info.text if not info.next_step else f"{info.text} —— {info.next_step}"
    st.error(text)
    trace_id = str(getattr(exc, "trace_id", "") or "")
    if info.show_trace and trace_id:
        with st.expander("排查用的编号"):
            st.code(trace_id)


def state_text(device: dict[str, Any]) -> str:
    """`state_source=unknown` 只显示「未知」；已确认的非标准值原样显示并截断。"""

    if str(device.get("state_source")) != "confirmed":
        return "未知"
    raw = str(device.get("state") or "").strip()
    if not raw:
        return "未知"
    if raw in STATE_LABELS:
        return STATE_LABELS[raw]
    return raw[:12] + ("…" if len(raw) > 12 else "")


def source_text(device: dict[str, Any]) -> str:
    return "已确认" if str(device.get("state_source")) == "confirmed" else "未知"


def badge_text(device: dict[str, Any]) -> str:
    return f"{state_text(device)} · 来源：{source_text(device)}"


def device_label(device: dict[str, Any]) -> str:
    return str(device.get("name") or device.get("id") or "未命名设备")


def _is_auth_error(error: str) -> bool:
    return "鉴权" in error or "401" in error or "403" in error


def gateway_view(status: dict[str, Any]) -> tuple[str, str]:
    """返回（语义级别, 人话）。四种状态必须能区分：在线 / 离线 / 超时 / 401。"""

    mode = str(status.get("mode") or "")
    if mode == "mock" or status.get("simulated"):
        return "info", "网关：模拟模式，不连真机"

    base_url = str(status.get("base_url") or "").strip() or "（未配置地址）"
    error = str(status.get("error") or "").strip()
    reachable = bool(status.get("reachable"))
    latency = status.get("latency_ms")
    latency_text = f"（{int(latency)} ms）" if isinstance(latency, int | float) else ""

    if reachable and _is_auth_error(error):
        return "warning", f"网关已连上{latency_text}，但鉴权被拒：{error}"
    if reachable:
        if error:
            return "warning", f"网关在线{latency_text}，但探测有异常：{error}"
        return "success", f"网关在线：{base_url}{latency_text}"
    if "超时" in error:
        return "warning", f"网关超时：{base_url} —— {error}"
    if error:
        return "error", f"网关离线：{base_url} 无响应 —— {error}"
    return "error", f"网关离线：{base_url} 无响应（探测失败，原因未知）"


def render_gateway_status(status: dict[str, Any]) -> None:
    level, text = gateway_view(status)
    {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}[level](text)
    last_success = format_time(status.get("last_success_at"))
    if last_success:
        st.caption(f"最近一次成功：{last_success}")


def render_sensor_cards(state: dict[str, Any]) -> None:
    """传感器本期不接入：三项都写「未接入」，**不得显示 0**。"""

    sensors = state.get("sensors") or {}
    columns = st.columns(3)
    available = bool(sensors.get("available"))
    cards = (
        ("温度", "temperature", "℃"),
        ("湿度", "humidity", "%"),
        ("人体", "presence", ""),
    )
    for column, (title, key, unit) in zip(columns, cards, strict=True):
        with column:
            value = sensors.get(key)
            if not available:
                st.metric(title, "未接入")
            elif key == "presence":
                text = "有人" if value else "没人" if value is False else "—"
                st.metric(title, text)
            else:
                text = f"—{unit}" if value is None else f"{value}{unit}"
                st.metric(title, text)
    if not available:
        st.caption("传感器本期未接入：这里只说明状态，不显示读数。")


def render_summary_bar(client: Any) -> dict[str, Any] | None:
    """常驻摘要条：网关为核心，附设备概况；服务连不上时明确说，不静默。"""

    try:
        state = client.state()
    except ApiError as exc:
        render_error(exc)
        if st.button("重试", key="summary_retry"):
            st.rerun()
        return None

    try:
        gateway = client.gateway()
    except ApiError as exc:
        st.warning(errors.error_text(exc))
        gateway = {}

    if gateway:
        level, text = gateway_view(gateway)
        prefix = {
            "success": "网关在线",
            "warning": "网关需注意",
            "error": "网关离线",
            "info": "网关模拟",
        }[level]
        devices = list(state.get("devices") or [])
        summary = "；".join(f"{device_label(item)} {state_text(item)}" for item in devices)
        st.caption(f"{prefix}　·　设备 {len(devices)} 台：{summary or '暂无设备'}")
        if text and level in ("warning", "error", "info"):
            st.caption(text)
    return dict(state)


def format_time(raw: Any) -> str:
    """ISO UTC → 本地 `MM-DD HH:MM:SS`；解析不出来原样返回（不编假时间）。"""

    if not raw:
        return ""
    text = str(raw).replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone().strftime("%m-%d %H:%M:%S")
