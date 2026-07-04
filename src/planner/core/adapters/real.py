"""Real adapter stubs. They exist so the registry, config keys, and types are
final now; the subprocess / tui_gateway.ws logic is wired in stage 4 without
touching any signature."""

from __future__ import annotations

from typing import TYPE_CHECKING

from planner.chat.contracts import ChatSendResult, GatewayStatus
from planner.core.adapters.base import (
    BoundaryInputs,
    BoundaryJudgment,
    SpawnRequest,
    SpawnResult,
)
from planner.days.contracts import PlanNode, PlanTree

if TYPE_CHECKING:
    from planner.core.config import Config

_STAGE = "wired in stage 4"


class RealSpawnAdapter:
    def __init__(self, config: Config) -> None:
        self._config = config

    def spawn(self, request: SpawnRequest) -> SpawnResult:
        raise NotImplementedError(_STAGE)


class RealBoundaryAdapter:
    def __init__(self, config: Config) -> None:
        self._config = config

    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment:
        raise NotImplementedError(_STAGE)

    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree:
        raise NotImplementedError(_STAGE)

    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode:
        raise NotImplementedError(_STAGE)


class RealGatewayAdapter:
    def __init__(self, config: Config) -> None:
        self._config = config

    def status(self) -> GatewayStatus:
        raise NotImplementedError(_STAGE)

    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
        raise NotImplementedError(_STAGE)
