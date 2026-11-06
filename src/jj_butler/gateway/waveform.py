"""波形编解码：base64url 无 padding 的小端 int32 数组（正数 mark、负数 space，微秒）。"""

from __future__ import annotations

import base64
import json
import struct


class WaveformError(ValueError):
    pass


def encode_waveform(values: list[int]) -> str:
    payload = b"".join(struct.pack("<i", int(value)) for value in values)
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_waveform(text: str) -> list[int]:
    """解析网关读到的最近波形；JSON 数组与 base64url 两种形态都接受。"""

    stripped = text.strip()
    if not stripped:
        raise WaveformError("空波形")

    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise WaveformError("波形 JSON 不合法") from exc
        if not isinstance(parsed, list) or not parsed:
            raise WaveformError("波形数组为空")
        values: list[int] = []
        for item in parsed:
            if isinstance(item, bool) or not isinstance(item, int):
                raise WaveformError("波形元素必须是整数")
            values.append(item)
        return values

    padded = stripped + "=" * (-len(stripped) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise WaveformError("波形不是合法的 base64url") from exc
    if not raw or len(raw) % 4:
        raise WaveformError("波形长度不是 4 字节的整数倍")

    values = [struct.unpack("<i", raw[index : index + 4])[0] for index in range(0, len(raw), 4)]
    if not values:
        raise WaveformError("波形数组为空")
    return values
