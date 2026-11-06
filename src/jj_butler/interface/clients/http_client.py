"""过渡前端的 HTTP 客户端：**只走 `/api` 契约**。

本模块只认识 `docs/api-contract.md` 里的路径、字段与错误码，不 import 任何后端
业务模块。Streamlit 页面全部通过它访问服务，换前端时这一层可以原样复用。

两件容易踩的事：

1. **进程级单例**：学码页用 `st.fragment` 每秒轮询，主脚本每轮 rerun 都会重建
   页面对象；若每次新建再 `close()`，轮询会拿到已关闭的 `httpx.Client`，抛
   `RuntimeError`。所以这里用模块级单例，页面与轮询**永远不 close**。
2. **两类「连不上」必须分开**：`AGENT_UNREACHABLE` 表示请求没有到达管家后端；
   `DEVICE_OFFLINE` 表示后端正常、红外网关/板子不通。文案与下一步完全不同。
"""

from __future__ import annotations

import atexit
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 30.0

# 前端自造码：请求没有到达管家后端（区别于后端返回的 DEVICE_OFFLINE）
AGENT_UNREACHABLE = "AGENT_UNREACHABLE"


class ApiError(Exception):
    """统一错误：错误码、服务端原文与人话在展示层分开处理。

    `code` 不直接展示给用户；展示层用 `streamlit/errors.py` 翻成人话。
    """

    def __init__(self, code: str, message: str = "", trace_id: str = "") -> None:
        self.code = code
        self.message = message
        self.trace_id = trace_id
        super().__init__(f"[{code}] {message}" if message else f"[{code}]")


@dataclass
class AgentClient:
    """管家服务的契约客户端。

    `token` 由启动脚本注入；服务端 token 为空时客户端也不发鉴权头。
    `transport` 仅供测试注入 `httpx.MockTransport`。
    """

    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    token: str = ""
    transport: httpx.BaseTransport | None = None
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # trust_env=False：不读 HTTP_PROXY 等环境变量，本机/局域网直连不能被代理劫持。
        self._client = httpx.Client(
            timeout=self.timeout,
            transport=self.transport,
            trust_env=False,
        )

    # ---------- 健康检查 ----------

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    # ---------- 状态 ----------

    def state(self) -> dict[str, Any]:
        return self._request("GET", "/api/state")

    # ---------- 设备 ----------

    def devices(self, *, room: str | None = None) -> dict[str, Any]:
        params = {"room": room} if room else None
        return self._request("GET", "/api/devices", params=params)

    def device(self, device_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/devices/{device_id}")

    def create_device(
        self,
        name: str,
        room: str,
        *,
        notes: str = "",
        learn_draft: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "room": room}
        if notes:
            body["notes"] = notes
        if learn_draft:
            body["learn_draft"] = True
        return self._request("POST", "/api/devices", json=body)

    def update_device(
        self,
        device_id: str,
        *,
        name: str | None = None,
        room: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        body = {
            key: value
            for key, value in (("name", name), ("room", room), ("notes", notes))
            if value is not None
        }
        return self._request("PATCH", f"/api/devices/{device_id}", json=body)

    def delete_device(self, device_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/api/devices/{device_id}")

    def delete_function(self, device_id: str, function_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/api/devices/{device_id}/functions/{function_id}")

    # ---------- 房间 ----------

    def rooms(self) -> dict[str, Any]:
        return self._request("GET", "/api/rooms")

    def create_room(self, name: str, display_name: str, *, sort_order: int = 0) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/rooms",
            json={"name": name, "display_name": display_name, "sort_order": sort_order},
        )

    def update_room(
        self,
        name: str,
        *,
        new_name: str | None = None,
        display_name: str | None = None,
        sort_order: int | None = None,
    ) -> dict[str, Any]:
        body = {
            key: value
            for key, value in (
                ("name", new_name),
                ("display_name", display_name),
                ("sort_order", sort_order),
            )
            if value is not None
        }
        return self._request("PATCH", f"/api/rooms/{name}", json=body)

    def delete_room(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", f"/api/rooms/{name}")

    # ---------- 学码 ----------

    def learn_start(self, device_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/learn/start", json={"device_id": device_id})

    def learn_capture(self, device_id: str) -> dict[str, Any]:
        return self._request("GET", "/api/learn/capture", params={"device_id": device_id})

    def learn_save(self, device_id: str, name: str, raw_code: list[int]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/learn/save",
            json={"device_id": device_id, "name": name, "raw_code": raw_code},
        )

    def learn_cancel(self, device_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/learn/cancel", json={"device_id": device_id})

    # ---------- 控制 ----------

    def control(self, function_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/control", json={"function_id": function_id})

    # ---------- 校正 ----------

    def correct(self, device_id: str, state: str, reason: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"device_id": device_id, "state": state}
        if reason:
            body["reason"] = reason
        return self._request("POST", "/api/correct", json=body)

    # ---------- 网关 ----------

    def gateway(self, *, refresh: bool = False) -> dict[str, Any]:
        # 只有用户点「重新检测」才带 refresh=true；普通轮询走 15 秒探测缓存。
        params = {"refresh": "true"} if refresh else None
        return self._request("GET", "/api/gateway", params=params)

    def update_gateway(self, base_url: str) -> dict[str, Any]:
        return self._request("PUT", "/api/gateway", json={"base_url": base_url})

    # ---------- 审计 ----------

    def audit(
        self,
        *,
        limit: int = 50,
        result: str | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if result:
            params["result"] = result
        if device_id:
            params["device_id"] = device_id
        return self._request("GET", "/api/audit", params=params)

    # ---------- 收尾（页面与轮询不得调用） ----------

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,  # noqa: A002 - 与 httpx 参数同名
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}"
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            response = self._client.request(method, url, json=json, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise ApiError(
                AGENT_UNREACHABLE,
                "请求没有及时返回，请稍后重试。",
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(
                AGENT_UNREACHABLE,
                "请求没有到达后端，请确认后端已启动、服务地址正确。",
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(
                "INTERNAL_ERROR",
                "服务端返回的内容无法识别。",
                _trace_of(response),
            ) from exc
        if not isinstance(payload, dict):
            raise ApiError(
                "INTERNAL_ERROR",
                "服务端返回的数据格式不对。",
                _trace_of(response),
            )

        # 只按 success is False 判错：GET /api/gateway 在 200 里也带 error（探测原因）。
        if response.status_code >= 400 or payload.get("success") is False:
            raise ApiError(
                str(payload.get("error") or "INTERNAL_ERROR"),
                str(payload.get("message") or ""),
                str(payload.get("trace_id") or _trace_of(response)),
            )
        return payload


def _trace_of(response: httpx.Response) -> str:
    """响应头里的 trace（错误体缺少 trace_id 时兜底）。"""

    return response.headers.get("X-Trace-Id", "")


_SHARED: AgentClient | None = None
_SHARED_KEY: tuple[str, str] | None = None


def shared_client(base_url: str = DEFAULT_BASE_URL, token: str = "") -> AgentClient:
    """整个进程共用的活客户端（Streamlit 页面与轮询都用它）。

    **任何页面/轮询都不得 `close()`**；只在进程退出时由 `atexit` 关闭。
    base_url/token 只在启动脚本注入一次，正常路径不会变化。
    """

    global _SHARED, _SHARED_KEY
    key = (base_url.rstrip("/"), token)
    if _SHARED is None or key != _SHARED_KEY:
        client = AgentClient(base_url=base_url, token=token)
        atexit.register(client.close)
        _SHARED = client
        _SHARED_KEY = key
    return _SHARED
