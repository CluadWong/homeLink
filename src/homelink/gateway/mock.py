"""mock 网关：不触网，默认 no_signal，可显式注入固定波形。"""

from __future__ import annotations

from collections.abc import Sequence

from homelink.gateway.base import IRGateway, ProbeOutcome
from homelink.gateway.waveform import encode_waveform

# 固定测试波形（mark/space 微秒），仅用于联调。
TEST_WAVEFORM: list[int] = [9000, -4500, 560, -560, 560, -1690, 560]


class MockIRGateway(IRGateway):
    mode = "mock"
    base_url = ""
    simulated = True

    def __init__(self, *, simulate_learn: bool = False) -> None:
        self.learn_active = False
        self.start_count = 0
        self.stop_count = 0
        self.read_count = 0
        self.replayed: list[tuple[int, ...]] = []
        self._injected_code: str | None = None
        if simulate_learn:
            self.inject_test_waveform()

    def inject_last_code(self, raw_code: str | None) -> None:
        """显式注入下一次读到的内容；None 恢复默认 no_signal。"""

        self._injected_code = raw_code

    def inject_test_waveform(self) -> None:
        self._injected_code = encode_waveform(TEST_WAVEFORM)

    def start_learn(self) -> None:
        self.learn_active = True
        self.start_count += 1

    def stop_learn(self) -> None:
        # 幂等：重复关闭也成功。
        self.learn_active = False
        self.stop_count += 1

    def read_last_code(self) -> str | None:
        self.read_count += 1
        return self._injected_code

    def replay(self, raw_code: Sequence[int]) -> None:
        values = tuple(int(value) for value in raw_code)
        if not values:
            raise ValueError("没有可回放的波形（空码）")
        self.replayed.append(values)

    def probe(self) -> ProbeOutcome:
        return ProbeOutcome(reachable=True, latency_ms=0, error=None, simulated=True)
