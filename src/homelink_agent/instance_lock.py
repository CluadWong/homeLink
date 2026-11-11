"""单实例锁：防止两个 daemon 同时触发同一计划。

用操作系统文件锁（Windows `msvcrt` / POSIX `fcntl`），进程退出自动释放，
不会留下需要手工清理的陈旧锁。
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import IO


def _lock(fh: IO[str]) -> None:
    fh.seek(0)
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl = importlib.import_module("fcntl")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fh: IO[str]) -> None:
    fh.seek(0)
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl = importlib.import_module("fcntl")
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class InstanceLock:
    """独占 `<agent.db>.lock`；第二个实例获取失败时抛 RuntimeError。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[str] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115 - 句柄需跨方法持有
        try:
            _lock(fh)
        except OSError as exc:
            fh.close()
            raise RuntimeError(f"已有 agentd 实例在运行（锁文件：{self.path}）") from exc
        self._fh = fh

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            _unlock(fh)
        finally:
            fh.close()

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
