"""设备仓储：运行期创建、adapter 恒 ir、删设备级联。"""

from __future__ import annotations

import builtins
import json
import secrets
import sqlite3
from collections.abc import Sequence
from typing import Any

from homelink.core.database import Database
from homelink.core.errors import AppError, ErrorCode
from homelink.core.timeutil import utc_now_iso

_ALLOWED_PATCH = {"name", "room", "notes"}


def _new_id() -> str:
    return f"dev_{secrets.token_hex(4)}"


def _device_row(row: sqlite3.Row, functions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "room": row["room"],
        "adapter": row["adapter"],
        "notes": row["notes"],
        "extra": json.loads(row["extra"]),
        "functions": functions,
    }


def _functions_by_device(
    conn: sqlite3.Connection, device_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    if not device_ids:
        return {}
    placeholders = ", ".join("?" for _ in device_ids)
    rows = conn.execute(
        f"""
        SELECT id, device_id, name FROM device_functions
        WHERE device_id IN ({placeholders})
        ORDER BY created_at, id
        """,
        tuple(device_ids),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["device_id"], []).append({"id": row["id"], "name": row["name"]})
    return grouped


class DeviceStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self, room: str | None = None) -> list[dict[str, Any]]:
        with self._db.reader() as conn:
            if room is None:
                rows = conn.execute("SELECT * FROM devices ORDER BY created_at, id").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM devices WHERE room = ? ORDER BY created_at, id",
                    (room,),
                ).fetchall()
            functions = _functions_by_device(conn, [row["id"] for row in rows])
        return [_device_row(row, functions.get(row["id"], [])) for row in rows]

    def get(self, device_id: str) -> dict[str, Any]:
        with self._db.reader() as conn:
            row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"设备 {device_id} 不存在")
            functions = _functions_by_device(conn, [device_id]).get(device_id, [])
        return _device_row(row, functions)

    def create(
        self,
        *,
        name: str,
        room: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        device_id = _new_id()
        with self._db.transaction() as conn:
            if conn.execute("SELECT 1 FROM rooms WHERE name = ?", (room,)).fetchone() is None:
                raise AppError(ErrorCode.NOT_FOUND, f"房间 {room} 不存在")
            conn.execute(
                """
                INSERT INTO devices(id, name, adapter, room, notes, extra, created_at, updated_at)
                VALUES (?, ?, 'ir', ?, ?, '{}', ?, ?)
                """,
                (
                    device_id,
                    name,
                    room,
                    notes,
                    now,
                    now,
                ),
            )
        return self.get(device_id)

    def patch(self, device_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        unknown = set(fields) - _ALLOWED_PATCH
        if unknown:
            raise ValueError(f"不支持修改的字段：{sorted(unknown)}")

        with self._db.transaction() as conn:
            if conn.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone() is None:
                raise AppError(ErrorCode.NOT_FOUND, f"设备 {device_id} 不存在")
            room = fields.get("room")
            if (
                room is not None
                and conn.execute("SELECT 1 FROM rooms WHERE name = ?", (room,)).fetchone() is None
            ):
                raise AppError(ErrorCode.NOT_FOUND, f"房间 {room} 不存在")

            assignments = ", ".join(f"{key} = ?" for key in fields)
            conn.execute(
                f"UPDATE devices SET {assignments}, updated_at = ? WHERE id = ?",
                (*fields.values(), utc_now_iso(), device_id),
            )
        return self.get(device_id)

    def update_extra(self, device_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """仅更新 `extra` 中的 Agent 元数据（type / aliases / risk_level）。"""

        with self._db.transaction() as conn:
            row = conn.execute("SELECT extra FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"设备 {device_id} 不存在")
            extra = json.loads(row["extra"] or "{}")
            extra.update(updates)
            conn.execute(
                "UPDATE devices SET extra = ?, updated_at = ? WHERE id = ?",
                (json.dumps(extra, ensure_ascii=False), utc_now_iso(), device_id),
            )
        return self.get(device_id)

    def delete(self, device_id: str) -> dict[str, Any]:
        with self._db.transaction() as conn:
            if conn.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone() is None:
                raise AppError(ErrorCode.NOT_FOUND, f"设备 {device_id} 不存在")
            functions = int(
                conn.execute(
                    "SELECT COUNT(*) FROM device_functions WHERE device_id = ?",
                    (device_id,),
                ).fetchone()[0]
            )
            codes = int(
                conn.execute(
                    "SELECT COUNT(*) FROM ir_codes WHERE device_id = ?", (device_id,)
                ).fetchone()[0]
            )
            states = int(
                conn.execute(
                    "SELECT COUNT(*) FROM device_states WHERE device_id = ?",
                    (device_id,),
                ).fetchone()[0]
            )
            conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        return {
            "device_id": device_id,
            "functions": functions,
            "codes": codes,
            "states": states,
        }

    def create_function_with_code(
        self, device_id: str, name: str, raw_code: Sequence[int]
    ) -> dict[str, Any]:
        """学码保存：功能与码同事务落库。"""

        function_id = f"fn_{secrets.token_hex(4)}"
        code_id = f"code_{secrets.token_hex(4)}"
        now = utc_now_iso()
        with self._db.transaction() as conn:
            row = conn.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"设备 {device_id} 不存在")

            conn.execute(
                """
                INSERT INTO device_functions(id, device_id, name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (function_id, device_id, name, now, now),
            )
            _insert_code_and_prune(conn, device_id, function_id, code_id, raw_code, now)

        return {"function_id": function_id, "name": name}

    def append_code(self, device_id: str, function_id: str, raw_code: Sequence[int]) -> str:
        """给已有功能追加一条码，并裁剪到最近 3 条。"""

        code_id = f"code_{secrets.token_hex(4)}"
        now = utc_now_iso()
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM device_functions WHERE id = ? AND device_id = ?",
                (function_id, device_id),
            ).fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"功能 {function_id} 不存在")
            _insert_code_and_prune(conn, device_id, function_id, code_id, raw_code, now)
        return code_id

    def get_function(self, function_id: str) -> dict[str, Any]:
        """按 `function_id` 唯一寻址；不存在即 404。"""

        with self._db.reader() as conn:
            row = conn.execute(
                "SELECT id, device_id, name FROM device_functions WHERE id = ?",
                (function_id,),
            ).fetchone()
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, f"功能 {function_id} 不存在")
        return {"id": row["id"], "device_id": row["device_id"], "name": row["name"]}

    def latest_code(self, function_id: str) -> builtins.list[int] | None:
        """该功能最新（`rowid` 最大）的一条码；没有码返回 None。"""

        with self._db.reader() as conn:
            row = conn.execute(
                "SELECT raw_code FROM ir_codes WHERE function_id = ? ORDER BY rowid DESC LIMIT 1",
                (function_id,),
            ).fetchone()
        if row is None:
            return None
        raw = json.loads(row["raw_code"])
        return [int(value) for value in raw]

    def delete_function(self, device_id: str, function_id: str) -> dict[str, Any]:
        with self._db.transaction() as conn:
            if conn.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone() is None:
                raise AppError(ErrorCode.NOT_FOUND, f"设备 {device_id} 不存在")
            row = conn.execute(
                "SELECT 1 FROM device_functions WHERE id = ? AND device_id = ?",
                (function_id, device_id),
            ).fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"功能 {function_id} 不存在")
            codes = int(
                conn.execute(
                    "SELECT COUNT(*) FROM ir_codes WHERE function_id = ?", (function_id,)
                ).fetchone()[0]
            )
            conn.execute("DELETE FROM device_functions WHERE id = ?", (function_id,))
        return {"function_id": function_id, "codes": codes}


def _insert_code_and_prune(
    conn: sqlite3.Connection,
    device_id: str,
    function_id: str,
    code_id: str,
    raw_code: Sequence[int],
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO ir_codes(id, device_id, function_id, raw_code, learned_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (code_id, device_id, function_id, json.dumps(raw_code), now, now),
    )
    _prune_codes(conn, function_id)


def _prune_codes(conn: sqlite3.Connection, function_id: str, keep: int = 3) -> None:
    """每功能只保留最近 3 条码（按写入顺序）。"""

    conn.execute(
        """
        DELETE FROM ir_codes
        WHERE function_id = ?
          AND rowid NOT IN (
              SELECT rowid FROM ir_codes WHERE function_id = ? ORDER BY rowid DESC LIMIT ?
          )
        """,
        (function_id, function_id, keep),
    )
