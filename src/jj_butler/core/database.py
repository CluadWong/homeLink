"""SQLite 连接与重建版最终 DDL。

SCHEMA_VERSION = 1：一次建到位，不复刻历史迁移，也不继承旧库版本。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA_NAME = "jj_butler"

DDL = """
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

CREATE INDEX idx_devices_room ON devices(room);
CREATE INDEX idx_device_functions_device ON device_functions(device_id);
CREATE INDEX idx_ir_codes_function ON ir_codes(function_id, id);
CREATE INDEX idx_device_states_device ON device_states(device_id, id);
CREATE INDEX idx_audit_logs_created ON audit_logs(id);
CREATE INDEX idx_audit_logs_device ON audit_logs(device_id, id);
CREATE INDEX idx_config_audit_created ON config_audit(id);
"""


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
            elif version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"数据库 schema 版本为 {version}，与目标 {SCHEMA_VERSION} 不一致；"
                    "重建版不支持历史迁移，请另写一次性导入脚本"
                )
            else:
                self._add_missing_audit_device_id(conn)
        finally:
            conn.close()

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
