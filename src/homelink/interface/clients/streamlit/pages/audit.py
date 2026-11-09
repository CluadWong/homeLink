"""审计页：只读列表，默认最近 50 条，倒序；失败原因说人话，不露内部术语。"""

from __future__ import annotations

from typing import Any

import streamlit as st

from homelink.interface.clients.http_client import AgentClient, ApiError
from homelink.interface.clients.streamlit import widgets

_RESULT_OPTIONS: tuple[tuple[str, str | None], ...] = (
    ("全部", None),
    ("成功", "success"),
    ("失败", "failed"),
)

_ACTION_LABELS: dict[str, str] = {
    "learn.start": "开始学码",
    "learn.save": "保存学码",
    "learn.cancel": "取消学码",
    "learn.idle_timeout": "学码超时自动结束",
}


def render_audit_page(client: AgentClient, state: dict[str, Any] | None) -> None:
    del state
    st.caption("只读记录，按时间倒序；默认最近 50 条。失败原因写成人话，不展示内部术语。")

    picked = st.radio(
        "结果",
        [label for label, _ in _RESULT_OPTIONS],
        horizontal=True,
        key="audit_result_filter",
    )
    result = next(value for label, value in _RESULT_OPTIONS if label == picked)

    try:
        payload = client.audit(limit=50, result=result)
    except ApiError as exc:
        widgets.render_error(exc)
        return

    records = list(payload.get("records") or [])
    if not records:
        st.info("暂无记录。")
        return

    device_names = _device_names(client)
    st.caption(f"共 {len(records)} 条（最多显示 50 条）")

    header = st.columns([2, 3, 3, 2, 5])
    for column, title in zip(header, ("时间", "动作", "设备", "结果", "说明"), strict=True):
        column.caption(title)

    for record in records:
        _render_record(record, device_names)


def _render_record(record: dict[str, Any], device_names: dict[str, str]) -> None:
    when, action, device, result, reason = st.columns([2, 3, 3, 2, 5])
    with when:
        st.write(widgets.format_time(record.get("created_at")) or "—")
    with action:
        st.write(action_label(str(record.get("action") or "")))
    with device:
        device_id = str(record.get("device_id") or "")
        st.write(device_names.get(device_id, "—") if device_id else "—")
    with result:
        st.write("成功" if record.get("result") == "success" else "失败")
    with reason:
        if record.get("result") == "failed":
            st.write(_human_reason(str(record.get("reason") or "")))
        else:
            st.write("—")


def action_label(action: str) -> str:
    """把内部 action 名翻成用户看得懂的动作。"""

    if action.endswith(".control"):
        return "调用功能"
    return _ACTION_LABELS.get(action, "其他操作")


def _human_reason(reason: str) -> str:
    text = reason.strip()
    if not text:
        return "没有记录具体原因"
    return text


def _device_names(client: AgentClient) -> dict[str, str]:
    try:
        payload = client.devices()
    except ApiError:
        return {}
    return {
        str(item.get("id")): widgets.device_label(item) for item in payload.get("devices") or []
    }
