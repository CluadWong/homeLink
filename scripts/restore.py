"""从备份恢复 SQLite 主库：先验备份、再挪旧库、后验恢复结果。

用法：
    python scripts/restore.py backups\\homelink-20261110-120000.db
    python scripts/restore.py <备份文件> --force   # 现有库会先改名 .pre-restore-<时间戳>

安全规则：
- 恢复前先对备份文件做 integrity_check，坏备份直接拒绝，不动现有库。
- 目标库存在时必须 --force；现有 .db 与 -wal 会改名保留（.pre-restore-*），-shm 可重建、直接删除。
- 请先停止服务（管理员 PowerShell 运行 scripts\\service\\uninstall-service.ps1 或停任务），
  Windows 下文件被占用时改名会失败，本身就是一层保护。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

try:
    from scripts.backup import _check_database, resolve_db_path
except ImportError:  # 直接以 python scripts/restore.py 运行时，scripts 不在 sys.path 上
    from backup import _check_database, resolve_db_path  # type: ignore[no-redef]


def restore_db(backup_file: Path, db_path: Path, force: bool) -> Path | None:
    """用备份覆盖 db_path，返回旧库改名后的路径（没有旧库时返回 None）。"""

    if not backup_file.exists():
        raise FileNotFoundError(f"备份文件不存在：{backup_file}")

    try:
        integrity, version = _check_database(backup_file)
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"备份文件校验失败：{exc}") from exc
    if integrity != "ok":
        raise RuntimeError(f"备份文件校验失败（{integrity}），已中止，现有库未动")

    if not db_path.exists():
        renamed: Path | None = None
    elif force:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        renamed = db_path.with_name(f"{db_path.name}.pre-restore-{stamp}")
        db_path.rename(renamed)
        wal = db_path.with_name(f"{db_path.name}-wal")
        if wal.exists():
            wal.rename(wal.with_name(f"{wal.name}.pre-restore-{stamp}"))
        shm = db_path.with_name(f"{db_path.name}-shm")
        if shm.exists():
            shm.unlink()
    else:
        raise RuntimeError(f"目标库已存在：{db_path}（加 --force 覆盖，旧库会改名保留）")

    shutil.copy2(backup_file, db_path)

    final_check, final_version = _check_database(db_path)
    if final_check != "ok":
        raise RuntimeError(f"恢复后校验失败（{final_check}），请检查磁盘与备份文件")
    if version != final_version:  # 理论上不可能，防御一次复制损坏
        raise RuntimeError(f"恢复前后 schema 版本不一致（{version} → {final_version}）")
    return renamed


def main() -> int:
    parser = argparse.ArgumentParser(description="HomeLink SQLite 恢复")
    parser.add_argument("backup_file", type=Path)
    parser.add_argument(
        "--db", type=Path, default=None, help="默认取 HOMELINK_DB_PATH 或 data/homelink.db"
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    db_path = args.db if args.db is not None else resolve_db_path()
    try:
        renamed = restore_db(args.backup_file, db_path, args.force)
    except (FileNotFoundError, RuntimeError, sqlite3.Error, OSError) as exc:
        print(f"恢复失败：{exc}", file=sys.stderr)
        return 1

    print(f"恢复完成：{args.backup_file} → {db_path}")
    if renamed is not None:
        print(f"  原库已保留：{renamed}")
    print("  如服务此前已停止，现在可重新启动服务。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
