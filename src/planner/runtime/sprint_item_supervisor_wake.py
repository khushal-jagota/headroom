"""Whether this Sprint Item needs its supervisor right now.

This is the whole question and nothing else: it reads, it decides, and it writes
nothing. The loop runs it, and the send runs it again once it holds the Item's
lifecycle lock, where the answer is final.

The answer never travels. A wake carries no Ticket id and no fact, because a fact
stated in a message can be wrong by the time the supervisor reads it. The supervisor
reads canonical state itself, which is current at the moment it reads.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Final

from planner.tickets.contracts import TicketStatus
from planner.tickets.logic.admission import SPRINT_ITEM_SUPERVISOR_ACTOR

# The whole message. It says nothing, so it cannot be wrong when it is read.
WAKE_TEXT: Final = "Your Sprint Item needs you. Read current context and act."
WAKE_SENDER_LABEL: Final = "Panels"

# What a supervisor should look at. A parked proposal is here because the supervisor
# reads it, weighs it, and can speak to its worker about it, whoever ends up approving
# it. A blocked Ticket is not: blocking is ordinary planned state, and the supervisor
# reads blockers from canonical state when it cares.
_WAKING_STATUSES: Final = (
    TicketStatus.awaiting_approval.value,
    TicketStatus.errored.value,
    TicketStatus.needs_user.value,
)

_CANDIDATE_TICKETS_SQL: Final = (
    "SELECT id, ticket_status, ticket_status_changed_at, fields FROM tickets "
    "WHERE sprint_item_id = ? "
    f"AND (ticket_status IN ({','.join('?' * len(_WAKING_STATUSES))}) OR stage = 'done')"
)

_ITEMS_WITH_CANDIDATES_SQL: Final = (
    "SELECT DISTINCT sprint_item_id FROM tickets "
    "WHERE sprint_item_id IS NOT NULL "
    f"AND (ticket_status IN ({','.join('?' * len(_WAKING_STATUSES))}) OR stage = 'done') "
    "ORDER BY sprint_item_id"
)

# A prompt Panels itself put into the conversation. The supervisor's own conversation
# also carries the person's messages, and those are not wakes.
_LAST_WAKE_SQL: Final = (
    "SELECT MAX(created_at) AS sent_at FROM conversation_events "
    "WHERE conversation_id = ? AND kind = 'prompt' "
    "AND json_extract(payload, '$.sender_label') = ?"
)


def sprint_item_ids_to_consider(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Every Sprint Item holding a Ticket that could want its supervisor.

    This is the cheap first cut for one pass. Each id it returns still goes through
    ``sprint_item_needs_supervisor``, which applies the rest of the question.
    """
    rows = conn.execute(_ITEMS_WITH_CANDIDATES_SQL, _WAKING_STATUSES).fetchall()
    return tuple(str(row["sprint_item_id"]) for row in rows)


def last_wake_sent_at(conn: sqlite3.Connection, conversation_id: str | None) -> int:
    """When Panels last woke this conversation, or 0 if it never has."""
    if conversation_id is None:
        return 0
    row = conn.execute(_LAST_WAKE_SQL, (conversation_id, WAKE_SENDER_LABEL)).fetchone()
    if row is None or row["sent_at"] is None:
        return 0
    return int(row["sent_at"])


def _proposed_by_the_supervisor(raw_fields: str) -> bool:
    """Whether this Ticket's parked proposal is the supervisor's own.

    A supervisor that proposed something and then parked it for its own review does not
    need telling. It remembers what it did.
    """
    try:
        slots = json.loads(raw_fields)
    except (TypeError, ValueError):
        return False
    if not isinstance(slots, dict):
        return False
    for slot in slots.values():
        if not isinstance(slot, dict):
            continue
        proposal = slot.get("proposal")
        if isinstance(proposal, dict) and proposal.get("proposed_by") == (
            SPRINT_ITEM_SUPERVISOR_ACTOR
        ):
            return True
    return False


def sprint_item_needs_supervisor(
    conn: sqlite3.Connection,
    sprint_item_id: str,
    *,
    conversation_id: str | None,
) -> bool:
    """Whether Panels should wake this Item's supervisor now."""
    since = last_wake_sent_at(conn, conversation_id)
    rows = conn.execute(
        _CANDIDATE_TICKETS_SQL, (sprint_item_id, *_WAKING_STATUSES)
    ).fetchall()
    for row in rows:
        # Only a Ticket that moved since the last wake counts. Without this the loop
        # would answer from standing state: the supervisor would finish a turn leaving
        # something it cannot resolve, and the next unrelated commit anywhere in Panels
        # would wake it again, and again.
        if int(row["ticket_status_changed_at"]) <= since:
            continue
        if str(row["ticket_status"]) == TicketStatus.awaiting_approval.value and (
            _proposed_by_the_supervisor(str(row["fields"]))
        ):
            continue
        return True
    return False
