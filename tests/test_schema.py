"""schema 与不变式：v2 最终 DDL、禁建结构、约束、级联。"""

from __future__ import annotations

import sqlite3

import pytest

from homelink.core.database import SCHEMA_VERSION, Database

NOW = "2026-01-01T00:00:00Z"

V1_DDL = """
CREATE TABLE rooms (
    name         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE devices (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
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
    name       TEXT NOT NULL,
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
    state        TEXT NOT NULL,
    state_source TEXT NOT NULL CHECK (state_source IN ('unknown', 'confirmed')),
    reason       TEXT,
    trace_id     TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    who        TEXT NOT NULL CHECK (who IN ('user', 'system')),
    action     TEXT NOT NULL,
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


def table_names(context) -> set[str]:
    with context.db.reader() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {row[0] for row in rows}


def columns_of(context, table: str) -> set[str]:
    with context.db.reader() as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def insert_device(context, device_id: str = "dev_test", room: str = "bedroom") -> str:
    with context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO devices(id, name, adapter, room, notes, extra, created_at, updated_at)
            VALUES (?, '测试灯', 'ir', ?, NULL, '{}', ?, ?)
            """,
            (device_id, room, NOW, NOW),
        )
    return device_id


def insert_function(context, device_id: str, function_id: str = "fn_test") -> str:
    with context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO device_functions(id, device_id, name, created_at, updated_at)
            VALUES (?, ?, '开', ?, ?)
            """,
            (function_id, device_id, NOW, NOW),
        )
    return function_id


def test_v1_database_migrates_in_place(tmp_path):
    db_path = tmp_path / "v1.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(V1_DDL)
        conn.execute("PRAGMA user_version = 1")
        conn.execute(
            "INSERT INTO rooms(name, display_name, created_at) VALUES ('living_room', '客厅', ?)",
            (NOW,),
        )
        conn.execute(
            """
            INSERT INTO devices(id, name, adapter, room, notes, extra, created_at, updated_at)
            VALUES ('dev_old', '旧灯', 'ir', 'living_room', NULL, '{}', ?, ?)
            """,
            (NOW, NOW),
        )
        conn.execute(
            """
            INSERT INTO device_functions(id, device_id, name, created_at, updated_at)
            VALUES ('fn_old', 'dev_old', '开', ?, ?)
            """,
            (NOW, NOW),
        )
        conn.execute(
            """
            INSERT INTO ir_codes(id, device_id, function_id, raw_code, learned_at, created_at)
            VALUES ('code_old', 'dev_old', 'fn_old', '[100, -200]', ?, ?)
            """,
            (NOW, NOW),
        )
        conn.commit()
    finally:
        conn.close()

    db = Database(db_path)
    db.initialize()

    assert db.user_version() == 2
    with db.reader() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {
            "rooms",
            "devices",
            "device_functions",
            "ir_codes",
            "device_states",
            "audit_logs",
            "config_audit",
            "scenes",
            "scene_items",
            "command_requests",
        }
        assert conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ir_codes").fetchone()[0] == 1
        audit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_logs)")}
        assert "device_id" in audit_columns
        conn.execute("INSERT INTO scenes(id, name, created_at) VALUES ('sc_1', '回家', ?)", (NOW,))
        conn.execute(
            """
            INSERT INTO command_requests(
                request_id, device_id, function_id, command, parameters, actor,
                status, error_code, error_message, trace_id, created_at, updated_at
            ) VALUES ('req_1', 'dev_old', 'fn_old', 'open', '{}', 'user',
                      'running', NULL, NULL, 'trace', ?, ?)
            """,
            (NOW, NOW),
        )

    db.initialize()
    assert db.user_version() == 2


def test_user_version_is_two(context):
    assert SCHEMA_VERSION == 2
    assert context.db.user_version() == 2


def test_exactly_ten_tables(context):
    assert table_names(context) == {
        "rooms",
        "devices",
        "device_functions",
        "ir_codes",
        "device_states",
        "audit_logs",
        "config_audit",
        "scenes",
        "scene_items",
        "command_requests",
    }


def test_forbidden_columns_absent(context):
    devices = columns_of(context, "devices")
    forbidden_devices = {
        "capability",
        "kind",
        "source",
        "enabled",
        "state",
        "state_source",
        "state_updated_at",
        "protocol",
        "ir_codes_path",
        "params_schema",
        "risk_level",
    }
    assert devices.isdisjoint(forbidden_devices)

    functions = columns_of(context, "device_functions")
    assert functions == {"id", "device_id", "name", "created_at", "updated_at"}

    codes = columns_of(context, "ir_codes")
    assert codes == {"id", "device_id", "function_id", "raw_code", "learned_at", "created_at"}

    audit = columns_of(context, "audit_logs")
    assert audit == {
        "id",
        "who",
        "action",
        "device_id",
        "context",
        "reason",
        "level",
        "result",
        "trace_id",
        "created_at",
    }
    assert "params" not in audit


def test_adapter_only_ir(context):
    with pytest.raises(sqlite3.IntegrityError), context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO devices(id, name, adapter, room, created_at, updated_at)
            VALUES ('dev_mqtt', '灯', 'mqtt', 'bedroom', ?, ?)
            """,
            (NOW, NOW),
        )


