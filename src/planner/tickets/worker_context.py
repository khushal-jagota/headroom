"""Ticket-specific producers for generic worker context."""

from __future__ import annotations

import sqlite3
from typing import Final

from planner.tickets.logic.admission import DIRECT_ACTORS
from planner.worker_context import data as worker_context_data

TICKET_CHANGED_CONTEXT_KEY: Final = "ticket_changed"
TICKET_CHANGED_TEXT: Final = (
    "This ticket changed outside your worker turn. Reread the ticket before continuing."
)


def set_ticket_changed(conn: sqlite3.Connection, ticket_id: str, actor: str) -> None:
    """Coalesce a notice for direct edits, never worker-owned writes."""
    if actor not in DIRECT_ACTORS:
        return
    worker_context_data.set_context(
        conn, ticket_id, TICKET_CHANGED_CONTEXT_KEY, TICKET_CHANGED_TEXT
    )
