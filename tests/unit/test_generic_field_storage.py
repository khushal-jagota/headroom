"""Generic Worker-type coverage for sparse values and one current proposal."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal
from tests.support.probe import FIELD_ALPHA as A_FIELD
from tests.support.probe import FIELD_BETA as B_FIELD
from tests.support.probe import NEEDS_ALPHA as A
from tests.support.probe import NEEDS_BETA as B
from tests.support.probe import NEEDS_LANDING as CLOSEOUT_STAGE
from tests.support.probe import install_probe_registry, uninstall_probe_registry

from planner.core.clock import TestClock
from planner.core.db import connect, create_schema
from planner.tickets import data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    PendingTicketProposal,
)
from planner.worker_types.contracts import WorkerTypeDefinition


@pytest.fixture
def probe_registry() -> Iterator[WorkerTypeDefinition]:
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Connection]:
    conn = connect(str(tmp_path / "probe.db"))
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def fake_clock() -> TestClock:
    return TestClock(datetime(2026, 7, 4, 12).astimezone())


def _create(conn: Connection, now: int) -> str:
    return data.create_ticket(
        conn,
        worker_type="probe",
        title="Probe",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="Agreed brief.",
    ).id


def test_probe_drive_uses_one_current_proposal_and_sparse_values(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkerTypeDefinition
) -> None:
    now = fake_clock.now_unix()
    tid = _create(tmp_db, now)
    ticket = data.accept_proposal(
        tmp_db,
        tid,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=B,
        next_holder=OWNER_PRINCIPAL,
    )
    assert ticket.stage == A and ticket.field_values == {"brief": "Agreed brief."}
    ticket = data.file_current_proposal(
        tmp_db, tid, body="alpha", principal=ticket_principal(tid), now=now
    )
    assert (
        ticket.stage == B
        and ticket.field_values[A_FIELD] == "alpha"
        and ticket.pending_proposal is None
    )
    ticket = data.file_current_proposal(
        tmp_db, tid, body="beta 1", principal=ticket_principal(tid), now=now
    )
    assert ticket.pending_proposal == PendingTicketProposal(B_FIELD, "beta 1", "worker", now)
    ticket = data.file_current_proposal(
        tmp_db, tid, body="beta 2", principal=ticket_principal(tid), now=now
    )
    assert ticket.pending_proposal == PendingTicketProposal(B_FIELD, "beta 2", "worker", now)
    ticket = data.accept_proposal(
        tmp_db,
        tid,
        field=B_FIELD,
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
    assert (
        ticket.stage == CLOSEOUT_STAGE
        and ticket.field_values[B_FIELD] == "beta 2"
        and ticket.pending_proposal is None
    )
    ticket = data.file_current_proposal(
        tmp_db, tid, body="landed", principal=ticket_principal(tid), now=now
    )
    ticket = data.accept_proposal(
        tmp_db,
        tid,
        field="consequences",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
    assert (
        ticket.stage == "done"
        and ticket.field_values["consequences"] == "landed"
        and ticket.pending_proposal is None
    )
