"""Agent 粗粒度 API：查设备、控设备、查结果、跑场景（不经过 LLM）。"""

from __future__ import annotations

from homelink.core.timeutil import utc_now_iso
from homelink.gateway.mock import TEST_WAVEFORM


def _make_device(
    context,
    client,
    *,
    name: str = "客厅空调",
    room: str = "living_room",
    functions: tuple[str, ...] = ("开机", "关机"),
) -> tuple[str, dict[str, str]]:
    body = client.post("/api/devices", json={"name": name, "room": room}).json()
    device_id = body["id"]
    now = utc_now_iso()
    resolved: dict[str, str] = {}
    with context.db.transaction() as conn:
        for index, function_name in enumerate(functions):
            function_id = f"fn_{device_id}_{index}"
            resolved[function_name] = function_id
            conn.execute(
                """
                INSERT INTO device_functions(id, device_id, name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (function_id, device_id, function_name, now, now),
            )
            conn.execute(
                """
                INSERT INTO ir_codes(id, device_id, function_id, raw_code, learned_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"code_{device_id}_{index}",
                    device_id,
                    function_id,
                    f"[{', '.join(map(str, TEST_WAVEFORM))}]",
                    now,
                    now,
                ),
            )
    return device_id, resolved


def test_find_devices_returns_agent_model(client, context):
    _make_device(context, client)

    body = client.get(
        "/api/agent/devices",
        params={"room": "living_room", "type": "air_conditioner", "name": "客厅"},
    ).json()["result"]

    assert body["count"] == 1
    model = body["devices"][0]
    assert model["type"] == "air_conditioner"
    assert model["room"] == "living_room"
    assert model["online"] is True
    assert model["online_scope"] == "gateway"
    assert model["risk_level"] == "low"
    assert model["state"] == {"power": "unknown"}
    assert model["state_readback"] is False
    assert {item["command"] for item in model["capabilities"]} == {"开机", "关机"}
    assert all(item["parameters"] == {} for item in model["capabilities"])


def test_metadata_updates_type_aliases_and_risk(client, context):
    device_id, _ = _make_device(context, client)

    updated = client.put(
        f"/api/agent/devices/{device_id}/metadata",
        json={"type": "heater", "aliases": ["客厅灯", "大灯"], "risk_level": "high"},
    ).json()["result"]

    assert updated["type"] == "heater"
    assert updated["aliases"] == ["客厅灯", "大灯"]
    assert updated["risk_level"] == "high"

    found = client.get("/api/agent/devices", params={"name": "大灯"}).json()["result"]
    assert found["count"] == 1


def test_state_and_snapshot_endpoints(client, context):
    device_id, _ = _make_device(context, client)

    state = client.get(f"/api/agent/devices/{device_id}/state").json()["result"]
    assert state["state"] == {"power": "unknown"}
    assert state["state_readback"] is False

    snapshot = client.get("/api/agent/snapshot").json()["result"]
    assert snapshot["gateway"]["reachable"] is True
    assert snapshot["devices"][0]["device_id"] == device_id
    assert snapshot["scenes"] == []


def test_control_by_command_name_is_idempotent(client, context):
    device_id, _ = _make_device(context, client)
    payload = {
        "device_id": device_id,
        "command": "开机",
        "request_id": "req-1",
    }

    first = client.post("/api/agent/control", json=payload)
    assert first.status_code == 200
    result = first.json()["result"]
    assert result["status"] == "sent"
    assert result["replayed"] is False

    replay = client.post("/api/agent/control", json=payload).json()["result"]
    assert replay["status"] == "sent"
    assert replay["replayed"] is True

    status = client.get("/api/agent/commands/req-1").json()["result"]
    assert status["status"] == "sent"
    assert status["device_id"] == device_id


def test_control_rejects_request_id_reuse_for_other_command(client, context):
    device_id, _ = _make_device(context, client)
    first = {
        "device_id": device_id,
        "command": "开机",
        "request_id": "req-2",
    }
    assert client.post("/api/agent/control", json=first).status_code == 200

    second = {**first, "command": "关机"}
    response = client.post("/api/agent/control", json=second)
    assert response.status_code == 409
    assert response.json()["error"] == "CONFLICT"


