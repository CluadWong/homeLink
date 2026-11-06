"""`GET /api/audit`：倒序、limit 边界、result 与 device_id 过滤。"""

from __future__ import annotations


def _write(
    context,
    action: str,
    result: str,
    *,
    reason: str | None = None,
    trace: str = "t",
    device_id: str | None = None,
):
    context.audit.write(
        who="user",
        action=action,
        device_id=device_id,
        result=result,
        reason=reason,
        trace_id=trace,
    )


def _seed(context) -> None:
    _write(context, "dev_a.control", "success", reason="已发送", trace="t1", device_id="dev_a")
    _write(context, "dev_a.control", "failed", reason="连不上", trace="t2", device_id="dev_a")
    _write(context, "dev_b.control", "success", reason="已发送", trace="t3", device_id="dev_b")
    _write(context, "learn.start", "success", reason=None, trace="t4")


def test_audit_default_is_descending_and_has_contract_fields(client, context):
    _seed(context)
    body = client.get("/api/audit").json()
    assert body["success"] is True
    assert body["trace_id"]
    assert [record["action"] for record in body["records"]] == [
        "learn.start",
        "dev_b.control",
        "dev_a.control",
        "dev_a.control",
    ]
    record = body["records"][0]
    assert set(record) == {"created_at", "action", "device_id", "result", "reason", "trace_id"}
    assert record["device_id"] is None
    assert body["records"][2]["device_id"] == "dev_a"
    assert body["records"][1]["device_id"] == "dev_b"


def test_audit_filters_by_result_and_device(client, context):
    _seed(context)

    failures = client.get("/api/audit", params={"result": "failed"}).json()["records"]
    assert len(failures) == 1
    assert failures[0]["trace_id"] == "t2"

    successes = client.get("/api/audit", params={"result": "success"}).json()["records"]
    assert len(successes) == 3

    by_device = client.get("/api/audit", params={"device_id": "dev_a"}).json()["records"]
    assert len(by_device) == 2
    assert all(record["device_id"] == "dev_a" for record in by_device)

    both = client.get("/api/audit", params={"device_id": "dev_a", "result": "success"}).json()[
        "records"
    ]
    assert len(both) == 1
    assert both[0]["trace_id"] == "t1"


def test_audit_limit_default_and_bounds(client, context):
    for index in range(60):
        _write(context, "dev_x.control", "success", trace=f"t{index}")

    default = client.get("/api/audit").json()["records"]
    assert len(default) == 50
    assert default[0]["trace_id"] == "t59"
    assert default[-1]["trace_id"] == "t10"

    assert len(client.get("/api/audit", params={"limit": "1"}).json()["records"]) == 1
    assert len(client.get("/api/audit", params={"limit": "200"}).json()["records"]) == 60

    for limit in ("0", "201", "-1"):
        response = client.get("/api/audit", params={"limit": limit})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_REQUEST"


def test_audit_invalid_result_400(client):
    response = client.get("/api/audit", params={"result": "denied"})
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_REQUEST"
