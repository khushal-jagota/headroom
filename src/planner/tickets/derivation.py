"""One place answers what is true about a Ticket.

A Ticket stores one fact about its state of control: the worker-step claim. The wakeup
system takes a claim when it sends a worker its step, and the claim is given back or
marked failed. Everything else a reader calls a "status" is a question about facts that
are already stored somewhere else — the parked proposal, and the Tickets that block this
one — asked at the moment of the read.

This module holds both halves of that answer, and holds them together on purpose. The
rule is a pure function over stored facts. Around it sit the two ways a caller supplies
those facts: a reader that already selected its rows builds them with
``stored_facts_from_row``, and a reader that holds only Ticket ids calls
``load_ticket_facts``. Splitting the rule from the loading would give the answer two
homes again, which is the thing this module exists to remove.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from planner.core import ticket_blocks
from planner.tickets.contracts import TicketStatus, WorkerStepClaim
from planner.worker_types.configuration import configured_worker_type_registry


@dataclass(frozen=True, slots=True)
class StoredTicketFacts:
    """Everything stored that the derivation reads, for one Ticket."""

    ticket_id: str
    stage: str
    worker_type: str
    worker_step_claim: WorkerStepClaim
    has_pending_proposal: bool
    has_live_blocker: bool


@dataclass(frozen=True, slots=True)
class TicketFacts:
    """What is true about one Ticket, derived at the moment it is read."""

    ticket_id: str
    ticket_status: TicketStatus
    blocked: bool
    waiting_to_closeout: bool


class AgentState(StrEnum):
    working = "working"
    idle = "idle"
    errored = "errored"


def derive_ticket_status(stored: StoredTicketFacts) -> TicketStatus:
    """The one rule. First match wins, and the order is the order the writers produced.

    A failed claim outranks everything: it is the reason no step is running. A parked
    proposal outranks a claim, because filing one gives the claim back. A claim outranks
    a blocker, because a Ticket whose step is out is not resting. What is left is a
    Ticket at rest, and ``blocked`` is what rest is called while another Ticket blocks it.
    """
    if stored.worker_step_claim is WorkerStepClaim.errored:
        return TicketStatus.errored
    if stored.has_pending_proposal:
        return TicketStatus.awaiting_approval
    if stored.worker_step_claim is WorkerStepClaim.out:
        return TicketStatus.agent
    if stored.has_live_blocker:
        return TicketStatus.blocked
    return TicketStatus.empty


# The same rule, as a SQL predicate, for the one reader that has to ask it of many rows
# inside a query rather than of facts it already holds. It must keep answering what
# ``derive_ticket_status`` answers: a Ticket is at rest when nothing has its step out,
# nothing failed, and nothing is parked on it. Whether a blocker holds it does not
# change that — `blocked` is a name for rest.
RESTS_PREDICATE = "t.worker_step_claim = 'none' AND t.pending_proposal IS NULL"


def ticket_rests(stored: StoredTicketFacts) -> bool:
    """Whether this Ticket is at rest. The Python face of ``RESTS_PREDICATE``."""
    return derive_ticket_status(stored) in (TicketStatus.empty, TicketStatus.blocked)


def derive_ticket_facts(stored: StoredTicketFacts) -> TicketFacts:
    ticket_status = derive_ticket_status(stored)
    gating_field = configured_worker_type_registry().require(stored.worker_type).gating_field(
        stored.stage
    )
    return TicketFacts(
        ticket_id=stored.ticket_id,
        ticket_status=ticket_status,
        blocked=stored.has_live_blocker,
        waiting_to_closeout=(
            gating_field == "closeout" and ticket_status is TicketStatus.empty
        ),
    )


def agent_state(
    ticket_status: TicketStatus,
    *,
    turn_is_running: bool,
    last_turn_failed: bool,
) -> AgentState:
    """What the employee on this Ticket is doing, once the live conversation is known."""
    if turn_is_running:
        return AgentState.working
    if ticket_status is TicketStatus.errored or last_turn_failed:
        return AgentState.errored
    return AgentState.idle


# The blocker question as a column, for a reader that selects one Ticket and would
# otherwise need a second query to ask it. A list read uses the bulk set instead.
HAS_LIVE_BLOCKER_COLUMN = (
    "EXISTS (SELECT 1 FROM ticket_blocks JOIN tickets blocker "
    "ON blocker.id = ticket_blocks.blocking_ticket_id "
    "WHERE ticket_blocks.blocked_ticket_id = tickets.id "
    f"AND {ticket_blocks.LIVE_BLOCKER_PREDICATE}) AS has_live_blocker"
)


def stored_facts_from_row(row: sqlite3.Row, *, has_live_blocker: bool) -> StoredTicketFacts:
    """Build the derivation's input from a row a reader already selected.

    The row must carry ``id``, ``stage``, ``worker_type``, ``worker_step_claim`` and
    ``pending_proposal``. A list read answers ``has_live_blocker`` from the one bulk set
    in ``ticket_blocks.blocked_ticket_ids``, so a list read stays a list read.
    """
    return StoredTicketFacts(
        ticket_id=str(row["id"]),
        stage=str(row["stage"]),
        worker_type=str(row["worker_type"]),
        worker_step_claim=WorkerStepClaim(str(row["worker_step_claim"])),
        has_pending_proposal=row["pending_proposal"] is not None,
        has_live_blocker=has_live_blocker,
    )


def load_ticket_facts(
    conn: sqlite3.Connection, ticket_ids: set[str] | None = None
) -> dict[str, TicketFacts]:
    """Derive for many Tickets in two queries, whatever the size of the set.

    ``ticket_ids`` of ``None`` means every Ticket.
    """
    if ticket_ids is not None and not ticket_ids:
        return {}
    columns = "id, stage, worker_type, worker_step_claim, pending_proposal"
    if ticket_ids is None:
        rows = conn.execute(f"SELECT {columns} FROM tickets").fetchall()
    else:
        placeholders = ",".join("?" for _ in ticket_ids)
        rows = conn.execute(
            f"SELECT {columns} FROM tickets WHERE id IN ({placeholders})",
            tuple(sorted(ticket_ids)),
        ).fetchall()
    blocked_ticket_ids = ticket_blocks.blocked_ticket_ids(conn)
    return {
        str(row["id"]): derive_ticket_facts(
            stored_facts_from_row(row, has_live_blocker=str(row["id"]) in blocked_ticket_ids)
        )
        for row in rows
    }


def ticket_facts(conn: sqlite3.Connection, ticket_id: str) -> TicketFacts | None:
    """The same answer for one Ticket."""
    return load_ticket_facts(conn, {ticket_id}).get(ticket_id)
