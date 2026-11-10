"""定时调度纯逻辑：cron 解析、持久化、到点执行与失败不重试。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from homelink_agent.scheduler import (
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
