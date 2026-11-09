"""`/api/gateway`：探测缓存、mock 字段、PUT 写本地覆盖层与 config_audit。"""

from __future__ import annotations

import json

import yaml

from homelink.gateway.base import ProbeOutcome
from homelink.gateway.mock import MockIRGateway


class _CountingGateway(MockIRGateway):
    def __init__(self) -> None:
        super().__init__()
        self.probe_calls = 0

    def probe(self) -> ProbeOutcome:
        self.probe_calls += 1
        return ProbeOutcome(reachable=True, latency_ms=0, error=None, simulated=True)


def test_gateway_status_mock_fields(client):
    response = client.get("/api/gateway")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["mode"] == "mock"
    assert body["base_url"] == ""
    assert body["pending_restart"] is False
    assert body["reachable"] is True
    assert body["simulated"] is True
    assert body["error"] is None
    assert body["latency_ms"] >= 0
    assert body["checked_at"]
    assert body["last_success_at"] == body["checked_at"]
    assert body["trace_id"]


def test_gateway_status_uses_cache_and_refresh_bypasses(build_client):
    gateway = _CountingGateway()
    client, context = build_client(gateway=gateway)

    first = client.get("/api/gateway").json()
    second = client.get("/api/gateway").json()
    assert gateway.probe_calls == 1
    assert first["checked_at"] == second["checked_at"]

    refreshed = client.get("/api/gateway", params={"refresh": "true"}).json()
    assert gateway.probe_calls == 2
    assert context.probe.status()["checked_at"] == refreshed["checked_at"]


def test_gateway_put_writes_local_overlay_and_config_audit(client, context):
    settings_path = context.config.config_dir / "settings.yaml"
    before = settings_path.read_text(encoding="utf-8")

    response = client.put("/api/gateway", json={"base_url": "http://192.168.1.10:6053"})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["mode"] == "mock"
    assert body["base_url"] == "http://192.168.1.10:6053"
    assert body["restart_required"] is True
    assert body["message"]
    assert body["trace_id"]

    after = client.get("/api/gateway").json()
    assert after["base_url"] == "http://192.168.1.10:6053"  # 已保存、未生效
    assert after["pending_restart"] is True

    overlay = yaml.safe_load(
        (context.config.config_dir / "settings.local.yaml").read_text(encoding="utf-8")
    )
    assert overlay["gateway"]["ir"]["base_url"] == "http://192.168.1.10:6053"
    assert settings_path.read_text(encoding="utf-8") == before

    record = context.config.audit_records()[-1]
    assert record.file == "settings.local.yaml"
    assert record.key_path == "gateway.ir.base_url"
    assert record.old_value is None
    assert json.loads(record.new_value) == "http://192.168.1.10:6053"
    assert record.who == "user"
    assert record.source == "api"
    assert record.result == "applied"
    assert record.trace_id == body["trace_id"]


def test_gateway_put_rejects_invalid_base_url_400(client):
    for value in ("", "   ", "ftp://192.168.1.10", "http://", "192.168.1.10:6053"):
        response = client.put("/api/gateway", json={"base_url": value})
        assert response.status_code == 400, value
        assert response.json()["error"] == "INVALID_REQUEST"
    assert client.put("/api/gateway", json={}).status_code == 400
