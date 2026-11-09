"""real 网关：直连 ESPHome web_server REST API。

- 学码开/关：`POST /switch/ir_learn_mode/turn_on|turn_off`
- 读最近波形：`GET /text_sensor/ir_last_code`
- 回放波形：`POST /infrared/ir_proxy_tx/transmit`（form body）

波形编码为 base64url 无 padding 的小端 int32 数组，载波恒 38000 Hz；
Basic 鉴权只在用户名（strip 后）与密码都非空时发送。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx

from homelink.gateway.base import (
    GatewayError,
    GatewayOfflineError,
    GatewayTimeoutError,
    GatewayUnauthorizedError,
    IRGateway,
    ProbeOutcome,
)
from homelink.gateway.waveform import encode_waveform

# 发送/学码请求超时（秒）；探测用配置里的 probe_timeout_seconds。
LEARN_REQUEST_TIMEOUT_SECONDS = 10.0
IR_CARRIER_FREQUENCY_HZ = 38000


class RealIRGateway(IRGateway):
    mode = "real"
    simulated = False

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        probe_timeout_seconds: float = 3.0,
        request_timeout_seconds: float = LEARN_REQUEST_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url 不能为空（gateway.mode=real 时必须配置网关地址）")
        self.base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._probe_timeout = probe_timeout_seconds
        self._request_timeout = request_timeout_seconds
        self._transport = transport

    def _auth(self) -> tuple[str, str] | None:
        # 仅用户名（strip 后）与密码都非空才发 Authorization；密码不 strip。
        username = self._username.strip()
        if not username or not self._password:
            return None
        return (username, self._password)

    def _client(self, timeout: float) -> httpx.Client:
        # trust_env=False：局域网设备不能被宿主的 http_proxy 劫持。
        return httpx.Client(
            timeout=timeout, trust_env=False, auth=self._auth(), transport=self._transport
        )

    def _request(self, method: str, path: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            with self._client(timeout) as client:
                response = client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise GatewayTimeoutError(
                f"设备 {self._host()} 无响应（连接超时 {timeout:g}s）。"
                "请确认板子已上电并连到 Wi-Fi，或核对网关地址。"
            ) from exc
        except httpx.ConnectError as exc:
            raise GatewayOfflineError(
                f"连不上设备 {self._host()}（连接被拒绝或网络不通）。"
                "请确认板子和本机在同一个 Wi-Fi，或核对网关地址。"
            ) from exc
        except httpx.HTTPError as exc:
            raise GatewayOfflineError(
                f"调用设备 {self._host()} 失败：{exc}。请检查网关地址与设备状态。"
            ) from exc
        self._raise_for_status(response)
        return response

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise GatewayUnauthorizedError(
                f"设备 {self._host()} 已连上但鉴权被拒（HTTP {response.status_code}）："
                "请核对 settings.local.yaml 的 gateway.ir.username / gateway.ir.password。"
            )
        if response.status_code >= 500:
            raise GatewayOfflineError(
                f"设备 {self._host()} 在线，但服务返回异常（HTTP {response.status_code}）。"
                "请检查 ESPHome 是否正常运行，或重启设备。"
            )
        if response.status_code >= 400:
            raise GatewayError(
                "bad_response",
                f"设备 {self._host()} 返回 HTTP {response.status_code}，请检查请求参数。",
            )

    def _host(self) -> str:
        return urlsplit(self.base_url).netloc or self.base_url

    # ---------- 学码 ----------

    def start_learn(self) -> None:
        self._request(
            "POST",
            "/switch/ir_learn_mode/turn_on",
            timeout=self._request_timeout,
        )

    def stop_learn(self) -> None:
        self._request(
            "POST",
            "/switch/ir_learn_mode/turn_off",
            timeout=self._request_timeout,
        )

    def read_last_code(self) -> str | None:
        response = self._request("GET", "/text_sensor/ir_last_code", timeout=self._request_timeout)
        return _extract_text(response)

    # ---------- 回放 ----------

    def replay(self, raw_code: Sequence[int]) -> None:
        values = tuple(int(value) for value in raw_code)
        if not values:
            raise ValueError("没有可回放的波形（空码）")
        data = {
            "data": encode_waveform(list(values)),
            "carrier_frequency": str(IR_CARRIER_FREQUENCY_HZ),
        }
        self._request(
            "POST",
            "/infrared/ir_proxy_tx/transmit",
            timeout=self._request_timeout,
            data=data,
        )

    # ---------- 探测 ----------

    def probe(self) -> ProbeOutcome:
        try:
            with self._client(self._probe_timeout) as client:
                response = client.request("GET", f"{self.base_url}/")
        except httpx.TimeoutException:
            return ProbeOutcome(
                reachable=False,
                latency_ms=0,
                error=f"设备 {self._host()} 探测超时（{self._probe_timeout:g}s）。",
            )
        except httpx.HTTPError as exc:
            return ProbeOutcome(
                reachable=False,
                latency_ms=0,
                error=f"连不上设备 {self._host()}：{exc}",
            )
        if response.status_code in (401, 403):
            return ProbeOutcome(
                reachable=True,
                latency_ms=0,
                error=f"网关在线但鉴权被拒（HTTP {response.status_code}）",
            )
        if response.status_code >= 500:
            return ProbeOutcome(
                reachable=False,
                latency_ms=0,
                error=f"网关服务异常（HTTP {response.status_code}）",
            )
        return ProbeOutcome(reachable=True, latency_ms=0, error=None, simulated=False)


def _extract_text(response: httpx.Response) -> str | None:
    """兼容 ESPHome 的 JSON（{"value": ...} / {"state": ...}）与纯文本两种返回。"""

    body = response.text.strip()
    if not body:
        return None
    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(payload, dict):
        value = payload.get("state")
        if value is None:
            value = payload.get("value")
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    return str(payload).strip()
