"""请求体模型：未声明字段一律拒绝（extra=forbid）。"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoomCreateRequest(_StrictBody):
    name: str
    display_name: str
    sort_order: int = 0

    @field_validator("name", "display_name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class RoomPatchRequest(_StrictBody):
    name: str | None = None
    display_name: str | None = None
    sort_order: int | None = None

    @field_validator("name", "display_name")
    @classmethod
    def _non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class DeviceCreateRequest(_StrictBody):
    name: str
    room: str
    notes: str | None = None

    @field_validator("name", "room")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class DevicePatchRequest(_StrictBody):
    name: str | None = None
    room: str | None = None
    notes: str | None = None

    @field_validator("name", "room")
    @classmethod
    def _non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class LearnStartRequest(_StrictBody):
    device_id: str

    @field_validator("device_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class LearnSaveRequest(_StrictBody):
    device_id: str
    # 功能名在开始学码前就由用户确定；40 字符上限与前端输入框一致。
    name: str = Field(min_length=1, max_length=40)
    raw_code: list[int] = Field(min_length=1)

    @field_validator("device_id", "name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class LearnCancelRequest(_StrictBody):
    device_id: str

    @field_validator("device_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class ControlRequest(_StrictBody):
    """只接受 `function_id`；`device_id` / `action` / `params` 一律未声明字段。"""

    function_id: str

    @field_validator("function_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class CorrectRequest(_StrictBody):
    device_id: str
    state: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=128)

    @field_validator("device_id", "state")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class GatewayBaseUrlRequest(_StrictBody):
    base_url: str

    @field_validator("base_url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        parts = urlsplit(text)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("必须是带 host 的 http/https 地址")
        return text


class AgentControlRequest(_StrictBody):
    device_id: str
    command: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(min_length=1, max_length=64)
    confirm: bool = False

    @field_validator("device_id", "command", "request_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class AgentSceneItem(_StrictBody):
    device_id: str
    command: str

    @field_validator("device_id", "command")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class AgentSceneCreateRequest(_StrictBody):
    name: str = Field(min_length=1, max_length=40)
    items: list[AgentSceneItem] = Field(min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class AgentMetadataRequest(_StrictBody):
    type: str | None = Field(default=None, max_length=32)
    aliases: list[str] | None = Field(default=None, max_length=20)
    risk_level: Literal["low", "high"] | None = None

    @field_validator("type")
    @classmethod
    def _type_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text

    @field_validator("aliases")
    @classmethod
    def _aliases_valid(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = [item.strip() for item in value if item.strip()]
        if any(len(item) > 40 for item in cleaned):
            raise ValueError("单个别名不能超过 40 个字符")
        return cleaned
