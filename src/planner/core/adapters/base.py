"""Adapter protocols and their request/result shapes for the chat gateway.
Stdlib only. The dependency arrow is core-adapters -> domain-contracts, never
the reverse.

Production chat uses the app-state SharedGateway singleton; fakes remain injectable
through this protocol in tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from planner.chat.contracts import (
    ChatSendResult,
    ChatStreamChunk,
    CommandCatalog,
    CommandRunResult,
    GatewayStatus,
)


class GatewayAdapter(Protocol):
    def status(self) -> GatewayStatus: ...
    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult: ...
    def stream(
        self, session_key: str | None, entity_id: str, text: str, mode: str
    ) -> Iterator[ChatStreamChunk]: ...
    # The gateway's own command/skill registry — stateless, gateway-wide, cached above.
    def catalog(self) -> CommandCatalog: ...
    # Run a /command on the entity's own session (the ticket's mind), mirroring send's
    # resume/create + drain shape. Skills run via command.dispatch -> prompt.submit.
    def run_command(
        self, session_key: str | None, entity_id: str, command: str
    ) -> CommandRunResult: ...
