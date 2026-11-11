"""通用契约：health、metrics、trace、鉴权、错误信封、请求校验。"""

from __future__ import annotations

import re

from homelink.interface.api.metrics import RequestMetrics


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "ok"
    assert body["schema_version"] == 3
    assert body["trace_id"]


def test_metrics_reports_health_calls_and_errors(client):
    client.get("/api/rooms")
    client.get("/api/devices/dev_missing")

    response = client.get("/api/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "ok"
    assert body["schema_version"] == 3
    assert body["gateway"] == {"mode": "mock", "base_url": ""}
    assert body["requests"]["total"] == 2
    assert body["requests"]["errors"] == 1
    assert body["requests"]["error_rate"] == 0.5
    assert body["requests"]["by_status"] == {"2xx": 1, "3xx": 0, "4xx": 1, "5xx": 0}
    assert body["latency_ms"]["samples"] == 2
    assert body["latency_ms"]["p50"] is not None
    assert body["uptime_seconds"] >= 0
    assert body["trace_id"]


def test_metrics_does_not_count_itself(client):
    client.get("/api/health")
    first = client.get("/api/metrics").json()
    second = client.get("/api/metrics").json()
    assert first["requests"]["total"] == second["requests"]["total"] == 1


def test_metrics_requires_token(client_factory):
    client = client_factory("secret-token")
    assert client.get("/api/metrics").status_code == 401


def test_request_metrics_counts_5xx_and_percentiles():
    metrics = RequestMetrics(clock=lambda: 100.0)
    metrics.record(status_code=200, latency_ms=10)
    metrics.record(status_code=503, latency_ms=30)

    snapshot = metrics.snapshot()
    assert snapshot["uptime_seconds"] == 0.0
    assert snapshot["requests"]["total"] == 2
    assert snapshot["requests"]["errors"] == 1
    assert snapshot["requests"]["by_status"] == {"2xx": 1, "3xx": 0, "4xx": 0, "5xx": 1}
    assert snapshot["latency_ms"] == {"p50": 10, "p95": 30, "samples": 2}


def test_trace_generated(client):
    response = client.get("/api/health")
    trace_id = response.json()["trace_id"]
    assert re.fullmatch(r"[0-9a-f]{8}", trace_id)
    assert response.headers["X-Trace-Id"] == trace_id


def test_trace_echoed_from_request(client):
    response = client.get("/api/health", headers={"X-Trace-Id": "abc-123"})
    assert response.json()["trace_id"] == "abc-123"
    assert response.headers["X-Trace-Id"] == "abc-123"


def test_trace_regenerated_when_too_long(client):
    response = client.get("/api/health", headers={"X-Trace-Id": "x" * 100})
    trace_id = response.json()["trace_id"]
    assert re.fullmatch(r"[0-9a-f]{8}", trace_id)


def test_error_envelope_shape(client):
    response = client.get("/api/devices/dev_missing")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"success", "error", "message", "trace_id"}
    assert body["success"] is False
    assert body["error"] == "NOT_FOUND"
    assert body["trace_id"] == response.headers["X-Trace-Id"]


def test_unknown_route_not_found(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"


def test_auth_rejects_missing_token(client_factory):
    client = client_factory("secret-token")
    response = client.get("/api/rooms")
    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"
    assert response.json()["trace_id"]


def test_auth_rejects_wrong_token(client_factory):
    client = client_factory("secret-token")
    response = client.get("/api/rooms", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_auth_accepts_correct_token(client_factory):
    client = client_factory("secret-token")
    assert client.get("/api/rooms").status_code == 401
    response = client.get("/api/rooms", headers={"Authorization": "Bearer secret-token"})
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_docs_not_protected(client_factory):
    client = client_factory("secret-token")
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_invalid_json_body(client):
    response = client.post(
        "/api/rooms", content="{bad", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_REQUEST"
