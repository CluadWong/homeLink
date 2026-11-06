"""schema 与不变式：v1 最终 DDL、禁建结构、约束、级联。"""

from __future__ import annotations

import sqlite3

import pytest

from jj_butler.core.database import SCHEMA_VERSION

NOW = "2026-01-01T00:00:00Z"


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


def test_user_version_is_one(context):
    assert SCHEMA_VERSION == 1
    assert context.db.user_version() == 1


def test_exactly_seven_tables(context):
    assert table_names(context) == {
        "rooms",
        "devices",
        "device_functions",
        "ir_codes",
        "device_states",
        "audit_logs",
        "config_audit",
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
