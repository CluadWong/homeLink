"""Agent 侧持久化：命令请求记录与场景。"""

from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any

from homelink.core.database import Database
from homelink.core.errors import AppError, ErrorCode
from homelink.core.timeutil import utc_now_iso
from homelink.services.agent.catalog import build_device_model
from homelink.services.device_management.devices import DeviceStore


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

    def list_scene_runs(self, scene_id: str, *, limit: int) -> list[dict[str, Any]]:
        """场景最近若干次执行，一次执行（同一 `trace_id`）一组，最新的在前。

        场景 id 形如 `sc_xxxxxx`、`request_id` 形如 `scene-sc_xxxxxx-0-abcd1234`，
        因此用 GLOB 而不是 LIKE：`_` 在 LIKE 里是单字符通配符。
        """

        pattern = f"scene-{scene_id}-*"
        with self._db.reader() as conn:
            trace_rows = conn.execute(
                """
                SELECT trace_id, MIN(created_at) AS started_at
                FROM command_requests
                WHERE request_id GLOB ?
                GROUP BY trace_id
                ORDER BY started_at DESC, trace_id DESC
                LIMIT ?
                """,
                (pattern, limit),
            ).fetchall()
            if not trace_rows:
                return []
            placeholders = ", ".join("?" for _ in trace_rows)
            rows = conn.execute(
                f"SELECT * FROM command_requests WHERE trace_id IN ({placeholders})"
                " AND request_id GLOB ? ORDER BY created_at, request_id",
                (*[row["trace_id"] for row in trace_rows], pattern),
            ).fetchall()

        grouped: dict[str, list[dict[str, Any]]] = {row["trace_id"]: [] for row in trace_rows}
        for row in rows:
            grouped[row["trace_id"]].append(dict(row))
        return [
            {
                "trace_id": row["trace_id"],
                "started_at": row["started_at"],
                "records": grouped[row["trace_id"]],
            }
            for row in trace_rows
        ]


