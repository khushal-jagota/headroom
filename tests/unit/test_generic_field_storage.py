"""Generic Worker-type coverage for sparse values and one current proposal."""

import json
from collections.abc import Iterator
from dataclasses import replace
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
from tests.support.probe import (
    install_probe_registry,
    seed_probe_worker_type,
    uninstall_probe_registry,
)

from planner.core.clock import TestClock
from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.tickets import data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    PendingTicketProposal,
)
from planner.tickets.logic import fields_codec
from planner.tickets.logic.decisions import Decision
from planner.worker_types.contracts import WorkerTypeDefinition


@pytest.fixture
def probe_registry(tmp_db: Connection) -> Iterator[WorkerTypeDefinition]:
    seed_probe_worker_type(tmp_db)
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
        brief="Agreed brief.",
    ).id


def test_probe_drive_uses_one_current_proposal_and_sparse_values(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkerTypeDefinition
) -> None:
    now = fake_clock.now_unix()
    tid = _create(tmp_db, now)
    historical = {"retired_probe_field": "  Historical text\nwith Unicode: café.  "}
    tmp_db.execute(
        "UPDATE tickets SET field_values=? WHERE id=?", (json.dumps(historical), tid)
    )
    tmp_db.commit()
    assert data.read_ticket(tmp_db, tid).field_values == {}
    data.audit_ticket_registry_integrity(tmp_db)
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
    ticket = data.edit_ticket(
        tmp_db, tid, edit={"field_values": {"brief": "Corrected brief."}},
        title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=now,
    )
    assert ticket.field_values == {"brief": "Corrected brief."}
    assert json.loads(tmp_db.execute(
        "SELECT field_values FROM tickets WHERE id=?", (tid,)
    ).fetchone()[0]) == {**historical, "brief": "Corrected brief."}
    with pytest.raises(PlannerError):
        data.edit_ticket(
            tmp_db, tid, edit={"field_values": {"retired_probe_field": "New text"}},
            title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=now,
        )
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

    stored = json.loads(tmp_db.execute(
        "SELECT field_values FROM tickets WHERE id=?", (tid,)
    ).fetchone()[0])
    assert stored == {**historical, **ticket.field_values}
    # Decisions replace declared state. Cleared values must not return from raw storage.
    with tmp_db:
        ticket = data._apply_decision(
            tmp_db, ticket,
            replace(Decision.from_ticket(ticket), field_values={"brief": "Corrected brief."}),
            now,
        )
    assert ticket.field_values == {"brief": "Corrected brief."}
    assert json.loads(tmp_db.execute(
        "SELECT field_values FROM tickets WHERE id=?", (tid,)
    ).fetchone()[0]) == {**historical, "brief": "Corrected brief."}


@pytest.mark.parametrize("raw", ["{", "[]", '"text"', '{"alpha": 1}', '{"removed": null}'])
def test_field_read_does_not_hide_corrupt_storage(raw: str) -> None:
    with pytest.raises((PlannerError, json.JSONDecodeError)):
        fields_codec.values_from_json(raw, ("alpha",))


def test_unknown_proposed_and_new_values_stay_invalid(
    probe_registry: WorkerTypeDefinition,
) -> None:
    for values, proposal in (
        ({"removed": "New text"}, None),
        ({}, PendingTicketProposal("removed", "New text", "worker", 1)),
    ):
        with pytest.raises(PlannerError, match="corrupt ticket field state"):
            fields_codec.validate_state(
                values, proposal, A, worker_type_definition=probe_registry,
            )
