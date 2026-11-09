"""ConfigService：覆盖层优先级、原子写、失败回滚、config_audit 落库。"""

from __future__ import annotations

import pytest
import yaml

from homelink.core import config as config_module
from homelink.core.config import (
    ConfigError,
    ConfigService,
    InMemoryConfigAuditStore,
    SqliteConfigAuditStore,
)
from homelink.core.database import Database

SETTINGS = {
    "gateway": {
        "mode": "mock",
        "ir": {"base_url": "http://gw.local", "username": "", "password": ""},
        "probe_timeout_seconds": 3,
    },
    "state": {"retention_days": 90},
    "audit": {"retention_days": 365},
    "api": {"token": ""},
}
DEVICES = {
    "rooms": [
        {"name": "bedroom", "display_name": "卧室", "sort_order": 1},
        {"name": "living_room", "display_name": "客厅"},
    ]
}


def write_config(config_dir, settings=None, devices=None) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(settings or SETTINGS, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (config_dir / "devices.yaml").write_text(
        yaml.safe_dump(devices or DEVICES, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_local_overlay_wins(tmp_path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    (config_dir / "settings.local.yaml").write_text(
        yaml.safe_dump(
            {
                "gateway": {
                    "mode": "real",
                    "ir": {
                        "username": "  alice  ",
                        "password": " s3cret ",
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    service = ConfigService.load(config_dir)
    assert service.settings.gateway.mode == "real"
    assert service.settings.gateway.base_url == "http://gw.local"
    assert service.settings.gateway.username == "alice"
    assert service.settings.gateway.password == " s3cret "  # 密码不 strip
    assert service.get("settings.yaml")["gateway"]["mode"] == "real"


def test_set_writes_atomically_and_records_applied(tmp_path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    store = InMemoryConfigAuditStore()
    service = ConfigService.load(config_dir, audit_store=store)

    service.set(
        "settings.yaml",
        "gateway.ir.base_url",
        "http://192.168.1.50",
        who="user",
        source="api",
        trace_id="tr-1",
    )

    assert service.get("settings.yaml")["gateway"]["ir"]["base_url"] == "http://192.168.1.50"
    assert list(config_dir.glob(".*.tmp")) == []
    record = store.records()[0]
    assert record.result == "applied"
    assert record.file == "settings.yaml"
    assert record.key_path == "gateway.ir.base_url"
    assert record.old_value == "http://gw.local"
    assert record.new_value == "http://192.168.1.50"
    assert record.who == "user"
    assert record.source == "api"
    assert record.trace_id == "tr-1"


def test_set_local_keeps_base_file_untouched(tmp_path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    original = (config_dir / "settings.yaml").read_text(encoding="utf-8")
    store = InMemoryConfigAuditStore()
    service = ConfigService.load(config_dir, audit_store=store)

    service.set_local(
        "settings.yaml",
        "gateway.ir.password",
        "top-secret",
        source="api",
        trace_id="tr-2",
    )

    assert (config_dir / "settings.yaml").read_text(encoding="utf-8") == original
    local = yaml.safe_load((config_dir / "settings.local.yaml").read_text(encoding="utf-8"))
    assert local["gateway"]["ir"]["password"] == "top-secret"
    assert service.get("settings.yaml")["gateway"]["ir"]["password"] == "top-secret"
    assert store.records()[0].file == "settings.local.yaml"
    assert store.records()[0].source == "api"


def test_write_failure_leaves_original_and_records_rejected(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    path = config_dir / "settings.yaml"
    original = path.read_text(encoding="utf-8")
    store = InMemoryConfigAuditStore()
    service = ConfigService.load(config_dir, audit_store=store)

    def boom(src, dst):
        raise OSError("磁盘只读")

    monkeypatch.setattr(config_module.os, "replace", boom)

    with pytest.raises(ConfigError):
        service.set("settings.yaml", "gateway.ir.base_url", "http://new.local")

    assert path.read_text(encoding="utf-8") == original
    assert list(config_dir.glob(".*.tmp")) == []
    assert store.records()[-1].result == "rejected"
    assert "磁盘只读" in (store.records()[-1].error or "")


def test_audit_failure_rolls_back_file(tmp_path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    path = config_dir / "settings.yaml"
    original = path.read_text(encoding="utf-8")

    class BrokenAuditStore:
        def append(self, record):
            raise RuntimeError("审计库不可用")

        def records(self):
            return []

    service = ConfigService.load(config_dir, audit_store=BrokenAuditStore())

    with pytest.raises(ConfigError):
        service.set("settings.yaml", "gateway.ir.base_url", "http://new.local")

    assert path.read_text(encoding="utf-8") == original


def test_invalid_value_is_rejected_without_touching_file(tmp_path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    path = config_dir / "settings.yaml"
    original = path.read_text(encoding="utf-8")
    store = InMemoryConfigAuditStore()
    service = ConfigService.load(config_dir, audit_store=store)

    with pytest.raises(ConfigError):
        service.set("settings.yaml", "gateway.mode", "mqtt", source="api")

    assert path.read_text(encoding="utf-8") == original
    assert store.records()[-1].result == "rejected"


def test_sqlite_config_audit_persists(tmp_path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    db = Database(tmp_path / "data" / "test.db")
    db.initialize()
    store = SqliteConfigAuditStore(db)
    service = ConfigService.load(config_dir, audit_store=store)

    service.set(
        "settings.yaml",
        "gateway.ir.base_url",
        "http://192.168.1.60",
        who="user",
        source="api",
        trace_id="tr-db",
    )

    records = store.records()
    assert len(records) == 1
    assert records[0].trace_id == "tr-db"
    assert records[0].source == "api"
    assert records[0].result == "applied"
    with db.reader() as conn:
        row = conn.execute("SELECT file, key_path, new_value FROM config_audit").fetchone()
    assert row["file"] == "settings.yaml"
    assert row["key_path"] == "gateway.ir.base_url"
    assert row["new_value"] == '"http://192.168.1.60"'
