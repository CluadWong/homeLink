"""按配置装配网关与探测。"""

from __future__ import annotations

from homelink.core.config import GatewayConfig
from homelink.gateway.base import IRGateway
from homelink.gateway.mock import MockIRGateway
from homelink.gateway.probe import GatewayProbe
from homelink.gateway.real import RealIRGateway


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
