"""What Panels knows about a Worker's lost memory, and nothing else does.

A Worker keeps its tools across a compaction. Its skills are files, and its Ticket is in
the database, so it can fetch anything it is missing. The one fact it cannot fetch is
that it was compacted at all: the conversation it was working from is simply shorter than
it was, and nothing in it says so. Panels records the boundary, so Panels is the only
side that can say it.

A boundary is *answered* once Panels has sent a worker-step message after it — a wake, or
the notice sent here. The automatic notice scan also leaves an uncertain notice alone;
uncertainty does not answer the memory-loss note owed on the next wake.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Final, Literal
from uuid import uuid4

from planner.tickets import data as tickets_data
from planner.tickets import locale_configuration
from planner.tickets.contracts import TicketLocale

WORKER_STEP_MESSAGE_PREFIX: Final = "worker_step_message_"
MEMORY_NOTICE_MESSAGE_PREFIX: Final = "worker_step_memory_notice_"
# Both ids begin with this, so one comparison asks "has Panels spoken since the boundary".
ANSWERING_SENDER_MESSAGE_PREFIX: Final = "worker_step_"


def new_worker_step_message_id() -> str:
    return f"{WORKER_STEP_MESSAGE_PREFIX}{uuid4().hex}"


def new_memory_notice_message_id() -> str:
    return f"{MEMORY_NOTICE_MESSAGE_PREFIX}{uuid4().hex}"


def is_memory_notice_sender_message_id(sender_message_id: str | None) -> bool:
    """Whether a prompt is the memory-loss notice, known by the id Panels minted for it.

    The notice is maintenance, not work, so the conversation core treats a turn that holds
    only notices as maintenance. It goes by delivery origin, never by the text.
    """
    return sender_message_id is not None and sender_message_id.startswith(
        MEMORY_NOTICE_MESSAGE_PREFIX
    )


def an_answer_is_already_waiting(sender_message_ids_awaiting_outcome: Iterable[str]) -> bool:
    """Report whether a worker-step message is on its way but not yet recorded.

    Such a message has no event row yet, so the boundary still looks unanswered to the
    record. That covers a queued message, and one that has just left the queue for the
    backend. Asking the conversation for both is what stops a second notice being sent
    every poll while the first one waits or is being written.
    """
    return any(
        sender_message_id.startswith(ANSWERING_SENDER_MESSAGE_PREFIX)
        for sender_message_id in sender_message_ids_awaiting_outcome
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


def conversation_holds_an_unanswered_memory_loss(
    conn: sqlite3.Connection, conversation_id: str | None
) -> bool:
    """Ask the rule about one conversation. A Ticket with no conversation has no memory.

    Every compaction counts here, Panels' own idle ``/compact`` included: the next wake
    carries the note whichever kind it was.
    """
    return _boundary_is_unanswered(
        conn, conversation_id, "automatically_compacted_through_sequence"
    )


def _working_turn_compaction_is_unanswered(
    conn: sqlite3.Connection, conversation_id: str | None
) -> bool:
    """Ask the rule about compactions inside a working turn only.

    Panels runs its own ``/compact`` after the conversation sat idle. The Worker was not
    part-way through anything then, so that boundary waits for the next wake instead of
    sending a notice that tells an idle Worker to continue.
    """
    return _boundary_is_unanswered(conn, conversation_id, "working_turn_compacted_through_sequence")


def _boundary_is_unanswered(
    conn: sqlite3.Connection,
    conversation_id: str | None,
    boundary_column: Literal[
        "automatically_compacted_through_sequence", "working_turn_compacted_through_sequence"
    ],
) -> bool:
    if conversation_id is None:
        return False
    row = conn.execute(
        f"SELECT {boundary_column} FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return False
    return memory_was_lost_unanswered(
        automatically_compacted_through_sequence=int(row[0]),
        latest_answering_prompt_sequence=_latest_answering_prompt_sequence(conn, conversation_id),
    )


def ticket_ids_holding_an_unanswered_memory_loss(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Find the Tickets whose Worker lost its memory part-way through a step.

    Only a Ticket whose claim is out has a step in flight. Every other Ticket is answered
    by its next wake, which is the cheaper moment and the one that already exists.
    """
    rows = conn.execute(
        "SELECT t.id, t.conversation_id FROM tickets t "
        "WHERE t.worker_step_claim = 'out' AND t.conversation_id IS NOT NULL"
    ).fetchall()
    return tuple(str(row[0]) for row in rows if ticket_needs_memory_notice(conn, str(row[0])))


def _latest_answering_prompt_sequence(conn: sqlite3.Connection, conversation_id: str) -> int | None:
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


def ticket_needs_memory_notice(conn: sqlite3.Connection, ticket_id: str) -> bool:
    """Check the automatic notice rule, without changing the next-wake memory rule."""
    row = conn.execute(
        "SELECT conversation_id,locale FROM tickets WHERE id=? AND worker_step_claim='out'",
        (ticket_id,),
    ).fetchone()
    if row is None or not locale_configuration.can_execute_locale(conn, TicketLocale(row[1])):
        return False
    conversation_id = row[0]
    if not _working_turn_compaction_is_unanswered(conn, conversation_id):
        return False
    # A backlogged Ticket keeps its claim, so the claim alone does not permit a wake.
    if tickets_data.automated_maintenance_suppression_reason(conn, conversation_id) is not None:
        return False
    # A possibly sent notice must not be retried automatically. It is not a delivered
    # prompt, so ordinary wakes still owe their memory-loss note. Uncertain owner
    # messages and worker-step openers do not consume a notice either.
    return (
        conn.execute(
            "SELECT 1 FROM conversation_events e JOIN conversations c USING(conversation_id) "
            "WHERE e.conversation_id=? AND e.kind='prompt_delivery_uncertain' "
            "AND e.sequence > c.working_turn_compacted_through_sequence "
            "AND substr(json_extract(e.payload,'$.sender_message_id'),1,?)=? LIMIT 1",
            (conversation_id, len(MEMORY_NOTICE_MESSAGE_PREFIX), MEMORY_NOTICE_MESSAGE_PREFIX),
        ).fetchone()
        is None
    )
