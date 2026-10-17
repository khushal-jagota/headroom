"""What Panels knows about a Worker's lost memory, and nothing else does.

A Worker keeps its tools across a compaction. Its skills are files, and its Ticket is in
the database, so it can fetch anything it is missing. The one fact it cannot fetch is
that it was compacted at all: the conversation it was working from is simply shorter than
it was, and nothing in it says so. Panels records the boundary, so Panels is the only
side that can say it.

A boundary is *answered* once Panels has sent a worker-step message after it — a wake, or
the notice sent here. Automatic permission is a separate durable caller fact: one consumed
authorization prevents replay even when no answering prompt exists.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from hashlib import sha256
from typing import Final
from uuid import uuid4

from planner.conversation.contracts import HeldPrompt
from planner.runtime.worker_memory_contracts import (
    MemoryNoticeAcquisition,
    MemoryNoticeEligibility,
    MemoryNoticeEventKey,
    MemoryNoticeEvidence,
    MemoryNoticeHold,
    MemoryNoticeInspection,
    MemoryNoticeReservation,
)
from planner.tickets import locale_configuration

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
    return tuple(
        str(row[0])
        for row in rows
        if (key := current_memory_notice_key(conn, str(row[0]))) is not None
        and memory_notice_eligibility(conn, key) is MemoryNoticeEligibility.eligible
    )


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


def current_memory_notice_key(
    conn: sqlite3.Connection, ticket_id: str
) -> MemoryNoticeEventKey | None:
    """Resolve immutable event identity; a watermark alone grants no permission."""
    row = conn.execute(
        "SELECT t.conversation_id,c.automatically_compacted_through_sequence,"
        "(SELECT MAX(e.sequence) FROM conversation_events e "
        "WHERE e.conversation_id=t.conversation_id AND e.kind='context_compacted') "
        "FROM tickets t JOIN conversations c ON c.conversation_id=t.conversation_id "
        "WHERE t.id=? AND t.worker_step_claim='out' AND t.locale=?",
        (ticket_id, locale_configuration.CURRENT_EXECUTION_LOCALE.value),
    ).fetchone()
    if row is None or row[2] is None:
        return None
    conversation_id, coverage, event_sequence = str(row[0]), int(row[1]), int(row[2])
    answer = _latest_answering_prompt_sequence(conn, conversation_id)
    if not memory_was_lost_unanswered(
        automatically_compacted_through_sequence=coverage,
        latest_answering_prompt_sequence=answer,
    ) or (answer is not None and answer >= event_sequence):
        return None
    return MemoryNoticeEventKey(
        ticket_id=ticket_id,
        conversation_id=conversation_id,
        context_compacted_sequence=event_sequence,
    )


def memory_notice_eligibility(
    conn: sqlite3.Connection, key: MemoryNoticeEventKey
) -> MemoryNoticeEligibility:
    if current_memory_notice_key(conn, key.ticket_id) != key:
        return MemoryNoticeEligibility.ineligible
    if conn.execute(
        "SELECT 1 FROM worker_memory_notice_holds WHERE ticket_id=? AND conversation_id=? "
        "AND (context_compacted_sequence IS NULL OR context_compacted_sequence=?)",
        (key.ticket_id, key.conversation_id, key.context_compacted_sequence),
    ).fetchone():
        return MemoryNoticeEligibility.held
    if conn.execute(
        "SELECT 1 FROM worker_memory_notice_reservations WHERE ticket_id=? AND conversation_id=? "
        "AND context_compacted_sequence=?",
        (key.ticket_id, key.conversation_id, key.context_compacted_sequence),
    ).fetchone():
        return MemoryNoticeEligibility.consumed
    return MemoryNoticeEligibility.eligible


def consume_memory_notice_authorization(
    conn: sqlite3.Connection, key: MemoryNoticeEventKey, *, now: int
) -> MemoryNoticeAcquisition:
    """Commit permission once, before I/O. Every later outcome retains consumption.

    Acquisition and commit exceptions propagate to the caller, which must send nothing.
    No receipt, attempt, delivery, answer, or running-state fact is written here.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        eligibility = memory_notice_eligibility(conn, key)
        if eligibility is not MemoryNoticeEligibility.eligible:
            conn.execute("ROLLBACK")
            return MemoryNoticeAcquisition(eligibility=eligibility)
        identity = json.dumps(
            [key.ticket_id, key.conversation_id, key.context_compacted_sequence],
            separators=(",", ":"),
        )
        reservation = MemoryNoticeReservation(
            key=key,
            sender_message_id=MEMORY_NOTICE_MESSAGE_PREFIX + sha256(identity.encode()).hexdigest(),
            consumed_at=now,
        )
        conn.execute(
            "INSERT INTO worker_memory_notice_reservations VALUES (?,?,?,?,?)",
            (
                key.ticket_id,
                key.conversation_id,
                key.context_compacted_sequence,
                reservation.sender_message_id,
                now,
            ),
        )
        conn.execute("COMMIT")
        return MemoryNoticeAcquisition(eligibility=eligibility, reservation=reservation)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def inspect_memory_notices(conn: sqlite3.Connection, ticket_id: str) -> MemoryNoticeInspection:
    """Read original associations and actual event references without inferred delivery."""
    reservations = tuple(
        MemoryNoticeReservation(
            key=MemoryNoticeEventKey(
                ticket_id=str(row[0]),
                conversation_id=str(row[1]),
                context_compacted_sequence=int(row[2]),
            ),
            sender_message_id=str(row[3]),
            consumed_at=int(row[4]),
        )
        for row in conn.execute(
            "SELECT * FROM worker_memory_notice_reservations WHERE ticket_id=? "
            "ORDER BY conversation_id,context_compacted_sequence",
            (ticket_id,),
        )
    )
    holds = tuple(
        MemoryNoticeHold(
            ticket_id=str(row[0]),
            conversation_id=str(row[1]),
            context_compacted_sequence=None if row[2] is None else int(row[2]),
            created_at=int(row[3]),
            reason=str(row[4]),
        )
        for row in conn.execute(
            "SELECT * FROM worker_memory_notice_holds WHERE ticket_id=? "
            "ORDER BY conversation_id,context_compacted_sequence",
            (ticket_id,),
        )
    )
    evidence = tuple(
        MemoryNoticeEvidence(
            conversation_id=str(row[0]),
            sequence=int(row[1]),
            kind=str(row[2]),
            sender_message_id=str(row[3]),
        )
        for row in conn.execute(
            "SELECT e.conversation_id,e.sequence,e.kind,"
            "json_extract(e.payload,'$.sender_message_id') "
            "FROM conversation_events e JOIN worker_memory_notice_reservations r "
            "ON r.conversation_id=e.conversation_id AND "
            "r.sender_message_id=json_extract(e.payload,'$.sender_message_id') "
            "WHERE r.ticket_id=? ORDER BY e.conversation_id,e.sequence",
            (ticket_id,),
        )
    )
    return MemoryNoticeInspection(reservations=reservations, holds=holds, evidence=evidence)


def require_memory_notice_storage(conn: sqlite3.Connection) -> None:
    """Fail closed before polling if migration did not complete."""
    conn.execute("SELECT 1 FROM worker_memory_notice_reservations LIMIT 1")
    conn.execute("SELECT 1 FROM worker_memory_notice_holds LIMIT 1")
