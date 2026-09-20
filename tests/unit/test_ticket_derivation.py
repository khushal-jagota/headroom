"""The one rule that says what is true about a Ticket, and the reads that feed it."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.core import ticket_blocks
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets import derivation
from planner.tickets.contracts import TicketStatus, WorkerStepClaim
from planner.tickets.derivation import StoredTicketFacts


def _stored(
    *,
    claim: WorkerStepClaim = WorkerStepClaim.none,
    proposal: bool = False,
    blocker: bool = False,
    stage: str = "needs_success",
) -> StoredTicketFacts:
    return StoredTicketFacts(
        ticket_id="t_rule",
        stage=stage,
        worker_type="coding",
        worker_step_claim=claim,
        has_pending_proposal=proposal,
        has_live_blocker=blocker,
    )


# Every combination of the three stored facts, and the one status each produces. The
# table is the rule: a reader that disagrees with it is the bug this module removes.
@pytest.mark.parametrize(
    ("claim", "proposal", "blocker", "expected"),
    [
        (WorkerStepClaim.none, False, False, TicketStatus.empty),
        (WorkerStepClaim.none, False, True, TicketStatus.blocked),
        (WorkerStepClaim.none, True, False, TicketStatus.awaiting_approval),
        (WorkerStepClaim.none, True, True, TicketStatus.awaiting_approval),
        (WorkerStepClaim.out, False, False, TicketStatus.agent),
        (WorkerStepClaim.out, False, True, TicketStatus.agent),
        (WorkerStepClaim.out, True, False, TicketStatus.awaiting_approval),
        (WorkerStepClaim.out, True, True, TicketStatus.awaiting_approval),
        (WorkerStepClaim.errored, False, False, TicketStatus.errored),
        (WorkerStepClaim.errored, False, True, TicketStatus.errored),
        (WorkerStepClaim.errored, True, False, TicketStatus.errored),
        (WorkerStepClaim.errored, True, True, TicketStatus.errored),
    ],
)
def test_the_rule_answers_one_status_for_every_combination_of_stored_facts(
    claim: WorkerStepClaim,
    proposal: bool,
    blocker: bool,
    expected: TicketStatus,
) -> None:
    assert (
        derivation.derive_ticket_status(
            _stored(claim=claim, proposal=proposal, blocker=blocker)
        )
        is expected
    )


def test_waiting_to_closeout_is_a_resting_ticket_on_its_closeout_stage(
    tmp_path: Path,
) -> None:
    # The Worker types come from a database, and this fact asks one which Stage it gates.
    _db(tmp_path).close()
    assert derivation.derive_ticket_facts(_stored(stage="needs_closeout")).waiting_to_closeout
    assert not derivation.derive_ticket_facts(_stored(stage="needs_plan")).waiting_to_closeout
    # A claim out is not resting, so there is nothing waiting.
    assert not derivation.derive_ticket_facts(
        _stored(stage="needs_closeout", claim=WorkerStepClaim.out)
    ).waiting_to_closeout


def test_agent_state_reads_the_live_turn_first_then_the_failed_claim() -> None:
    working = derivation.agent_state(
        TicketStatus.errored, turn_is_running=True, last_turn_failed=True
    )
    assert working is derivation.AgentState.working
    assert (
        derivation.agent_state(
            TicketStatus.errored, turn_is_running=False, last_turn_failed=False
        )
        is derivation.AgentState.errored
    )
    assert (
        derivation.agent_state(TicketStatus.empty, turn_is_running=False, last_turn_failed=True)
        is derivation.AgentState.errored
    )
    assert (
        derivation.agent_state(TicketStatus.empty, turn_is_running=False, last_turn_failed=False)
        is derivation.AgentState.idle
    )


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "derivation.db"))
    create_schema(conn)
    return conn


def _ticket(conn: sqlite3.Connection, title: str) -> str:
    return tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title=title,
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    ).id


class _CountingConnection:
    """Counts the reads a caller makes, so a bulk read cannot quietly become N of them."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.queries = 0

    def execute(self, sql: str, parameters: object = ()) -> sqlite3.Cursor:
        self.queries += 1
        return self._conn.execute(sql, parameters)  # type: ignore[arg-type]


