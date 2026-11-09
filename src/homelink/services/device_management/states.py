"""设备状态仓储：只追加、读取取最新，来源只有 unknown / confirmed。

`device_states` 是事件流；`devices` 不存状态。读取一律按写入顺序取最新一条，
因此用 SQLite 的隐式 `rowid` 排序，避免随机 `st_*` 主键影响先后。
"""

from __future__ import annotations

import secrets
from typing import Any

from homelink.core.database import Database
from homelink.core.errors import AppError, ErrorCode
from homelink.core.timeutil import utc_now_iso

_STATE_UNKNOWN = "unknown"
_STATE_CONFIRMED = "confirmed"


def _new_id() -> str:
    return f"st_{secrets.token_hex(4)}"


class DeviceStateStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(
        self,
        device_id: str,
        state: str,
        state_source: str,
        *,
        reason: str | None = None,
        trace_id: str,
    ) -> dict[str, Any]:
        """追加一条状态记录；`state_source` 由调用方决定。"""

        record_id = _new_id()
        created_at = utc_now_iso()
        with self._db.transaction() as conn:
            row = conn.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"设备 {device_id} 不存在")
            conn.execute(
                """
                INSERT INTO device_states(
                    id, device_id, state, state_source, reason, trace_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (record_id, device_id, state, state_source, reason, trace_id, created_at),
            )
        return {
            "id": record_id,
            "device_id": device_id,
            "state": state,
            "state_source": state_source,
            "reason": reason,
            "trace_id": trace_id,
            "created_at": created_at,
        }

    def correct(
        self, device_id: str, state: str, reason: str | None, *, trace_id: str
    ) -> dict[str, Any]:
        """用户校正：来源恒 `confirmed`，`reason` 缺省「用户校正」。"""

        return self.append(
            device_id,
            state,
            _STATE_CONFIRMED,
            reason=reason or "用户校正",
            trace_id=trace_id,
        )

    def list_with_devices(self) -> list[dict[str, Any]]:
        """全部设备与其最新一条状态；没有记录时回退 unknown / null。"""

        with self._db.reader() as conn:
            rows = conn.execute("""
                SELECT d.id AS id, d.name AS name,
                       s.state AS state, s.state_source AS state_source,
                       s.created_at AS updated_at
                FROM devices d
                LEFT JOIN device_states s ON s.rowid = (
                    SELECT s2.rowid FROM device_states s2
                    WHERE s2.device_id = d.id
                    ORDER BY s2.rowid DESC LIMIT 1
                )
                ORDER BY d.created_at, d.id
                """).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "state": row["state"] if row["state"] is not None else _STATE_UNKNOWN,
                "state_source": (
                    row["state_source"] if row["state_source"] is not None else _STATE_UNKNOWN
                ),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
