"""网关层：波形编解码、mock 行为、探测缓存。"""

from __future__ import annotations

import pytest

from jj_butler.gateway.base import GatewayError, ProbeOutcome
from jj_butler.gateway.mock import TEST_WAVEFORM, MockIRGateway
from jj_butler.gateway.probe import GatewayProbe
from jj_butler.gateway.waveform import WaveformError, decode_waveform, encode_waveform


def test_waveform_roundtrip():
    assert decode_waveform(encode_waveform([9000, -4500, 560])) == [9000, -4500, 560]
    assert decode_waveform("[1, -2, 3]") == [1, -2, 3]


@pytest.mark.parametrize("bad", ["", "   ", "!!!", "AA", "AAAA", "[]", '["x"]', "[1, true]"])
def test_decode_rejects_malformed(bad):
    with pytest.raises(WaveformError):
        decode_waveform(bad)


def test_mock_default_is_no_signal():
    gateway = MockIRGateway()
    assert gateway.read_last_code() is None


def test_mock_learn_toggle_is_idempotent():
    gateway = MockIRGateway()
    gateway.start_learn()
    assert gateway.learn_active is True
    gateway.stop_learn()
    gateway.stop_learn()
    assert gateway.learn_active is False
    assert gateway.stop_count == 2


def test_mock_inject_fixed_waveform():
    gateway = MockIRGateway()
    gateway.inject_test_waveform()
    raw = gateway.read_last_code()
    assert raw is not None
    assert decode_waveform(raw) == TEST_WAVEFORM
    gateway.inject_last_code("bad")
    assert gateway.read_last_code() == "bad"


def test_mock_probe_shape():
    outcome = MockIRGateway().probe()
    assert outcome.reachable is True
    assert outcome.simulated is True
    assert outcome.error is None


def test_mock_replay_records_waveform():
    gateway = MockIRGateway()
    gateway.replay([9000, -4500, 560])
    assert gateway.replayed == [(9000, -4500, 560)]
    with pytest.raises(ValueError):
        gateway.replay([])


class _CountingGateway(MockIRGateway):
    def __init__(self) -> None:
        super().__init__()
        self.probe_calls = 0

    def probe(self) -> ProbeOutcome:
        self.probe_calls += 1
        return ProbeOutcome(reachable=True, latency_ms=0, error=None)


def test_probe_cached_for_15_seconds_and_refresh_bypasses():
    now = [0.0]
    gateway = _CountingGateway()
    probe = GatewayProbe(gateway, ttl_seconds=15.0, clock=lambda: now[0])

    first = probe.status()
    second = probe.status()
    assert gateway.probe_calls == 1
    assert first["last_success_at"] == second["last_success_at"]
    assert second["simulated"] is True and second["base_url"] == ""

    now[0] = 14.9
    probe.status()
    assert gateway.probe_calls == 1

    now[0] = 15.1
    probe.status()
    assert gateway.probe_calls == 2

    probe.status(refresh=True)
    assert gateway.probe_calls == 3


def test_probe_marks_unreachable():
    class _Down(MockIRGateway):
        def probe(self) -> ProbeOutcome:
            return ProbeOutcome(reachable=False, latency_ms=0, error="boom")

    probe = GatewayProbe(_Down(), ttl_seconds=15.0)
    result = probe.status()
    assert result["reachable"] is False
    assert result["error"] == "boom"
    assert result["last_success_at"] is None


def test_real_gateway_requires_both_credentials():
    from jj_butler.gateway.real import RealIRGateway

    gateway = RealIRGateway("http://gw.local", "  ", "secret")
    assert gateway._auth() is None  # 用户名空 → 不发鉴权
    gateway = RealIRGateway("http://gw.local", "user", "")
    assert gateway._auth() is None  # 密码空 → 不发鉴权
    gateway = RealIRGateway("http://gw.local", "user", "secret")
    assert gateway._auth() == ("user", "secret")
    assert gateway.mode == "real" and gateway.simulated is False


def test_gateway_error_kinds():
    with pytest.raises(GatewayError) as caught:
        raise GatewayError("unreachable", "连不上")
    assert caught.value.kind == "unreachable"
