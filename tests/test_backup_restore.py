"""运维脚本（backup / restore / rotate_logs）的单元测试，全部基于临时文件。"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest
from scripts import backup as backup_mod
from scripts import restore as restore_mod
from scripts import rotate_logs as rotate_mod


def _make_db(path: Path, marker: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        conn.execute("DELETE FROM t")
        conn.execute("INSERT INTO t (v) VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _read_marker(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT v FROM t").fetchone()
    finally:
        conn.close()
    assert row is not None
    return str(row[0])


def test_backup_creates_verified_snapshot_and_prunes(tmp_path: Path) -> None:
    db = tmp_path / "homelink.db"
    _make_db(db, "v1")
    out = tmp_path / "backups"
    out.mkdir()
    for stale in range(5):  # 预置 5 份旧备份，验证裁剪
        (out / f"homelink-2026010{stale}-000000.db").write_bytes(b"old")

    target = backup_mod.backup_db(db, out, keep=3)

    assert target.exists()
    assert _read_marker(target) == "v1"
    assert backup_mod._check_database(target)[0] == "ok"
    remaining = sorted(out.glob("homelink-*.db"))
    assert len(remaining) == 3  # 7 份里保留最近 3 份
    assert target in remaining


def test_backup_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        backup_mod.backup_db(tmp_path / "nope.db", tmp_path / "out", keep=1)


def test_backup_agent_db_roundtrip_keeps_schedules(tmp_path: Path) -> None:
    agent_db = tmp_path / "agent.db"
    _make_db(agent_db, "schedule-1")
    out = tmp_path / "backups"

    target = backup_mod.backup_agent_db(agent_db, out, keep=2)

    assert target is not None
    assert target.name.startswith("agent-")
    assert _read_marker(target) == "schedule-1"
    agent_db.unlink()  # 模拟 agent.db 丢失
    restore_mod.restore_db(target, agent_db, force=True)
    assert _read_marker(agent_db) == "schedule-1"


def test_backup_agent_db_missing_returns_none(tmp_path: Path) -> None:
    assert backup_mod.backup_agent_db(tmp_path / "none.db", tmp_path / "out", keep=1) is None
    assert not (tmp_path / "out").exists()


def test_prune_backups_keeps_prefixes_separate(tmp_path: Path) -> None:
    out = tmp_path / "backups"
    out.mkdir()
    for i in range(3):
        (out / f"homelink-2026010{i}-000000.db").write_bytes(b"old")
    (out / "agent-20260101-000000.db").write_bytes(b"agent")

    backup_mod.prune_backups(out, keep=1, prefix="agent")

    assert len(list(out.glob("homelink-*.db"))) == 3
    assert len(list(out.glob("agent-*.db"))) == 1


def test_restore_agent_backup_from_cli_targets_agent_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_db = tmp_path / "agent.db"
    _make_db(agent_db, "schedule-2")
    target = backup_mod.backup_agent_db(agent_db, tmp_path / "backups", keep=1)
    assert target is not None
    agent_db.unlink()

    monkeypatch.setenv("AGENTD_DB_PATH", str(agent_db))
    monkeypatch.setattr(sys, "argv", ["restore.py", str(target)])

    assert restore_mod.main() == 0
    assert _read_marker(agent_db) == "schedule-2"


def test_restore_roundtrip_with_force_and_wal_sidecar(tmp_path: Path) -> None:
    db = tmp_path / "homelink.db"
    _make_db(db, "before")
    out = tmp_path / "backups"
    target = backup_mod.backup_db(db, out, keep=1)
    _make_db(db, "after")  # 模拟库被写坏/写脏
    (tmp_path / "homelink.db-wal").write_bytes(b"stale")  # 遗留 WAL 侧车

    renamed = restore_mod.restore_db(target, db, force=True)

    assert renamed is not None and renamed.exists()
    assert not db.with_name("homelink.db-wal").exists()  # 侧车已随旧库挪走
    assert _read_marker(db) == "before"  # 数据回到备份时点


def test_restore_refuses_existing_target_without_force(tmp_path: Path) -> None:
    db = tmp_path / "homelink.db"
    _make_db(db, "current")
    backup_file = tmp_path / "homelink-20260101-000000.db"
    _make_db(backup_file, "old")

    with pytest.raises(RuntimeError, match="--force"):
        restore_mod.restore_db(backup_file, db, force=False)
    assert _read_marker(db) == "current"  # 现有库未被触碰


def test_restore_rejects_corrupt_backup(tmp_path: Path) -> None:
    db = tmp_path / "homelink.db"
    _make_db(db, "current")
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database")

    with pytest.raises(RuntimeError, match="校验失败"):
        restore_mod.restore_db(bad, db, force=True)
    assert _read_marker(db) == "current"


def test_resolve_db_path_honors_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMELINK_DB_PATH", str(tmp_path / "custom.db"))
    assert backup_mod.resolve_db_path() == tmp_path / "custom.db"

    monkeypatch.delenv("HOMELINK_DB_PATH", raising=False)
    os.environ.pop("HOMELINK_DB_PATH", None)
    expected = backup_mod._repo_root() / "data" / "homelink.db"
    assert backup_mod.resolve_db_path() == expected


def test_rotate_skips_small_files_and_respects_keep(tmp_path: Path) -> None:
    small = tmp_path / "small.log"
    small.write_text("tiny")
    assert not rotate_mod.rotate_file(small, max_bytes=100, keep=3)
    assert small.read_text() == "tiny"
    assert not list(tmp_path.glob("small.log.*"))


def test_rotate_big_file_copy_truncates_and_shifts(tmp_path: Path) -> None:
    log = tmp_path / "agent-audit.jsonl"
    log.write_bytes(b"first" * 100)
    assert rotate_mod.rotate_file(log, max_bytes=100, keep=3)
    assert log.read_bytes() == b""  # 原文件被清空，进程可继续追加
    first = tmp_path / "agent-audit.jsonl.1"
    assert first.read_bytes() == b"first" * 100

    log.write_bytes(b"second" * 100)  # 第二轮：.1 → .2，新内容进 .1
    assert rotate_mod.rotate_file(log, max_bytes=100, keep=3)
    assert (tmp_path / "agent-audit.jsonl.2").read_bytes() == b"first" * 100
    assert (tmp_path / "agent-audit.jsonl.1").read_bytes() == b"second" * 100

    log.write_bytes(b"third" * 100)
    log.with_name("agent-audit.jsonl.3").write_bytes(b"oldest")  # 触发 keep=3 上限
    assert rotate_mod.rotate_file(log, max_bytes=100, keep=3)
    assert not (tmp_path / "agent-audit.jsonl.3").exists()  # 最旧份被挤出
