"""定时调度（P3）：Agent 侧维护 schedules 表，到点经 MCP 执行场景。

设计取舍：
- 计划存独立 SQLite（默认 `data/agent.db`），不碰 `homelink.db`，执行层不知道调度的存在；
- 只支持 5 段 cron（分 时 日 月 周），支持 `*`、数字、`a-b`、`*/n` 与逗号列表；
  周字段 0 或 7 表示周日，1 表示周一，与标准 cron 一致；
- 到点最多执行一次，失败只写审计、不自动重发（红外无回传，重复发送的代价更大）。
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

from homelink_agent.audit import AuditLog

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id          TEXT PRIMARY KEY,
    scene_id    TEXT NOT NULL,
    cron        TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    confirm     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    last_run_at TEXT,
    last_status TEXT,
    last_error  TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',
    trigger_id  TEXT
)
"""


@dataclass(frozen=True)
class Schedule:
    id: str
    scene_id: str
    cron: str
    enabled: bool
    confirm: bool
    created_at: str
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    source: str = "manual"
    trigger_id: str | None = None


@dataclass(frozen=True)
class SceneTriggerSchedule:
    """后端场景里的一个定时触发器，同步成本地计划后由调度器到点执行。"""

    scene_id: str
    trigger_id: str
    cron: str
    enabled: bool = True


RunScene = Callable[[Schedule], Awaitable[None]]
LoadScenePlans = Callable[[], Awaitable[list[SceneTriggerSchedule]]]


def _row_to_schedule(row: sqlite3.Row) -> Schedule:
    return Schedule(
        id=str(row["id"]),
        scene_id=str(row["scene_id"]),
        cron=str(row["cron"]),
        enabled=bool(row["enabled"]),
        confirm=bool(row["confirm"]),
        created_at=str(row["created_at"]),
        last_run_at=None if row["last_run_at"] is None else str(row["last_run_at"]),
        last_status=None if row["last_status"] is None else str(row["last_status"]),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
        source=str(row["source"]),
        trigger_id=None if row["trigger_id"] is None else str(row["trigger_id"]),
    )


class ScheduleStore:
    """schedules 表读写；单连接 + 线程锁，测试可用 `:memory:`。"""

    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """旧库没有 source / trigger_id：补列，已有手工计划视为 source='manual'。"""

        columns = {str(row[1]) for row in self._conn.execute("PRAGMA table_info(schedules)")}
        if "source" not in columns:
            self._conn.execute(
                "ALTER TABLE schedules ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
            )
        if "trigger_id" not in columns:
            self._conn.execute("ALTER TABLE schedules ADD COLUMN trigger_id TEXT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def list_all(self) -> list[Schedule]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM schedules ORDER BY created_at, id").fetchall()
        return [_row_to_schedule(row) for row in rows]

    def add(
        self,
        *,
        scene_id: str,
        cron: str,
        enabled: bool,
        confirm: bool,
        now: datetime | None = None,
    ) -> Schedule:
        schedule = Schedule(
            id=uuid.uuid4().hex[:12],
            scene_id=scene_id,
            cron=cron,
            enabled=enabled,
            confirm=confirm,
            created_at=(now or datetime.now()).isoformat(timespec="seconds"),
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO schedules(id, scene_id, cron, enabled, confirm, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    schedule.id,
                    schedule.scene_id,
                    schedule.cron,
                    int(schedule.enabled),
                    int(schedule.confirm),
                    schedule.created_at,
                ),
            )
            self._conn.commit()
        return schedule

    def delete(self, schedule_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            self._conn.commit()
        return cursor.rowcount > 0

    def mark_run(self, schedule_id: str, *, status: str, error: str | None, at: datetime) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE schedules SET last_run_at = ?, last_status = ?, last_error = ?"
                " WHERE id = ?",
                (at.isoformat(timespec="seconds"), status, error, schedule_id),
            )
            self._conn.commit()

    def sync_scene_triggers(self, desired: list[SceneTriggerSchedule]) -> dict[str, int]:
        """把后端场景的定时触发器对齐到本地计划表。

        - 以 `(scene_id, trigger_id)` 为幂等键，只动 `source='scene'` 的行；
        - 新增 / 改 cron 或 enabled / 删除已消失的触发器；
        - 已存在行的 `last_run_at` 保留，避免同步把「同一分钟已执行」抹掉。
        """

        added = updated = removed = 0
        with self._lock:
            rows = self._conn.execute("SELECT * FROM schedules WHERE source = 'scene'").fetchall()
            existing = {
                (str(row["scene_id"]), str(row["trigger_id"])): row
                for row in rows
                if row["trigger_id"] is not None
            }
            wanted = {(item.scene_id, item.trigger_id): item for item in desired}
            for key, item in wanted.items():
                row = existing.get(key)
                if row is None:
                    self._conn.execute(
                        "INSERT INTO schedules"
                        "(id, scene_id, cron, enabled, confirm, created_at, source, trigger_id)"
                        " VALUES (?, ?, ?, ?, 0, ?, 'scene', ?)",
                        (
                            uuid.uuid4().hex[:12],
                            item.scene_id,
                            item.cron,
                            int(item.enabled),
                            datetime.now().isoformat(timespec="seconds"),
                            item.trigger_id,
                        ),
                    )
                    added += 1
                elif str(row["cron"]) != item.cron or bool(row["enabled"]) != item.enabled:
                    self._conn.execute(
                        "UPDATE schedules SET cron = ?, enabled = ? WHERE id = ?",
                        (item.cron, int(item.enabled), str(row["id"])),
                    )
                    updated += 1
            for key, row in existing.items():
                if key not in wanted:
                    self._conn.execute("DELETE FROM schedules WHERE id = ?", (str(row["id"]),))
                    removed += 1
            self._conn.commit()
        return {"added": added, "updated": updated, "removed": removed}


