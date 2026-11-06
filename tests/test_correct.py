"""`POST /api/correct`：用户校正、缺省 reason、空 state 400、404。"""

from __future__ import annotations


def _create_device(client) -> str:
    return client.post("/api/devices", json={"name": "灯", "room": "bedroom"}).json()["id"]


def test_correct_writes_confirmed_state(client, context):
    device_id = _create_device(client)
    response = client.post("/api/correct", json={"device_id": device_id, "state": "on"})
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "success": True,
        "device_id": device_id,
        "state": "on",
        "state_source": "confirmed",
    }
    assert "trace_id" not in body

    with context.db.reader() as conn:
        row = conn.execute(
            "SELECT state, state_source, reason FROM device_states "
            "WHERE device_id = ? ORDER BY rowid DESC LIMIT 1",
            (device_id,),
        ).fetchone()
    assert row["state"] == "on"
    assert row["state_source"] == "confirmed"
    assert row["reason"] == "用户校正"


def test_correct_uses_given_reason(client, context):
    device_id = _create_device(client)
    client.post("/api/correct", json={"device_id": device_id, "state": "off", "reason": "遥控器"})
    with context.db.reader() as conn:
        reason = conn.execute(
            "SELECT reason FROM device_states WHERE device_id = ? ORDER BY rowid DESC LIMIT 1",
            (device_id,),
        ).fetchone()["reason"]
    assert reason == "遥控器"


def test_correct_reflects_in_state(client):
    device_id = _create_device(client)
    client.post("/api/correct", json={"device_id": device_id, "state": "on"})
    state = client.get("/api/state").json()["devices"][0]
    assert state["state"] == "on"
    assert state["state_source"] == "confirmed"
    assert state["updated_at"] is not None


def test_correct_accepts_free_string_state(client):
    device_id = _create_device(client)
    response = client.post("/api/correct", json={"device_id": device_id, "state": "半开"})
    assert response.status_code == 200
    assert response.json()["state"] == "半开"


def test_correct_empty_state_400(client):
    device_id = _create_device(client)
    for state in ("", "   "):
        response = client.post("/api/correct", json={"device_id": device_id, "state": state})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_REQUEST"
    assert client.post("/api/correct", json={"device_id": device_id}).status_code == 400


def test_correct_unknown_device_404(client):
    response = client.post("/api/correct", json={"device_id": "dev_missing", "state": "on"})
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"
