"""定时调度纯逻辑：cron 解析、持久化、到点执行与失败不重试。"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from homelink_agent.scheduler import (
    AGENT_SCHEMA_VERSION,
    SceneTriggerSchedule,
    Schedule,
    Scheduler,
    ScheduleStore,
    cron_matches,
    validate_cron,
)


def test_cron_wildcards_match_every_minute() -> None:
    assert cron_matches("* * * * *", datetime(2026, 11, 10, 9, 30))


def test_cron_steps_ranges_and_lists() -> None:
    assert cron_matches("*/15 * * * *", datetime(2026, 11, 10, 9, 30))
    assert not cron_matches("*/15 * * * *", datetime(2026, 11, 10, 9, 31))
    assert cron_matches("0 7-9 * * *", datetime(2026, 11, 10, 8, 0))
    assert not cron_matches("0 7-9 * * *", datetime(2026, 11, 10, 10, 0))
    assert cron_matches("30 6,18 * * *", datetime(2026, 11, 10, 18, 30))


def test_cron_weekday_and_sunday_alias() -> None:
    # 2026-11-09 是周一、11-10 是周二、11-15 是周日
    assert cron_matches("0 8 * * 1", datetime(2026, 11, 9, 8, 0))
    assert not cron_matches("0 8 * * 1", datetime(2026, 11, 10, 8, 0))
    assert cron_matches("0 8 * * 0", datetime(2026, 11, 15, 8, 0))
    assert cron_matches("0 8 * * 7", datetime(2026, 11, 15, 8, 0))


def test_cron_day_and_weekday_are_or_when_both_restricted() -> None:
    # 标准 cron：日与周都受限时满足其一即可
    assert cron_matches("0 8 1 * 1", datetime(2026, 12, 1, 8, 0))  # 周二，但 1 号
    assert cron_matches("0 8 1 * 1", datetime(2026, 11, 9, 8, 0))  # 周一，非 1 号
    assert not cron_matches("0 8 1 * 1", datetime(2026, 11, 10, 8, 0))


def test_cron_crosses_midnight_and_month_boundaries() -> None:
    assert not cron_matches("0 0 * * *", datetime(2026, 11, 11, 23, 59))
    assert cron_matches("0 0 * * *", datetime(2026, 11, 12, 0, 0))
    assert cron_matches("59 23 * * *", datetime(2026, 11, 11, 23, 59))


def test_cron_month_end_and_leap_day() -> None:
    assert cron_matches("0 0 31 * *", datetime(2026, 12, 31, 0, 0))
    assert not cron_matches("0 0 31 * *", datetime(2026, 11, 30, 0, 0))
    assert cron_matches("0 0 29 2 *", datetime(2028, 2, 29, 0, 0))
    assert not cron_matches("0 0 29 2 *", datetime(2027, 2, 28, 0, 0))


@pytest.mark.parametrize(
    "expression",
    [
        "* * * *",  # 段数不足
        "60 * * * *",  # 分钟越界
        "*/0 * * * *",  # 步长必须为正
        "0 24 * * *",  # 小时越界
        "0 8 * 13 *",  # 月份越界
        "0 8 * * 8",  # 周字段越界（0-7）
        "0 8 * * MON",  # 不支持别名
        "0-5-9 * * * *",  # 不是合法数字
    ],
)
def test_cron_rejects_invalid(expression: str) -> None:
    with pytest.raises(ValueError):
        validate_cron(expression)


def test_store_persists_across_reopen(tmp_path: Path) -> None:
    path = str(tmp_path / "agent.db")
    store = ScheduleStore(path)
    created = store.add(scene_id="scene-1", cron="0 7 * * *", enabled=True, confirm=False)
    store.close()

    reopened = ScheduleStore(path)
    try:
        items = reopened.list_all()
    finally:
        reopened.close()

    assert [item.id for item in items] == [created.id]
    assert items[0].scene_id == "scene-1"
    assert items[0].enabled is True
    assert items[0].last_run_at is None


def _store_with(*scenes: tuple[str, bool]) -> ScheduleStore:
    store = ScheduleStore(":memory:")
    for scene_id, enabled in scenes:
        store.add(scene_id=scene_id, cron="* * * * *", enabled=enabled, confirm=False)
    return store


def test_scheduler_runs_due_schedule_once_per_minute() -> None:
    store = _store_with(("scene-1", True))
    runs: list[str] = []

    async def runner(schedule: Schedule) -> None:
        runs.append(schedule.id)

    scheduler = Scheduler(store, runner)
    moment = datetime(2026, 11, 10, 7, 0)
    try:
        first = asyncio.run(scheduler.tick(moment))
        assert [item["status"] for item in first] == ["ok"]
        # 同一分钟重复检查不重复执行
        assert asyncio.run(scheduler.tick(datetime(2026, 11, 10, 7, 0, 30))) == []
        # 次日同一时刻再次执行
        second = asyncio.run(scheduler.tick(datetime(2026, 11, 11, 7, 0)))
        assert [item["scene_id"] for item in second] == ["scene-1"]
    finally:
        store.close()

    assert runs == [first[0]["schedule_id"], first[0]["schedule_id"]]


def test_scheduler_skips_disabled_and_records_failure_without_retry() -> None:
    store = _store_with(("s-ok", True), ("s-off", False), ("s-bad", True))

    async def runner(schedule: Schedule) -> None:
        if schedule.scene_id == "s-bad":
            raise RuntimeError("boom")

    scheduler = Scheduler(store, runner)
    moment = datetime(2026, 11, 10, 9, 0)
    try:
        executed = asyncio.run(scheduler.tick(moment))
        assert {item["scene_id"] for item in executed} == {"s-ok", "s-bad"}
        rows = {row.scene_id: row for row in store.list_all()}
        assert rows["s-ok"].last_status == "ok"
        assert rows["s-ok"].last_run_at == moment.isoformat(timespec="seconds")
        assert rows["s-bad"].last_status == "error"
        assert "boom" in (rows["s-bad"].last_error or "")
        assert rows["s-off"].last_run_at is None  # 停用的计划不执行
        # 失败不重试：同一分钟内不再触发
        assert asyncio.run(scheduler.tick(datetime(2026, 11, 10, 9, 0, 40))) == []
    finally:
        store.close()


def test_scheduler_does_not_rerun_same_minute_after_restart() -> None:
    store = ScheduleStore(":memory:")
    schedule = store.add(scene_id="scene-1", cron="* * * * *", enabled=True, confirm=False)
    moment = datetime(2026, 11, 10, 9, 0)
    store.mark_run(schedule.id, status="ok", error=None, at=moment)
    runs: list[str] = []

    async def runner(item: Schedule) -> None:
        runs.append(item.id)

    try:
        # 重启后 last_run_at 落在同一分钟，说明本次已经跑过
        assert asyncio.run(Scheduler(store, runner).tick(moment)) == []
    finally:
        store.close()

    assert runs == []


def test_store_migrates_legacy_schedules_table(tmp_path: Path) -> None:
    path = tmp_path / "agent.db"
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE schedules (id TEXT PRIMARY KEY, scene_id TEXT NOT NULL,"
        " cron TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,"
        " confirm INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,"
        " last_run_at TEXT, last_status TEXT, last_error TEXT)"
    )
    legacy.execute(
        "INSERT INTO schedules(id, scene_id, cron, enabled, confirm, created_at)"
        " VALUES ('old-1', 'scene-old', '0 7 * * *', 1, 0, '2026-11-01T07:00:00')"
    )
    legacy.commit()
    legacy.close()

    store = ScheduleStore(str(path))
    try:
        items = store.list_all()
        assert [(item.id, item.source, item.trigger_id) for item in items] == [
            ("old-1", "manual", None)
        ]
        store.sync_scene_triggers(
            [SceneTriggerSchedule(scene_id="scene-1", trigger_id="tr-1", cron="0 8 * * *")]
        )
        assert {item.scene_id for item in store.list_all()} == {"scene-old", "scene-1"}
    finally:
        store.close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == AGENT_SCHEMA_VERSION


def test_store_sets_user_version_on_create(tmp_path: Path) -> None:
    path = tmp_path / "agent.db"
    ScheduleStore(str(path)).close()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == AGENT_SCHEMA_VERSION


def test_store_rejects_newer_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "agent.db"
    newer = sqlite3.connect(path)
    newer.execute(f"PRAGMA user_version = {AGENT_SCHEMA_VERSION + 1}")
    newer.commit()
    newer.close()

    with pytest.raises(RuntimeError, match="高于当前支持"):
        ScheduleStore(str(path))


def test_sync_scene_triggers_adds_updates_and_removes() -> None:
    store = ScheduleStore(":memory:")
    manual = store.add(scene_id="scene-manual", cron="0 6 * * *", enabled=True, confirm=True)
    try:
        first = store.sync_scene_triggers(
            [
                SceneTriggerSchedule(scene_id="scene-1", trigger_id="tr-1", cron="0 7 * * *"),
                SceneTriggerSchedule(scene_id="scene-1", trigger_id="tr-2", cron="0 8 * * *"),
            ]
        )
        assert first == {"added": 2, "updated": 0, "removed": 0}

        second = store.sync_scene_triggers(
            [
                SceneTriggerSchedule(
                    scene_id="scene-1", trigger_id="tr-1", cron="0 9 * * *", enabled=False
                )
            ]
        )
        assert second == {"added": 0, "updated": 1, "removed": 1}

        rows = {(row.scene_id, row.trigger_id): row for row in store.list_all()}
        assert set(rows) == {("scene-manual", None), ("scene-1", "tr-1")}
        assert rows[("scene-1", "tr-1")].cron == "0 9 * * *"
        assert rows[("scene-1", "tr-1")].enabled is False
        assert rows[("scene-manual", None)].confirm is True  # 手工计划不受同步影响

        third = store.sync_scene_triggers([])
        assert third == {"added": 0, "updated": 0, "removed": 1}
        assert [row.scene_id for row in store.list_all()] == [manual.scene_id]
    finally:
        store.close()


def test_sync_scene_triggers_keeps_last_run() -> None:
    store = ScheduleStore(":memory:")
    moment = datetime(2026, 11, 10, 7, 0)
    try:
        store.sync_scene_triggers(
            [SceneTriggerSchedule(scene_id="scene-1", trigger_id="tr-1", cron="0 7 * * *")]
        )
        schedule = store.list_all()[0]
        store.mark_run(schedule.id, status="ok", error=None, at=moment)

        store.sync_scene_triggers(
            [SceneTriggerSchedule(scene_id="scene-1", trigger_id="tr-1", cron="0 7 * * *")]
        )
        row = store.list_all()[0]
        assert row.last_run_at == moment.isoformat(timespec="seconds")
        assert row.last_status == "ok"
    finally:
        store.close()


def test_scheduler_sync_skips_without_source_and_respects_interval() -> None:
    store = ScheduleStore(":memory:")
    calls: list[int] = []

    async def runner(schedule: Schedule) -> None:
        return None

    async def plans() -> list[SceneTriggerSchedule]:
        calls.append(1)
        return [SceneTriggerSchedule(scene_id="scene-1", trigger_id="tr-1", cron="0 7 * * *")]

    moment = datetime(2026, 11, 10, 7, 0)
    try:
        assert asyncio.run(Scheduler(store, runner).sync(moment)) is None

        scheduler = Scheduler(store, runner, sync=plans, sync_seconds=300)
        assert asyncio.run(scheduler.sync(moment)) == {"added": 1, "updated": 0, "removed": 0}
        # 未到间隔：不发第二次请求
        assert asyncio.run(scheduler.sync(datetime(2026, 11, 10, 7, 1))) is None
        assert asyncio.run(scheduler.sync(datetime(2026, 11, 10, 7, 5))) == {
            "added": 0,
            "updated": 0,
            "removed": 0,
        }
        assert len(calls) == 2
    finally:
        store.close()
