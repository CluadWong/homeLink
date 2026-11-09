"""SQLite 连接与最终 DDL。

SCHEMA_VERSION = 2：v1 七张业务表保持不变；v2 新增 Agent 侧的场景与命令记录，
老库按 v1 -> v2 原地补表，不重建设备数据。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA_NAME = "homelink"

AGENT_DDL = """
CREATE TABLE IF NOT EXISTS scenes (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0 AND length(name) <= 40),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scene_items (
    id          TEXT PRIMARY KEY,
    scene_id    TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    device_id   TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    function_id TEXT NOT NULL REFERENCES device_functions(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS command_requests (
    request_id    TEXT PRIMARY KEY,
    device_id     TEXT NOT NULL,
    function_id   TEXT NOT NULL,
    command       TEXT NOT NULL,
    parameters    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(parameters)),
    actor         TEXT NOT NULL DEFAULT 'user'
                  CHECK (length(trim(actor)) > 0 AND length(actor) <= 64),
    status        TEXT NOT NULL CHECK (status IN ('running', 'sent', 'failed')),
    error_code    TEXT,
    error_message TEXT,
    trace_id      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

DDL = (
    """
CREATE TABLE rooms (
    name         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE devices (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL CHECK (length(trim(name)) > 0),
    adapter    TEXT NOT NULL DEFAULT 'ir' CHECK (adapter = 'ir'),
    room       TEXT NOT NULL REFERENCES rooms(name) ON UPDATE CASCADE,
    notes      TEXT,
    extra      TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extra)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE device_functions (
    id         TEXT PRIMARY KEY,
    device_id  TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    name       TEXT NOT NULL CHECK (length(trim(name)) > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE ir_codes (
    id          TEXT PRIMARY KEY,
    device_id   TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    function_id TEXT NOT NULL REFERENCES device_functions(id) ON DELETE CASCADE,
    raw_code    TEXT NOT NULL CHECK (json_valid(raw_code) AND json_array_length(raw_code) > 0),
    learned_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE device_states (
    id           TEXT PRIMARY KEY,
    device_id    TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    state        TEXT NOT NULL CHECK (length(state) > 0 AND length(state) <= 64),
    state_source TEXT NOT NULL CHECK (state_source IN ('unknown', 'confirmed')),
    reason       TEXT,
    trace_id     TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    who        TEXT NOT NULL CHECK (who IN ('user', 'system')),
    action     TEXT NOT NULL,
    device_id  TEXT,
    context    TEXT,
    reason     TEXT,
    level      TEXT NOT NULL DEFAULT 'low' CHECK (level = 'low'),
    result     TEXT NOT NULL CHECK (result IN ('success', 'failed')),
    trace_id   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE config_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file       TEXT NOT NULL,
    key_path   TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    who        TEXT NOT NULL CHECK (who IN ('user', 'system')),
    source     TEXT NOT NULL CHECK (source IN ('startup', 'api')),
    result     TEXT NOT NULL CHECK (result IN ('applied', 'rejected', 'rolled_back')),
    error      TEXT,
    trace_id   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

"""
    + AGENT_DDL
    + """

CREATE INDEX idx_devices_room ON devices(room);
CREATE INDEX idx_device_functions_device ON device_functions(device_id);
CREATE INDEX idx_ir_codes_function ON ir_codes(function_id, id);
CREATE INDEX idx_device_states_device ON device_states(device_id, id);
CREATE INDEX idx_audit_logs_created ON audit_logs(id);
CREATE INDEX idx_audit_logs_device ON audit_logs(device_id, id);
CREATE INDEX idx_config_audit_created ON config_audit(id);
CREATE INDEX idx_scene_items_scene ON scene_items(scene_id, position);
CREATE INDEX idx_command_requests_device ON command_requests(device_id, created_at);
"""
)


class Database:
    """单文件 SQLite；每次操作独立连接，不跨线程共享连接。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                conn.executescript(DDL)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version == 1:
                self._migrate_v1_to_v2(conn)
            elif version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"数据库 schema 版本为 {version}，与目标 {SCHEMA_VERSION} 不一致；"
                    "只支持 v1 -> v2 原地补表，其余版本请另写一次性迁移脚本"
                )
            else:
                self._add_missing_audit_device_id(conn)
        finally:
            conn.close()

    @staticmethod
    def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
        """v1 -> v2：只补 Agent 场景与命令记录表，设备/功能/码/状态/审计原样保留。"""

        Database._add_missing_audit_device_id(conn)
        conn.executescript(AGENT_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _add_missing_audit_device_id(conn: sqlite3.Connection) -> None:
        """v1 内调整：老库补 `audit_logs.device_id` 可空列，`user_version` 不变。"""

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_logs)")}
        if "device_id" not in columns:
            conn.execute("ALTER TABLE audit_logs ADD COLUMN device_id TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_device ON audit_logs(device_id, id)"
            )

    def user_version(self) -> int:
        with self.reader() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
