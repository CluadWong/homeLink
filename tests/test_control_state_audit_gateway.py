"""批 3：控制、状态、校正、审计、网关。全部 mock，不触网。"""

from __future__ import annotations

import json

import yaml

from jj_butler.core.timeutil import utc_now_iso
from jj_butler.gateway.base import GatewayOfflineError, ProbeOutcome
from jj_butler.gateway.mock import TEST_WAVEFORM, MockIRGateway


def create_device(client, *, name: str = "测试灯") -> dict:
    return client.post("/api/devices", json={"name": name, "room": "bedroom"}).json()


def seed_function(context, device_id: str, *, name: str = "无码功能", code=None) -> str:
    """直接落一条功能（可选带码），用于构造「功能存在但没有码」。"""

    now = utc_now_iso()
    function_id = f"fn_{device_id[-4:]}"
    with context.db.transaction() as conn:
        conn.execute(
            "INSERT INTO device_functions(id, device_id, name, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (function_id, device_id, name, now, now),
        )
        if code is not None:
            conn.execute(
                "INSERT INTO ir_codes(id, device_id, function_id, raw_code, learned_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"code_{device_id[-4:]}",
                    device_id,
                    function_id,
                    str(list(code)).replace(" ", ""),
                    now,
                    now,
                ),
            )
    return function_id


def learn_function(client, device_id: str, *, name: str = "开") -> str:
    """走学码四步的 start + save，得到一条带码功能。"""

    assert client.post("/api/learn/start", json={"device_id": device_id}).status_code == 200
    body = client.post(
        "/api/learn/save",
        json={"device_id": device_id, "name": name, "raw_code": TEST_WAVEFORM},
    ).json()
    return body["function_id"]


