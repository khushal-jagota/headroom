"""Real adapters.

The chat gateway's real implementation is the app-state SharedGateway installed
by server startup; this module's RealGatewayAdapter is only a registry
placeholder and never owns a GatewayChild.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from planner.chat.contracts import (
    ChatHistory,
    CommandCatalog,
    GatewayStatus,
    HumanChatObservation,
)
from planner.core.adapters.base import HumanSessionKeyBinder
from planner.core.errors import ErrorCode, PlannerError

if TYPE_CHECKING:
    from planner.core.config import Config


class RealGatewayAdapter:
    """Registry placeholder; create_app replaces it with SharedGateway in production."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def _offline(self) -> PlannerError:
        return PlannerError(
            ErrorCode.gateway_offline,
            "shared gateway is not attached",
            {"detail": "create_app installs SharedGateway in production startup"},
        )

    def status(self) -> GatewayStatus:
        from planner.minds.config import resolve_hermes_python

        python = resolve_hermes_python()
        if not python.exists():
            return GatewayStatus(available=False, detail=f"hermes interpreter not found: {python}")
        return GatewayStatus(available=False, detail="shared gateway is not attached")

    def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
        raise self._offline()

    def run_human_turn(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
    ) -> Iterator[HumanChatObservation]:
        raise self._offline()

    def interrupt(self, session_key: str, entity_id: str) -> None:
        raise self._offline()

    def catalog(self) -> CommandCatalog:
        raise self._offline()
