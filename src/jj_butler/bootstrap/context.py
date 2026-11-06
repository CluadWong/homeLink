"""唯一装配入口：build_context(config_dir, db_path)。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jj_butler.brain.audit import AuditStore
from jj_butler.core.config import ConfigService, SqliteConfigAuditStore
from jj_butler.core.database import Database
from jj_butler.core.timeutil import utc_now_iso
from jj_butler.gateway.base import IRGateway
from jj_butler.gateway.factory import build_gateway, build_gateway_probe
from jj_butler.gateway.probe import GatewayProbe
from jj_butler.services.device_management.control import ControlService
from jj_butler.services.device_management.devices import DeviceStore
from jj_butler.services.device_management.learn import (
    DEFAULT_IDLE_TTL_SECONDS,
    LearnService,
)
from jj_butler.services.device_management.rooms import RoomStore
from jj_butler.services.device_management.states import DeviceStateStore

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    config: ConfigService
    db: Database
    rooms: RoomStore
    devices: DeviceStore
    gateway: IRGateway
    probe: GatewayProbe
    audit: AuditStore
    learn: LearnService
    states: DeviceStateStore
    control: ControlService
    # `PUT /api/gateway` 已落盘、但尚待重启生效的地址；进程重启后自然清空。
    pending_base_url: str | None = None


def build_context(
    config_dir: str | Path,
    db_path: str | Path,
    *,
    gateway_override: IRGateway | None = None,
    learn_idle_ttl_seconds: float = DEFAULT_IDLE_TTL_SECONDS,
) -> AppContext:
    db = Database(db_path)
    db.initialize()
    config = ConfigService.load(config_dir, audit_store=SqliteConfigAuditStore(db))

    rooms = RoomStore(db)
    devices = DeviceStore(db)
    sync_config_to_database(config, db)

    # 保留期清理在播种之后、状态恢复之前；失败只告警，不能阻断启动。
    try:
        purge_retention(config, db)
    except Exception as exc:  # 清理失败不阻断启动
        logger.warning("启动清理失败，已跳过：%s", exc)

    gateway = gateway_override or build_gateway(config.settings.gateway)
    probe = build_gateway_probe(gateway)
    audit = AuditStore(db)
    states = DeviceStateStore(db)
    control = ControlService(devices, gateway, audit, states)
    learn = LearnService(
        db,
        devices,
        gateway,
        audit,
        idle_ttl_seconds=learn_idle_ttl_seconds,
    )

    return AppContext(
        config=config,
        db=db,
        rooms=rooms,
        devices=devices,
        gateway=gateway,
        probe=probe,
        audit=audit,
        learn=learn,
        states=states,
        control=control,
    )


def sync_config_to_database(config: ConfigService, db: Database) -> None:
    """只做房间播种：仅补缺失房间，不覆盖运行期通过 API 做的修改。"""

    with db.transaction() as conn:
        for seed in config.rooms:
            conn.execute(
                """
                INSERT OR IGNORE INTO rooms(name, display_name, sort_order, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (seed.name, seed.display_name, seed.sort_order, utc_now_iso()),
            )


def purge_retention(config: ConfigService, db: Database, *, now: datetime | None = None) -> None:
    """按保留期清理 `device_states` 与 `audit_logs`（只按 created_at 早于 cutoff）。"""

    current = now or datetime.now(UTC)
    state_cutoff = _cutoff(current, config.settings.state_retention_days)
    audit_cutoff = _cutoff(current, config.settings.audit_retention_days)
    with db.transaction() as conn:
        conn.execute("DELETE FROM device_states WHERE created_at < ?", (state_cutoff,))
        conn.execute("DELETE FROM audit_logs WHERE created_at < ?", (audit_cutoff,))


def _cutoff(now: datetime, days: int) -> str:
    moment = (now - timedelta(days=days)).replace(microsecond=0)
    return moment.isoformat().replace("+00:00", "Z")
