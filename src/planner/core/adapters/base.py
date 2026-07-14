"""Adapter protocol for the chat gateway observation transport.
Stdlib only. The dependency arrow is core-adapters -> domain-contracts, never
the reverse.

Production chat uses the app-state SharedGateway singleton; fakes remain injectable
through this protocol in tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

from planner.chat.contracts import (
    ChatHistory,
    ChatStreamChunk,
    CommandCatalog,
    GatewayStatus,
)


class GatewayAdapter(Protocol):
    def status(self) -> GatewayStatus: ...
    def history(self, session_key: str | None, entity_id: str) -> ChatHistory: ...
    def stream(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        on_session_key: Callable[[str], None] | None = None,
        image_paths: tuple[Path, ...] = (),
    ) -> Iterator[ChatStreamChunk]: ...
    def interrupt(self, session_key: str, entity_id: str) -> None: ...
    # The gateway's own command/skill registry — stateless, gateway-wide, cached above.
    def catalog(self) -> CommandCatalog: ...
