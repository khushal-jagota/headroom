"""Adapter protocol for the chat gateway observation transport.
Stdlib only. The dependency arrow is core-adapters -> domain-contracts, never
the reverse.

Production chat uses the app-state SharedGateway singleton; fakes remain injectable
through this protocol in tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol, TypeAlias

from planner.chat.contracts import (
    ChatHistory,
    CommandCatalog,
    GatewayStatus,
    HumanChatObservation,
)

HumanSessionKeyBinder: TypeAlias = Callable[[str], str]  # noqa: UP040 -- frozen AD06 declaration


class GatewayAdapter(Protocol):
    def status(self) -> GatewayStatus: ...
    def history(self, session_key: str | None, entity_id: str) -> ChatHistory: ...
    def run_human_turn(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
    ) -> Iterator[HumanChatObservation]: ...
    def interrupt(self, session_key: str, entity_id: str) -> None: ...
    # The gateway's own command/skill registry — stateless, gateway-wide, cached above.
    def catalog(self) -> CommandCatalog: ...
