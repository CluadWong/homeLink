"""房间仓储：CRUD、device_count、非空不可删、重命名同步设备。"""

from __future__ import annotations

import sqlite3
from typing import Any

from homelink.core.database import Database
from homelink.core.errors import AppError, ErrorCode
from homelink.core.timeutil import utc_now_iso

_ALLOWED_PATCH = {"name", "display_name", "sort_order"}


def _room_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "display_name": row["display_name"],
        "sort_order": row["sort_order"],
        "device_count": row["device_count"],
    }


class RoomStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self) -> list[dict[str, Any]]:
        with self._db.reader() as conn:
            rows = conn.execute("""
                SELECT r.name, r.display_name, r.sort_order,
                       (SELECT COUNT(*) FROM devices d WHERE d.room = r.name) AS device_count
                FROM rooms r
                ORDER BY r.sort_order, r.name
                """).fetchall()
        return [_room_row(row) for row in rows]

    def get(self, name: str) -> dict[str, Any]:
        with self._db.reader() as conn:
            row = conn.execute(
                """
                SELECT r.name, r.display_name, r.sort_order,
                       (SELECT COUNT(*) FROM devices d WHERE d.room = r.name) AS device_count
                FROM rooms r WHERE r.name = ?
                """,
                (name,),
            ).fetchone()
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, f"房间 {name} 不存在")
        return _room_row(row)

    def create(self, *, name: str, display_name: str, sort_order: int) -> dict[str, Any]:
        with self._db.transaction() as conn:
            if conn.execute("SELECT 1 FROM rooms WHERE name = ?", (name,)).fetchone():
                raise AppError(ErrorCode.CONFLICT, f"房间 {name} 已存在")
            conn.execute(
                "INSERT INTO rooms(name, display_name, sort_order, created_at) VALUES (?, ?, ?, ?)",
                (name, display_name, sort_order, utc_now_iso()),
            )
        return self.get(name)

    def patch(self, name: str, fields: dict[str, Any]) -> dict[str, Any]:
        unknown = set(fields) - _ALLOWED_PATCH
        if unknown:
            raise ValueError(f"不支持修改的字段：{sorted(unknown)}")

        target = fields.get("name", name)
        with self._db.transaction() as conn:
            row = conn.execute("SELECT name FROM rooms WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"房间 {name} 不存在")
            if (
                target != name
                and conn.execute("SELECT 1 FROM rooms WHERE name = ?", (target,)).fetchone()
            ):
                raise AppError(ErrorCode.CONFLICT, f"房间 {target} 已存在")

            assignments = ", ".join(f"{key} = ?" for key in fields)
            # devices.room 的外键带 ON UPDATE CASCADE，重命名会同步其下设备。
            conn.execute(
                f"UPDATE rooms SET {assignments} WHERE name = ?",
                (*fields.values(), name),
            )
        return self.get(target)

    def delete(self, name: str) -> dict[str, Any]:
        with self._db.transaction() as conn:
            row = conn.execute("SELECT 1 FROM rooms WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"房间 {name} 不存在")
            count = int(
                conn.execute("SELECT COUNT(*) FROM devices WHERE room = ?", (name,)).fetchone()[0]
            )
            if count:
                raise AppError(ErrorCode.CONFLICT, f"房间 {name} 下还有 {count} 台设备，不能删除")
            conn.execute("DELETE FROM rooms WHERE name = ?", (name,))
        return {"name": name}