def test_many_tickets_are_derived_in_a_fixed_number_of_queries(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    ticket_ids = {_ticket(conn, f"Ticket {index}") for index in range(60)}

    counting = _CountingConnection(conn)
    facts = derivation.load_ticket_facts(counting, ticket_ids)  # type: ignore[arg-type]

    assert set(facts) == ticket_ids
    # One read of the Tickets, one of the blocks. Sixty Tickets, two queries.
    assert counting.queries == 2

    counting_one = _CountingConnection(conn)
    derivation.load_ticket_facts(counting_one, {next(iter(ticket_ids))})  # type: ignore[arg-type]
    assert counting_one.queries == 2
    conn.close()


def test_a_live_blocker_makes_a_resting_ticket_read_as_blocked(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    blocker_id = _ticket(conn, "Blocker")
    blocked_id = _ticket(conn, "Blocked")
    ticket_blocks.add_ticket_block(conn, blocker_id, blocked_id, 2)

    facts = derivation.load_ticket_facts(conn, {blocker_id, blocked_id})
    assert facts[blocked_id].blocked is True
    assert facts[blocked_id].ticket_status is TicketStatus.awaiting_approval  # proposal wins
    assert facts[blocker_id].blocked is False

    # With the proposal gone, what is left is rest, and rest with a live blocker is blocked.
    conn.execute("UPDATE tickets SET pending_proposal = NULL WHERE id = ?", (blocked_id,))
    assert (
        derivation.load_ticket_facts(conn, {blocked_id})[blocked_id].ticket_status
        is TicketStatus.blocked
    )
    conn.close()


def test_one_ticket_is_read_without_a_second_query_for_its_blockers(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    blocker_id = _ticket(conn, "Blocker")
    blocked_id = _ticket(conn, "Blocked")
    ticket_blocks.add_ticket_block(conn, blocker_id, blocked_id, 2)
    conn.execute("UPDATE tickets SET pending_proposal = NULL WHERE id = ?", (blocked_id,))

    counting = _CountingConnection(conn)
    ticket = tickets_data.read_ticket(counting, blocked_id)  # type: ignore[arg-type]

    assert ticket.ticket_status is TicketStatus.blocked
    # The blocker question travels as a column on the row the read already fetched.
    assert counting.queries == 1
    conn.close()


def _updated_at(conn: sqlite3.Connection, ticket_id: str) -> int:
    return int(
        conn.execute("SELECT updated_at FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0]
    )


def test_a_block_arriving_or_leaving_is_activity_on_the_ticket_it_holds(
    tmp_path: Path,
) -> None:
    """What a blocked Ticket reads as is derived. When it last moved is still recorded.

    The Workspace rail orders by that time, so a Ticket that has just become blocked, or
    that its blocker has just freed, has to rise where the user is looking.
    """
    conn = _db(tmp_path)
    blocker_id = _ticket(conn, "Blocker")
    blocked_id = _ticket(conn, "Blocked")
    before = _updated_at(conn, blocked_id)

    ticket_blocks.add_ticket_block(conn, blocker_id, blocked_id, before + 100)
    assert _updated_at(conn, blocked_id) == before + 100

    ticket_blocks.remove_ticket_block(conn, blocker_id, blocked_id, before + 200)
    assert _updated_at(conn, blocked_id) == before + 200
    conn.close()


def test_completing_a_blocker_is_activity_on_every_ticket_it_frees(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    blocker_id = _ticket(conn, "Blocker")
    blocked_id = _ticket(conn, "Blocked")
    ticket_blocks.add_ticket_block(conn, blocker_id, blocked_id, 2)
    settled = _updated_at(conn, blocked_id)

    tickets_data.drop_ticket(conn, blocker_id, principal=OWNER_PRINCIPAL, now=settled + 500)

    assert _updated_at(conn, blocked_id) == settled + 500
    assert (
        derivation.load_ticket_facts(conn, {blocked_id})[blocked_id].blocked is False
    )
    conn.close()
