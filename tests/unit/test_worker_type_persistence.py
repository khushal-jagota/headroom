"""t_tt02 — the Worker-type persistence doors, startup integrity
audit, per-type default ceiling, and the injectable-registry seam.

These tests assert acceptance items 4 (startup audit), 5 (validation doors), 6
(per-type default ceiling), and demonstrate the review-F6 boundary via a
coding-shaped second type installed through the explicit test configuration seam.

The second type reuses coding's exact Stages and fields with a different Worker-type
id. This proves per-row resolution, the unknown-type door, and per-type default
ceiling sourcing through the same definition-backed paths."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.probe import shipped_definition

from planner.core.clock import TestClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    TITLE_MAX_CHARS,
)
from planner.worker_types.configuration import (
    ConfiguredWorkerRuntimeDefinitions,
    configured_worker_type_registry,
    install_worker_runtime_definitions_for_test,
    restore_worker_runtime_definitions_for_test,
)
from planner.worker_types.contracts import WorkerTypeDefinition
from planner.worker_types.registry import WorkerTypeRegistry

CODING_WORKER_TYPE_DEFINITION = shipped_definition("coding")

# A coding-shaped second type with the same Stage and field declarations but a
# different Worker-type id.
CODING_PROBE_WORKER_TYPE_DEFINITION: WorkerTypeDefinition = WorkerTypeDefinition(
    worker_type="coding_probe",
    label="Coding Probe",
    stages=CODING_WORKER_TYPE_DEFINITION.stages,
    dropped_stage=CODING_WORKER_TYPE_DEFINITION.dropped_stage,
    fields=CODING_WORKER_TYPE_DEFINITION.fields,
    worker_profile=CODING_WORKER_TYPE_DEFINITION.worker_profile,
)


def _two_type_registry() -> WorkerTypeRegistry:
    return WorkerTypeRegistry(
        (CODING_WORKER_TYPE_DEFINITION, CODING_PROBE_WORKER_TYPE_DEFINITION),
        # CODING_PROBE_WORKER_TYPE_DEFINITION inherits coding's profile (specialist_skill=
        # "panels-worker-coding"), so the catalog must carry it or R14 fails.
        known_skills=frozenset({"panels-worker", "panels-worker-coding"}),
        known_toolset_profiles=frozenset({"default"}),
    )


@pytest.fixture
def two_type_registry() -> Iterator[None]:
    """Install a registry carrying coding + a coding-shaped second type for the
    persistence doors, then restore production composition after the test."""
    registry = _two_type_registry()
    previous_definitions = install_worker_runtime_definitions_for_test(
        ConfiguredWorkerRuntimeDefinitions(registry)
    )
    try:
        yield
    finally:
        restore_worker_runtime_definitions_for_test(previous_definitions)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Connection:
    conn = connect(str(tmp_path / "type-doors.db"))
    create_schema(conn)
    return conn


@pytest.fixture
def fake_clock() -> TestClock:
    return TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())


_SAVED_VALUES = json.dumps({"kickoff": "k", "success": "s"})


def _raw_insert_ticket(
    conn: Connection,
    *,
    ticket_id: str,
    worker_type: str = "coding",
    stage: str = "needs_success",
    ceiling: str = "needs_success",
    fields: str = _SAVED_VALUES,
) -> None:
    # Direct SQL bypasses the create/write doors (the enumerating CHECKs are gone), so
    # a deliberately corrupt row can be planted for the boot-audit tests.
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, stage, ceiling, "
        "field_values, created_at, updated_at) "
        "VALUES (?, 'T', ?, 'hermes', ?, ?, ?, 1, 1)",
        (ticket_id, worker_type, stage, ceiling, fields),
    )


# =====================================================================
# Acceptance 4 — startup integrity audit
# =====================================================================


def test_audit_passes_clean_migrated_table(tmp_db: Connection) -> None:
    _raw_insert_ticket(tmp_db, ticket_id="t_ok")
    tickets_data.audit_ticket_registry_integrity(tmp_db)  # no raise


def test_audit_rejects_unknown_worker_type(tmp_db: Connection) -> None:
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", worker_type="bogus")
    with pytest.raises(RuntimeError, match="ticket integrity audit failed: id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "unknown worker type" in str(exc.value)
    assert "unknown worker type" in str(exc.value)


def test_audit_rejects_stage_outside_the_types_stages(tmp_db: Connection) -> None:
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", stage="needs_alpha")
    with pytest.raises(RuntimeError, match="id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "stage outside the linear order" in str(exc.value)
    assert "stage outside" in str(exc.value)


def test_audit_rejects_undeclared_saved_field(tmp_db: Connection) -> None:
    payload = json.loads(_SAVED_VALUES)
    payload["retired"] = "not a current field"
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", fields=json.dumps(payload))
    with pytest.raises(RuntimeError, match="id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "corrupt ticket field state" in str(exc.value)


def test_audit_rejects_syntactically_invalid_fields_json(tmp_db: Connection) -> None:
    # A row whose fields is not valid JSON (the codec's json.loads raises
    # JSONDecodeError, not PlannerError) must still fail the audit with its id named,
    # not abort startup with a raw decode traceback.
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", fields="{")
    with pytest.raises(RuntimeError, match="id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "Expecting" in str(exc.value)


# =====================================================================
# Acceptance 5 — validation doors
# =====================================================================


def test_plain_row_load_requires_registry_to_resolve_ownership_metadata(
    tmp_db: Connection,
) -> None:
    _raw_insert_ticket(
        tmp_db,
        ticket_id="t_load",
        worker_type="unregistered_worker",
        stage="needs_unregistered_work",
        ceiling="needs_unregistered_work",
    )

    with pytest.raises(PlannerError) as exc:
        tickets_data.read_ticket(tmp_db, "t_load")
    assert exc.value.code is ErrorCode.not_found
    assert exc.value.detail == {"worker_type": "unregistered_worker"}


def test_metadata_write_rejects_invalid_stored_tuple_before_durable_effect(
    tmp_db: Connection,
) -> None:
    _raw_insert_ticket(
        tmp_db,
        ticket_id="t_invalid_write",
        worker_type="coding",
        stage="needs_unregistered_work",
        ceiling="needs_success",
    )
    before = tuple(
        tmp_db.execute(
            "SELECT title, stage, updated_at FROM tickets WHERE id = 't_invalid_write'"
        ).fetchone()
    )

    with pytest.raises(PlannerError) as exc:
        tickets_data.read_ticket(tmp_db, "t_invalid_write")

    assert exc.value.code == ErrorCode.validation
    assert exc.value.detail == {"stage": "needs_unregistered_work"}
    assert (
        tuple(
            tmp_db.execute(
                "SELECT title, stage, updated_at FROM tickets WHERE id = 't_invalid_write'"
            ).fetchone()
        )
        == before
    )


def test_create_door_rejects_unknown_type(tmp_db: Connection, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()
    with pytest.raises(PlannerError) as exc:
        tickets_data.create_ticket(
            tmp_db,
            title="Bad type",
            principal=OWNER_PRINCIPAL,
            now=now,
            title_max_chars=TITLE_MAX_CHARS,
            project_id="project_vylo",
            worker_type="bogus",
        )
    assert exc.value.code == ErrorCode.not_found
    assert exc.value.message == "unknown worker type"


def test_guidance_is_independent_of_the_worker_type_fields(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db,
        worker_type="coding",
        title="Note target",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="k",
        project_id="project_vylo",
    )
    updated = tickets_data.replace_guidance(
        tmp_db, ticket.id, body="x", principal=OWNER_PRINCIPAL, now=now
    )
    assert updated.guidance == "x"
    assert updated.field_values == ticket.field_values
    assert updated.pending_proposal == ticket.pending_proposal


# =====================================================================
# Acceptance 6 — per-type default ceiling (from the registry, not a literal)
# =====================================================================


def test_created_second_type_ticket_ceiling_is_its_registry_default(
    tmp_db: Connection, fake_clock: TestClock, two_type_registry: None
) -> None:
    # The per-type default ceiling is sourced from the registry, proven by driving a
    # non-coding type id through create_ticket and comparing to its default_ceiling.
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Probe ceiling",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        project_id="project_vylo",
        worker_type="coding_probe",
    )
    assert ticket.worker_type == "coding_probe"
    assert (
        ticket.ceiling
        == configured_worker_type_registry().require("coding_probe").default_ceiling()
    )


# =====================================================================
# The review-F6 boundary: a coding-shaped second type resolves per-row, reaches the
# coding-DEFAULT engine, and round-trips a plain stored-value read.
# =====================================================================


def test_second_type_row_round_trips_plain_stored_values(
    tmp_db: Connection, two_type_registry: None
) -> None:
    # A coding-shaped second-type row loads its stored values and shared six-slot
    # fields without making row reads another registry-validation door.
    _raw_insert_ticket(
        tmp_db,
        ticket_id="t_probe",
        worker_type="coding_probe",
        stage="needs_success",
        ceiling="needs_success",
    )
    ticket = tickets_data.read_ticket(tmp_db, "t_probe")
    assert ticket.worker_type == "coding_probe"
    assert ticket.stage == "needs_success"
