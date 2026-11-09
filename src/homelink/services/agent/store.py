"""Agent 侧持久化：命令请求记录与场景。"""

from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any

from homelink.core.database import Database
from homelink.core.errors import AppError, ErrorCode
from homelink.core.timeutil import utc_now_iso


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


class CommandRequestStore:
    """`command_requests`：幂等键、执行结果与状态查询的唯一来源。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._db.reader() as conn:
            row = conn.execute(
                "SELECT * FROM command_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def create(
        self,
        *,
        request_id: str,
        device_id: str,
        function_id: str,
        command: str,
        parameters: dict[str, Any],
        actor: str,
        trace_id: str,
    ) -> None:
        now = utc_now_iso()
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO command_requests(
                    request_id, device_id, function_id, command, parameters, actor,
                    status, trace_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)
                """,
                (
                    request_id,
                    device_id,
                    function_id,
                    command,
                    json.dumps(parameters, ensure_ascii=False),
                    actor,
                    trace_id,
                    now,
                    now,
                ),
            )

    def finish(
        self,
        request_id: str,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE command_requests
                SET status = ?, error_code = ?, error_message = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (status, error_code, error_message, utc_now_iso(), request_id),
            )


class SceneStore:
    """场景 = 一组按顺序执行的功能；不引入触发条件与调度。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def list_all(self) -> list[dict[str, Any]]:
        with self._db.reader() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at FROM scenes ORDER BY created_at, id"
            ).fetchall()
            scenes = [self._with_items(conn, row) for row in rows]
        return scenes

    def get(self, scene_id: str) -> dict[str, Any]:
        with self._db.reader() as conn:
            row = conn.execute(
                "SELECT id, name, created_at FROM scenes WHERE id = ?", (scene_id,)
            ).fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"场景 {scene_id} 不存在")
            return self._with_items(conn, row)

    def create(self, name: str, items: list[dict[str, str]]) -> dict[str, Any]:
        scene_id = _new_id("sc")
        now = utc_now_iso()
        with self._db.transaction() as conn:
            if conn.execute("SELECT 1 FROM scenes WHERE name = ?", (name,)).fetchone() is not None:
                raise AppError(ErrorCode.CONFLICT, f"场景名「{name}」已存在")
            conn.execute(
                "INSERT INTO scenes(id, name, created_at) VALUES (?, ?, ?)",
                (scene_id, name, now),
            )
            for position, item in enumerate(items):
                conn.execute(
                    """
                    INSERT INTO scene_items(
                        id, scene_id, device_id, function_id, position, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id("si"),
                        scene_id,
                        item["device_id"],
                        item["function_id"],
                        position,
                        now,
                    ),
                )
        return self.get(scene_id)

    def delete(self, scene_id: str) -> dict[str, Any]:
        scene = self.get(scene_id)
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM scenes WHERE id = ?", (scene_id,))
        return scene

    @staticmethod
    def _with_items(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        items = conn.execute(
            """
            SELECT i.device_id, i.function_id, i.position, f.name AS command
            FROM scene_items i
            JOIN device_functions f ON f.id = i.function_id
            WHERE i.scene_id = ?
            ORDER BY i.position
            """,
            (row["id"],),
        ).fetchall()
        return {
            "scene_id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "items": [
                {
                    "device_id": item["device_id"],
                    "function_id": item["function_id"],
                    "command": item["command"],
                }
                for item in items
            ],
        }
