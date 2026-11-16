"""Fail one selected real Ticket preference writer before it can commit."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from planner.tickets import data as tickets_data


class ScriptedTicketChoiceFault:
    def __init__(self) -> None:
        self.identity: str | None = None
        self.pending = False
        self.injected_failures = 0
        self.error = RuntimeError("Ticket model write unavailable")
        self.original: Callable[..., Any] | None = None

    def arm(self, identity: str) -> dict[str, bool]:
        if self.identity is not None:
            raise ValueError("A Ticket choice failure is already armed")
        self.identity = identity
        self.pending = True
        original = tickets_data.write_ticket_last_chosen_configuration
        self.original = original

        def write(*args: Any, **kwargs: Any) -> Any:
            if kwargs.get("expected_conversation_id") == self.identity and self.pending:
                self.pending = False
                self.injected_failures += 1
                raise self.error
            return original(*args, **kwargs)

        tickets_data.write_ticket_last_chosen_configuration = write
        return {"armed": True}

    def observe(self, identity: str) -> dict[str, bool | int | str]:
        if self.identity != identity:
            raise ValueError("No matching Ticket choice failure")
        return {
            "pending": self.pending,
            "injected_failures": self.injected_failures,
            "detail": str(self.error),
        }

    def close(self) -> None:
        if self.original is not None:
            tickets_data.write_ticket_last_chosen_configuration = self.original
        self.original = None
        self.identity = None
        self.pending = False
