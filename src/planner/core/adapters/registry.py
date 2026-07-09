"""Adapter registry (§9.4). Selects real vs fake (vs offline) gateway from
config. `auto` resolves to fake under test mode and real otherwise; explicit
values always win. An unknown value fails loudly at startup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from planner.core.adapters.base import GatewayAdapter
from planner.core.adapters.fakes import (
    EchoGatewayAdapter,
    OfflineGatewayAdapter,
)
from planner.core.adapters.real import RealGatewayAdapter
from planner.core.errors import ErrorCode, PlannerError

if TYPE_CHECKING:
    from planner.core.config import Config


@dataclass(frozen=True)
class Adapters:
    gateway: GatewayAdapter


def _resolve_auto(choice: str, test_mode: bool) -> str:
    if choice == "auto":
        return "fake" if test_mode else "real"
    return choice


def _select_gateway(config: Config) -> GatewayAdapter:
    choice = _resolve_auto(config.gateway_adapter, config.test_mode)
    if choice == "fake":
        return EchoGatewayAdapter()
    if choice == "slow_fake":
        return EchoGatewayAdapter(stream_delay_seconds=1.5)
    if choice == "offline":
        return OfflineGatewayAdapter()
    if choice == "real":
        return RealGatewayAdapter(config)
    raise PlannerError(ErrorCode.validation, f"unknown gateway_adapter: {config.gateway_adapter!r}")


def build_adapters(config: Config) -> Adapters:
    return Adapters(
        gateway=_select_gateway(config),
    )
