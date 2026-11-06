"""`GET /api/state`：空库、最新状态、传感器占位。"""

from __future__ import annotations


def test_state_empty_database(client):
    body = client.get("/api/state").json()
    assert body == {
        "devices": [],
        "sensors": {
            "available": False,
            "temperature": None,
            "humidity": None,
            "presence": None,
        },
    }
    assert "success" not in body
    assert "trace_id" not in body


def test_state_defaults_to_unknown_without_records(client):
    created = client.post("/api/devices", json={"name": "灯", "room": "bedroom"}).json()
    body = client.get("/api/state").json()
    assert body["devices"] == [
        {
            "id": created["id"],
            "name": "灯",
            "state": "unknown",
            "state_source": "unknown",
            "updated_at": None,
        }
    ]


def test_state_reads_latest_record_per_device(client, context):
    first = client.post("/api/devices", json={"name": "灯一", "room": "bedroom"}).json()
    second = client.post("/api/devices", json={"name": "灯二", "room": "living_room"}).json()

    context.states.append(first["id"], "on", "confirmed", reason="旧", trace_id="t1")
    context.states.append(first["id"], "off", "confirmed", reason="新", trace_id="t2")
    context.states.append(second["id"], "unknown", "unknown", reason="control", trace_id="t3")

    body = client.get("/api/state").json()
    devices = {item["id"]: item for item in body["devices"]}
    assert devices[first["id"]]["state"] == "off"
    assert devices[first["id"]]["state_source"] == "confirmed"
    assert devices[first["id"]]["updated_at"] is not None
    assert devices[second["id"]]["state"] == "unknown"
    assert devices[second["id"]]["state_source"] == "unknown"


def test_state_sensors_are_all_null(client):
    body = client.get("/api/state").json()
    assert body["sensors"]["available"] is False
    assert body["sensors"]["temperature"] is None
    assert body["sensors"]["humidity"] is None
    assert body["sensors"]["presence"] is None
