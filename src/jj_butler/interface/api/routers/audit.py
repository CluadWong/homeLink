"""审计端点：`GET /api/audit` —— 只读、按写入倒序。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from jj_butler.core.errors import AppError, ErrorCode
from jj_butler.interface.api.security import require_token

router = APIRouter(prefix="/api", tags=["audit"], dependencies=[Depends(require_token)])

_VALID_RESULTS = ("success", "failed", "all")
_MIN_LIMIT = 1
_MAX_LIMIT = 200


@router.get("/audit")
def list_audit(
    request: Request,
    limit: int = Query(default=50),
    result: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
) -> dict:
    if not _MIN_LIMIT <= limit <= _MAX_LIMIT:
        raise AppError(ErrorCode.INVALID_REQUEST, "limit 必须在 1 到 200 之间")
    if result is not None and result not in _VALID_RESULTS:
        raise AppError(ErrorCode.INVALID_REQUEST, "result 只能取 success、failed 或 all")

    context = request.app.state.context
    records = context.audit.list_records(
        limit=limit,
        result=None if result in (None, "all") else result,
        device_id=device_id,
    )
    return {"success": True, "records": records, "trace_id": request.state.trace_id}
