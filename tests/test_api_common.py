"""通用契约：health、trace、鉴权、错误信封、请求校验。"""

from __future__ import annotations

import re


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "ok"
    assert body["schema_version"] == 1
    assert body["trace_id"]


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
