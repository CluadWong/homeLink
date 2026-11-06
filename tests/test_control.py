"""`POST /api/control`：按 function_id 回放最新码、写 unknown 状态与审计。"""

from __future__ import annotations

import sqlite3

from jj_butler.core.timeutil import utc_now_iso
from jj_butler.gateway.base import GatewayError, ProbeOutcome
from jj_butler.gateway.mock import TEST_WAVEFORM, MockIRGateway


def _make_function(
    context, client, *, codes: list[tuple[str, list[int]]] | None = None
) -> tuple[str, str]:
    device = client.post("/api/devices", json={"name": "测试灯", "room": "bedroom"}).json()
    device_id = device["id"]
    function_id = "fn_test_1"
    now = utc_now_iso()
    with context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO device_functions(id, device_id, name, created_at, updated_at)
            VALUES (?, ?, '开', ?, ?)
            """,
            (function_id, device_id, now, now),
        )
        for code_id, raw in codes or []:
            conn.execute(
                """
                INSERT INTO ir_codes(id, device_id, function_id, raw_code, learned_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (code_id, device_id, function_id, f"[{', '.join(map(str, raw))}]", now, now),
            )
    return device_id, function_id


def test_control_replays_latest_code_and_writes_unknown_state(client, context):
    device_id, function_id = _make_function(context, client, codes=[("code_1", TEST_WAVEFORM)])

    response = client.post("/api/control", json={"function_id": function_id})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["trace_id"]
    assert body["result"]["function_id"] == function_id
    assert body["result"]["device_id"] == device_id
    assert body["result"]["state"] == "unknown"
    assert body["result"]["message"] == "信号已发出"
    assert context.gateway.replayed == [tuple(TEST_WAVEFORM)]

    state = client.get("/api/state").json()["devices"][0]
    assert state["state"] == "unknown"
    assert state["state_source"] == "unknown"
    assert state["updated_at"] is not None

    with context.db.reader() as conn:
        row = conn.execute(
            "SELECT state, state_source, reason, trace_id FROM device_states "
            "WHERE device_id = ? ORDER BY rowid DESC LIMIT 1",
            (device_id,),
        ).fetchone()
    assert row["state"] == "unknown"
    assert row["state_source"] == "unknown"
    assert row["reason"] == f"control:{function_id}"
    assert row["trace_id"] == body["trace_id"]

    latest = context.audit.recent(1)[0]
    assert latest["action"] == f"{device_id}.control"
    assert latest["who"] == "user"
    assert latest["result"] == "success"
    assert latest["trace_id"] == body["trace_id"]


def test_control_picks_code_with_largest_id(client, context):
    device_id, function_id = _make_function(
        context,
        client,
        codes=[("code_1", [100, -200]), ("code_2", TEST_WAVEFORM)],
    )
    response = client.post("/api/control", json={"function_id": function_id})
    assert response.status_code == 200
    assert context.gateway.replayed == [tuple(TEST_WAVEFORM)]
    assert device_id


def test_control_unknown_function_404(client):
    response = client.post("/api/control", json={"function_id": "fn_missing"})
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"


def test_control_function_without_code_404(client, context):
    device_id, function_id = _make_function(context, client, codes=[])
    response = client.post("/api/control", json={"function_id": function_id})
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "NOT_FOUND"
    assert "还没有可用码" in body["message"]
    assert context.gateway.replayed == []


def test_control_rejects_undeclared_fields(client, context):
    device_id, function_id = _make_function(context, client, codes=[("code_1", TEST_WAVEFORM)])
    for payload in (
        {"function_id": function_id, "device_id": device_id},
        {"function_id": function_id, "action": "on"},
        {"function_id": function_id, "params": {}},
    ):
        response = client.post("/api/control", json=payload)
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_REQUEST"
    assert client.post("/api/control", json={}).status_code == 400
    assert context.gateway.replayed == []


def test_control_gateway_offline_is_503_and_audited(build_client):
    class _Offline(MockIRGateway):
        def replay(self, raw_code):
            raise GatewayError("unreachable", "连不上设备")

    client, context = build_client(gateway=_Offline())
    device_id, function_id = _make_function(context, client, codes=[("code_1", TEST_WAVEFORM)])
    response = client.post("/api/control", json={"function_id": function_id})
    assert response.status_code == 503
    assert response.json()["error"] == "DEVICE_OFFLINE"

    latest = context.audit.recent(1)[0]
    assert latest["action"] == f"{device_id}.control"
    assert latest["result"] == "failed"
    assert latest["who"] == "user"

    assert client.get("/api/state").json()["devices"][0]["state"] == "unknown"
    assert client.get("/api/state").json()["devices"][0]["updated_at"] is None


def test_control_does_not_use_probe_as_precondition(build_client):
    class _ProbeDown(MockIRGateway):
        def probe(self) -> ProbeOutcome:
            return ProbeOutcome(reachable=False, latency_ms=0, error="probe failed")

    client, context = build_client(gateway=_ProbeDown())
    device_id, function_id = _make_function(context, client, codes=[("code_1", TEST_WAVEFORM)])
    response = client.post("/api/control", json={"function_id": function_id})
    assert response.status_code == 200
    assert context.gateway.replayed == [tuple(TEST_WAVEFORM)]
    assert device_id


def test_control_audit_failure_does_not_rollback_signal(context, client, monkeypatch):
    device_id, function_id = _make_function(context, client, codes=[("code_1", TEST_WAVEFORM)])

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("audit unavailable")

    monkeypatch.setattr(context.audit, "write", _boom)
    response = client.post("/api/control", json={"function_id": function_id})
    assert response.status_code == 200
    assert context.gateway.replayed == [tuple(TEST_WAVEFORM)]
    assert device_id
