"""学码四步：会话、捕获三值、保存原子性、撤壳、TTL、码保留、删功能。"""

from __future__ import annotations

import json
import time

from jj_butler.gateway.base import GatewayError
from jj_butler.gateway.mock import TEST_WAVEFORM, MockIRGateway


def wait_for(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def create_device(client, *, draft: bool = False) -> dict:
    return client.post(
        "/api/devices", json={"name": "测试灯", "room": "bedroom", "learn_draft": draft}
    ).json()


def start_session(client, context, *, draft: bool = False) -> dict:
    device = create_device(client, draft=draft)
    response = client.post("/api/learn/start", json={"device_id": device["id"]})
    assert response.status_code == 200, response.text
    return device


def save_function(client, context, device_id: str, name: str = "开") -> dict:
    response = client.post(
        "/api/learn/save",
        json={"device_id": device_id, "name": name, "raw_code": TEST_WAVEFORM},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------- start / capture


def test_start_opens_receiver(client, context):
    device = start_session(client, context)
    body = client.post("/api/learn/start", json={"device_id": device["id"]}).json()
    assert body["success"] is True
    assert body["message"] == "已开始接收红外信号"
    assert body["device_id"] == device["id"]
    assert body["trace_id"]
    assert context.gateway.learn_active is True


def test_start_unknown_device_404(client):
    response = client.post("/api/learn/start", json={"device_id": "dev_missing"})
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"


def test_capture_without_session_404(client):
    device = create_device(client)
    response = client.get("/api/learn/capture", params={"device_id": device["id"]})
    assert response.status_code == 404


def test_capture_no_signal_is_200(client, context):
    device = start_session(client, context)
    response = client.get("/api/learn/capture", params={"device_id": device["id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["captured"] is False
    assert body["reason"] == "no_signal"
    assert body["raw_code"] is None
    assert body["message"] == "还没有收到信号，请在设备前按键"
    assert body["trace_id"]


def test_capture_success(client, context):
    device = start_session(client, context)
    context.gateway.inject_test_waveform()
    body = client.get("/api/learn/capture", params={"device_id": device["id"]}).json()
    assert body["captured"] is True
    assert body["raw_code"] == TEST_WAVEFORM
    assert body["reason"] is None


def test_capture_malformed_waveform(client, context):
    device = start_session(client, context)
    context.gateway.inject_last_code("not-a-waveform")
    body = client.get("/api/learn/capture", params={"device_id": device["id"]}).json()
    assert body["captured"] is False
    assert body["reason"] == "malformed"


def test_capture_gateway_unreachable_is_200(build_client):
    class _Unreachable(MockIRGateway):
        def read_last_code(self):
            raise GatewayError("unreachable", "无法连接网关")

    client, context = build_client(gateway=_Unreachable())
    device = start_session(client, context)
    body = client.get("/api/learn/capture", params={"device_id": device["id"]}).json()
    assert body["captured"] is False
    assert body["reason"] == "gateway_unreachable"


def test_capture_gateway_unauthorized_is_401(build_client):
    class _Unauthorized(MockIRGateway):
        def read_last_code(self):
            raise GatewayError("unauthorized", "网关在线但鉴权被拒")

    client, context = build_client(gateway=_Unauthorized())
    device = start_session(client, context)
    response = client.get("/api/learn/capture", params={"device_id": device["id"]})
    assert response.status_code == 401
    assert response.json()["error"] == "DEVICE_UNAUTHORIZED"


def test_start_gateway_unreachable_is_503(build_client):
    class _Unreachable(MockIRGateway):
        def start_learn(self):
            raise GatewayError("unreachable", "无法连接网关")

    client, _ = build_client(gateway=_Unreachable())
    device = create_device(client)
    response = client.post("/api/learn/start", json={"device_id": device["id"]})
    assert response.status_code == 503
    assert response.json()["error"] == "DEVICE_OFFLINE"


def test_start_gateway_unauthorized_is_401_and_audited(build_client):
    class _Unauthorized(MockIRGateway):
        def start_learn(self):
            raise GatewayError("unauthorized", "网关在线但鉴权被拒")

    client, context = build_client(gateway=_Unauthorized())
    device = create_device(client)
    response = client.post("/api/learn/start", json={"device_id": device["id"]})
    assert response.status_code == 401
    latest = context.audit.recent(1)[0]
    assert latest["action"] == "learn.start"
    assert latest["result"] == "failed"


# ---------------------------------------------------------------------- save


def test_save_persists_learned_function_and_code(client, context):
    device = start_session(client, context, draft=True)
    context.gateway.inject_test_waveform()
    client.get("/api/learn/capture", params={"device_id": device["id"]})

    body = save_function(client, context, device["id"])
    assert body["success"] is True
    assert body["function_id"].startswith("fn_")
    assert body["name"] == "开"
    assert body["receiver_stopped"] is True
    assert body["receiver_error"] is None
    assert context.gateway.learn_active is False

    detail = client.get(f"/api/devices/{device['id']}").json()
    assert detail["functions"] == [{"id": body["function_id"], "name": "开"}]
    assert detail["extra"] == {}  # 保存成功后不再是草稿

    with context.db.reader() as conn:
        rows = conn.execute(
            "SELECT raw_code FROM ir_codes WHERE function_id = ?", (body["function_id"],)
        ).fetchall()
    assert [json.loads(row["raw_code"]) for row in rows] == [TEST_WAVEFORM]


def test_save_without_session_404(client):
    device = create_device(client)
    response = client.post(
        "/api/learn/save",
        json={"device_id": device["id"], "name": "开", "raw_code": TEST_WAVEFORM},
    )
    assert response.status_code == 404


def test_save_empty_waveform_400(client, context):
    device = start_session(client, context)
    response = client.post(
        "/api/learn/save",
        json={"device_id": device["id"], "name": "开", "raw_code": []},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_REQUEST"


def test_save_allows_duplicate_function_names(client, context):
    device = create_device(client)
    for _ in range(2):
        client.post("/api/learn/start", json={"device_id": device["id"]})
        save_function(client, context, device["id"], name="开")

    detail = client.get(f"/api/devices/{device['id']}").json()
    assert [item["name"] for item in detail["functions"]] == ["开", "开"]


def test_codes_kept_to_latest_three(context):
    device = create_device(context_client(context), draft=True)
    created = context.devices.create_function_with_code(device["id"], "开", TEST_WAVEFORM)
    function_id = created["function_id"]

    for _ in range(4):
        context.devices.append_code(device["id"], function_id, TEST_WAVEFORM)

    with context.db.reader() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM ir_codes WHERE function_id = ?", (function_id,)
        ).fetchone()[0]
    assert count == 3


def context_client(context):
    from fastapi.testclient import TestClient

    from jj_butler.interface.api.app import create_app

    return TestClient(create_app(context))


# ------------------------------------------------------------- delete function


def test_delete_function_cascades_codes(client, context):
    device = start_session(client, context, draft=True)
    body = save_function(client, context, device["id"])
    context.devices.append_code(device["id"], body["function_id"], TEST_WAVEFORM)
    context.devices.append_code(device["id"], body["function_id"], TEST_WAVEFORM)

    response = client.delete(f"/api/devices/{device['id']}/functions/{body['function_id']}")
    assert response.status_code == 200
    assert response.json()["deleted"] == {"function_id": body["function_id"], "codes": 3}

    with context.db.reader() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ir_codes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM device_functions").fetchone()[0] == 0

    assert client.get(f"/api/devices/{device['id']}").status_code == 200
    again = client.delete(f"/api/devices/{device['id']}/functions/{body['function_id']}")
    assert again.status_code == 404


# -------------------------------------------------------------------- cancel


def test_cancel_discards_empty_draft_shell(client, context):
    device = start_session(client, context, draft=True)
    body = client.post("/api/learn/cancel", json={"device_id": device["id"]}).json()
    assert body["success"] is True
    assert body["draft_discarded"] is True
    assert body["message"] == "已取消学码，草稿设备已删除"
    assert client.get(f"/api/devices/{device['id']}").status_code == 404


def test_cancel_keeps_non_draft_device(client, context):
    device = create_device(client)
    body = client.post("/api/learn/cancel", json={"device_id": device["id"]}).json()
    assert body["draft_discarded"] is False
    assert client.get(f"/api/devices/{device['id']}").status_code == 200


def test_cancel_keeps_draft_with_data(client, context):
    device = start_session(client, context, draft=True)
    save_function(client, context, device["id"])
    body = client.post("/api/learn/cancel", json={"device_id": device["id"]}).json()
    assert body["draft_discarded"] is False
    assert client.get(f"/api/devices/{device['id']}").status_code == 200


def test_cancel_is_idempotent(client):
    device = create_device(client)
    first = client.post("/api/learn/cancel", json={"device_id": device["id"]})
    second = client.post("/api/learn/cancel", json={"device_id": device["id"]})
    assert first.status_code == second.status_code == 200
    assert second.json()["draft_discarded"] is False


# ---------------------------------------------------------------------- TTL


def test_idle_timeout_cleans_session_audits_and_discards_draft(build_client):
    client, context = build_client(idle_ttl=0.15)
    device = start_session(client, context, draft=True)

    # 不能用 capture 轮询：每次 capture 都会重置服务端 TTL，反而让会话永不超时。
    # 以超时审计出现为信号：它写在 stop_learn 与撤壳之后，出现即代表收尾完成。
    assert wait_for(
        lambda: bool(context.audit.recent(1))
        and context.audit.recent(1)[0]["action"] == "learn.idle_timeout"
    )
    assert client.get("/api/learn/capture", params={"device_id": device["id"]}).status_code == 404
    assert context.gateway.stop_count >= 1

    latest = context.audit.recent(1)[0]
    assert latest["action"] == "learn.idle_timeout"
    assert latest["who"] == "system"
    assert latest["result"] == "success"
    assert latest["reason"] == "空闲超时"
    assert latest["trace_id"]

    assert client.get(f"/api/devices/{device['id']}").status_code == 404


def test_idle_timeout_keeps_device_with_data(build_client):
    client, context = build_client(idle_ttl=0.15)
    device = start_session(client, context, draft=True)
    save_function(client, context, device["id"])

    context.gateway.inject_test_waveform()
    client.post("/api/learn/start", json={"device_id": device["id"]})

    # 同样不能用 capture 轮询（会续期）；以超时审计出现作为会话已回收的信号。
    assert wait_for(
        lambda: bool(context.audit.recent(1))
        and context.audit.recent(1)[0]["action"] == "learn.idle_timeout"
    )
    assert client.get("/api/learn/capture", params={"device_id": device["id"]}).status_code == 404

    assert client.get(f"/api/devices/{device['id']}").status_code == 200


def test_save_cancels_idle_timer(build_client):
    client, context = build_client(idle_ttl=0.15)
    device = start_session(client, context, draft=True)
    save_function(client, context, device["id"])
    time.sleep(0.35)

    actions = [row["action"] for row in context.audit.recent(20)]
    assert "learn.idle_timeout" not in actions


def test_cancel_cancels_idle_timer(build_client):
    client, context = build_client(idle_ttl=0.15)
    device = start_session(client, context)
    client.post("/api/learn/cancel", json={"device_id": device["id"]})
    time.sleep(0.35)

    actions = [row["action"] for row in context.audit.recent(20)]
    assert "learn.idle_timeout" not in actions


def test_capture_extends_idle_window(build_client):
    client, context = build_client(idle_ttl=0.6)
    device = start_session(client, context)
    time.sleep(0.3)
    assert client.get("/api/learn/capture", params={"device_id": device["id"]}).status_code == 200
    time.sleep(0.3)
    assert client.get("/api/learn/capture", params={"device_id": device["id"]}).status_code == 200


def test_learn_actions_are_audited_as_user(client, context):
    device = start_session(client, context, draft=True)
    save_function(client, context, device["id"])
    rows = context.audit.recent(5)
    actions = {(row["action"], row["who"]) for row in rows}
    assert ("learn.start", "user") in actions
    assert ("learn.save", "user") in actions
