"""在线备份 SQLite 主库：服务不停也能拿到一致性快照。

用法：
    python scripts/backup.py                    # 备份主库 + agent 库到 backups/，各保留最近 7 份
    python scripts/backup.py --keep 14          # 保留最近 14 份
    python scripts/backup.py --output-dir D:\\hl-backups
    python scripts/backup.py --skip-agent       # 只备份 homelink.db

主库为 `homelink-<时间戳>.db`，daemon 定时任务库为 `agent-<时间戳>.db`；
两个前缀各自裁剪，互不影响。agent.db 不存在时跳过，不算失败。

原理：用 sqlite3 的 Connection.backup() 在线快照，WAL 未合并也能拿到一致数据；
备份后立即做 integrity_check，坏文件不留进备份目录。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_KEEP = 7


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_db_path() -> Path:
    """与服务端同源：HOMELINK_DB_PATH 优先，否则仓库 data/homelink.db。"""

    raw = os.environ.get("HOMELINK_DB_PATH", "").strip()
    if raw:
        return Path(raw)
    return _repo_root() / "data" / "homelink.db"


def resolve_agent_db_path() -> Path:
    """与 daemon 同源：AGENTD_DB_PATH 优先，否则仓库 data/agent.db。"""

    raw = os.environ.get("AGENTD_DB_PATH", "").strip()
    if raw:
        return Path(raw)
    return _repo_root() / "data" / "agent.db"


def _check_database(path: Path) -> tuple[str, int]:
    """返回 (integrity 结果, schema 版本)，供备份后与恢复后各验一次。"""

    conn = sqlite3.connect(path)
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()
    return integrity, version


def backup_db(db_path: Path, output_dir: Path, keep: int) -> Path:
    """在线快照到 output_dir，校验后裁剪旧份，返回备份文件路径。"""

    return _snapshot_to(db_path, output_dir, keep, "homelink")


def backup_agent_db(db_path: Path, output_dir: Path, keep: int) -> Path | None:
    """备份 daemon 的定时任务库；库不存在时返回 None（daemon 未启动过）。"""

    if not db_path.exists():
        return None
    return _snapshot_to(db_path, output_dir, keep, "agent")


def _snapshot_to(db_path: Path, output_dir: Path, keep: int, prefix: str) -> Path:
    """把 db_path 在线快照为 `<prefix>-<时间戳>.db`，校验后按前缀裁剪。"""

    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在：{db_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.db"

    source = sqlite3.connect(db_path)
    try:
        snapshot = sqlite3.connect(target)
        try:
            source.backup(snapshot)
        finally:
            snapshot.close()
    finally:
        source.close()

    integrity, version = _check_database(target)
    if integrity != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError(f"备份校验失败（{integrity}），已删除坏文件：{target}")

    prune_backups(output_dir, keep, prefix)
    return target


def prune_backups(output_dir: Path, keep: int, prefix: str = "homelink") -> list[Path]:
    """按文件名（即时间戳）倒序保留同前缀的 keep 份，返回被删除的文件。"""

    backups = sorted(output_dir.glob(f"{prefix}-*.db"), reverse=True)
    removed = backups[keep:]
    for old in removed:
        old.unlink(missing_ok=True)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="HomeLink SQLite 在线备份")
    parser.add_argument("--output-dir", type=Path, default=_repo_root() / "backups")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    parser.add_argument(
        "--agent-db",
        type=Path,
        default=None,
        help="daemon 定时任务库，默认取 AGENTD_DB_PATH 或 data/agent.db",
    )
    parser.add_argument("--skip-agent", action="store_true", help="只备份主库，跳过 agent.db")
    args = parser.parse_args()

    keep = max(1, args.keep)
    db_path = resolve_db_path()
    try:
        target = backup_db(db_path, args.output_dir, keep)
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as exc:
        print(f"[ALERT] 备份失败：{exc}", file=sys.stderr)
        return 1

    integrity, version = _check_database(target)
    size_kb = target.stat().st_size / 1024
    print(f"备份完成：{target}")
    print(f"  完整性 {integrity}，schema v{version}，{size_kb:.0f} KB")

    if not args.skip_agent:
        agent_path = args.agent_db if args.agent_db is not None else resolve_agent_db_path()
        try:
            agent_target = backup_agent_db(agent_path, args.output_dir, keep)
        except (RuntimeError, sqlite3.Error) as exc:
            print(f"[ALERT] Agent 库备份失败：{exc}", file=sys.stderr)
            return 1
        if agent_target is None:
            print(f"跳过 Agent 库（不存在）：{agent_path}")
        else:
            agent_integrity, agent_version = _check_database(agent_target)
            print(f"Agent 库备份完成：{agent_target}")
            print(
                f"  完整性 {agent_integrity}，schema v{agent_version}，{agent_target.stat().st_size / 1024:.0f} KB"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
