"""网关层：波形编解码、mock 行为、探测缓存。"""

from __future__ import annotations

from pathlib import Path

import pytest

from homelink.gateway.base import GatewayError, ProbeOutcome
from homelink.gateway.mock import TEST_WAVEFORM, MockIRGateway
from homelink.gateway.probe import GatewayProbe
from homelink.gateway.waveform import (
    WaveformEmptyError,
    WaveformError,
    WaveformFormatError,
    WaveformLengthError,
    decode_waveform,
    encode_waveform,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_waveform_roundtrip():
    assert decode_waveform(encode_waveform([9000, -4500, 560])) == [9000, -4500, 560]
    assert decode_waveform("[1, -2, 3]") == [1, -2, 3]


def test_decode_real_board_csv_sample():
    """真机 `ir_last_code` 原样是逗号分隔字符串（2026-11-08 从板子 GET 取得）。"""

    text = (FIXTURES / "ir_last_code_real.txt").read_text(encoding="utf-8")
    values = decode_waveform(text)
    assert len(values) == 100
    assert values[0] == 8997
    assert values[1] == -4482
    assert values[-1] == -10000


def test_decode_bracketed_csv_sample():
    text = (FIXTURES / "ir_last_code_real.txt").read_text(encoding="utf-8")
    assert decode_waveform(f"[{text}]") == decode_waveform(text)


def test_decode_distinguishes_malformed_kinds():
    with pytest.raises(WaveformEmptyError) as empty:
        decode_waveform("  ")
    assert empty.value.kind == "empty"

    with pytest.raises(WaveformFormatError) as fmt:
        decode_waveform("abc,def")
    assert fmt.value.kind == "format"

    with pytest.raises(WaveformLengthError) as short:
        decode_waveform("9000")
    assert short.value.kind == "length"


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


def test_probe_401_is_reachable_but_does_not_refresh_success_time():
    class _AuthRejected(MockIRGateway):
        calls = 0

        def probe(self) -> ProbeOutcome:
            type(self).calls += 1
            if type(self).calls == 1:
                return ProbeOutcome(reachable=True, latency_ms=0, error=None)
            return ProbeOutcome(
                reachable=True,
                latency_ms=0,
                error="网关在线但鉴权被拒（HTTP 401）",
            )

    probe = GatewayProbe(_AuthRejected(), ttl_seconds=15.0)
    ok = probe.status()
    assert ok["last_success_at"] is not None

    denied = probe.status(refresh=True)
    assert denied["reachable"] is True
    assert "鉴权被拒" in denied["error"]
    assert denied["last_success_at"] == ok["last_success_at"]


def test_real_gateway_requires_both_credentials():
    from homelink.gateway.real import RealIRGateway

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
