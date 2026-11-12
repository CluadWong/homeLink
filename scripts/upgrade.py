"""一键升级 / 回滚：版本检查 → 备份 → 迁移 → 校验 → 记版本，失败自动回滚。

用法（先停服务：`scripts\\service\\uninstall-service.ps1`，或停 Docker 容器）：
    python scripts/upgrade.py                 # 升级主库 + agent 库，备份到 backups/
    python scripts/upgrade.py --dry-run       # 只做版本与配置检查，不写任何文件
    python scripts/upgrade.py --skip-agent    # 不动 agent.db
    python scripts/upgrade.py --rollback      # 回滚到上次升级前的库与配置快照

安全规则：
- 升级前把 `homelink.db`、`agent.db`（如存在）在线快照到 `backups/`，并把
  `settings*.yaml` / `devices*.yaml` 复制成 `backups/config-<时间戳>/`。
- 迁移只走 `Database.initialize()` / `ScheduleStore` 的原地补表，迁移前先校验
  配置能解析、库版本不高于程序支持的版本（拒绝降级）。
- 迁移或校验失败 → 用刚生成的快照自动回滚，退出码 1；成功则写 `data/upgrade.json`
  记录前后版本与快照路径，供 `--rollback` 使用。
- 回滚只还原数据与配置；旧版程序需自行切回（旧镜像 / 旧代码），否则新代码会再次
  把库升级。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

try:
    from scripts.backup import backup_agent_db, backup_db, resolve_agent_db_path, resolve_db_path
    from scripts.restore import restore_db
except ImportError:  # 直接以 python scripts/upgrade.py 运行时，scripts 不在 sys.path 上
    from backup import (  # type: ignore[no-redef]
        backup_agent_db,
        backup_db,
        resolve_agent_db_path,
        resolve_db_path,
    )
    from restore import restore_db  # type: ignore[no-redef]

MARKER_NAME = "upgrade.json"
CONFIG_FILES = ("settings.yaml", "settings.local.yaml", "devices.yaml", "devices.local.yaml")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_config_dir() -> Path:
    raw = os.environ.get("HOMELINK_CONFIG_DIR", "").strip()
    return Path(raw) if raw else _repo_root() / "config"


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version("homelink")
    except Exception:
        data = tomllib.loads((_repo_root() / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data.get("project", {}).get("version", "unknown"))


def _read_schema(path: Path) -> int:
    """库不存在视为 0（首次安装，由启动流程建库）。"""

    if not path.exists():
        return 0
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _check_integrity(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def _marker_path(main_db: Path) -> Path:
    return main_db.parent / MARKER_NAME


def _read_marker(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _check_config(config_dir: Path) -> str:
    """用真实加载器校验配置（未知键、坏 YAML、缺文件都会在这里暴露）。"""

    from homelink.core.config import ConfigService

    settings = ConfigService.load(config_dir).settings
    return settings.gateway.mode


def _snapshot_config(config_dir: Path, backup_dir: Path, stamp: str) -> Path | None:
    """把现存配置文件原样复制到 backups/config-<时间戳>/；没有配置时返回 None。"""

    files = [config_dir / name for name in CONFIG_FILES if (config_dir / name).is_file()]
    if not files:
        return None
    target = backup_dir / f"config-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    for path in files:
        shutil.copy2(path, target / path.name)
    return target


def _write_marker(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _restore_config(snapshot: Path, config_dir: Path) -> list[str]:
    restored: list[str] = []
    for path in sorted(snapshot.glob("*.yaml")):
        shutil.copy2(path, config_dir / path.name)
        restored.append(path.name)
    return restored


def run_upgrade(
    *,
    main_db: Path,
    agent_db: Path,
    config_dir: Path | None,
    backup_dir: Path,
    keep: int = 7,
    skip_agent: bool = False,
    dry_run: bool = False,
) -> int:
    """执行一次升级；返回进程退出码（0 成功，1 失败且已尝试回滚）。"""

    from homelink.core.database import SCHEMA_VERSION, Database

    app_version = _app_version()
    main_before = _read_schema(main_db)
    agent_before = _read_schema(agent_db)
    if main_before > SCHEMA_VERSION:
        print(
            f"拒绝升级：homelink.db schema v{main_before} 高于程序支持的 v{SCHEMA_VERSION}；"
            "请回滚程序或使用对应版本",
            file=sys.stderr,
        )
        return 1
    if config_dir is not None:
        try:
            mode = _check_config(config_dir)
        except Exception as exc:
            print(f"配置检查失败，未做任何改动：{exc}", file=sys.stderr)
            return 1
    else:
        mode = "（跳过配置检查）"

    print(f"程序版本 {app_version}，homelink.db v{main_before} → v{SCHEMA_VERSION}")
    print(f"agent.db v{agent_before}（跳过：{skip_agent}），网关 {mode}")
    if dry_run:
        print("dry-run：检查通过，未写任何文件")
        return 0

    marker_path = _marker_path(main_db)
    previous = _read_marker(marker_path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    main_backup: Path | None = None
    agent_backup: Path | None = None
    config_backup: Path | None = None
    try:
        main_backup = backup_db(main_db, backup_dir, keep)
        if not skip_agent:
            agent_backup = backup_agent_db(agent_db, backup_dir, keep)
        if config_dir is not None:
            config_backup = _snapshot_config(config_dir, backup_dir, stamp)
        print(f"已备份：{main_backup}")

        Database(main_db).initialize()
        if agent_backup is not None:
            from homelink_agent.scheduler import ScheduleStore

            ScheduleStore(str(agent_db)).close()

        main_after = _read_schema(main_db)
        if main_after != SCHEMA_VERSION:
            raise RuntimeError(f"homelink.db 迁移后为 v{main_after}，期望 v{SCHEMA_VERSION}")
        if _check_integrity(main_db) != "ok":
            raise RuntimeError("homelink.db 迁移后完整性校验失败")
        agent_after = _read_schema(agent_db) if agent_backup is not None else agent_before
    except BaseException as exc:  # 升到一半失败必须回滚，Ctrl+C 也算
        print(f"升级失败：{exc}", file=sys.stderr)
        if main_backup is None and config_backup is None and agent_backup is None:
            print("失败发生在备份阶段，未做任何改动", file=sys.stderr)
            return 1
        print("开始自动回滚…", file=sys.stderr)
        try:
            if main_backup is not None:
                restore_db(main_backup, main_db, force=True)
            if agent_backup is not None:
                restore_db(agent_backup, agent_db, force=True)
            if config_backup is not None and config_dir is not None:
                restored = _restore_config(config_backup, config_dir)
                print(f"配置已还原：{', '.join(restored) or '无'}", file=sys.stderr)
        except Exception as rollback_exc:
            print(
                f"回滚失败：{rollback_exc}；请手动用 {main_backup} 恢复",
                file=sys.stderr,
            )
            return 1
        print("回滚完成，数据保持升级前状态", file=sys.stderr)
        return 1

    _write_marker(
        marker_path,
        {
            "from_version": previous.get("app_version", "unknown"),
            "app_version": app_version,
            "schema_version": main_after,
            "agent_schema_version": agent_after,
            "main_backup": str(main_backup),
            "agent_backup": None if agent_backup is None else str(agent_backup),
            "config_backup": None if config_backup is None else str(config_backup),
            "upgraded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
    print(f"升级完成：homelink.db v{main_before} → v{main_after}，agent.db v{agent_after}")
    print(f"版本记录：{marker_path}")
    return 0


def run_rollback(*, main_db: Path, agent_db: Path, config_dir: Path | None) -> int:
    """按 data/upgrade.json 还原上次升级前的库与配置；返回退出码。"""

    marker_path = _marker_path(main_db)
    marker = _read_marker(marker_path)
    main_backup = marker.get("main_backup")
    if not main_backup:
        print(f"没有可回滚的记录：{marker_path} 不存在或缺少 main_backup", file=sys.stderr)
        return 1

    try:
        restore_db(Path(str(main_backup)), main_db, force=True)
        agent_backup = marker.get("agent_backup")
        if agent_backup and Path(str(agent_backup)).exists():
            restore_db(Path(str(agent_backup)), agent_db, force=True)
        config_backup = marker.get("config_backup")
        if config_backup and Path(str(config_backup)).is_dir() and config_dir is not None:
            restored = _restore_config(Path(str(config_backup)), config_dir)
            print(f"配置已还原：{', '.join(restored) or '无'}")
    except Exception as exc:
        print(f"回滚失败：{exc}", file=sys.stderr)
        return 1

    marker["rolled_back_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    marker["app_version"] = marker.get("from_version", "unknown")
    _write_marker(marker_path, marker)
    print(f"回滚完成：{main_db}（旧库已改名 .pre-restore-* 保留）")
    print("记得把程序切回旧版本，否则启动时会再次升级数据库")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HomeLink 一键升级 / 回滚")
    parser.add_argument(
        "--main-db", type=Path, default=None, help="默认 HOMELINK_DB_PATH 或 data/homelink.db"
    )
    parser.add_argument(
        "--agent-db", type=Path, default=None, help="默认 AGENTD_DB_PATH 或 data/agent.db"
    )
    parser.add_argument(
        "--config-dir", type=Path, default=None, help="默认 HOMELINK_CONFIG_DIR 或 config/"
    )
    parser.add_argument("--backup-dir", type=Path, default=None, help="默认仓库 backups/")
    parser.add_argument("--keep", type=int, default=7, help="每类备份保留份数")
    parser.add_argument("--skip-agent", action="store_true", help="不迁移、不备份 agent.db")
    parser.add_argument("--dry-run", action="store_true", help="只检查版本与配置")
    parser.add_argument("--rollback", action="store_true", help="按 upgrade.json 回滚")
    args = parser.parse_args(argv)

    main_db = args.main_db or resolve_db_path()
    agent_db = args.agent_db or resolve_agent_db_path()
    config_dir = args.config_dir or _default_config_dir()
    backup_dir = args.backup_dir or _repo_root() / "backups"
    if args.rollback:
        return run_rollback(main_db=main_db, agent_db=agent_db, config_dir=config_dir)
    return run_upgrade(
        main_db=main_db,
        agent_db=agent_db,
        config_dir=config_dir,
        backup_dir=backup_dir,
        keep=max(1, args.keep),
        skip_agent=args.skip_agent,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
