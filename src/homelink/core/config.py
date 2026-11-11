"""配置服务：配置读写的唯一入口。

- 读：`settings.yaml` / `devices.yaml`，再与同名 `*.local.yaml` 深合并（后者优先）；
- 写：只经 `ConfigService`；原子落盘（临时文件 + `os.replace`），失败回滚并写 `config_audit`；
- 身份与来源：`who` ∈ user/system，`source` ∈ startup/api。

配置写入失败必须留痕：校验失败写 `rejected`，落盘失败写 `rejected`，
审计写入失败则恢复旧文件并写 `rolled_back`（不能「改了但没记录」）。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from homelink.core.database import Database

LOCAL_OVERLAY_SUFFIX = ".local"

_KNOWN_FILES = {"settings.yaml", "devices.yaml"}
_WHO_VALUES = {"user", "system"}
_SOURCE_VALUES = {"startup", "api"}

_DOC_HINT = "配置说明见 docs/README.md「配置在哪」。"

# 缺文件时直接给可复制的示例，避免使用者去代码里猜格式。
_SETTINGS_MINIMAL = """gateway:
  mode: mock
  ir:
    base_url: ""
    username: ""
    password: ""

frontend:
  enabled: true
  dist_path: "frontend/dist"
"""

_DEVICES_MINIMAL = """rooms: []
"""


def _missing_config_message(path: Path) -> str:
    """必需配置缺失时的人话：文件全路径 + 最小示例 + 文档指针。"""

    example = _SETTINGS_MINIMAL if path.name == "settings.yaml" else _DEVICES_MINIMAL
    return (
        f"配置不可用：找不到必需配置文件 {path}\n"
        "请创建该文件（下面是最小可用示例；mock 模式不会连接真实板子、不会发红外）：\n\n"
        f"{example}\n"
        f"{_DOC_HINT}"
    )


def _broken_config_message(path: Path, detail: str) -> str:
    """配置存在但解析/形状不合法时的人话。"""

    return (
        f"配置不可用：配置文件无法解析 {path}\n"
        f"原因：{detail}\n"
        "请检查 YAML 缩进与冒号（`key: value` 的冒号后要有空格），"
        f"或按最小示例重写该文件。{_DOC_HINT}"
    )


class ConfigError(RuntimeError):
    """配置不合法或写入失败：原文件保持不变。"""


class ConfigValidationError(ConfigError):
    """配置校验失败。"""


@dataclass(frozen=True)
class GatewayConfig:
    mode: str
    base_url: str
    username: str
    password: str
    probe_timeout_seconds: float


@dataclass(frozen=True)
class RoomSeed:
    name: str
    display_name: str
    sort_order: int


@dataclass(frozen=True)
class FrontendConfig:
    """生产静态托管：`dist` 存在时同源提供页面，否则只提供 API。"""

    enabled: bool = True
    dist_path: str = "frontend/dist"


@dataclass(frozen=True)
class Settings:
    gateway: GatewayConfig
    state_retention_days: int
    audit_retention_days: int
    command_retention_days: int
    api_token: str
    frontend: FrontendConfig = field(default_factory=FrontendConfig)


@dataclass(frozen=True)
class ConfigAuditRecord:
    """一条配置变更审计，字段对齐 `config_audit` 表。"""

    file: str
    key_path: str
    old_value: Any
    new_value: Any
    who: str
    source: str
    result: str
    trace_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None


class ConfigAuditStore(Protocol):
    """配置审计存储接口。"""

    def append(self, record: ConfigAuditRecord) -> None:
        """写入一条审计记录。"""

    def records(self) -> list[ConfigAuditRecord]:
        """按写入顺序返回全部审计记录。"""


class InMemoryConfigAuditStore:
    """内存审计存储，用于单元测试与无数据库场景。"""

    def __init__(self) -> None:
        self._records: list[ConfigAuditRecord] = []

    def append(self, record: ConfigAuditRecord) -> None:
        self._records.append(record)

    def records(self) -> list[ConfigAuditRecord]:
        return list(self._records)


class SqliteConfigAuditStore:
    """`config_audit` 表的 SQLite 实现。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, record: ConfigAuditRecord) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO config_audit(
                    file, key_path, old_value, new_value, who, source,
                    result, error, trace_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.file,
                    record.key_path,
                    _to_json(record.old_value),
                    _to_json(record.new_value),
                    record.who,
                    record.source,
                    record.result,
                    record.error,
                    record.trace_id,
                    record.created_at.isoformat(),
                ),
            )

    def records(self) -> list[ConfigAuditRecord]:
        with self._db.reader() as conn:
            rows = conn.execute("""
                SELECT file, key_path, old_value, new_value, who, source,
                       result, error, trace_id, created_at
                FROM config_audit ORDER BY id
                """).fetchall()
        return [
            ConfigAuditRecord(
                file=row["file"],
                key_path=row["key_path"],
                old_value=row["old_value"],
                new_value=row["new_value"],
                who=row["who"],
                source=row["source"],
                result=row["result"],
                trace_id=row["trace_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                error=row["error"],
            )
            for row in rows
        ]


class ConfigService:
    """配置读写唯一入口。

    `settings` / `rooms` 是**装配期快照**：运行期改文件不会自动生效，
    必须重启（没有文件监听）。`get()` 每次从磁盘重建合并视图。
    """

    def __init__(
        self,
        config_dir: str | Path,
        *,
        audit_store: ConfigAuditStore | None = None,
        who: str = "system",
        source: str = "startup",
    ) -> None:
        self.config_dir = Path(config_dir)
        self._audit_store: ConfigAuditStore = audit_store or InMemoryConfigAuditStore()
        self._default_who = who
        self._default_source = source
        self.settings = _read_settings(self.config_dir)
        self.rooms = _read_room_seeds(self.config_dir)

    @classmethod
    def load(
        cls,
        config_dir: str | Path,
        *,
        audit_store: ConfigAuditStore | None = None,
    ) -> ConfigService:
        return cls(config_dir, audit_store=audit_store)

    # ---------- 读 ----------

    def get(self, file_name: str) -> Mapping[str, Any]:
        """返回「基础文件 + 本地覆盖层」的合并视图（只读用的普通映射副本）。"""

        self._require_known(file_name)
        return _effective(self.config_dir, file_name)

    def audit_records(self) -> list[ConfigAuditRecord]:
        return self._audit_store.records()

    # ---------- 写 ----------

    def set(
        self,
        file_name: str,
        key_path: str,
        value: Any,
        *,
        who: str | None = None,
        source: str | None = None,
        trace_id: str | None = None,
    ) -> Any:
        """改基础配置文件中的单个键并原子落盘；只写主文件，不烘入本地覆盖层。"""

        self._require_known(file_name)
        operator, origin, trace = self._identity(who, source, trace_id)
        path = self.config_dir / file_name
        base = _read_yaml(path)
        overlay = _read_overlay(path)
        old_value = _get_or_none(base, key_path)

        updated = copy.deepcopy(base)
        container, key = _locate_or_create(updated, key_path)
        container[key] = value

        effective = _deep_merge(updated, overlay)
        self._validate(file_name, effective, operator, origin, trace, key_path, old_value, value)

        old_text = path.read_text(encoding="utf-8") if path.exists() else None
        try:
            _atomic_write_text(path, _dump_yaml(updated))
        except OSError as exc:
            self._audit(
                file_name,
                key_path,
                old_value,
                value,
                operator,
                origin,
                "rejected",
                trace,
                str(exc),
            )
            raise ConfigError(f"配置写入失败：{file_name}（{exc}）") from exc

        try:
            self._audit(
                file_name, key_path, old_value, value, operator, origin, "applied", trace, None
            )
        except Exception as exc:  # 审计落库失败 → 整体回滚，不允许「改了但没记录」
            _restore(path, old_text)
            self._audit_best_effort(
                file_name, key_path, old_value, value, operator, origin, trace, str(exc)
            )
            raise ConfigError(f"配置审计写入失败，变更已回滚：{file_name}") from exc
        return value

    def set_local(
        self,
        file_name: str,
        key_path: str,
        value: Any,
        *,
        who: str | None = None,
        source: str | None = None,
        trace_id: str | None = None,
    ) -> Any:
        """写本地覆盖层（`<stem>.local.yaml`），不碰入库的主文件。"""

        self._require_known(file_name)
        operator, origin, trace = self._identity(who, source, trace_id)
        base_path = self.config_dir / file_name
        overlay_path = self.config_dir / f"{base_path.stem}{LOCAL_OVERLAY_SUFFIX}.yaml"
        base = _read_yaml(base_path)
        overlay = _read_overlay(base_path)
        old_value = _get_or_none(overlay, key_path)

        updated_overlay = copy.deepcopy(overlay)
        container, key = _locate_or_create(updated_overlay, key_path)
        container[key] = value

        effective = _deep_merge(base, updated_overlay)
        self._validate(file_name, effective, operator, origin, trace, key_path, old_value, value)

        old_text = overlay_path.read_text(encoding="utf-8") if overlay_path.exists() else None
        try:
            _atomic_write_text(overlay_path, _dump_yaml(updated_overlay))
        except OSError as exc:
            self._audit(
                overlay_path.name,
                key_path,
                old_value,
                value,
                operator,
                origin,
                "rejected",
                trace,
                str(exc),
            )
            raise ConfigError(f"本地覆盖层写入失败：{overlay_path.name}（{exc}）") from exc

        try:
            self._audit(
                overlay_path.name,
                key_path,
                old_value,
                value,
                operator,
                origin,
                "applied",
                trace,
                None,
            )
        except Exception as exc:
            _restore(overlay_path, old_text)
            self._audit_best_effort(
                overlay_path.name,
                key_path,
                old_value,
                value,
                operator,
                origin,
                trace,
                str(exc),
            )
            raise ConfigError(f"配置审计写入失败，变更已回滚：{overlay_path.name}") from exc
        return value

    # ---------- 内部 ----------

    def _require_known(self, file_name: str) -> None:
        if file_name not in _KNOWN_FILES:
            known = ", ".join(sorted(_KNOWN_FILES))
            raise ConfigValidationError(f"未知配置文件：{file_name}（已知：{known}）")

    def _identity(
        self, who: str | None, source: str | None, trace_id: str | None
    ) -> tuple[str, str, str]:
        operator = who or self._default_who
        origin = source or self._default_source
        if operator not in _WHO_VALUES:
            raise ConfigValidationError(f"who 只能取 user / system，收到 {operator}")
        if origin not in _SOURCE_VALUES:
            raise ConfigValidationError(f"source 只能取 startup / api，收到 {origin}")
        return operator, origin, trace_id or uuid.uuid4().hex[:8]

    def _validate(
        self,
        file_name: str,
        data: dict[str, Any],
        operator: str,
        origin: str,
        trace: str,
        key_path: str,
        old_value: Any,
        new_value: Any,
    ) -> None:
        try:
            if file_name == "settings.yaml":
                _parse_settings(data)
            else:
                _parse_room_seeds(data)
        except ConfigValidationError as exc:
            self._audit(
                file_name,
                key_path,
                old_value,
                new_value,
                operator,
                origin,
                "rejected",
                trace,
                str(exc),
            )
            raise ConfigValidationError(str(exc)) from exc

    def _audit(
        self,
        file_name: str,
        key_path: str,
        old_value: Any,
        new_value: Any,
        who: str,
        source: str,
        result: str,
        trace_id: str,
        error: str | None,
    ) -> None:
        self._audit_store.append(
            ConfigAuditRecord(
                file=file_name,
                key_path=key_path,
                old_value=old_value,
                new_value=new_value,
                who=who,
                source=source,
                result=result,
                trace_id=trace_id,
                error=error,
            )
        )

    def _audit_best_effort(
        self,
        file_name: str,
        key_path: str,
        old_value: Any,
        new_value: Any,
        who: str,
        source: str,
        trace_id: str,
        error: str,
    ) -> None:
        # 审计存储本身不可用：回滚已经完成，不能让第二个异常盖住主错误。
        with suppress(Exception):
            self._audit(
                file_name,
                key_path,
                old_value,
                new_value,
                who,
                source,
                "rolled_back",
                trace_id,
                error,
            )


# ---------- YAML 读取与校验 ----------


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(_missing_config_message(path)) from None
    except yaml.YAMLError as exc:
        raise ConfigValidationError(_broken_config_message(path, str(exc))) from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigValidationError(_broken_config_message(path, "顶层必须是映射"))
    return raw


def _read_overlay(base_path: Path) -> dict[str, Any]:
    path = base_path.with_name(f"{base_path.stem}{LOCAL_OVERLAY_SUFFIX}.yaml")
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigValidationError(f"本地覆盖文件解析失败：{path.name}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigValidationError(f"本地覆盖文件顶层必须是映射：{path.name}")
    return raw


def _effective(config_dir: Path, file_name: str) -> dict[str, Any]:
    base_path = config_dir / file_name
    base = _read_yaml(base_path)
    overlay = _read_overlay(base_path)
    return _deep_merge(base, overlay)


def _read_settings(config_dir: Path) -> Settings:
    return _parse_settings(_effective(config_dir, "settings.yaml"))


def _read_room_seeds(config_dir: Path) -> tuple[RoomSeed, ...]:
    return _parse_room_seeds(_effective(config_dir, "devices.yaml"))


_SETTINGS_KEYS = {"gateway", "state", "audit", "commands", "api", "frontend"}
_GATEWAY_KEYS = {"mode", "ir", "probe_timeout_seconds"}
_IR_KEYS = {"base_url", "username", "password"}
_RETENTION_KEYS = {"retention_days"}
_API_KEYS = {"token"}
_FRONTEND_KEYS = {"enabled", "dist_path"}
_DEVICES_KEYS = {"rooms"}
_ROOM_KEYS = {"name", "display_name", "sort_order"}


def _parse_settings(raw: Mapping[str, Any]) -> Settings:
    _reject_unknown(raw, _SETTINGS_KEYS, "settings.yaml")

    gateway = _mapping(raw.get("gateway"), "settings.yaml:gateway")
    _reject_unknown(gateway, _GATEWAY_KEYS, "settings.yaml:gateway")
    mode = _text(gateway.get("mode"), "settings.yaml:gateway.mode")
    if mode not in ("mock", "real"):
        raise ConfigValidationError("settings.yaml:gateway.mode 只能取 mock 或 real")

    ir = _mapping(gateway.get("ir"), "settings.yaml:gateway.ir")
    _reject_unknown(ir, _IR_KEYS, "settings.yaml:gateway.ir")
    base_url = _text(ir.get("base_url"), "settings.yaml:gateway.ir.base_url").strip()
    username = _text(ir.get("username"), "settings.yaml:gateway.ir.username").strip()
    # 密码不 strip：首尾空格可能是真实字符。
    password = _text(ir.get("password"), "settings.yaml:gateway.ir.password")

    probe_raw = gateway.get("probe_timeout_seconds", 3)
    if isinstance(probe_raw, bool) or not isinstance(probe_raw, (int, float)):
        raise ConfigValidationError("settings.yaml:gateway.probe_timeout_seconds 必须是正数")
    if probe_raw <= 0:
        raise ConfigValidationError("settings.yaml:gateway.probe_timeout_seconds 必须是正数")
    probe = float(probe_raw)

    state = _mapping(raw.get("state"), "settings.yaml:state", optional=True)
    _reject_unknown(state, _RETENTION_KEYS, "settings.yaml:state")
    state_days = _integer(state.get("retention_days"), "settings.yaml:state.retention_days", 90)

    audit = _mapping(raw.get("audit"), "settings.yaml:audit", optional=True)
    _reject_unknown(audit, _RETENTION_KEYS, "settings.yaml:audit")
    audit_days = _integer(audit.get("retention_days"), "settings.yaml:audit.retention_days", 365)

    commands = _mapping(raw.get("commands"), "settings.yaml:commands", optional=True)
    _reject_unknown(commands, _RETENTION_KEYS, "settings.yaml:commands")
    command_days = _integer(
        commands.get("retention_days"), "settings.yaml:commands.retention_days", 180
    )

    api = _mapping(raw.get("api"), "settings.yaml:api", optional=True)
    _reject_unknown(api, _API_KEYS, "settings.yaml:api")
    token = _text(api.get("token"), "settings.yaml:api.token").strip()

    frontend = _mapping(raw.get("frontend"), "settings.yaml:frontend", optional=True)
    _reject_unknown(frontend, _FRONTEND_KEYS, "settings.yaml:frontend")
    frontend_enabled = _boolean(
        frontend.get("enabled"), "settings.yaml:frontend.enabled", default=True
    )
    dist_path = (
        _text(
            frontend.get("dist_path"), "settings.yaml:frontend.dist_path", "frontend/dist"
        ).strip()
        or "frontend/dist"
    )

    return Settings(
        gateway=GatewayConfig(
            mode=mode,
            base_url=base_url,
            username=username,
            password=password,
            probe_timeout_seconds=probe,
        ),
        state_retention_days=state_days,
        audit_retention_days=audit_days,
        command_retention_days=command_days,
        api_token=token,
        frontend=FrontendConfig(enabled=frontend_enabled, dist_path=dist_path),
    )


def _parse_room_seeds(raw: Mapping[str, Any]) -> tuple[RoomSeed, ...]:
    _reject_unknown(raw, _DEVICES_KEYS, "devices.yaml")

    rooms_raw = raw.get("rooms", [])
    if not isinstance(rooms_raw, list):
        raise ConfigValidationError("devices.yaml:rooms 必须是列表")

    seeds: list[RoomSeed] = []
    seen: set[str] = set()
    for index, item in enumerate(rooms_raw, start=1):
        where = f"devices.yaml:rooms 第 {index} 项"
        if not isinstance(item, dict):
            raise ConfigValidationError(f"{where} 必须是映射")
        _reject_unknown(item, _ROOM_KEYS, where)

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigValidationError(f"{where} 缺少非空 name")
        name = name.strip()
        if name in seen:
            raise ConfigValidationError(f"devices.yaml:rooms 名称重复：{name}")
        seen.add(name)

        display_raw = item.get("display_name")
        if display_raw is not None and not isinstance(display_raw, str):
            raise ConfigValidationError(f"{where} display_name 必须是字符串")
        display = (display_raw or "").strip() or name

        sort_order = _integer(item.get("sort_order"), f"{where} sort_order", 0)
        seeds.append(RoomSeed(name=name, display_name=display, sort_order=sort_order))
    return tuple(seeds)


def _reject_unknown(section: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ConfigValidationError(f"{where} 含未知键：{', '.join(unknown)}")


def _mapping(value: Any, where: str, *, optional: bool = False) -> dict[str, Any]:
    if value is None:
        if optional:
            return {}
        raise ConfigValidationError(f"{where} 缺失")
    if not isinstance(value, dict):
        raise ConfigValidationError(f"{where} 必须是映射")
    return value


def _text(value: Any, where: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigValidationError(f"{where} 必须是字符串")
    return value


def _integer(value: Any, where: str, default: int, *, minimum: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{where} 必须是整数")
    if value < minimum:
        raise ConfigValidationError(f"{where} 不能小于 {minimum}")
    return value


def _boolean(value: Any, where: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigValidationError(f"{where} 必须是布尔值 true / false")
    return value


# ---------- 键路径与合并 ----------


def _locate_or_create(root: dict[str, Any], key_path: str) -> tuple[dict[str, Any], str]:
    """按点分路径定位父容器；中间层缺失就建空 dict。"""

    if not key_path:
        raise ConfigValidationError("key_path 不能为空")
    parts = key_path.split(".")
    node = root
    for part in parts[:-1]:
        child = node.get(part)
        if child is None:
            child = {}
            node[part] = child
        if not isinstance(child, dict):
            raise ConfigValidationError(f"键路径无法定位：{key_path}")
        node = child
    return node, parts[-1]


def _get_or_none(root: Mapping[str, Any], key_path: str) -> Any:
    node: Any = root
    for part in key_path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """深合并：overlay 覆盖 base；list 整体替换，不做逐元素合并。"""

    result = dict(base)
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = value
    return result


def _dump_yaml(data: Mapping[str, Any]) -> str:
    return yaml.safe_dump(
        dict(data),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def _atomic_write_text(path: Path, text: str) -> None:
    """临时文件 + `os.replace`：不会留下半截文件。"""

    handle_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _restore(path: Path, old_text: str | None) -> None:
    if old_text is None:
        path.unlink(missing_ok=True)
        return
    _atomic_write_text(path, old_text)


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)