def test_control_rejects_parameters_and_unknown_command(client, context):
    device_id, _ = _make_device(context, client)

    with_parameters = client.post(
        "/api/agent/control",
        json={
            "device_id": device_id,
            "command": "开机",
            "parameters": {"brightness": 80},
            "request_id": "req-3",
        },
    )
    assert with_parameters.status_code == 400
    assert with_parameters.json()["error"] == "INVALID_REQUEST"

    unknown = client.post(
        "/api/agent/control",
        json={"device_id": device_id, "command": "调亮度", "request_id": "req-4"},
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "NOT_FOUND"


def test_high_risk_requires_confirmation(client, context):
    device_id, _ = _make_device(context, client, name="取暖器", functions=("开机",))
    client.put(
        f"/api/agent/devices/{device_id}/metadata",
        json={"risk_level": "high"},
    )

    denied = client.post(
        "/api/agent/control",
        json={"device_id": device_id, "command": "开机", "request_id": "req-5"},
    )
    assert denied.status_code == 409
    assert denied.json()["error"] == "CONFIRMATION_REQUIRED"

    allowed = client.post(
        "/api/agent/control",
        json={
            "device_id": device_id,
            "command": "开机",
            "request_id": "req-6",
            "confirm": True,
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["result"]["status"] == "sent"


def test_offline_gateway_returns_error_and_records_failure(client, context, monkeypatch):
    device_id, _ = _make_device(context, client)
    monkeypatch.setattr(
        context.probe,
        "status",
        lambda **kwargs: {
            "mode": "mock",
            "base_url": "",
            "reachable": False,
            "checked_at": utc_now_iso(),
            "latency_ms": 0,
            "error": "unreachable",
            "last_success_at": None,
            "simulated": True,
        },
    )

    response = client.post(
        "/api/agent/control",
        json={"device_id": device_id, "command": "开机", "request_id": "req-7"},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "DEVICE_OFFLINE"

    record = client.get("/api/agent/commands/req-7").json()["result"]
    assert record["status"] == "failed"
    assert record["error_code"] == "DEVICE_OFFLINE"


def test_scene_create_run_and_delete(client, context):
    device_id, functions = _make_device(context, client)
    created = client.post(
        "/api/agent/scenes",
        json={
            "name": "回家",
            "items": [
                {"device_id": device_id, "command": "开机"},
                {"device_id": device_id, "command": "关机"},
            ],
        },
    ).json()["result"]
    scene_id = created["scene_id"]
    assert [item["function_id"] for item in created["items"]] == [
        functions["开机"],
        functions["关机"],
    ]
    assert created["risk_level"] == "low"
    assert created["contains_high_risk"] is False

    listed = client.get("/api/agent/scenes").json()["result"]
    assert listed["count"] == 1
    assert listed["scenes"][0]["contains_high_risk"] is False

    run = client.post(f"/api/agent/scenes/{scene_id}/run")
    assert run.status_code == 200
    result = run.json()["result"]
    assert result["status"] == "sent"
    assert len(result["results"]) == 2

    assert client.delete(f"/api/agent/scenes/{scene_id}").status_code == 200
    assert client.post(f"/api/agent/scenes/{scene_id}/run").status_code == 404


def _command_count(context) -> int:
    with context.db.reader() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM command_requests").fetchone()[0])


def _make_high_risk_scene(client, context) -> str:
    device_id, _ = _make_device(context, client, name="取暖器", functions=("开机", "关机"))
    return client.post(
        "/api/agent/scenes",
        json={"name": "取暖", "items": [{"device_id": device_id, "command": "开机"}]},
    ).json()["result"]["scene_id"]


def test_scene_payload_aggregates_high_risk(client, context):
    scene_id = _make_high_risk_scene(client, context)

    scene = client.get("/api/agent/scenes").json()["result"]["scenes"][0]
    assert scene["scene_id"] == scene_id
    assert scene["risk_level"] == "high"
    assert scene["contains_high_risk"] is True


def test_high_risk_scene_requires_confirmation(client, context):
    scene_id = _make_high_risk_scene(client, context)

    denied = client.post(f"/api/agent/scenes/{scene_id}/run")
    assert denied.status_code == 409
    assert denied.json()["error"] == "CONFIRMATION_REQUIRED"
    assert _command_count(context) == 0  # 未确认时不产生任何发送记录

    confirmed = client.post(f"/api/agent/scenes/{scene_id}/run", json={"confirm": True})
    assert confirmed.status_code == 200
    assert confirmed.json()["result"]["status"] == "sent"
    assert _command_count(context) == 1  # 每条场景项只执行一次

    # 无 body 等价于 confirm=false，再次被拦
    again = client.post(f"/api/agent/scenes/{scene_id}/run")
    assert again.status_code == 409
    assert _command_count(context) == 1


def test_scene_run_rejects_undeclared_fields(client, context):
    scene_id = _make_high_risk_scene(client, context)

    response = client.post(f"/api/agent/scenes/{scene_id}/run", json={"force": True})
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_REQUEST"
    assert _command_count(context) == 0


def test_automations_are_explicitly_not_supported(client):
    response = client.post("/api/agent/automations", json={})
    assert response.status_code == 501
    assert response.json()["error"] == "NOT_SUPPORTED"
