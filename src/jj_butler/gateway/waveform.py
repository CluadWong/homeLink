"""波形编解码：真机是逗号分隔的微秒序列，mock 用 base64url 小端 int32。

ESPHome 的 `text_sensor/ir_last_code` 真机输出形如
``"8997,-4482,566,-1673,566,-553,…,566,-10000"``（正数 mark、负数 space，微秒）；
mock 仍沿用 base64url（无 padding）的小端 int32 数组。两种形态都在这里统一成
``list[int]``，上层不感知传输形态。
"""

from __future__ import annotations

import base64
import re
import struct

# 一段完整波形至少要有「头 mark + 头 space + 至少一个 mark」，再短就不是红外帧了。
MIN_WAVEFORM_SEGMENTS = 3

_INT_ONLY = re.compile(r"-?\d+")


class WaveformError(ValueError):
    """波形无法解析；`kind` 用来区分空值 / 格式不认识 / 长度不足。"""

    kind = "format"
    label = "格式不认识"


class WaveformEmptyError(WaveformError):
    kind = "empty"
    label = "空值"


class WaveformFormatError(WaveformError):
    kind = "format"
    label = "格式不认识"


class WaveformLengthError(WaveformError):
    kind = "length"
    label = "长度不足"


def encode_waveform(values: list[int]) -> str:
    payload = b"".join(struct.pack("<i", int(value)) for value in values)
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_waveform(text: str) -> list[int]:
    """解析网关读到的最近波形。

    接受三种常见形态：真机的逗号分隔序列、带方括号的序列（JSON 数组或字符串）、
    以及 mock 的 base64url。格式不认识时抛 `WaveformFormatError`，并保留原有
    「空值 / 长度不足」的独立错误类型。
    """

    stripped = text.strip()
    if not stripped:
        raise WaveformEmptyError("板子返回空值")

    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1].strip()
        if not stripped:
            raise WaveformEmptyError("波形数组为空")

    if "," in stripped:
        values = _parse_int_list(stripped)
    elif _INT_ONLY.fullmatch(stripped):
        values = [int(stripped)]
    else:
        values = _decode_base64(stripped)

    if not values:
        raise WaveformEmptyError("波形数组为空")
    if len(values) < MIN_WAVEFORM_SEGMENTS:
        raise WaveformLengthError(
            f"只收到 {len(values)} 段，至少需要 {MIN_WAVEFORM_SEGMENTS} 段"
        )
    return values


def _parse_int_list(text: str) -> list[int]:
    values: list[int] = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            raise WaveformFormatError(f"波形里有空段：{text[:60]}…")
        try:
            values.append(int(token))
        except ValueError as exc:
            raise WaveformFormatError(f"波形里出现了非数字：{token[:20]!r}") from exc
    return values


def _decode_base64(text: str) -> list[int]:
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise WaveformFormatError("波形不是逗号分隔数字、JSON 数组或合法 base64url") from exc
    if not raw:
        raise WaveformEmptyError("波形为空")
    if len(raw) % 4:
        raise WaveformLengthError("base64 波形长度不是 4 字节的整数倍")

    values = [struct.unpack("<i", raw[index : index + 4])[0] for index in range(0, len(raw), 4)]
    if not values:
        raise WaveformEmptyError("波形数组为空")
    return values