def test_device_requires_existing_room(context):
    with pytest.raises(sqlite3.IntegrityError):
        insert_device(context, device_id="dev_noroom", room="ghost_room")


def test_ir_codes_reject_empty_and_invalid_waveform(context):
    device_id = insert_device(context)
    function_id = insert_function(context, device_id)

    for raw_code in ("[]", "not-json"):
        with pytest.raises(sqlite3.IntegrityError), context.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO ir_codes(id, device_id, function_id, raw_code, learned_at, created_at)
                VALUES ('code_bad', ?, ?, ?, ?, ?)
                """,
                (device_id, function_id, raw_code, NOW, NOW),
            )


def test_state_source_rejects_estimated(context):
    device_id = insert_device(context)
    with pytest.raises(sqlite3.IntegrityError), context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO device_states(
                id, device_id, state, state_source, reason, trace_id, created_at
            ) VALUES ('st_estimated', ?, 'on', 'estimated', NULL, 'trace', ?)
            """,
            (device_id, NOW),
        )


def test_state_must_be_non_empty_and_bounded(context):
    device_id = insert_device(context)
    for state in ("", "x" * 65):
        with pytest.raises(sqlite3.IntegrityError), context.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO device_states(
                    id, device_id, state, state_source, reason, trace_id, created_at
                ) VALUES ('st_bad', ?, ?, 'unknown', NULL, 'trace', ?)
                """,
                (device_id, state, NOW),
            )


def test_audit_logs_level_defaults_low_and_who_is_user_or_system(context):
    with context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs(who, action, context, reason, result, trace_id, created_at)
            VALUES ('user', 'control', '{"function_id": "fn_x"}', NULL, 'success', 'trace', ?)
            """,
            (NOW,),
        )
        level = conn.execute("SELECT level FROM audit_logs").fetchone()[0]
    assert level == "low"

    with pytest.raises(sqlite3.IntegrityError), context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs(who, action, result, trace_id, created_at)
            VALUES ('agent', 'control', 'success', 'trace', ?)
            """,
            (NOW,),
        )

    with pytest.raises(sqlite3.IntegrityError), context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs(who, action, result, trace_id, created_at)
            VALUES ('user', 'control', 'denied', 'trace', ?)
            """,
            (NOW,),
        )


def test_config_audit_source_limited_to_startup_and_api(context):
    for source in ("startup", "api"):
        with context.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO config_audit(
                    file, key_path, old_value, new_value, who, source,
                    result, error, trace_id, created_at
                ) VALUES ('settings.local.yaml', 'gateway.ir.base_url', NULL, 'http://x',
                          'user', ?, 'applied', NULL, 'trace', ?)
                """,
                (source, NOW),
            )

    with pytest.raises(sqlite3.IntegrityError), context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO config_audit(
                file, key_path, who, source, result, trace_id, created_at
            ) VALUES ('settings.local.yaml', 'x', 'system', 'cli', 'applied', 'trace', ?)
            """,
            (NOW,),
        )

    with pytest.raises(sqlite3.IntegrityError), context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO config_audit(
                file, key_path, who, source, result, trace_id, created_at
            ) VALUES ('settings.local.yaml', 'x', 'system', 'agent', 'applied', 'trace', ?)
            """,
            (NOW,),
        )

    with pytest.raises(sqlite3.IntegrityError), context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO config_audit(
                file, key_path, who, source, result, trace_id, created_at
            ) VALUES ('settings.local.yaml', 'x', 'system', 'api', 'ok', 'trace', ?)
            """,
            (NOW,),
        )


def test_delete_device_cascades_functions_codes_states(context):
    device_id = insert_device(context)
    function_id = insert_function(context, device_id)
    with context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO ir_codes(id, device_id, function_id, raw_code, learned_at, created_at)
            VALUES ('code_1', ?, ?, '[100, -200]', ?, ?)
            """,
            (device_id, function_id, NOW, NOW),
        )
        conn.execute(
            """
            INSERT INTO device_states(id, device_id, state, state_source, reason, trace_id, created_at)
            VALUES ('st_1', ?, 'unknown', 'unknown', 'control:fn_test', 'trace', ?)
            """,
            (device_id, NOW),
        )
        conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))

        for table in ("device_functions", "ir_codes", "device_states"):
            remaining = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert remaining == 0, table
