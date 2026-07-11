"""t_tt02 — the persistence-layer registry validation doors, the startup integrity
audit, per-type default ceiling, and the injectable-registry seam.

These tests assert acceptance items 4 (startup audit), 5 (validation doors), 6
(per-type default ceiling), and demonstrate the review-F6 boundary via a
coding-SHAPED second type installed through ``coding_bridge.set_registry_for_test``.

The second type reuses coding's exact stages and fields (so the fixed six-slot
codec accepts it and its state/ceiling ids round-trip ``TicketState``) with a
different ``type_id`` — this proves per-row type resolution, the unknown-type door,
and per-type ``default_ceiling`` sourcing, and demonstrates that a coding-shaped
non-coding row reaches the coding-DEFAULT engine paths (the binding reason no
second PRODUCTION type may be registered until t_tt02b threads the definition
through resolution/external-work)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core.clock import TestClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.ticket_types.coding import CODING_DEFINITION
from planner.ticket_types.contracts import WorkflowDefinition
from planner.ticket_types.registry import build_registry
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    FieldName,
    TicketState,
)
from planner.tickets.logic import coding_bridge

# A coding-SHAPED second type: coding's exact stages/fields (so the codec accepts it
# and its state/ceiling ids are TicketState members), different type_id.
CODING_PROBE_DEFINITION: WorkflowDefinition = WorkflowDefinition(
    type_id="coding_probe",
    label="Coding Probe",
    stages=CODING_DEFINITION.stages,
    dropped_stage=CODING_DEFINITION.dropped_stage,
    fields=CODING_DEFINITION.fields,
    worker_profile=CODING_DEFINITION.worker_profile,
    transition_hooks=(),
    supports_prefix_reconciliation=CODING_DEFINITION.supports_prefix_reconciliation,
)


def _two_type_registry():
    return build_registry(
        [CODING_DEFINITION, CODING_PROBE_DEFINITION],
        known_skills=frozenset({"panels-worker"}),
        known_toolset_profiles=frozenset({"default"}),
    )


@pytest.fixture
def two_type_registry():
    """Install a registry carrying coding + a coding-shaped second type for the
    persistence doors, then restore production coding-only after the test."""
    coding_bridge.set_registry_for_test(_two_type_registry())
    try:
        yield
    finally:
        coding_bridge.set_registry_for_test(None)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Connection:
    conn = connect(str(tmp_path / "type-doors.db"))
    create_schema(conn)
    return conn


@pytest.fixture
def fake_clock() -> TestClock:
    return TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())


_SIX_SLOT_FIELDS = json.dumps(
    {
        "kickoff": {"value": "k", "proposal": None, "user_note": None},
        "success": {"value": "s", "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
)


def _raw_insert_ticket(
    conn: Connection,
    *,
    ticket_id: str,
    ticket_type: str = "coding",
    state: str = "needs_success",
    ceiling: str = "needs_success",
    fields: str = _SIX_SLOT_FIELDS,
) -> None:
    # Direct SQL bypasses the create/load doors (the enumerating CHECKs are gone), so
    # a deliberately-corrupt row can be planted for the audit / load-door tests.
    conn.execute(
        "INSERT INTO tickets (id, title, ticket_type, state, ceiling, fields, "
        "created_at, updated_at) VALUES (?, 'T', ?, ?, ?, ?, 1, 1)",
        (ticket_id, ticket_type, state, ceiling, fields),
    )


# =====================================================================
# Acceptance 4 — startup integrity audit
# =====================================================================


def test_audit_passes_clean_migrated_table(tmp_db: Connection) -> None:
    _raw_insert_ticket(tmp_db, ticket_id="t_ok")
    tickets_data.audit_ticket_registry_integrity(tmp_db)  # no raise


def test_audit_rejects_unknown_ticket_type(tmp_db: Connection) -> None:
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", ticket_type="bogus")
    with pytest.raises(RuntimeError, match="ticket integrity audit failed: id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "unknown ticket type" in str(exc.value)
    assert "bogus" in str(exc.value)


def test_audit_rejects_state_outside_the_types_stages(tmp_db: Connection) -> None:
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", state="needs_alpha")
    with pytest.raises(RuntimeError, match="id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "state outside the linear order" in str(exc.value)
    assert "needs_alpha" in str(exc.value)


def test_audit_rejects_ceiling_outside_the_types_range(tmp_db: Connection) -> None:
    # needs_kickoff is excluded from the ceiling range (leading bookend).
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", ceiling="needs_kickoff")
    with pytest.raises(RuntimeError, match="id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "ceiling outside the type's range" in str(exc.value)
    assert "needs_kickoff" in str(exc.value)


def test_audit_rejects_missing_declared_field(tmp_db: Connection) -> None:
    payload = json.loads(_SIX_SLOT_FIELDS)
    del payload["closeout"]                       # a declared field missing
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", fields=json.dumps(payload))
    with pytest.raises(RuntimeError, match="id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "corrupt ticket fields JSON" in str(exc.value)


def test_audit_rejects_malformed_slot(tmp_db: Connection) -> None:
    payload = json.loads(_SIX_SLOT_FIELDS)
    payload["success"] = {"value": 123, "proposal": None, "user_note": None}  # non-str value
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", fields=json.dumps(payload))
    with pytest.raises(RuntimeError, match="id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "corrupt ticket fields JSON" in str(exc.value)


def test_audit_rejects_syntactically_invalid_fields_json(tmp_db: Connection) -> None:
    # A row whose fields is not valid JSON (the codec's json.loads raises
    # JSONDecodeError, not PlannerError) must still fail the audit with its id named,
    # not abort startup with a raw decode traceback.
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", fields="{")
    with pytest.raises(RuntimeError, match="id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "fields JSON is not valid JSON" in str(exc.value)


def test_audit_lenient_on_extra_top_level_fields_key(tmp_db: Connection) -> None:
    # A legacy 'result' key alongside a valid six-slot set must NOT fail the audit
    # (the codec ignores extra top-level keys — pins the intentional leniency).
    payload = json.loads(_SIX_SLOT_FIELDS)
    payload["result"] = {"value": "legacy", "proposal": None, "user_note": None}
    _raw_insert_ticket(tmp_db, ticket_id="t_extra", fields=json.dumps(payload))
    tickets_data.audit_ticket_registry_integrity(tmp_db)  # no raise


def test_audit_stops_at_first_corrupt_id_in_order(tmp_db: Connection) -> None:
    _raw_insert_ticket(tmp_db, ticket_id="t_002", ticket_type="bogus")
    _raw_insert_ticket(tmp_db, ticket_id="t_001", state="needs_alpha")
    # ORDER BY id => t_001 is scanned first, so its reason is the one reported.
    with pytest.raises(RuntimeError, match="id=t_001") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "state outside the linear order" in str(exc.value)


# =====================================================================
# Acceptance 5 — validation doors
# =====================================================================


def test_load_door_rejects_bad_state(tmp_db: Connection) -> None:
    _raw_insert_ticket(tmp_db, ticket_id="t_load", state="needs_alpha")
    with pytest.raises(PlannerError) as exc:
        tickets_data.read_ticket(tmp_db, "t_load")
    assert exc.value.code == ErrorCode.validation
    assert exc.value.message == "state outside the linear order"


def test_load_door_rejects_bad_ceiling(tmp_db: Connection) -> None:
    _raw_insert_ticket(tmp_db, ticket_id="t_load", ceiling="needs_kickoff")
    with pytest.raises(PlannerError) as exc:
        tickets_data.read_ticket(tmp_db, "t_load")
    assert exc.value.code == ErrorCode.scope_invalid


def test_persist_door_rejects_out_of_range_ceiling(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    # The pre-persist door in _apply_decision validates the prospective (state,
    # ceiling) before any SQL. A Decision carrying needs_kickoff as a ceiling (out of
    # range) must raise before the UPDATE — assert no mutation via a post-error read.
    from planner.tickets.logic.decisions import Decision

    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db, title="Persist door", actor="human", now=now,
        title_max_chars=TITLE_MAX_CHARS, project_id="project_vylo",
    )
    before = tickets_data.read_ticket(tmp_db, ticket.id)
    with pytest.raises(PlannerError) as exc:
        tickets_data._apply_decision(
            tmp_db,
            before,
            Decision(events=(), new_ceiling=TicketState.needs_kickoff),
            now,
        )
    assert exc.value.code == ErrorCode.scope_invalid
    after = tickets_data.read_ticket(tmp_db, ticket.id)
    assert after.ceiling == before.ceiling


def test_create_door_rejects_unknown_type(tmp_db: Connection, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()
    with pytest.raises(PlannerError) as exc:
        tickets_data.create_ticket(
            tmp_db, title="Bad type", actor="human", now=now,
            title_max_chars=TITLE_MAX_CHARS, project_id="project_vylo",
            ticket_type="bogus",
        )
    assert exc.value.code == ErrorCode.not_found
    assert exc.value.message == "unknown ticket type"


def test_external_work_create_door_rejects_unknown_type(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    with pytest.raises(PlannerError) as exc:
        tickets_data.create_ticket_from_external_work(
            tmp_db, title="Bad type", target_state=TicketState.needs_success,
            provided_values={FieldName.success: "s"}, actor="human", now=now,
            title_max_chars=TITLE_MAX_CHARS, kickoff_note="k", project_id="project_vylo",
            ticket_type="bogus",
        )
    assert exc.value.code == ErrorCode.not_found


def test_note_door_rejects_undeclared_field(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket_from_external_work(
        tmp_db, title="Note target", target_state=TicketState.needs_approach,
        provided_values={FieldName.success: "success value"}, actor="human", now=now,
        title_max_chars=TITLE_MAX_CHARS, kickoff_note="k", project_id="project_vylo",
    )
    with pytest.raises(PlannerError) as exc:
        tickets_data.set_field_user_note(
            tmp_db, ticket.id, field="not_a_field",  # type: ignore[arg-type]
            user_note="x", actor="human", now=now,
        )
    assert exc.value.code == ErrorCode.validation
    assert exc.value.message == "unknown ticket field"


# =====================================================================
# Acceptance 6 — per-type default ceiling (from the registry, not a literal)
# =====================================================================


def test_created_coding_ticket_ceiling_is_registry_default(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db, title="Ceiling", actor="human", now=now,
        title_max_chars=TITLE_MAX_CHARS, project_id="project_vylo",
    )
    assert ticket.ceiling.value == coding_bridge.default_ceiling("coding")
    assert ticket.ceiling.value == "needs_success"


def test_created_second_type_ticket_ceiling_is_its_registry_default(
    tmp_db: Connection, fake_clock: TestClock, two_type_registry: None
) -> None:
    # The per-type default ceiling is sourced from the registry, proven by driving a
    # non-coding type id through create_ticket and comparing to its default_ceiling.
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db, title="Probe ceiling", actor="human", now=now,
        title_max_chars=TITLE_MAX_CHARS, project_id="project_vylo",
        ticket_type="coding_probe",
    )
    assert ticket.ticket_type == "coding_probe"
    assert ticket.ceiling.value == coding_bridge.default_ceiling("coding_probe")


# =====================================================================
# The review-F6 boundary: a coding-shaped second type resolves per-row, reaches the
# coding-DEFAULT engine, and round-trips the load door.
# =====================================================================


def test_second_type_row_resolves_and_round_trips_the_load_door(
    tmp_db: Connection, two_type_registry: None
) -> None:
    # A coding-shaped second-type row passes the load door (type resolved from the
    # row, state/ceiling valid for it, fields decode via the shared six-slot codec)
    # and carries its type on the domain object.
    _raw_insert_ticket(
        tmp_db, ticket_id="t_probe", ticket_type="coding_probe",
        state="needs_success", ceiling="needs_success",
    )
    ticket = tickets_data.read_ticket(tmp_db, "t_probe")
    assert ticket.ticket_type == "coding_probe"
    assert ticket.state is TicketState.needs_success


def test_second_type_reaches_coding_default_engine_paths(
    tmp_db: Connection, fake_clock: TestClock, two_type_registry: None
) -> None:
    # review-F6 demonstration: a coding-shaped non-coding row flows through the
    # coding-DEFAULT resolution engine (decide_*) — it advances by coding semantics,
    # NOT a second definition threaded through resolution. This is exactly why no
    # second PRODUCTION definition may be registered until t_tt02b threads the
    # definition through resolution/external-work.
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db, title="Probe engine", actor="human", now=now,
        title_max_chars=TITLE_MAX_CHARS, project_id="project_vylo",
        ticket_type="coding_probe",
    )
    # Kickoff acceptance advances by the coding-default engine (needs_kickoff ->
    # needs_success), proving the coding-default path is taken for a non-coding type.
    accepted = tickets_data.accept_proposal(
        tmp_db, ticket.id, field=FieldName.kickoff, actor="human", now=now,
        next_ceiling=NO_FURTHER, at_cap=AtCap.propose,
    )
    assert accepted.state is TicketState.needs_success
