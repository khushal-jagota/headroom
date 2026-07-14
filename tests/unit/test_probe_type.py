"""t_tt02x — the canonical ``probe`` type contract + a compact drive-to-done.

``probe`` is the synthetic SECOND Worker type (``tests/support/probe.py``),
registered TEST-ONLY, that proves the persistence + engine stack is genuinely
N-ary and not ``coding`` in disguise. The exhaustive DATA-layer drive with EXACT
per-transition event assertions lives in ``test_generic_field_storage.py`` (t_tt02b,
T5); this file asserts probe's *contract* and drives the canonical fixture once,
compactly, to prove the shared fixture reaches ``done``.

Every assertion is a concrete value:
- the serialized manifest is asserted as the FULL dict (mirroring the coding
  manifest test's exactness), plus a golden gate map, field order, and default
  ceiling;
- production ``coding_registry()`` is asserted coding-only (probe is test-only);
- the compact drive asserts the exact final state / accepted values, and that NO
  worker session (chat_session_key / chat_turns) was created;
- the negative cases assert the COMPLETE PlannerError (exact code + message + full
  detail dict).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.probe import (
    FIELD_ALPHA,
    FIELD_BETA,
    NEEDS_ALPHA,
    NEEDS_BETA,
    PROBE_DEFINITION,
    build_probe_registry,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.core.clock import TestClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.ticket_types.contracts import WorkflowDefinition
from planner.ticket_types.logic import validation, views
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    FieldName,
)
from planner.tickets.logic import coding_bridge, fields_codec

# =====================================================================
# fixtures
# =====================================================================


@pytest.fixture
def probe_registry() -> Iterator[WorkflowDefinition]:
    """Install the canonical coding + probe registry for the persistence/engine
    doors, restoring production coding-only afterward."""
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Connection]:
    conn = connect(str(tmp_path / "probe-type.db"))
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def fake_clock() -> TestClock:
    return TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())


# =====================================================================
# Acceptance 1 — the registry validates probe; its contract asserted exactly
# =====================================================================


def test_registry_validates_probe() -> None:
    # The full validator accepts the probe definition (no raise), through the same
    # door the production registry uses at build.
    validation.validate_definition(
        PROBE_DEFINITION,
        known_skills=frozenset({"panels-worker", "probe-worker"}),
        known_toolset_profiles=frozenset({"default"}),
    )
    # And build succeeds: the coding + probe registry offers both.
    assert build_probe_registry().type_ids() == ("coding", "probe")


# The exact serialized manifest — the full dict (mirrors the coding manifest test).
PROBE_MANIFEST = {
    "worker_type": "probe",
    "label": "Probe",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
        },
        {"id": "needs_alpha", "label": "Alpha", "gating_field": "alpha", "is_terminal": False},
        {"id": "needs_beta", "label": "Beta", "gating_field": "beta", "is_terminal": False},
        {"id": "done", "label": "Done", "gating_field": None, "is_terminal": True},
    ],
    "dropped": {"id": "dropped", "label": "Dropped", "gating_field": None, "is_terminal": True},
    "advance": {
        "needs_kickoff": "needs_alpha",
        "needs_alpha": "needs_beta",
        "needs_beta": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "alpha", "label": "Alpha"},
        {"id": "beta", "label": "Beta"},
    ],
    "ceiling_range": ["needs_kickoff", "needs_alpha", "needs_beta", "done"],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "probe-worker",
}


def test_probe_manifest_exact() -> None:
    assert build_probe_registry().manifest("probe") == PROBE_MANIFEST


def test_probe_manifest_json_roundtrips() -> None:
    assert json.loads(json.dumps(build_probe_registry().manifest("probe"))) == PROBE_MANIFEST


def test_probe_gate_map_is_golden() -> None:
    assert build_probe_registry().gate_map("probe") == {
        "needs_kickoff": "kickoff",
        "needs_alpha": "alpha",
        "needs_beta": "beta",
    }


def test_probe_field_order_is_golden() -> None:
    assert build_probe_registry().field_ids("probe") == ("kickoff", "alpha", "beta")


def test_probe_default_ceiling_is_needs_kickoff() -> None:
    # Default ceiling is the leading needs_kickoff for every type. Probe's per-type
    # FIRST WORKER stage is needs_alpha (NOT coding's needs_success) — the distinct
    # concept that still proves per-type derivation.
    assert build_probe_registry().default_ceiling("probe") == "needs_kickoff"
    assert build_probe_registry().ceiling_range("probe")[0] == "needs_kickoff"
    assert views.first_worker_stage(PROBE_DEFINITION) == "needs_alpha"


def test_probe_supports_prefix_reconciliation() -> None:
    # True (consumed by t_tt03 external-work); it is NOT serialized into the manifest.
    assert build_probe_registry().require("probe").supports_prefix_reconciliation is True
    assert "supports_prefix_reconciliation" not in build_probe_registry().manifest("probe")


# =====================================================================
# Acceptance 1 (cont.) — production stays coding-only; probe is test-only
# =====================================================================


def test_production_registry_is_coding_and_new_worker() -> None:
    # No test registry installed: the production singleton offers the two shipped types.
    # probe stays test-only.
    assert coding_bridge.coding_registry().type_ids() == ("coding", "new_worker")


def test_installed_probe_does_not_leak_into_production_singleton() -> None:
    # Installing the test registry supersedes require()/default_ceiling(), but the
    # production ``coding_registry()`` (and thus ``coding_definition()``) is untouched —
    # it still offers exactly the shipped types (coding + new_worker), never probe.
    install_probe_registry()
    try:
        assert coding_bridge.require("probe").type_id == "probe"
        assert coding_bridge.coding_registry().type_ids() == ("coding", "new_worker")
        assert coding_bridge.coding_definition().type_id == "coding"
    finally:
        uninstall_probe_registry()
    # After teardown, require("probe") is unknown again.
    with pytest.raises(PlannerError) as exc:
        coding_bridge.require("probe")
    assert exc.value.code == ErrorCode.not_found


# =====================================================================
# Acceptance 2 — a compact drive-to-done through the real data.* writers
# (the exhaustive per-event drive is t_tt02b T5; this proves the canonical
# fixture reaches done and creates no worker session)
# =====================================================================


def _worker_session_exists(conn: Connection, tid: str) -> bool:
    """A worker session shows up as a durable chat_session_key on the ticket OR any
    chat_turns row for it. The DATA-layer drive must create neither."""
    row = conn.execute("SELECT chat_session_key FROM tickets WHERE id = ?", (tid,)).fetchone()
    if row["chat_session_key"] is not None:
        return True
    turns = conn.execute("SELECT 1 FROM chat_turns WHERE entity_id = ? LIMIT 1", (tid,)).fetchone()
    return turns is not None


def test_probe_drives_to_done_via_real_writers(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkflowDefinition
) -> None:
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Probe",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="probe",
    )
    tid = ticket.id

    # Created at needs_kickoff scoped to the leading needs_kickoff ceiling, kickoff parked.
    assert ticket.stage == "needs_kickoff"
    assert ticket.ceiling == "needs_kickoff"
    assert fields_codec.get_slot(ticket.fields, "kickoff").proposal is not None

    # accept kickoff, expanding the ceiling onward to needs_beta -> advances to needs_alpha.
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field=FieldName.kickoff,
        actor="human",
        now=now,
        next_ceiling=NEEDS_BETA,
        at_cap=AtCap.propose,
    )
    assert t.stage == NEEDS_ALPHA
    assert t.ceiling == NEEDS_BETA

    # propose alpha: ceiling (needs_beta) is BEYOND state (needs_alpha) -> AUTO-ACCEPT + advance.
    t = tickets_data.file_proposal(
        tmp_db, tid, field=FIELD_ALPHA, body="alpha body", actor="agent", now=now
    )
    assert t.stage == NEEDS_BETA
    assert fields_codec.get_slot(t.fields, FIELD_ALPHA).value == "alpha body"
    assert fields_codec.get_slot(t.fields, FIELD_ALPHA).proposal is None

    # at needs_beta (ceiling ==): propose beta -> parks; then accept -> done.
    tickets_data.file_proposal(
        tmp_db, tid, field=FIELD_BETA, body="beta body", actor="agent", now=now
    )
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field=FIELD_BETA,
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.stop,
    )

    # Exact final state: done, both worker fields settled to their accepted values.
    assert t.stage == "done"
    assert fields_codec.get_slot(t.fields, FIELD_ALPHA).value == "alpha body"
    assert fields_codec.get_slot(t.fields, FIELD_BETA).value == "beta body"
    assert fields_codec.get_slot(t.fields, FIELD_BETA).proposal is None

    # No worker turn / session was created by the DATA-layer drive.
    assert _worker_session_exists(tmp_db, tid) is False


def test_probe_drives_to_dropped(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkflowDefinition
) -> None:
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Probe",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="probe",
    )
    # drop is the universal reserved bookend; it terminates a probe ticket at dropped.
    t = tickets_data.drop_ticket(tmp_db, ticket.id, actor="human", now=now)
    assert t.stage == "dropped"
    assert _worker_session_exists(tmp_db, ticket.id) is False


# =====================================================================
# Acceptance 4 — negative cases: exact PlannerError codes
# =====================================================================


def test_create_with_unknown_type_raises_not_found(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkflowDefinition
) -> None:
    # The creation door resolves the type through the registry; an unknown id raises
    # the exact not_found payload (even with the probe registry installed).
    now = fake_clock.now_unix()
    with pytest.raises(PlannerError) as exc:
        tickets_data.create_ticket(
            tmp_db,
            title="Nope",
            actor="human",
            now=now,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="ghost",
        )
    assert exc.value.code == ErrorCode.not_found
    assert exc.value.message == "unknown worker type"
    assert exc.value.detail == {"worker_type": "ghost"}


def test_probe_invalid_stage_rejected(probe_registry: WorkflowDefinition) -> None:
    # A Stage that is neither a probe linear stage nor the reserved dropped: the shared
    # load/persist validator raises "stage outside the linear order".
    from planner.tickets.logic import ticket_type_guard

    with pytest.raises(PlannerError) as exc:
        ticket_type_guard.resolve_and_validate("probe", stage="needs_ghost", ceiling=NEEDS_ALPHA)
    assert exc.value.code == ErrorCode.validation
    assert exc.value.message == "stage outside the linear order"
    assert exc.value.detail == {"stage": "needs_ghost"}


def test_probe_invalid_ceiling_rejected(probe_registry: WorkflowDefinition) -> None:
    # needs_ghost is not a probe stage at all, so it is not in probe's ceiling range
    # (needs_kickoff, needs_alpha, needs_beta, done): the validator raises scope_invalid.
    # (needs_kickoff is now a VALID ceiling — the leading default — so it can no longer
    # serve as the out-of-range example.)
    from planner.tickets.logic import ticket_type_guard

    with pytest.raises(PlannerError) as exc:
        ticket_type_guard.resolve_and_validate("probe", stage=NEEDS_ALPHA, ceiling="needs_ghost")
    assert exc.value.code == ErrorCode.scope_invalid
    assert exc.value.message == "ceiling outside the type's range"
    assert exc.value.detail == {"worker_type": "probe", "ceiling": "needs_ghost"}


def test_probe_invalid_field_rejected(probe_registry: WorkflowDefinition) -> None:
    # The field door (codec) rejects a slot access for a field not declared by probe,
    # with the exact validation payload — proving probe's OWN field set is the boundary.
    fields = fields_codec.fields_from_json(
        json.dumps(
            {
                "kickoff": {"value": None, "proposal": None, "user_note": None},
                FIELD_ALPHA: {"value": None, "proposal": None, "user_note": None},
                FIELD_BETA: {"value": None, "proposal": None, "user_note": None},
            }
        ),
        probe_registry,
    )
    with pytest.raises(PlannerError) as exc:
        fields_codec.get_slot(fields, "success")
    assert exc.value.code == ErrorCode.validation
    assert exc.value.detail == {"field": "success"}
