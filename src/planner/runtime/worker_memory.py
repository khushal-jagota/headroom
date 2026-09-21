"""What Panels knows about a Worker's lost memory, and nothing else does.

A Worker keeps its tools across a compaction. Its skills are files, and its Ticket is in
the database, so it can fetch anything it is missing. The one fact it cannot fetch is
that it was compacted at all: the conversation it was working from is simply shorter than
it was, and nothing in it says so. Panels records the boundary, so Panels is the only
side that can say it.

A boundary is *answered* once Panels has sent a worker-step message after it — a wake, or
the notice sent here. That is what keeps a boundary from being reported twice without
storing a second copy of it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Final
from uuid import uuid4

from planner.conversation.contracts import HeldPrompt

WORKER_STEP_MESSAGE_PREFIX: Final = "worker_step_message_"
MEMORY_NOTICE_MESSAGE_PREFIX: Final = "worker_step_memory_notice_"
# Both ids begin with this, so one comparison asks "has Panels spoken since the boundary".
ANSWERING_SENDER_MESSAGE_PREFIX: Final = "worker_step_"


def new_worker_step_message_id() -> str:
    return f"{WORKER_STEP_MESSAGE_PREFIX}{uuid4().hex}"


def new_memory_notice_message_id() -> str:
    return f"{MEMORY_NOTICE_MESSAGE_PREFIX}{uuid4().hex}"


def an_answer_is_already_waiting(held_prompts: Iterable[HeldPrompt]) -> bool:
    """Report whether a worker-step message is queued but not yet delivered.

    A queued message has no event row until it runs, so the boundary still looks
    unanswered to the record. Asking the conversation what it is holding is what stops a
    second notice being sent every poll while the first one waits.
    """
    return any(
        (held.sender_message_id or "").startswith(ANSWERING_SENDER_MESSAGE_PREFIX)
        for held in held_prompts
    )


def memory_was_lost_unanswered(
    *,
    automatically_compacted_through_sequence: int,
    latest_answering_prompt_sequence: int | None,
) -> bool:
    """Decide whether a compaction boundary is still unreported to this Worker.

    A conversation Panels has never sent a worker-step message into has nothing to
    re-orient: its first wake arrives at a Worker that never had the context anyway.
    """
    if latest_answering_prompt_sequence is None:
        return False
    return automatically_compacted_through_sequence > latest_answering_prompt_sequence


def ticket_is_on_the_planning_day(
    conn: sqlite3.Connection, ticket_id: str, *, planning_day_id: str
) -> bool:
    """Ask the membership question again, for a Ticket the scan chose a moment ago."""
    return (
        conn.execute(
            "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
            (planning_day_id, ticket_id),
        ).fetchone()
        is not None
    )


def conversation_holds_an_unanswered_memory_loss(
    conn: sqlite3.Connection, conversation_id: str | None
) -> bool:
    """Ask the rule about one conversation. A Ticket with no conversation has no memory."""
    if conversation_id is None:
        return False
    row = conn.execute(
        "SELECT automatically_compacted_through_sequence FROM conversations "
        "WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return False
    return memory_was_lost_unanswered(
        automatically_compacted_through_sequence=int(row[0]),
        latest_answering_prompt_sequence=_latest_answering_prompt_sequence(
            conn, conversation_id
        ),
    )


def ticket_ids_holding_an_unanswered_memory_loss(
    conn: sqlite3.Connection, *, planning_day_id: str
) -> tuple[str, ...]:
    """Find the Tickets whose Worker lost its memory part-way through a step.

    Only a Ticket whose claim is out has a step in flight. Every other Ticket is answered
    by its next wake, which is the cheaper moment and the one that already exists.

    Day membership is how Panels decides whether a Ticket's work runs at all, and a
    notice is work: it can restart a Worker. So this asks the same question a wake asks,
    and a Ticket that is not on the planning day is left alone however old its boundary
    is.
    """
    rows = conn.execute(
        "SELECT t.id, t.conversation_id FROM tickets t "
        "JOIN day_tickets dt ON dt.ticket_id = t.id "
        "WHERE dt.day_id = ? AND t.worker_step_claim = 'out' AND t.conversation_id IS NOT NULL",
        (planning_day_id,),
    ).fetchall()
    return tuple(
        str(row[0])
        for row in rows
        if conversation_holds_an_unanswered_memory_loss(conn, str(row[1]))
    )


def _latest_answering_prompt_sequence(
    conn: sqlite3.Connection, conversation_id: str
) -> int | None:
    # substr, not LIKE: '_' is a single-character wildcard in LIKE, and the prefix is
    # mostly underscores.
    row = conn.execute(
        "SELECT MAX(sequence) FROM conversation_events "
        "WHERE conversation_id = ? AND kind = 'prompt' "
        "AND substr(json_extract(payload,'$.sender_message_id'),1,?) = ?",
        (conversation_id, len(ANSWERING_SENDER_MESSAGE_PREFIX), ANSWERING_SENDER_MESSAGE_PREFIX),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])
