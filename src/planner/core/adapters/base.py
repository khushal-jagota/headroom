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
    CommandCatalog,
    GatewayStatus,
    HumanChatObservation,
)
from planner.tickets.contracts import EmployeeSessionHistory

HumanSessionKeyBinder: TypeAlias = Callable[[str], str]  # noqa: UP040 -- frozen AD06 declaration


class GatewayAdapter(Protocol):
    def status(self) -> GatewayStatus: ...
    def stored_session_keys_for_live_session_id(self, live_session_id: str) -> tuple[str, ...]: ...
    def read_employee_session_history(
        self,
        employee_session_id: str,
        ticket_id: str,
    ) -> EmployeeSessionHistory: ...
    def run_human_turn(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
        *,
        require_existing_session: bool = False,
    ) -> Iterator[HumanChatObservation]: ...
    def interrupt(self, session_key: str, entity_id: str) -> None: ...
    def respond_to_clarification(
        self, session_key: str, entity_id: str, request_id: str, answer: str
    ) -> None: ...
    # The gateway's own command/skill registry — stateless, gateway-wide, cached above.
    def catalog(self) -> CommandCatalog: ...
