"""设备端点：运行期创建、adapter 恒 ir、PATCH 白名单、级联删除。"""

from __future__ import annotations

NOW = "2026-01-01T00:00:00Z"


def test_create_device(client):
    body = client.post(
        "/api/devices",
        json={"name": "客厅灯", "room": "living_room", "notes": "备注"},
    ).json()
    assert body["success"] is True
    assert body["id"].startswith("dev_")
    assert len(body["id"]) == 12
    assert body["adapter"] == "ir"
    assert body["room"] == "living_room"
    assert body["notes"] == "备注"
    assert body["extra"] == {}
    assert body["functions"] == []
    assert body["trace_id"]


def test_create_device_rejects_adapter_field(client):
    response = client.post("/api/devices", json={"name": "灯", "room": "bedroom", "adapter": "ir"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "INVALID_REQUEST"
    assert "adapter" in body["message"]
    assert body["trace_id"]


def test_create_device_learn_draft_flag(client):
    body = client.post(
        "/api/devices", json={"name": "草稿灯", "room": "bedroom", "learn_draft": True}
    ).json()
    assert body["extra"] == {"learn_draft": True}


def test_create_device_unknown_room(client):
    response = client.post("/api/devices", json={"name": "灯", "room": "ghost"})
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"


def test_create_device_missing_name(client):
    response = client.post("/api/devices", json={"room": "bedroom"})
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_REQUEST"


def test_list_devices_and_room_filter(client):
    client.post("/api/devices", json={"name": "卧室灯", "room": "bedroom"})
    client.post("/api/devices", json={"name": "客厅灯", "room": "living_room"})

    all_devices = client.get("/api/devices").json()["devices"]
    assert {device["name"] for device in all_devices} == {"卧室灯", "客厅灯"}

    filtered = client.get("/api/devices", params={"room": "bedroom"}).json()["devices"]
    assert [device["name"] for device in filtered] == ["卧室灯"]

    assert client.get("/api/devices", params={"room": "ghost"}).json()["devices"] == []


def test_get_device_detail_and_404(client):
    created = client.post("/api/devices", json={"name": "灯", "room": "bedroom"}).json()
    detail = client.get(f"/api/devices/{created['id']}").json()
    assert detail["id"] == created["id"]
    assert detail["functions"] == []

    assert client.get("/api/devices/dev_missing").status_code == 404


def test_patch_device_fields(client):
    created = client.post("/api/devices", json={"name": "灯", "room": "bedroom"}).json()

    response = client.patch(
        f"/api/devices/{created['id']}",
        json={"name": "新灯", "room": "living_room", "notes": "改过"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "新灯"
    assert body["room"] == "living_room"
    assert body["notes"] == "改过"


def test_patch_device_rejects_unknown_field(client):
    created = client.post("/api/devices", json={"name": "灯", "room": "bedroom"}).json()
    response = client.patch(f"/api/devices/{created['id']}", json={"adapter": "ir"})
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_REQUEST"


def test_patch_device_rejects_empty_body_and_null_name(client):
    created = client.post("/api/devices", json={"name": "灯", "room": "bedroom"}).json()
    assert client.patch(f"/api/devices/{created['id']}", json={}).status_code == 400
    assert client.patch(f"/api/devices/{created['id']}", json={"name": None}).status_code == 400


def test_patch_device_unknown_room(client):
    created = client.post("/api/devices", json={"name": "灯", "room": "bedroom"}).json()
    response = client.patch(f"/api/devices/{created['id']}", json={"room": "ghost"})
    assert response.status_code == 404


def test_delete_device_cascades(client, context):
    created = client.post("/api/devices", json={"name": "灯", "room": "bedroom"}).json()
    device_id = created["id"]

    with context.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO device_functions(id, device_id, name, created_at, updated_at)
            VALUES ('fn_1', ?, '开', ?, ?)
            """,
            (device_id, NOW, NOW),
        )
        conn.execute(
            """
            INSERT INTO ir_codes(id, device_id, function_id, raw_code, learned_at, created_at)
            VALUES ('code_1', ?, 'fn_1', '[100, -200]', ?, ?)
            """,
            (device_id, NOW, NOW),
        )
        conn.execute(
            """
            INSERT INTO device_states(id, device_id, state, state_source, reason, trace_id, created_at)
            VALUES ('st_1', ?, 'unknown', 'unknown', 'control:fn_1', 'trace', ?)
            """,
            (device_id, NOW),
        )

    response = client.delete(f"/api/devices/{device_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] == {
        "device_id": device_id,
        "functions": 1,
        "codes": 1,
        "states": 1,
    }

    with context.db.reader() as conn:
        for table in ("devices", "device_functions", "ir_codes", "device_states"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table

    assert client.delete(f"/api/devices/{device_id}").status_code == 404
