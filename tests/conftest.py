from __future__ import annotations

import copy
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

# 模块级 app 会在 import 时装配；把它的数据库指到临时目录，测试绝不碰仓库 data/。
os.environ.setdefault(
    "HOMELINK_DB_PATH",
    str(Path(tempfile.mkdtemp(prefix="homelink-test-boot-")) / "boot.db"),
)

from homelink.bootstrap.context import AppContext, build_context  # noqa: E402
from homelink.interface.api.app import create_app  # noqa: E402

BASE_SETTINGS: dict[str, Any] = {
    "gateway": {
        "mode": "mock",
        "ir": {"base_url": "", "username": "", "password": ""},
        "probe_timeout_seconds": 3,
    },
    "state": {"retention_days": 90},
    "audit": {"retention_days": 365},
    "api": {"token": ""},
    # 单元测试默认纯 API：静态托管的开关/降级用例单独写配置。
    "frontend": {"enabled": False, "dist_path": "frontend/dist"},
}

BASE_DEVICES: dict[str, Any] = {
    "rooms": [
        {"name": "bedroom", "display_name": "卧室", "sort_order": 1},
        {"name": "living_room", "display_name": "客厅"},
    ]
}


def write_config(config_dir, *, token: str = "") -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    settings = copy.deepcopy(BASE_SETTINGS)
    settings["api"]["token"] = token
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (config_dir / "devices.yaml").write_text(
        yaml.safe_dump(BASE_DEVICES, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def make_context(tmp_path, *, token: str = "", **kwargs) -> AppContext:
    config_dir = tmp_path / "config"
    write_config(config_dir, token=token)
    return build_context(config_dir, tmp_path / "data" / "test.db", **kwargs)


@pytest.fixture()
def context(tmp_path) -> AppContext:
    return make_context(tmp_path)


@pytest.fixture()
def client(context) -> Iterator[TestClient]:
    with TestClient(create_app(context)) as test_client:
        yield test_client


@pytest.fixture()
def client_factory(tmp_path):
    def _make(token: str = "") -> TestClient:
        return TestClient(create_app(make_context(tmp_path, token=token)))

    return _make


@pytest.fixture()
def build_client(tmp_path):
    """返回 (TestClient, AppContext)，可按需替换网关或压缩空闲 TTL。"""

    def _build(*, token: str = "", gateway=None, idle_ttl: float = 600.0):
        context = make_context(
            tmp_path,
            token=token,
            gateway_override=gateway,
            learn_idle_ttl_seconds=idle_ttl,
        )
        return TestClient(create_app(context)), context

    return _build
