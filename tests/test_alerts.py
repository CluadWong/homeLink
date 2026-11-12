"""告警去重与 JSONL 落盘；写文件失败不能影响调用方。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homelink_agent import alerts


@pytest.fixture(autouse=True)
def _clean_state():
    alerts.reset()
    yield
    alerts.reset()


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_emit_writes_jsonl(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "alerts.jsonl"
    monkeypatch.setenv("AGENT_ALERT_PATH", str(path))

    assert alerts.emit("schedule_failed", "连续失败 3 次", key="s1", detail="boom", now=100.0)

    records = _records(path)
    assert len(records) == 1
    assert records[0]["kind"] == "schedule_failed"
    assert records[0]["key"] == "s1"
    assert records[0]["detail"] == "boom"
    assert records[0]["at"]


def test_emit_dedupes_within_cooldown(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "alerts.jsonl"
    monkeypatch.setenv("AGENT_ALERT_PATH", str(path))

    assert alerts.emit("schedule_failed", "第一次", key="s1", now=100.0)
    assert not alerts.emit("schedule_failed", "第二次", key="s1", now=200.0)
    assert len(_records(path)) == 1


def test_emit_records_again_after_cooldown(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "alerts.jsonl"
    monkeypatch.setenv("AGENT_ALERT_PATH", str(path))

    assert alerts.emit("schedule_failed", "第一次", key="s1", now=100.0)
    assert alerts.emit("schedule_failed", "第二次", key="s1", now=100.0 + alerts.COOLDOWN_SECONDS)
    assert len(_records(path)) == 2


def test_emit_survives_unwritable_path(tmp_path: Path, monkeypatch) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("AGENT_ALERT_PATH", str(blocker / "alerts.jsonl"))

    assert alerts.emit("ollama_unavailable", "连不上", now=1.0) is True