def audit_rows(context) -> list[dict]:
    with context.db.reader() as conn:
        rows = conn.execute(
            "SELECT id, who, action, device_id, context, reason, result, trace_id FROM audit_logs"
            " ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def state_rows(context, device_id: str) -> list[dict]:
    with context.db.reader() as conn:
        rows = conn.execute(
            "SELECT id, state, state_source, reason, trace_id FROM device_states"
            " WHERE device_id = ? ORDER BY rowid",
            (device_id,),
        ).fetchall()
    return [dict(row) for row in rows]


class _SendDownGateway(MockIRGateway):
    """发送失败 + 探测也失败：证明控制不看探测结果。"""

    def replay(self, raw_code) -> None:
        raise GatewayOfflineError("连不上网关 192.0.2.10:6053")

    def probe(self) -> ProbeOutcome:
        return ProbeOutcome(reachable=False, latency_ms=0, error="down")


class _ProbeDownGateway(MockIRGateway):
    def probe(self) -> ProbeOutcome:
        return ProbeOutcome(reachable=False, latency_ms=0, error="down")


# ---------------------------------------------------------------- control


def test_control_success_replays_latest_code(client, context):
    device = create_device(client)
    function_id = learn_function(client, device["id"])

    response = client.post("/api/control", json={"function_id": function_id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["result"]["state"] == "unknown"
    assert body["result"]["message"] == "信号已发出"
    assert body["result"]["device_id"] == device["id"]
    assert body["trace_id"]

    assert context.gateway.replayed[-1] == tuple(TEST_WAVEFORM)
    states = state_rows(context, device["id"])
    assert states[-1]["state"] == "unknown"
    assert states[-1]["state_source"] == "unknown"
    audit = audit_rows(context)
    assert audit[-1]["action"] == f"{device['id']}.control"
    assert audit[-1]["result"] == "success"


def test_control_function_without_code_404(client, context):
    device = create_device(client)
    function_id = seed_function(context, device["id"])

    response = client.post("/api/control", json={"function_id": function_id})
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "NOT_FOUND"
    assert "码" in body["message"]

    audit = audit_rows(context)
    assert audit[-1]["result"] == "failed"
    assert audit[-1]["action"] == f"{device['id']}.control"
    assert state_rows(context, device["id"]) == []


def test_control_unknown_function_404(client):
    response = client.post("/api/control", json={"function_id": "fn_missing"})
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"


def test_control_send_failure_is_503(build_client):
    client, context = build_client(gateway=_SendDownGateway())
    device = create_device(client)
    function_id = learn_function(client, device["id"])

    response = client.post("/api/control", json={"function_id": function_id})
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "DEVICE_OFFLINE"
    assert "连不上" in body["message"]
    assert state_rows(context, device["id"]) == []
    audit = audit_rows(context)
    assert audit[-1]["result"] == "failed"


def test_control_does_not_precheck_probe(build_client):
    client, context = build_client(gateway=_ProbeDownGateway())
    device = create_device(client)
    function_id = learn_function(client, device["id"])

    response = client.post("/api/control", json={"function_id": function_id})
    assert response.status_code == 200, response.text


def test_control_rejects_extra_fields(client, context):
    device = create_device(client)
    function_id = learn_function(client, device["id"])

    response = client.post(
        "/api/control", json={"function_id": function_id, "device_id": device["id"]}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_REQUEST"


# ---------------------------------------------------------------- state


def test_state_is_read_only_and_sensors_are_null(client, context):
    device = create_device(client)

    first = client.get("/api/state").json()
    second = client.get("/api/state").json()

    assert "success" not in first
    assert "trace_id" not in first
    assert first["sensors"] == {
        "available": False,
        "temperature": None,
        "humidity": None,
        "presence": None,
    }
    assert [item["id"] for item in first["devices"]] == [device["id"]]
    assert first["devices"][0]["state"] == "unknown"
    assert first["devices"][0]["state_source"] == "unknown"
    assert first["devices"][0]["updated_at"] is None
    assert first == second
    assert state_rows(context, device["id"]) == []


# ---------------------------------------------------------------- correct


def test_correct_writes_confirmed_and_default_reason(client, context):
    device = create_device(client)

    body = client.post("/api/correct", json={"device_id": device["id"], "state": "on"}).json()
    assert body == {
        "success": True,
        "device_id": device["id"],
        "state": "on",
        "state_source": "confirmed",
    }

    rows = state_rows(context, device["id"])
    assert rows[-1]["state"] == "on"
    assert rows[-1]["state_source"] == "confirmed"
    assert rows[-1]["reason"] == "用户校正"

    state = client.get("/api/state").json()
    assert state["devices"][0]["state"] == "on"
    assert state["devices"][0]["state_source"] == "confirmed"
    assert state["devices"][0]["updated_at"]


def test_correct_keeps_user_reason(client, context):
    device = create_device(client)
    body = client.post(
        "/api/correct",
        json={"device_id": device["id"], "state": "off", "reason": "手动按了遥控"},
    ).json()
    assert body["success"] is True
    assert state_rows(context, device["id"])[-1]["reason"] == "手动按了遥控"


def test_correct_rejects_empty_and_too_long_state(client):
    device = create_device(client)

    empty = client.post("/api/correct", json={"device_id": device["id"], "state": "   "})
    assert empty.status_code == 400
    assert empty.json()["error"] == "INVALID_REQUEST"

    long_state = client.post("/api/correct", json={"device_id": device["id"], "state": "a" * 65})
    assert long_state.status_code == 400
    assert long_state.json()["error"] == "INVALID_REQUEST"


def test_correct_rejects_too_long_reason(client):
    device = create_device(client)

    response = client.post(
        "/api/correct",
        json={"device_id": device["id"], "state": "on", "reason": "a" * 129},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_REQUEST"


def test_correct_unknown_device_404(client):
    response = client.post("/api/correct", json={"device_id": "dev_ghost", "state": "on"})
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"


# ---------------------------------------------------------------- audit


def _write_audit(context, action: str, result: str, device_id: str | None = None) -> None:
    context.audit.write(
        who="user", action=action, device_id=device_id, result=result, trace_id="trace-test"
    )


def test_audit_filters_and_order(client, context):
    _write_audit(context, "dev_aaaa.control", "success", "dev_aaaa")
    _write_audit(context, "dev_aaaa.control", "failed", "dev_aaaa")
    _write_audit(context, "dev_bbbb.control", "success", "dev_bbbb")
    _write_audit(context, "rooms.create", "success")

    body = client.get("/api/audit").json()
    assert body["success"] is True
    assert [row["action"] for row in body["records"]] == [
        "rooms.create",
        "dev_bbbb.control",
        "dev_aaaa.control",
        "dev_aaaa.control",
    ]

    failed = client.get("/api/audit", params={"result": "failed"}).json()["records"]
    assert [row["result"] for row in failed] == ["failed"]

    success = client.get("/api/audit", params={"result": "success"}).json()["records"]
    assert len(success) == 3

    everything = client.get("/api/audit", params={"result": "all"}).json()["records"]
    assert len(everything) == 4

    prefixed = client.get("/api/audit", params={"device_id": "dev_aaaa"}).json()["records"]
    assert {row["device_id"] for row in prefixed} == {"dev_aaaa"}
    assert len(prefixed) == 2
    limited = client.get("/api/audit", params={"limit": 2}).json()["records"]
    assert len(limited) == 2


def test_audit_rejects_invalid_params(client):
    for params in (
        {"limit": 0},
        {"limit": 201},
        {"result": "denied"},
    ):
        response = client.get("/api/audit", params=params)
        assert response.status_code == 400, params
        assert response.json()["error"] == "INVALID_REQUEST"


def test_audit_record_shape(client, context):
    _write_audit(context, "dev_aaaa.control", "success", "dev_aaaa")
    row = client.get("/api/audit").json()["records"][0]
    assert set(row) == {"created_at", "action", "device_id", "result", "reason", "trace_id"}


def test_learn_audit_rows_carry_device_id_and_are_filterable(client, context):
    device = create_device(client)
    function_id = learn_function(client, device["id"])

    rows = audit_rows(context)
    learn_rows = [row for row in rows if row["action"].startswith("learn.")]
    assert {row["action"] for row in learn_rows} == {"learn.start", "learn.save"}
    assert {row["device_id"] for row in learn_rows} == {device["id"]}

    filtered = client.get("/api/audit", params={"device_id": device["id"]}).json()["records"]
    assert {row["action"] for row in filtered} == {"learn.start", "learn.save"}

    # 设备 id 精确匹配：带前缀但不是同一台设备不应命中。
    other = client.get("/api/audit", params={"device_id": device["id"][:-1]}).json()["records"]
    assert other == []

    # 控制成功后再确认 device_id 落在控制审计上。
    assert client.post("/api/control", json={"function_id": function_id}).status_code == 200
    control = client.get("/api/audit", params={"device_id": device["id"]}).json()["records"]
    assert any(row["action"] == f"{device['id']}.control" for row in control)


# ---------------------------------------------------------------- gateway


def test_get_gateway_probe_shape(client, context):
    body = client.get("/api/gateway").json()
    assert body["success"] is True
    assert body["mode"] == "mock"
    assert body["base_url"] == ""
    assert body["reachable"] is True
    assert body["simulated"] is True
    assert body["error"] is None
    assert body["last_success_at"]
    assert body["pending_restart"] is False
    assert body["trace_id"]

    again = client.get("/api/gateway", params={"refresh": True}).json()
    assert again["success"] is True
    assert again["reachable"] is True


def test_get_gateway_cached_within_15_seconds(build_client):
    class _Counting(MockIRGateway):
        probe_calls = 0

        def probe(self) -> ProbeOutcome:
            _Counting.probe_calls += 1
            return ProbeOutcome(reachable=True, latency_ms=0, error=None)

    client, _ = build_client(gateway=_Counting())
    client.get("/api/gateway")
    client.get("/api/gateway")
    assert _Counting.probe_calls == 1

    client.get("/api/gateway", params={"refresh": True})
    assert _Counting.probe_calls == 2


def test_put_gateway_writes_local_overlay_and_audit(build_client):
    client, context = build_client()

    response = client.put("/api/gateway", json={"base_url": "http://192.0.2.10:6053"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["base_url"] == "http://192.0.2.10:6053"
    assert body["restart_required"] is True
    assert body["trace_id"]

    after = client.get("/api/gateway").json()
    assert after["base_url"] == "http://192.0.2.10:6053"
    assert after["pending_restart"] is True

    overlay = context.config.config_dir / "settings.local.yaml"
    assert overlay.exists()
    data = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    assert data["gateway"]["ir"]["base_url"] == "http://192.0.2.10:6053"

    with context.db.reader() as conn:
        rows = conn.execute(
            "SELECT file, key_path, new_value, who, source, result FROM config_audit" " ORDER BY id"
        ).fetchall()
    last = dict(rows[-1])
    assert last["file"] == "settings.local.yaml"
    assert last["key_path"] == "gateway.ir.base_url"
    assert json.loads(last["new_value"]) == "http://192.0.2.10:6053"
    assert last["who"] == "user"
    assert last["source"] == "api"
    assert last["result"] == "applied"


def test_put_gateway_rejects_invalid_base_url(client):
    for value in ("  ", "ftp://192.0.2.10", "http://", "192.0.2.10:6053"):
        response = client.put("/api/gateway", json={"base_url": value})
        assert response.status_code == 400, value
        assert response.json()["error"] == "INVALID_REQUEST"
