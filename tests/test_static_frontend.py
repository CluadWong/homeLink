"""生产静态托管：开关、history 回退、`/api/*` 不吞 404、缺 dist 降级。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from homelink.bootstrap.context import build_context
from homelink.core.config import ConfigService, ConfigValidationError
from homelink.interface.api.app import create_app


def _write_config(config_dir: Path, *, frontend: dict | None) -> None:
    settings = {
        "gateway": {
            "mode": "mock",
            "ir": {"base_url": "", "username": "", "password": ""},
        },
        "api": {"token": ""},
    }
    if frontend is not None:
        settings["frontend"] = frontend
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (config_dir / "devices.yaml").write_text("rooms: []\n", encoding="utf-8")


def _client(tmp_path: Path, *, frontend: dict | None) -> TestClient:
    config_dir = tmp_path / "config"
    _write_config(config_dir, frontend=frontend)
    context = build_context(config_dir, tmp_path / "data" / "test.db")
    return TestClient(create_app(context))


def _make_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>HomeLink</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('jj');", encoding="utf-8")
    return dist


def test_serves_index_assets_and_history_fallback(tmp_path):
    _make_dist(tmp_path)
    client = _client(tmp_path, frontend={"enabled": True, "dist_path": "frontend/dist"})

    root = client.get("/")
    assert root.status_code == 200
    assert "HomeLink" in root.text

    deep_link = client.get("/devices/abc")
    assert deep_link.status_code == 200
    assert "HomeLink" in deep_link.text

    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert "console.log('jj');" in asset.text


def test_unknown_api_path_stays_json_404(tmp_path):
    _make_dist(tmp_path)
    client = _client(tmp_path, frontend={"enabled": True, "dist_path": "frontend/dist"})

    for path in ("/api/unknown-path", "/api"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"] == "NOT_FOUND"
        assert "<html" not in response.text


def test_disabled_frontend_is_pure_api(tmp_path):
    client = _client(tmp_path, frontend={"enabled": False, "dist_path": "frontend/dist"})

    assert client.get("/api/health").status_code == 200

    root = client.get("/")
    assert root.status_code == 404
    assert root.headers["content-type"].startswith("application/json")
    assert root.json()["error"] == "NOT_FOUND"


def test_missing_dist_degrades_with_hint(tmp_path):
    client = _client(tmp_path, frontend={"enabled": True, "dist_path": "frontend/dist"})

    root = client.get("/")
    assert root.status_code == 503
    assert "前端未构建" in root.text
    assert "npm run build" in root.text

    assert client.get("/api/health").status_code == 200
    unknown = client.get("/api/nope")
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "NOT_FOUND"


def test_frontend_config_defaults_and_validation(tmp_path):
    config_dir = tmp_path / "config"
    _write_config(config_dir, frontend=None)
    service = ConfigService(config_dir)
    assert service.settings.frontend.enabled is True
    assert service.settings.frontend.dist_path == "frontend/dist"

    _write_config(config_dir, frontend={"enabled": "yes", "dist_path": "frontend/dist"})
    with pytest.raises(ConfigValidationError):
        ConfigService(config_dir)
