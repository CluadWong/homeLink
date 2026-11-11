"""调度状态代理端点：agentd 可达与不可达两种表现。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from homelink.interface.api.app import create_app
from homelink.interface.clients.agentd import AgentdUnavailableError

_FAILED_SCHEDULE: dict[str, Any] = {
    "id": "s1",
    "scene_id": "scene-1",
    "cron": "0 23 * * *",
    "enabled": True,
    "confirm": False,
    "created_at": "2026-11-11T20:00:00",
    "last_run_at": "2026-11-11T23:00:00",
    "last_status": "error",
    "last_error": "RuntimeError: 场景执行 partial",
    "source": "scene",
    "trigger_id": "trigger-1",
}


class _StubAgentd:
    def __init__(self, items: list[dict[str, Any]], *, error: bool = False) -> None:
        self._items = items
        self._error = error
        self.calls = 0

    def list_schedules(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self._error:
            raise AgentdUnavailableError("connection refused")
        return self._items


def test_schedules_are_proxied_with_last_status(context) -> None:
    app = create_app(context)
    app.state.agentd = _StubAgentd([_FAILED_SCHEDULE])

    with TestClient(app) as client:
        response = client.get("/api/agent/schedules")

    payload = response.json()
    assert response.status_code == 200
    assert payload["result"]["count"] == 1
    schedule = payload["result"]["schedules"][0]
    assert schedule["last_status"] == "error"
    assert schedule["last_error"] == "RuntimeError: 场景执行 partial"
    assert app.state.agentd.calls == 1


def test_schedules_unavailable_maps_to_503(context) -> None:
    app = create_app(context)
    app.state.agentd = _StubAgentd([], error=True)

    with TestClient(app) as client:
        response = client.get("/api/agent/schedules")

    payload = response.json()
    assert response.status_code == 503
    assert payload["success"] is False
    assert payload["error"] == "DEVICE_OFFLINE"
    assert "agentd" in payload["message"]
