"""real 网关：Basic 鉴权头规则、401/403/超时映射、回放 form body。"""

from __future__ import annotations

import base64
from urllib.parse import parse_qs

import httpx
import pytest

from jj_butler.gateway.base import GatewayOfflineError, GatewayUnauthorizedError
from jj_butler.gateway.real import IR_CARRIER_FREQUENCY_HZ, RealIRGateway
from jj_butler.gateway.waveform import decode_waveform, encode_waveform

BASE_URL = "http://gw.local"


def make_gateway(handler, *, username="", password="") -> RealIRGateway:
    return RealIRGateway(
        BASE_URL,
        username,
        password,
        transport=httpx.MockTransport(handler),
        request_timeout_seconds=1.0,
        probe_timeout_seconds=1.0,
    )


def test_no_authorization_header_without_both_credentials():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    for username, password in (("", ""), ("  ", "secret"), ("user", "")):
        gateway = make_gateway(handler, username=username, password=password)
        gateway.start_learn()
        assert "authorization" not in seen[-1].headers


def test_basic_authorization_header_when_both_credentials_present():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    gateway = make_gateway(handler, username="  user  ", password="pass")
    gateway.start_learn()

    expected = base64.b64encode(b"user:pass").decode("ascii")
    assert seen[-1].headers["authorization"] == f"Basic {expected}"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_rejection_maps_to_unauthorized(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    gateway = make_gateway(handler)
    with pytest.raises(GatewayUnauthorizedError) as caught:
        gateway.start_learn()
    assert caught.value.kind == "unauthorized"
    assert "鉴权被拒" in caught.value.message


def test_timeout_maps_to_offline():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    gateway = make_gateway(handler)
    with pytest.raises(GatewayOfflineError) as caught:
        gateway.start_learn()
    assert caught.value.kind == "unreachable"
    assert "无响应" in caught.value.message


def test_connect_error_maps_to_offline():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    gateway = make_gateway(handler)
    with pytest.raises(GatewayOfflineError) as caught:
        gateway.read_last_code()
    assert "连不上" in caught.value.message


def test_server_error_maps_to_offline():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    gateway = make_gateway(handler)
    with pytest.raises(GatewayOfflineError):
        gateway.stop_learn()


def test_replay_posts_base64url_waveform_and_carrier():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/infrared/ir_proxy_tx/transmit"
        form = parse_qs(request.content.decode("ascii"))
        captured.update({key: values[0] for key, values in form.items()})
        return httpx.Response(200)

    gateway = make_gateway(handler)
    gateway.replay([9000, -4500, 560])

    assert captured["carrier_frequency"] == str(IR_CARRIER_FREQUENCY_HZ)
    assert captured["data"] == encode_waveform([9000, -4500, 560])
    assert decode_waveform(captured["data"]) == [9000, -4500, 560]


def test_replay_rejects_empty_waveform():
    gateway = make_gateway(lambda request: httpx.Response(200))
    with pytest.raises(ValueError):
        gateway.replay([])


def test_read_last_code_accepts_json_and_plain_text():
    def json_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"state": "9000,-4500"})

    gateway = make_gateway(json_handler)
    assert gateway.read_last_code() == "9000,-4500"

    def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"state": ""})

    assert make_gateway(empty_handler).read_last_code() is None


def test_probe_marks_simulated_false_and_reports_offline():
    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    outcome = make_gateway(ok).probe()
    assert outcome.reachable is True
    assert outcome.simulated is False

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    outcome = make_gateway(down).probe()
    assert outcome.reachable is False
    assert "连不上" in (outcome.error or "")