class SceneStore:
    """场景是编排层：动作组只声明能力，目标设备在运行时逐台解析能力。

    - `scene_action_groups` + `scene_action_targets` 是唯一事实来源；
      `items` 只是兼容旧契约的扁平只读视图。
    - 触发器是独立策略对象（manual / schedule / voice / device_state），彼此不依赖。
    - 设备被删除时目标行级联清理；某台设备失去能力只影响它自己，不影响其他设备。
    - 场景 payload 附带风险聚合（`risk_level` / `contains_high_risk`），
      供执行层在不重查设备的情况下做高风险确认闸门。
    """

    def __init__(self, db: Database, devices: DeviceStore) -> None:
        self._db = db
        self._devices = devices

    def list_all(self) -> list[dict[str, Any]]:
        with self._db.reader() as conn:
            rows = conn.execute(
                "SELECT id, name, enabled, created_at, updated_at FROM scenes"
                " ORDER BY created_at, id"
            ).fetchall()
            return [self._with_groups(conn, row) for row in rows]

    def get(self, scene_id: str) -> dict[str, Any]:
        with self._db.reader() as conn:
            row = conn.execute(
                "SELECT id, name, enabled, created_at, updated_at FROM scenes WHERE id = ?",
                (scene_id,),
            ).fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"场景 {scene_id} 不存在")
            return self._with_groups(conn, row)

    def create(
        self,
        *,
        name: str,
        groups: list[dict[str, Any]],
        triggers: list[dict[str, Any]],
        enabled: bool = True,
    ) -> dict[str, Any]:
        scene_id = _new_id("sc")
        now = utc_now_iso()
        with self._db.transaction() as conn:
            if conn.execute("SELECT 1 FROM scenes WHERE name = ?", (name,)).fetchone() is not None:
                raise AppError(ErrorCode.CONFLICT, f"场景名「{name}」已存在")
            conn.execute(
                "INSERT INTO scenes(id, name, enabled, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (scene_id, name, int(enabled), now, now),
            )
            self._insert_groups(conn, scene_id, groups, now)
            self._insert_triggers(conn, scene_id, triggers, now)
        return self.get(scene_id)

    def update(
        self,
        scene_id: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        groups: list[dict[str, Any]] | None = None,
        triggers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """局部更新；`groups` / `triggers` 传入即整组替换，`None` 表示保持原样。"""

        self.get(scene_id)
        now = utc_now_iso()
        with self._db.transaction() as conn:
            if name is not None:
                if (
                    conn.execute(
                        "SELECT 1 FROM scenes WHERE name = ? AND id <> ?", (name, scene_id)
                    ).fetchone()
                    is not None
                ):
                    raise AppError(ErrorCode.CONFLICT, f"场景名「{name}」已存在")
                conn.execute("UPDATE scenes SET name = ? WHERE id = ?", (name, scene_id))
            if enabled is not None:
                conn.execute("UPDATE scenes SET enabled = ? WHERE id = ?", (int(enabled), scene_id))
            conn.execute("UPDATE scenes SET updated_at = ? WHERE id = ?", (now, scene_id))
            if groups is not None:
                conn.execute("DELETE FROM scene_action_groups WHERE scene_id = ?", (scene_id,))
                self._insert_groups(conn, scene_id, groups, now)
            if triggers is not None:
                conn.execute("DELETE FROM scene_triggers WHERE scene_id = ?", (scene_id,))
                self._insert_triggers(conn, scene_id, triggers, now)
        return self.get(scene_id)

    def delete(self, scene_id: str) -> dict[str, Any]:
        scene = self.get(scene_id)
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM scenes WHERE id = ?", (scene_id,))
        return scene

    # ---- 写入辅助 ----

    def _insert_groups(
        self,
        conn: sqlite3.Connection,
        scene_id: str,
        groups: list[dict[str, Any]],
        now: str,
    ) -> None:
        for position, group in enumerate(groups):
            group_id = _new_id("sg")
            conn.execute(
                """
                INSERT INTO scene_action_groups(
                    id, scene_id, name, capability, position, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    scene_id,
                    group["name"],
                    group["capability"],
                    position,
                    now,
                ),
            )
            for target_position, device_id in enumerate(group["targets"]):
                conn.execute(
                    """
                    INSERT INTO scene_action_targets(id, group_id, device_id, position, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (_new_id("st"), group_id, device_id, target_position, now),
                )

    def _insert_triggers(
        self,
        conn: sqlite3.Connection,
        scene_id: str,
        triggers: list[dict[str, Any]],
        now: str,
    ) -> None:
        for position, trigger in enumerate(triggers):
            conn.execute(
                """
                INSERT INTO scene_triggers(
                    id, scene_id, type, config, enabled, position, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("tr"),
                    scene_id,
                    trigger["type"],
                    json.dumps(trigger.get("config") or {}, ensure_ascii=False),
                    int(trigger.get("enabled", True)),
                    position,
                    now,
                    now,
                ),
            )

    # ---- 读取辅助 ----

    def _with_groups(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        group_rows = conn.execute(
            """
            SELECT id, name, capability FROM scene_action_groups
            WHERE scene_id = ? ORDER BY position, id
            """,
            (row["id"],),
        ).fetchall()
        groups: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        for group_row in group_rows:
            target_rows = conn.execute(
                """
                SELECT t.device_id, d.name AS device_name, d.room,
                       (
                           SELECT f.id FROM device_functions f
                           WHERE f.device_id = t.device_id AND f.name = ?
                           ORDER BY f.created_at, f.id LIMIT 1
                       ) AS function_id
                FROM scene_action_targets t
                JOIN devices d ON d.id = t.device_id
                WHERE t.group_id = ?
                ORDER BY t.position, t.id
                """,
                (group_row["capability"], group_row["id"]),
            ).fetchall()
            targets = [
                {
                    "device_id": target["device_id"],
                    "device_name": target["device_name"],
                    "room": target["room"],
                    "function_id": target["function_id"],
                    "command": group_row["capability"],
                }
                for target in target_rows
            ]
            groups.append(
                {
                    "group_id": group_row["id"],
                    "name": group_row["name"],
                    "capability": group_row["capability"],
                    "targets": targets,
                }
            )
            for target in targets:
                items.append(
                    {
                        "device_id": target["device_id"],
                        "function_id": target["function_id"],
                        "command": group_row["capability"],
                    }
                )

        trigger_rows = conn.execute(
            """
            SELECT id, type, config, enabled FROM scene_triggers
            WHERE scene_id = ? ORDER BY position, id
            """,
            (row["id"],),
        ).fetchall()
        triggers = [
            {
                "trigger_id": trigger["id"],
                "type": trigger["type"],
                "enabled": bool(trigger["enabled"]),
                "config": json.loads(trigger["config"]),
            }
            for trigger in trigger_rows
        ]

        scene: dict[str, Any] = {
            "scene_id": row["id"],
            "name": row["name"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "action_groups": groups,
            "triggers": triggers,
            # 兼容旧契约：按动作组 → 目标的顺序扁平化，保留 function_id 与 command。
            "items": items,
        }
        contains_high_risk = any(
            build_device_model(self._devices.get(device_id), None, online=True)["risk_level"]
            == "high"
            for device_id in {item["device_id"] for item in items}
        )
        scene["contains_high_risk"] = contains_high_risk
        scene["risk_level"] = "high" if contains_high_risk else "low"
        return scene
