"""Whether this Sprint Item needs its supervisor right now, and what to tell it.

This is the whole question and nothing else: it reads, it decides, and it writes
nothing. The loop runs it, and the send runs it again once it holds the Item's
lifecycle lock, where the answer is final. It answers with the message itself, so
the verdict and the words cannot disagree.

The message names each Ticket that moved and says what happened to it. Every line
is a past event, never a claim about now, because a claim about now can be wrong by
the time the supervisor reads it. What happened stays true. The supervisor still
reads canonical state itself before it acts, and that is current when it reads.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Final, NamedTuple

from planner.tickets.contracts import TicketStatus
from planner.tickets.logic.admission import SPRINT_ITEM_SUPERVISOR_ACTOR

WAKE_SENDER_LABEL: Final = "Panels"

# What a supervisor should look at. A parked proposal is here because the supervisor
# reads it, weighs it, and can speak to its worker about it, whoever ends up approving
# it. `paired` is here because a paired Stage is a conversation the supervisor can join.
# A blocked Ticket is not: blocking is ordinary planned state, and the supervisor reads
# blockers from canonical state when it cares.
_WAKING_STATUSES: Final = (
    TicketStatus.awaiting_approval.value,
    TicketStatus.errored.value,
    TicketStatus.needs_user.value,
    TicketStatus.paired.value,
)

# Oldest first, so the message reads in the order the moves happened, and the same
# state always produces the same text.
_CANDIDATE_TICKETS_SQL: Final = (
    "SELECT id, stage, ticket_status, ticket_status_changed_at, fields FROM tickets "
    "WHERE sprint_item_id = ? "
    f"AND (ticket_status IN ({','.join('?' * len(_WAKING_STATUSES))}) OR stage = 'done') "
    "ORDER BY ticket_status_changed_at, id"
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
    ``sprint_item_wake_message``, which applies the rest of the question.
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


class _ParkedProposal(NamedTuple):
    field: str
    proposed_by: str
    created_at: int


def _parked_proposals(raw_fields: str) -> tuple[_ParkedProposal, ...]:
    """Every proposal parked on this Ticket, newest last.

    There can be more than one. Below its ceiling a Worker may propose a field the
    Ticket has already passed, and that parks like any other proposal.
    """
    try:
        slots = json.loads(raw_fields)
    except (TypeError, ValueError):
        return ()
    if not isinstance(slots, dict):
        return ()
    parked: list[_ParkedProposal] = []
    for field, slot in slots.items():
        if not isinstance(slot, dict):
            continue
        proposal = slot.get("proposal")
        if not isinstance(proposal, dict):
            continue
        created_at = proposal.get("created_at")
        parked.append(
            _ParkedProposal(
                field=str(field),
                proposed_by=str(proposal.get("proposed_by")),
                created_at=created_at if isinstance(created_at, int) else 0,
            )
        )
    return tuple(sorted(parked, key=lambda proposal: proposal.created_at))


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _what_happened(row: sqlite3.Row) -> str | None:
    """The one line this Ticket adds to the wake, or nothing when it adds none.

    Every line is a past event. A Ticket that moved into a state the supervisor must
    leave alone returns nothing, and the wake goes without it.
    """
    ticket_id = str(row["id"])
    parked = _parked_proposals(str(row["fields"]))
    if str(row["stage"]) == "done":
        return f"{ticket_id} finished"
    status = str(row["ticket_status"])
    if status == TicketStatus.awaiting_approval.value:
        # A supervisor that proposed something and then parked it for its own review
        # does not need telling. It remembers what it did.
        if any(
            proposal.proposed_by == SPRINT_ITEM_SUPERVISOR_ACTOR for proposal in parked
        ):
            return None
        if not parked:
            return f"{ticket_id} proposed something"
        field = parked[-1].field
        return f"{ticket_id} proposed {_article(field)} {field}"
    if status == TicketStatus.paired.value:
        # A Ticket at `paired` with a proposal still parked got there one way only: the
        # user replied to that proposal. Filing a proposal writes approval status first,
        # and resolving one clears the proposal off the field. The reply is the user's
        # business and the proposal is still theirs, so this is not the supervisor's wake.
        if parked:
            return None
        return f"{ticket_id} entered a paired stage"
    if status == TicketStatus.needs_user.value:
        return f"{ticket_id} asked for human help"
    if status == TicketStatus.errored.value:
        return f"{ticket_id} hit a backend failure"
    return None


def sprint_item_wake_message(
    conn: sqlite3.Connection,
    sprint_item_id: str,
    *,
    conversation_id: str | None,
) -> str | None:
    """What Panels should wake this Item's supervisor with, or nothing at all."""
    since = last_wake_sent_at(conn, conversation_id)
    rows = conn.execute(
        _CANDIDATE_TICKETS_SQL, (sprint_item_id, *_WAKING_STATUSES)
    ).fetchall()
    lines: list[str] = []
    for row in rows:
        # Only a Ticket that moved since the last wake counts. Without this the loop
        # would answer from standing state: the supervisor would finish a turn leaving
        # something it cannot resolve, and the next unrelated commit anywhere in Panels
        # would wake it again, and again.
        if int(row["ticket_status_changed_at"]) <= since:
            continue
        line = _what_happened(row)
        if line is not None:
            lines.append(line)
    if not lines:
        return None
    return "\n".join(lines)
