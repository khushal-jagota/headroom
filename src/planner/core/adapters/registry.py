"""Adapter registry (§9.4). Selects real vs fake (vs offline gateway) per adapter
from config keys. `auto` resolves to fake under test mode and real otherwise;
explicit values always win. An unknown value fails loudly at startup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from planner.core.adapters.base import BoundaryAdapter, GatewayAdapter
from planner.core.adapters.fakes import (
    EchoGatewayAdapter,
    FakeBoundaryAdapter,
    OfflineGatewayAdapter,
)
from planner.core.adapters.real import RealBoundaryAdapter, RealGatewayAdapter
from planner.core.errors import ErrorCode, PlannerError

if TYPE_CHECKING:
    from planner.core.config import Config


@dataclass(frozen=True)
class Adapters:
    boundary: BoundaryAdapter
    gateway: GatewayAdapter


def _resolve_auto(choice: str, test_mode: bool) -> str:
    if choice == "auto":
        return "fake" if test_mode else "real"
    return choice


def _select_boundary(config: Config) -> BoundaryAdapter:
    choice = _resolve_auto(config.boundary_adapter, config.test_mode)
    if choice == "fake":
        return FakeBoundaryAdapter()
    if choice == "real":
        return RealBoundaryAdapter(config)
    raise PlannerError(
        ErrorCode.validation, f"unknown boundary_adapter: {config.boundary_adapter!r}"
    )


def _select_gateway(config: Config) -> GatewayAdapter:
    choice = _resolve_auto(config.gateway_adapter, config.test_mode)
    if choice == "fake":
        return EchoGatewayAdapter()
    if choice == "offline":
        return OfflineGatewayAdapter()
    if choice == "real":
        return RealGatewayAdapter(config)
    raise PlannerError(ErrorCode.validation, f"unknown gateway_adapter: {config.gateway_adapter!r}")


def build_adapters(config: Config) -> Adapters:
    return Adapters(
        boundary=_select_boundary(config),
        gateway=_select_gateway(config),
    )
