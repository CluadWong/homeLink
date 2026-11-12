"""升级 / 回滚脚本的单元测试：临时库、临时备份目录，不碰真实 data/。"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from scripts import upgrade as upgrade_mod

from homelink.core.database import SCHEMA_VERSION, Database


def _make_db(path: Path, marker: str = "before") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Database(path).initialize()
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS test_marker (v TEXT)")
        conn.execute("DELETE FROM test_marker")
        conn.execute("INSERT INTO test_marker (v) VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _read_marker_value(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return str(conn.execute("SELECT v FROM test_marker").fetchone()[0])
    finally:
        conn.close()


def _run(tmp_path: Path, **kwargs: object) -> int:
    params: dict[str, object] = {
        "main_db": tmp_path / "data" / "homelink.db",
        "agent_db": tmp_path / "data" / "agent.db",
        "config_dir": None,
        "backup_dir": tmp_path / "backups",
    }
    params.update(kwargs)
    return upgrade_mod.run_upgrade(**params)  # type: ignore[arg-type]


def test_upgrade_backs_up_migrates_and_writes_marker(tmp_path: Path) -> None:
    db = tmp_path / "data" / "homelink.db"
    _make_db(db)

    assert _run(tmp_path) == 0

    backups = list((tmp_path / "backups").glob("homelink-*.db"))
    assert len(backups) == 1
    assert _read_marker_value(db) == "before"

    marker = json.loads((tmp_path / "data" / "upgrade.json").read_text(encoding="utf-8"))
    assert marker["schema_version"] == SCHEMA_VERSION
    assert marker["main_backup"] == str(backups[0])
    assert marker["upgraded_at"]


def test_upgrade_rolls_back_when_migration_fails(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "data" / "homelink.db"
    _make_db(db)

    def boom(self) -> None:
        raise RuntimeError("迁移炸了")

    monkeypatch.setattr(Database, "initialize", boom)

    assert _run(tmp_path) == 1

    assert _read_marker_value(db) == "before"  # 回滚后数据仍在
    assert not (tmp_path / "data" / "upgrade.json").exists()
    assert list((tmp_path / "data").glob("homelink.db.pre-restore-*"))


def test_upgrade_refuses_schema_newer_than_program(tmp_path: Path) -> None:
    db = tmp_path / "data" / "homelink.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
    finally:
        conn.close()

    assert _run(tmp_path) == 1
    assert not (tmp_path / "backups").exists()


def test_upgrade_aborts_before_backup_when_config_invalid(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", config_dir)
    settings = config_dir / "settings.yaml"
    settings.write_text(
        settings.read_text(encoding="utf-8") + "\nunknown_key: 1\n", encoding="utf-8"
    )
    db = tmp_path / "data" / "homelink.db"
    _make_db(db)

    assert _run(tmp_path, config_dir=config_dir) == 1
    assert not (tmp_path / "backups").exists()
    assert _read_marker_value(db) == "before"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "data" / "homelink.db"
    _make_db(db)

    assert _run(tmp_path, dry_run=True) == 0
    assert not (tmp_path / "backups").exists()
    assert not (tmp_path / "data" / "upgrade.json").exists()


def test_rollback_restores_database_and_config(tmp_path: Path) -> None:
    db = tmp_path / "data" / "homelink.db"
    _make_db(db, marker="before")
    backups = tmp_path / "backups"
    backups.mkdir()
    snapshot = backups / "homelink-20260101-000000.db"
    shutil.copy2(db, snapshot)

    config_dir = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", config_dir)
    config_backup = backups / "config-20260101-000000"
    config_backup.mkdir()
    shutil.copy2(config_dir / "settings.yaml", config_backup / "settings.yaml")

    (tmp_path / "data" / "upgrade.json").write_text(
        json.dumps(
            {
                "from_version": "0.1.0",
                "app_version": "0.2.0",
                "main_backup": str(snapshot),
                "agent_backup": None,
                "config_backup": str(config_backup),
            }
        ),
        encoding="utf-8",
    )

    # 模拟升级后的变化：库多了一行、配置被改
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO test_marker (v) VALUES ('after')")
        conn.commit()
    finally:
        conn.close()
    original = (config_dir / "settings.yaml").read_text(encoding="utf-8")
    (config_dir / "settings.yaml").write_text(original + "\n# changed\n", encoding="utf-8")

    assert (
        upgrade_mod.run_rollback(
            main_db=db, agent_db=tmp_path / "data" / "agent.db", config_dir=config_dir
        )
        == 0
    )

    assert _read_marker_value(db) == "before"
    assert (config_dir / "settings.yaml").read_text(encoding="utf-8") == original
    marker = json.loads((tmp_path / "data" / "upgrade.json").read_text(encoding="utf-8"))
    assert marker["rolled_back_at"]
    assert marker["app_version"] == "0.1.0"


def test_rollback_without_marker_fails(tmp_path: Path) -> None:
    db = tmp_path / "data" / "homelink.db"
    _make_db(db)

    assert (
        upgrade_mod.run_rollback(main_db=db, agent_db=tmp_path / "agent.db", config_dir=None) == 1
    )
