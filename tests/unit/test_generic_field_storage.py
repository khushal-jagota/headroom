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
from tests.support.probe import install_probe_registry, shipped_definition, uninstall_probe_registry

from planner.core.clock import TestClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.tickets import data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    PendingTicketProposal,
)
from planner.tickets.logic import fields_codec, machine
from planner.worker_types.contracts import WorkerTypeDefinition

CODING_WORKER_TYPE_DEFINITION = shipped_definition("coding")

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


def test_sparse_values_codec_round_trip_and_declared_order(
    probe_registry: WorkerTypeDefinition,
) -> None:
    raw = fields_codec.values_to_json({B_FIELD: "B", A_FIELD: "A"})
    values = fields_codec.values_from_json(raw, probe_registry.field_ids())
    assert dict(values) == {A_FIELD: "A", B_FIELD: "B"}
    assert fields_codec.field_value(values, A_FIELD, worker_type_definition=probe_registry) == "A"
    with pytest.raises(PlannerError):
        fields_codec.field_value(values, "success", worker_type_definition=probe_registry)


def test_proposal_codec_round_trip_is_strict() -> None:
    proposal = PendingTicketProposal("success", "text", "worker", 4)
    assert fields_codec.proposal_from_json(fields_codec.proposal_to_json(proposal)) == proposal
    with pytest.raises(PlannerError):
        fields_codec.proposal_from_json('{"field":"success"}')


def test_resolve_scope_distinguishes_unknown_from_too_early() -> None:
    with pytest.raises(PlannerError) as unknown:
        machine.resolve_scope(
            "needs_approach",
            "bogus",
            AtCap.stop,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
    assert unknown.value.code == ErrorCode.scope_invalid
    with pytest.raises(PlannerError) as early:
        machine.resolve_scope(
            "needs_plan",
            "needs_approach",
            AtCap.stop,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
    assert early.value.detail == {"next_ceiling": "needs_approach", "new_stage": "needs_plan"}


def _create(conn: Connection, now: int) -> str:
    return data.create_ticket(
        conn,
        worker_type="probe",
        title="Probe",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
    ).id


def test_probe_drive_uses_one_current_proposal_and_sparse_values(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkerTypeDefinition
) -> None:
    now = fake_clock.now_unix()
    tid = _create(tmp_db, now)
    ticket = data.accept_proposal(
        tmp_db,
        tid,
        field="kickoff",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=B,
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    assert ticket.stage == A and ticket.field_values == {"kickoff": ""}
    ticket = data.file_current_proposal_with_recap(
        tmp_db, tid, body="alpha", recap="r1", principal=ticket_principal(tid), now=now
    )
    assert (
        ticket.stage == B
        and ticket.field_values[A_FIELD] == "alpha"
        and ticket.pending_proposal is None
    )
    ticket = data.file_current_proposal_with_recap(
        tmp_db, tid, body="beta 1", recap="r2", principal=ticket_principal(tid), now=now
    )
    assert ticket.pending_proposal == PendingTicketProposal(B_FIELD, "beta 1", "worker", now)
    ticket = data.file_current_proposal_with_recap(
        tmp_db, tid, body="beta 2", recap="r3", principal=ticket_principal(tid), now=now
    )
    assert ticket.pending_proposal == PendingTicketProposal(B_FIELD, "beta 2", "worker", now)
    ticket = data.accept_proposal(
        tmp_db,
        tid,
        field=B_FIELD,
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.stop,
        next_holder=OWNER_PRINCIPAL,
    )
    assert (
        ticket.stage == "done"
        and ticket.field_values[B_FIELD] == "beta 2"
        and ticket.pending_proposal is None
    )


def test_recap_writer_infers_probe_gate(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkerTypeDefinition
) -> None:
    now = fake_clock.now_unix()
    tid = _create(tmp_db, now)
    data.accept_proposal(
        tmp_db,
        tid,
        field="kickoff",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    ticket = data.file_current_proposal_with_recap(
        tmp_db, tid, body="alpha", recap="probe recap", principal=ticket_principal(tid), now=now
    )
    assert ticket.pending_proposal is not None
    assert ticket.recap == "probe recap" and ticket.pending_proposal.field == A_FIELD


def test_current_gate_is_enforced_by_state_validation(probe_registry: WorkerTypeDefinition) -> None:
    with pytest.raises(PlannerError):
        fields_codec.validate_state(
            {},
            PendingTicketProposal(B_FIELD, "too early", "agent", 1),
            A,
            worker_type_definition=probe_registry,
        )


def test_probe_survives_create_and_reload(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkerTypeDefinition
) -> None:
    ticket = data.read_ticket(tmp_db, _create(tmp_db, fake_clock.now_unix()))
    assert ticket.stage == "needs_kickoff" and type(ticket.stage) is str
