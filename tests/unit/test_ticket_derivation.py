"""The one rule that says what is true about a Ticket, and the reads that feed it."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from planner.core.db import connect, create_schema
from planner.tickets import derivation
from planner.tickets.contracts import TicketStatus, WorkerStepClaim
from planner.tickets.derivation import StoredTicketFacts


def _stored(
    *,
    claim: WorkerStepClaim = WorkerStepClaim.none,
    proposal: bool = False,
    blocker: bool = False,
    stage: str = "needs_success_condition",
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




def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "derivation.db"))
    create_schema(conn)
    return conn
