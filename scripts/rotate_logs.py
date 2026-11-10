"""日志轮转：超阈值按 copy-truncate 滚动 logs/ 下的 .log 与 .jsonl。

用法：
    python scripts/rotate_logs.py               # 默认单文件超 5 MB 滚动，保留 3 份
    python scripts/rotate_logs.py --max-mb 10 --keep 5

为什么用 copy-truncate：service.log 与 agent-audit.jsonl 被运行中的进程持有，
Windows 下改名会失败；先复制再清空不影响正在写日志的进程。
代价是复制与清空之间新写入的极少量日志会丢，运维日志可接受。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_KEEP = 3
_SUFFIXES = ("*.log", "*.jsonl")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def rotate_file(path: Path, max_bytes: int, keep: int) -> bool:
    """单文件滚动：超过 max_bytes 才动；返回是否发生了滚动。"""

    if not path.exists() or path.stat().st_size < max_bytes:
        return False

    for index in range(keep - 1, 0, -1):  # .2 → .3，.1 → .2，……腾出 .1
        older = path.with_name(f"{path.name}.{index}")
        newer = path.with_name(f"{path.name}.{index + 1}")
        if older.exists():
            older.replace(newer)
    oldest = path.with_name(f"{path.name}.{keep}")
    if oldest.exists():
        oldest.unlink()

    data = path.read_bytes()
    path.with_name(f"{path.name}.1").write_bytes(data)
    path.write_bytes(b"")
    return True


def rotate_logs(logs_dir: Path, max_bytes: int, keep: int) -> list[Path]:
    """滚动目录下全部日志，返回发生滚动的文件。"""

    rotated: list[Path] = []
    for pattern in _SUFFIXES:
        for path in sorted(logs_dir.glob(pattern)):
            if rotate_file(path, max_bytes, keep):
                rotated.append(path)
    return rotated


def main() -> int:
    parser = argparse.ArgumentParser(description="HomeLink 日志轮转（copy-truncate）")
    parser.add_argument("--logs-dir", type=Path, default=_repo_root() / "logs")
    parser.add_argument("--max-mb", type=float, default=DEFAULT_MAX_BYTES / 1024 / 1024)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    args = parser.parse_args()

    max_bytes = int(args.max_mb * 1024 * 1024)
    rotated = rotate_logs(args.logs_dir, max_bytes, max(1, args.keep))
    if rotated:
        for path in rotated:
            size_kb = path.with_name(f"{path.name}.1").stat().st_size / 1024
            print(f"已滚动：{path.name}（{size_kb:.0f} KB → .1）")
    else:
        print("没有超过阈值的日志，未滚动。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
