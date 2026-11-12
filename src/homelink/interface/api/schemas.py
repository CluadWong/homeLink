"""请求体模型：未声明字段一律拒绝（extra=forbid）。"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class AgentChatRequest(_StrictBody):
    """界面文字对话：长度与 agentd 一致；不带 session_id 表示新会话。"""

    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)

    @field_validator("message")
    @classmethod
    def _message_non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
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


class AgentSceneTarget(_StrictBody):
    device_id: str

    @field_validator("device_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class AgentSceneActionGroup(_StrictBody):
    """动作组：一条能力 + 一组支持该能力的设备。"""

    name: str | None = Field(default=None, max_length=40)
    capability: str = Field(min_length=1, max_length=40)
    targets: list[AgentSceneTarget] = Field(min_length=1, max_length=20)

    @field_validator("capability")
    @classmethod
    def _capability_non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text

    @field_validator("name")
    @classmethod
    def _name_optional(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value.strip() or None

    @field_validator("targets")
    @classmethod
    def _targets_unique(cls, value: list[AgentSceneTarget]) -> list[AgentSceneTarget]:
        device_ids = [target.device_id for target in value]
        if len(set(device_ids)) != len(device_ids):
            raise ValueError("同一动作组内的设备不能重复")
        return value


class AgentSceneTrigger(_StrictBody):
    """触发器是策略对象：类型决定 config 形状，各类型实现互不依赖。"""

    type: Literal["manual", "schedule", "voice", "device_state"]
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _config_matches_type(self) -> AgentSceneTrigger:
        if self.type == "schedule":
            cron = self.config.get("cron")
            if not isinstance(cron, str) or len(cron.split()) != 5:
                raise ValueError("schedule 触发器需要 5 段 cron 表达式")
        elif self.type == "voice":
            phrases = self.config.get("phrases")
            if (
                not isinstance(phrases, list)
                or not phrases
                or not all(isinstance(item, str) and item.strip() for item in phrases)
            ):
                raise ValueError("voice 触发器需要非空的 phrases 列表")
            if any(len(item.strip()) > 40 for item in phrases if isinstance(item, str)):
                raise ValueError("voice 短语不能超过 40 个字符")
        elif self.type == "device_state":
            device_id = self.config.get("device_id")
            if not isinstance(device_id, str) or not device_id.strip():
                raise ValueError("device_state 触发器需要 device_id")
        return self


class AgentSceneCreateRequest(_StrictBody):
    """`items` 为旧契约（每项一个功能），`action_groups` 为按能力归组的新契约。"""

    name: str = Field(min_length=1, max_length=40)
    enabled: bool = True
    items: list[AgentSceneItem] | None = None
    action_groups: list[AgentSceneActionGroup] | None = None
    triggers: list[AgentSceneTrigger] | None = None

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text

    @model_validator(mode="after")
    def _one_action_source(self) -> AgentSceneCreateRequest:
        if (self.items is None) == (self.action_groups is None):
            raise ValueError("items 与 action_groups 必须且只能提供一种")
        if self.items is not None and not 1 <= len(self.items) <= 20:
            raise ValueError("items 需要 1–20 条")
        if self.action_groups is not None and not 1 <= len(self.action_groups) <= 20:
            raise ValueError("action_groups 需要 1–20 个")
        if self.triggers is not None and len(self.triggers) > 10:
            raise ValueError("triggers 最多 10 个")
        return self


class AgentScenePatchRequest(_StrictBody):
    name: str | None = Field(default=None, max_length=40)
    enabled: bool | None = None
    action_groups: list[AgentSceneActionGroup] | None = None
    triggers: list[AgentSceneTrigger] | None = None

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text

    @model_validator(mode="after")
    def _at_least_one_field(self) -> AgentScenePatchRequest:
        if (
            self.name is None
            and self.enabled is None
            and self.action_groups is None
            and self.triggers is None
        ):
            raise ValueError("至少要修改 name / enabled / action_groups / triggers 之一")
        if self.action_groups is not None and not 1 <= len(self.action_groups) <= 20:
            raise ValueError("action_groups 需要 1–20 个")
        if self.triggers is not None and len(self.triggers) > 10:
            raise ValueError("triggers 最多 10 个")
        return self


class AgentSceneRunRequest(_StrictBody):
    """跑场景的可选请求体；包含高风险设备的场景必须 confirm=true。"""

    confirm: bool = False


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
