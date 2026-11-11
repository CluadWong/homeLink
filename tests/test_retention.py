"""启动保留期清理：按 created_at 删除旧行，失败不阻断启动。"""

from __future__ import annotations

from pathlib import Path

import yaml

from homelink.bootstrap import context as context_module
from homelink.bootstrap.context import build_context
from homelink.core.database import Database
from homelink.core.timeutil import utc_now_iso

OLD = "2000-01-01T00:00:00Z"


def _write_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "gateway": {
            "mode": "mock",
            "ir": {"base_url": "", "username": "", "password": ""},
            "probe_timeout_seconds": 3,
        },
        "state": {"retention_days": 90},
        "audit": {"retention_days": 365},
        "commands": {"retention_days": 180},
        "api": {"token": ""},
    }
    devices = {"rooms": [{"name": "bedroom", "display_name": "卧室", "sort_order": 1}]}
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (config_dir / "devices.yaml").write_text(
        yaml.safe_dump(devices, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _seed_history(db_path: Path) -> None:
    db = Database(db_path)
    db.initialize()
    recent = utc_now_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO rooms(name, display_name, sort_order, created_at) VALUES (?, ?, ?, ?)",
            ("bedroom", "卧室", 1, recent),
        )
        conn.execute(
            """
            INSERT INTO devices(id, name, adapter, room, notes, extra, created_at, updated_at)
            VALUES ('dev_old', '旧灯', 'ir', 'bedroom', NULL, '{}', ?, ?)
            """,
            (OLD, OLD),
        )
        conn.execute(
            """
            INSERT INTO device_states(id, device_id, state, state_source, reason, trace_id, created_at)
            VALUES ('st_old', 'dev_old', 'on', 'confirmed', NULL, 't', ?)
            """,
            (OLD,),
        )
        conn.execute(
            """
            INSERT INTO device_states(id, device_id, state, state_source, reason, trace_id, created_at)
            VALUES ('st_new', 'dev_old', 'off', 'confirmed', NULL, 't', ?)
            """,
            (recent,),
        )
        conn.execute(
            """
            INSERT INTO audit_logs(who, action, context, reason, level, result, trace_id, created_at)
            VALUES ('system', 'old.control', NULL, NULL, 'low', 'success', 't', ?)
            """,
            (OLD,),
        )
        conn.execute(
            """
            INSERT INTO audit_logs(who, action, context, reason, level, result, trace_id, created_at)
            VALUES ('system', 'new.control', NULL, NULL, 'low', 'success', 't', ?)
            """,
            (recent,),
        )
        conn.execute(
            """
            INSERT INTO command_requests(request_id, device_id, function_id, command, status, trace_id, created_at, updated_at)
            VALUES ('cmd_old', 'dev_old', 'fn', 'toggle', 'sent', 't-old', ?, ?)
            """,
            (OLD, OLD),
        )
        conn.execute(
            """
            INSERT INTO command_requests(request_id, device_id, function_id, command, status, trace_id, created_at, updated_at)
            VALUES ('cmd_new', 'dev_old', 'fn', 'toggle', 'sent', 't-new', ?, ?)
            """,
            (recent, recent),
        )


def test_retention_purges_only_rows_older_than_cutoff(tmp_path):
    config_dir = tmp_path / "config"
    db_path = tmp_path / "data" / "test.db"
    _write_config(config_dir)
    _seed_history(db_path)

    context = build_context(config_dir, db_path)
    with context.db.reader() as conn:
        states = conn.execute("SELECT id FROM device_states ORDER BY id").fetchall()
        audits = conn.execute("SELECT action FROM audit_logs ORDER BY id").fetchall()
        commands = conn.execute(
            "SELECT request_id FROM command_requests ORDER BY request_id"
        ).fetchall()

    assert [row["id"] for row in states] == ["st_new"]
    assert [row["action"] for row in audits] == ["new.control"]
    assert [row["request_id"] for row in commands] == ["cmd_new"]


def test_retention_defaults_command_days_to_180(tmp_path):
    config_dir = tmp_path / "config"
    _write_config(config_dir)
    settings_path = config_dir / "settings.yaml"
    raw = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    raw.pop("commands")
    settings_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    context = build_context(config_dir, tmp_path / "data" / "test.db")

    assert context.config.settings.command_retention_days == 180


def test_retention_failure_does_not_block_startup(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    _write_config(config_dir)

    def _boom(*args, **kwargs):
        raise RuntimeError("disk unavailable")

    monkeypatch.setattr(context_module, "purge_retention", _boom)
    context = build_context(config_dir, tmp_path / "data" / "test.db")

    assert context.rooms.list()
    assert {room["name"] for room in context.rooms.list()} == {"bedroom"}


def test_retention_keeps_recent_rows_on_repeated_startup(tmp_path):
    config_dir = tmp_path / "config"
    db_path = tmp_path / "data" / "test.db"
    _write_config(config_dir)
    _seed_history(db_path)

    build_context(config_dir, db_path)
    build_context(config_dir, db_path)

    db = Database(db_path)
    with db.reader() as conn:
        count = conn.execute("SELECT COUNT(*) FROM device_states").fetchone()[0]
    assert count == 1
