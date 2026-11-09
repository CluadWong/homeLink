"""设备页：设备增删改查 + 房间新建 / 改名 / 排序 / 删除。设备本期不可启停。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import streamlit as st

from homelink.interface.clients.http_client import AgentClient, ApiError
from homelink.interface.clients.streamlit import errors, widgets


def room_name_for(display_name: str) -> str:
    """由显示名派生内部 `name`（ASCII 规范化；全中文时用稳定的短哈希）。"""

    normalized = re.sub(r"[^a-z0-9]+", "", display_name.strip().lower())
    if normalized:
        return normalized
    digest = hashlib.sha1(display_name.strip().encode("utf-8")).hexdigest()[:8]
    return f"room_{digest}"


def render_devices_page(client: AgentClient, state: dict[str, Any] | None) -> None:
    del state
    try:
        rooms_payload = client.rooms()
        devices_payload = client.devices()
    except ApiError as exc:
        widgets.render_error(exc)
        return

    rooms = list(rooms_payload.get("rooms") or [])
    devices = list(devices_payload.get("devices") or [])
    room_map = {
        str(item.get("name")): str(item.get("display_name") or item.get("name")) for item in rooms
    }

    st.caption("设备本期不可启停；这里只管理名称、房间与学码功能。")

    _render_add_device(client, rooms, room_map)
    _render_room_management(client, rooms)

    st.divider()
    if not devices:
        st.info("还没有设备。用上面的「新增设备」把家里的遥控设备接进来。")
        return

    grouped: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        grouped.setdefault(str(device.get("room") or ""), []).append(device)

    for room_name, group in grouped.items():
        st.markdown(f"**{room_map.get(room_name, room_name or '未分组')}**")
        for device in group:
            _render_device_card(client, device, room_map)

    for room in rooms:
        if str(room.get("name")) not in grouped:
            st.markdown(f"**{room_map.get(str(room.get('name')), str(room.get('name')))}**")
            st.caption("暂无设备")


def _render_add_device(
    client: AgentClient,
    rooms: list[dict[str, Any]],
    room_map: dict[str, str],
) -> None:
    with st.expander("新增设备", expanded=False):
        if not rooms:
            st.info("还没有房间。先在下面「管理房间」新建一个，再添加设备。")
            return
        with st.form("add_device_form"):
            name = st.text_input("设备名称", placeholder="客厅电视", key="add_device_name")
            room = st.selectbox(
                "房间",
                [str(item.get("name")) for item in rooms],
                format_func=lambda value: room_map.get(value, value),
                key="add_device_room",
            )
            notes = st.text_input("备注（可选）", key="add_device_notes")
            submitted = st.form_submit_button("保存", type="primary", use_container_width=True)
            if not submitted:
                return
            if not name.strip():
                st.warning("设备名称不能为空。")
                return
            widgets.call_or_flash(
                client.create_device,
                name.strip(),
                room,
                notes=notes.strip(),
                ok=f"已添加「{name.strip()}」。",
            )
            st.rerun()


def _render_room_management(client: AgentClient, rooms: list[dict[str, Any]]) -> None:
    with st.expander("管理房间", expanded=False):
        st.caption("改名只改显示名；删除只能删空房间，非空时后端会说明还剩几台设备。")
        ordered = sorted(
            rooms, key=lambda item: (int(item.get("sort_order") or 0), str(item.get("name")))
        )
        _render_create_room(client, ordered)

        for index, room in enumerate(ordered):
            name = str(room.get("name"))
            display = str(room.get("display_name") or name)
            count = int(room.get("device_count") or 0)
            with st.form(f"room_form_{name}"):
                st.markdown(f"**{display}**　{count} 台设备")
                new_display = st.text_input("显示名", value=display, key=f"room_display_{name}")
                rename = st.form_submit_button(
                    "保存显示名", use_container_width=True, key=f"room_rename_{name}"
                )
                if rename:
                    text = new_display.strip()
                    if not text:
                        st.warning("房间显示名不能为空。")
                    elif text == display:
                        st.caption("没有变化。")
                    else:
                        try:
                            client.update_room(name, display_name=text)
                        except ApiError as exc:
                            widgets.flash("error", _original_message(exc), exc.code)
                            st.rerun()
                        else:
                            widgets.flash("success", f"房间已改名为「{text}」。")
                            st.rerun()

            up_col, down_col, delete_col = st.columns(3)
            if up_col.button(
                "上移",
                key=f"room_up_{name}",
                disabled=index == 0,
                use_container_width=True,
            ):
                _move_room(client, ordered, index, -1)
            if down_col.button(
                "下移",
                key=f"room_down_{name}",
                disabled=index == len(ordered) - 1,
                use_container_width=True,
            ):
                _move_room(client, ordered, index, 1)
            if delete_col.button("删除房间", key=f"room_delete_{name}", use_container_width=True):
                st.session_state[f"confirm_delete_room_{name}"] = True
                st.rerun()

            if st.session_state.get(f"confirm_delete_room_{name}"):
                _render_room_delete(client, name, display, count)
            st.divider()


def _render_create_room(client: AgentClient, rooms: list[dict[str, Any]]) -> None:
    with st.form("create_room_form"):
        display = st.text_input("新房间显示名", placeholder="书房", key="new_room_display")
        st.caption("内部标识会按名称自动生成，不用你填。")
        submitted = st.form_submit_button("新建房间", use_container_width=True)
        if not submitted:
            return
        text = display.strip()
        if not text:
            st.warning("房间显示名不能为空。")
            return
        next_order = max((int(item.get("sort_order") or 0) for item in rooms), default=0) + 1
        try:
            client.create_room(room_name_for(text), text, sort_order=next_order)
        except ApiError as exc:
            widgets.flash(
                "error",
                f"{_original_message(exc)} —— 换个名称，或先确认房间里是否已有同名项。",
                exc.code,
            )
            st.rerun()
        else:
            widgets.flash("success", f"已新建房间「{text}」。")
            st.rerun()


def _move_room(
    client: AgentClient,
    ordered: list[dict[str, Any]],
    index: int,
    step: int,
) -> None:
    current = ordered[index]
    target = ordered[index + step]
    current_order = int(current.get("sort_order") or 0)
    target_order = int(target.get("sort_order") or 0)
    if current_order == target_order:
        current_order, target_order = index, index + step
    try:
        client.update_room(str(current.get("name")), sort_order=target_order)
        client.update_room(str(target.get("name")), sort_order=current_order)
    except ApiError as exc:
        widgets.flash("error", _original_message(exc), exc.code)
        st.rerun()
    st.rerun()


def _render_room_delete(
    client: AgentClient,
    name: str,
    display: str,
    count: int,
) -> None:
    key = f"confirm_delete_room_{name}"
    hint = (
        f"房间下还有 {count} 台设备，后端会拒绝删除。"
        if count
        else "这个房间是空的，删除后不可恢复。"
    )
    st.warning(f"确定删除房间「{display}」吗？{hint}")
    yes_col, no_col = st.columns(2)
    if yes_col.button(
        "确认删除",
        key=f"room_delete_yes_{name}",
        type="primary",
        use_container_width=True,
    ):
        st.session_state.pop(key, None)
        try:
            client.delete_room(name)
        except ApiError as exc:
            # 非空房间的 409：后端原话 + 明确的处置建议。
            widgets.flash(
                "error",
                f"{_original_message(exc)} —— 先把房间里的设备移到别的房间，或删除设备后再删房间。",
                exc.code,
            )
            st.rerun()
        else:
            widgets.flash("success", f"已删除房间「{display}」。")
            st.rerun()
    if no_col.button("取消", key=f"room_delete_no_{name}", use_container_width=True):
        st.session_state.pop(key, None)
        st.rerun()


def _render_device_card(
    client: AgentClient,
    device: dict[str, Any],
    room_map: dict[str, str],
) -> None:
    device_id = str(device.get("id"))
    functions = list(device.get("functions") or [])
    title = (
        f"{widgets.device_label(device)}　"
        f"{room_map.get(str(device.get('room')), str(device.get('room') or ''))}　"
        f"· {len(functions)} 个功能"
    )
    with st.expander(title):
        if device.get("notes"):
            st.caption(f"备注：{device.get('notes')}")
        if functions:
            st.markdown("**学码功能**")
            for function in functions:
                _render_function_row(client, device_id, function)
        else:
            st.caption("还没有功能。去「学码」页录一个按键。")

        _render_device_edit(client, device, room_map)
        _render_device_delete(client, device)


def _render_function_row(
    client: AgentClient,
    device_id: str,
    function: dict[str, Any],
) -> None:
    function_id = str(function.get("id"))
    name = str(function.get("name") or "未命名功能")
    row, action = st.columns([4, 1])
    with row:
        st.markdown(f"- {name}")
    with action:
        if st.button("删除", key=f"delete_function_{function_id}", use_container_width=True):
            st.session_state[f"confirm_function_{function_id}"] = True
            st.rerun()

    if st.session_state.get(f"confirm_function_{function_id}"):
        st.warning(f"确定删除功能「{name}」吗？会同时删除它的红外码，无法恢复。")
        yes_col, no_col = st.columns(2)
        if yes_col.button(
            "确认删除",
            key=f"confirm_function_yes_{function_id}",
            type="primary",
            use_container_width=True,
        ):
            st.session_state.pop(f"confirm_function_{function_id}", None)
            widgets.call_or_flash(
                client.delete_function,
                device_id,
                function_id,
                ok=f"已删除功能「{name}」。",
            )
            st.rerun()
        if no_col.button(
            "再想想",
            key=f"confirm_function_no_{function_id}",
            use_container_width=True,
        ):
            st.session_state.pop(f"confirm_function_{function_id}", None)
            st.rerun()


def _render_device_edit(
    client: AgentClient,
    device: dict[str, Any],
    room_map: dict[str, str],
) -> None:
    device_id = str(device.get("id"))
    current_room = str(device.get("room") or "")
    room_keys = list(room_map.keys())
    if current_room and current_room not in room_keys:
        room_keys.append(current_room)

    with st.form(f"edit_device_{device_id}"):
        new_name = st.text_input(
            "名称",
            value=str(device.get("name") or ""),
            key=f"edit_name_{device_id}",
        )
        new_room = st.selectbox(
            "房间",
            room_keys,
            index=room_keys.index(current_room) if current_room in room_keys else 0,
            format_func=lambda value: room_map.get(value, value),
            key=f"edit_room_{device_id}",
        )
        new_notes = st.text_input(
            "备注",
            value=str(device.get("notes") or ""),
            key=f"edit_notes_{device_id}",
        )
        submitted = st.form_submit_button("保存修改", use_container_width=True)
        if not submitted:
            return

        changes: dict[str, str] = {}
        if new_name.strip() and new_name.strip() != str(device.get("name") or ""):
            changes["name"] = new_name.strip()
        if new_room != current_room:
            changes["room"] = new_room
        if new_notes.strip() != str(device.get("notes") or ""):
            changes["notes"] = new_notes.strip()
        if not changes:
            st.caption("没有变化。")
            return
        widgets.call_or_flash(
            client.update_device,
            device_id,
            ok="设备已更新。",
            **changes,
        )
        st.rerun()


def _render_device_delete(client: AgentClient, device: dict[str, Any]) -> None:
    device_id = str(device.get("id"))
    key = f"confirm_delete_device_{device_id}"
    if not st.session_state.get(key):
        if st.button("删除设备", key=f"delete_device_{device_id}", use_container_width=True):
            st.session_state[key] = True
            st.rerun()
        return

    st.warning(f"删除「{widgets.device_label(device)}」会一并删除它的所有功能与红外码，无法恢复。")
    yes_col, no_col = st.columns(2)
    if yes_col.button(
        "确认删除",
        key=f"delete_device_yes_{device_id}",
        type="primary",
        use_container_width=True,
    ):
        st.session_state.pop(key, None)
        widgets.call_or_flash(
            client.delete_device,
            device_id,
            ok=f"已删除「{widgets.device_label(device)}」。",
        )
        st.rerun()
    if no_col.button("再想想", key=f"delete_device_no_{device_id}", use_container_width=True):
        st.session_state.pop(key, None)
        st.rerun()


def _original_message(exc: ApiError) -> str:
    """后端 `message` 原话（带具体原因）；没有时退回人话。"""

    return exc.message or errors.error_text(exc)
