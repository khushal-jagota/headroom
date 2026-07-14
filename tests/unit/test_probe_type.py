from __future__ import annotations

from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.probe import (
    FIELD_ALPHA,
    FIELD_BETA,
    NEEDS_ALPHA,
    NEEDS_BETA,
    PROBE_WORKER_TYPE_DEFINITION,
    build_probe_registry,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.core.clock import TestClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, StageOwnershipMode, TicketStatus
from planner.tickets.logic import fields_codec
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.fixture
def tmp_db(tmp_path: Path) -> Connection:
    conn = connect(str(tmp_path / "probe.db"))
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def fake_clock() -> TestClock:
    return TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())


@pytest.fixture(autouse=True)
def probe_registry() -> None:
    install_probe_registry()
    yield
    uninstall_probe_registry()


def test_probe_registry_is_explicit_and_ordered() -> None:
    assert build_probe_registry().registered_worker_types() == (
        "coding",
        "new_worker",
        "probe",
    )
    assert configured_worker_type_registry().require("probe") is PROBE_WORKER_TYPE_DEFINITION


def test_probe_definition_owns_novel_workflow_behavior() -> None:
    definition = PROBE_WORKER_TYPE_DEFINITION
    assert definition.stage_ids() == (
        "needs_kickoff",
        NEEDS_ALPHA,
        NEEDS_BETA,
        "done",
    )
    assert definition.field_ids() == ("kickoff", FIELD_ALPHA, FIELD_BETA)
    assert definition.default_ceiling() == "needs_kickoff"
    assert definition.first_worker_stage() == NEEDS_ALPHA
    assert definition.reconciliation_field_order() == (
        "kickoff",
        FIELD_ALPHA,
        FIELD_BETA,
    )


def test_probe_ticket_drives_through_real_writers(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Probe work",
        worker_type="probe",
        actor="human",
        now=now,
        title_max_chars=200,
    )
    ticket = tickets_data.accept_proposal(
        tmp_db,
        ticket.id,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling=NEEDS_BETA,
        at_cap=AtCap.propose,
    )
    assert ticket.stage == NEEDS_ALPHA
    assert ticket.ticket_status is TicketStatus.user_takeover
    ticket = tickets_data.set_stage_ownership(
        tmp_db,
        ticket.id,
        stage=NEEDS_ALPHA,
        ownership_mode=StageOwnershipMode.worker,
        now=now,
    )
    assert ticket.ticket_status is TicketStatus.empty
    ticket = tickets_data.file_proposal(
        tmp_db,
        ticket.id,
        field=FIELD_ALPHA,
        body="alpha value",
        actor="agent",
        now=now,
    )
    assert ticket.stage == NEEDS_BETA
    assert ticket.ticket_status is TicketStatus.paired_work
    assert fields_codec.get_slot(ticket.fields, FIELD_ALPHA).value == "alpha value"


def test_plain_read_requires_registry_for_ownership_metadata(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Stored probe",
        worker_type="probe",
        actor="human",
        now=fake_clock.now_unix(),
        title_max_chars=200,
    )
    uninstall_probe_registry()
    with pytest.raises(PlannerError) as exc:
        tickets_data.read_ticket(tmp_db, ticket.id)
    assert exc.value.code is ErrorCode.not_found
    assert exc.value.detail == {"worker_type": "probe"}
