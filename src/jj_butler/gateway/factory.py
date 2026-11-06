"""按配置装配网关与探测。"""

from __future__ import annotations

from jj_butler.core.config import GatewayConfig
from jj_butler.gateway.base import IRGateway
from jj_butler.gateway.mock import MockIRGateway
from jj_butler.gateway.probe import GatewayProbe
from jj_butler.gateway.real import RealIRGateway


def build_gateway(gateway: GatewayConfig) -> IRGateway:
    if gateway.mode == "mock":
        return MockIRGateway()
    return RealIRGateway(
        gateway.base_url,
        gateway.username,
        gateway.password,
        probe_timeout_seconds=gateway.probe_timeout_seconds,
    )


def build_gateway_probe(gateway: IRGateway, *, ttl_seconds: float = 15.0) -> GatewayProbe:
    return GatewayProbe(gateway, ttl_seconds=ttl_seconds)
