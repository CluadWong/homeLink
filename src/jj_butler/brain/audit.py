"""审计落点：`audit_logs` 的唯一写入抽象。

控制、学码、配置写入都通过 `AuditSink` 落库；审计失败**不得**回滚已发出的红外。
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from jj_butler.core.database import Database
from jj_butler.core.timeutil import utc_now_iso


@dataclass(frozen=True)
class AuditRecord:
    """一条操作审计，字段对齐 `audit_logs` 表。"""

    who: str
    action: str
    result: str
    trace_id: str
    context: Any = None
    reason: str | None = None
    device_id: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    level: str = "low"


class AuditSink(ABC):
    """审计落点抽象。"""

    @abstractmethod
    def write(self, record: AuditRecord) -> None:
        """写入一条审计记录。"""


class SqliteAuditSink(AuditSink):
    """把审计写入 `audit_logs`（与 `config_audit` 不合并）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def write(self, record: AuditRecord) -> None:
        if record.who not in ("user", "system"):
            raise ValueError(f"who 只能取 user / system，收到 {record.who}")
        if record.result not in ("success", "failed"):
            raise ValueError(f"result 只能取 success / failed，收到 {record.result}")
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs(
                    who, action, device_id, context, reason, level, result, trace_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.who,
                    record.action,
                    record.device_id,
                    _to_json(record.context),
                    record.reason,
                    "low",
                    record.result,
                    record.trace_id,
                    record.created_at,
                ),
            )


class AuditStore:
    """服务层沿用的便捷包装；内部委托给 `SqliteAuditSink`。"""

    def __init__(self, db: Database) -> None:
        self._sink = SqliteAuditSink(db)
        self._db = db

    def write(
        self,
        *,
        who: str,
        action: str,
        result: str,
        trace_id: str,
        context: dict[str, Any] | None = None,
        reason: str | None = None,
        device_id: str | None = None,
        level: str = "low",
    ) -> None:
        self._sink.write(
            AuditRecord(
                who=who,
                action=action,
                result=result,
                trace_id=trace_id,
                context=context,
                reason=reason,
                device_id=device_id,
                level=level,
            )
        )

    def recent(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._db.reader() as conn:
            return conn.execute(
                "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def list_records(
        self,
        *,
        limit: int = 50,
        result: str | None = None,
        device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """按写入倒序读取审计；`device_id` 按 `audit_logs.device_id` 精确过滤。"""

        clauses: list[str] = []
        params: list[Any] = []
        if result is not None:
            clauses.append("result = ?")
            params.append(result)
        if device_id:
            clauses.append("device_id = ?")
            params.append(device_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._db.reader() as conn:
            rows = conn.execute(
                "SELECT created_at, action, device_id, result, reason, trace_id FROM audit_logs"
                f"{where} ORDER BY id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [
            {
                "created_at": row["created_at"],
                "action": row["action"],
                "device_id": row["device_id"],
                "result": row["result"],
                "reason": row["reason"],
                "trace_id": row["trace_id"],
            }
            for row in rows
        ]


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)
