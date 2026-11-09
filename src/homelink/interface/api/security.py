"""Bearer 鉴权：api.token 为空则完全不校验。"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, Request

from homelink.core.errors import AppError, ErrorCode


def require_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    token = request.app.state.context.config.settings.api_token
    if not token:
        return

    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or not hmac.compare_digest(provided, token):
        raise AppError(ErrorCode.UNAUTHORIZED, "缺少或无效的 Bearer token")