class CronExpression(NamedTuple):
    minute: frozenset[int]
    hour: frozenset[int]
    day: frozenset[int]
    month: frozenset[int]
    weekday: frozenset[int]
    day_any: bool
    weekday_any: bool


def _to_int(text: str, part: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"不是数字：{part}") from None


def _expand(field: str, lo: int, hi: int) -> set[int]:
    values: set[int] = set()
    for raw in field.split(","):
        part = raw.strip()
        if not part:
            raise ValueError("存在空的 cron 分段")
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) == 0:
                raise ValueError(f"步长必须是正整数：{part}")
            step = int(step_text)
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            head, _, tail = base.partition("-")
            start, end = _to_int(head, part), _to_int(tail, part)
        else:
            start = _to_int(base, part)
            end = hi if step_text else start
        if start < lo or end > hi or start > end:
            raise ValueError(f"超出取值范围 {lo}-{hi}：{part}")
        values.update(range(start, end + 1, step))
    return values


@lru_cache(maxsize=128)
def parse_cron(expression: str) -> CronExpression:
    """解析并校验 5 段 cron；失败抛 ValueError，消息可直接回给调用方。"""

    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"需要 5 段（分 时 日 月 周），实际 {len(fields)} 段")
    sets = [_expand(field, lo, hi) for field, (lo, hi) in zip(fields, _RANGES, strict=True)]
    weekday = frozenset(0 if value == 7 else value for value in sets[4])
    return CronExpression(
        minute=frozenset(sets[0]),
        hour=frozenset(sets[1]),
        day=frozenset(sets[2]),
        month=frozenset(sets[3]),
        weekday=weekday,
        day_any=fields[2].strip() == "*",
        weekday_any=fields[4].strip() == "*",
    )


def validate_cron(expression: str) -> None:
    parse_cron(expression)


def cron_matches(expression: str, moment: datetime) -> bool:
    """按分钟精度判断某个时刻是否命中 cron。"""

    cron = parse_cron(expression)
    if moment.minute not in cron.minute or moment.hour not in cron.hour:
        return False
    if moment.month not in cron.month:
        return False
    day_ok = moment.day in cron.day
    weekday_ok = (moment.isoweekday() % 7) in cron.weekday
    if cron.day_any or cron.weekday_any:
        # 任一方是 * 时，只由另一方决定，等价于标准 cron 的「两条件都要满足」
        return day_ok and weekday_ok
    return day_ok or weekday_ok


class Scheduler:
    """后台循环：每分钟最多检查一次，命中且未执行过就调用 runner。

    `sync` 非空时，按 `sync_seconds` 间隔把后端场景的定时触发器同步到本地计划表；
    同步失败只记审计，不影响已有计划的执行。
    """

    def __init__(
        self,
        store: ScheduleStore,
        runner: RunScene,
        *,
        tick_seconds: float = 20.0,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] = datetime.now,
        sync: LoadScenePlans | None = None,
        sync_seconds: float = 300.0,
    ) -> None:
        self._store = store
        self._runner = runner
        self._tick = tick_seconds
        self._audit = audit
        self._clock = clock
        self._sync = sync
        self._sync_seconds = sync_seconds
        self._last_sync: datetime | None = None
        self._last_minute: str | None = None

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._tick)
            try:
                await self.sync()
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 单次检查失败不能让调度线程退出
                if self._audit is not None:
                    self._audit.write("schedule_tick_error", error=str(exc))

    async def sync(self, now: datetime | None = None) -> dict[str, int] | None:
        """按间隔拉取场景定时触发器；未到间隔或未配置来源时返回 None。"""

        if self._sync is None:
            return None
        moment = now or self._clock()
        if (
            self._last_sync is not None
            and (moment - self._last_sync).total_seconds() < self._sync_seconds
        ):
            return None
        result = self._store.sync_scene_triggers(await self._sync())
        self._last_sync = moment
        if any(result.values()) and self._audit is not None:
            self._audit.write("schedule_synced", **result)
        return result

    async def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        moment = (now or self._clock()).replace(second=0, microsecond=0)
        minute_key = moment.isoformat(timespec="minutes")
        if minute_key == self._last_minute:
            return []
        self._last_minute = minute_key
        executed: list[dict[str, Any]] = []
        for schedule in self._store.list_all():
            if not schedule.enabled or not cron_matches(schedule.cron, moment):
                continue
            if schedule.last_run_at is not None and schedule.last_run_at[:16] == minute_key:
                continue  # 重启后同一分钟内不重复执行
            error: str | None = None
            try:
                await self._runner(schedule)
            except Exception as exc:  # 执行失败：记审计、不重试
                error = f"{type(exc).__name__}: {exc}"
            status = "ok" if error is None else "error"
            self._store.mark_run(schedule.id, status=status, error=error, at=moment)
            if self._audit is not None:
                if error is None:
                    self._audit.write(
                        "schedule_run", schedule_id=schedule.id, scene_id=schedule.scene_id
                    )
                else:
                    self._audit.write(
                        "schedule_error",
                        schedule_id=schedule.id,
                        scene_id=schedule.scene_id,
                        error=error,
                    )
            executed.append(
                {
                    "schedule_id": schedule.id,
                    "scene_id": schedule.scene_id,
                    "status": status,
                    "error": error,
                }
            )
        return executed


def dump_schedule(schedule: Schedule) -> dict[str, Any]:
    return asdict(schedule)
