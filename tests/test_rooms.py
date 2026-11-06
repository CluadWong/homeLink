"""房间端点：CRUD、409 语义、重命名同步设备。"""

from __future__ import annotations


def test_seeded_rooms(client):
    body = client.get("/api/rooms").json()
    assert body["success"] is True
    assert set(body) == {"success", "rooms", "trace_id"}
    rooms = {room["name"]: room for room in body["rooms"]}
    assert rooms["bedroom"]["display_name"] == "卧室"
    assert rooms["bedroom"]["sort_order"] == 1
    assert rooms["living_room"]["display_name"] == "客厅"
    assert rooms["living_room"]["sort_order"] == 0
    assert all(room["device_count"] == 0 for room in body["rooms"])


def test_create_room(client):
    response = client.post("/api/rooms", json={"name": "kitchen", "display_name": "厨房"})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["room"] == {
        "name": "kitchen",
        "display_name": "厨房",
        "sort_order": 0,
        "device_count": 0,
    }
    assert body["trace_id"]


def test_create_duplicate_room_conflict(client):
    response = client.post("/api/rooms", json={"name": "bedroom", "display_name": "主卧"})
    assert response.status_code == 409
    assert response.json()["error"] == "CONFLICT"


def test_create_room_missing_display_name(client):
    response = client.post("/api/rooms", json={"name": "kitchen"})
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_REQUEST"


def test_create_room_unknown_field(client):
    response = client.post(
        "/api/rooms", json={"name": "kitchen", "display_name": "厨房", "color": "red"}
    )
    assert response.status_code == 400
    assert "color" in response.json()["message"]


def test_patch_room_rename_syncs_devices(client):
    device = client.post("/api/devices", json={"name": "灯", "room": "bedroom"}).json()

    response = client.patch("/api/rooms/bedroom", json={"name": "master"})
    assert response.status_code == 200
    assert response.json()["room"]["name"] == "master"

    detail = client.get(f"/api/devices/{device['id']}").json()
    assert detail["room"] == "master"
    assert client.patch("/api/rooms/bedroom", json={"display_name": "x"}).status_code == 404


def test_patch_room_conflict(client):
    response = client.patch("/api/rooms/bedroom", json={"name": "living_room"})
    assert response.status_code == 409


def test_patch_room_not_found(client):
    response = client.patch("/api/rooms/ghost", json={"display_name": "幽灵"})
    assert response.status_code == 404


def test_patch_room_empty_body(client):
    response = client.patch("/api/rooms/bedroom", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_REQUEST"


def test_patch_room_rejects_null(client):
    response = client.patch("/api/rooms/bedroom", json={"sort_order": None})
    assert response.status_code == 400


def test_delete_room_requires_empty(client):
    client.post("/api/devices", json={"name": "灯", "room": "bedroom"})

    busy = client.delete("/api/rooms/bedroom")
    assert busy.status_code == 409
    assert busy.json()["error"] == "CONFLICT"

    empty = client.delete("/api/rooms/living_room")
    assert empty.status_code == 200
    assert empty.json()["deleted"] == {"name": "living_room"}

    assert client.delete("/api/rooms/living_room").status_code == 404
