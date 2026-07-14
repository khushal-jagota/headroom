"""t_tt01 — the engine parameterization proof.

These tests are additive; they assert the four acceptance items the BRIEF names:
the engine reads no lifecycle constant for its decisions (A), the codec is
definition-validated yet lenient on unknown extras (B), the linear-order lookups
are string-id-native and two definitions flow through the engine at once (C), the
note write-path validates the field and stays a non-writer of canonical position
(E), and canonical stage/ceiling/at_cap flow only through _apply_decision (D).

The synthetic definition uses stage/field ids that are NOT CodingStage/FieldName
members, built with "".join(...) so CPython interning cannot mask a surviving
identity comparison. The typing cases (F) are machine-checked by the repo's strict
mypy run over this file (reported separately by the implementer's gate).
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core.clock import TestClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.ticket_types.contracts import (
    FieldDef,
    Stage,
    WorkerProfile,
    WorkflowDefinition,
)
from planner.ticket_types.logic import views
from planner.ticket_types.registry import build_registry
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    TITLE_MAX_CHARS,
    AtCap,
    CodingStage,
    FieldName,
    FieldSlot,
    Proposal,
    TicketFields,
)
from planner.tickets.logic import coding_bridge, fields_codec, machine

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
_MACHINE_PY = _SRC_ROOT / "planner" / "tickets" / "logic" / "machine.py"
_DATA_PY = _SRC_ROOT / "planner" / "tickets" / "data.py"


# --- the synthetic, non-enum definition ----------------------------------------
# "".join((...)) builds a genuinely distinct runtime str object; an adjacent-literal
# "+" would be constant-folded and interned, which would NOT defeat interning.
_A = "".join(("needs_", "alpha"))
_B = "".join(("needs_", "beta"))
_FA = "".join(("al", "pha"))
_FB = "".join(("be", "ta"))

SYNTHETIC = WorkflowDefinition(
    type_id="probe",
    label="Probe",
    stages=(
        Stage(id="needs_kickoff", label="Kickoff", gating_field="kickoff", is_terminal=False),
        Stage(id=_A, label="Alpha", gating_field=_FA, is_terminal=False),
        Stage(id=_B, label="Beta", gating_field=_FB, is_terminal=False),
        Stage(id="done", label="Done", gating_field=None, is_terminal=True),
    ),
    dropped_stage=Stage(id="dropped", label="Dropped", gating_field=None, is_terminal=True),
    fields=(
        FieldDef(id="kickoff", label="Kickoff"),
        FieldDef(id=_FA, label="Alpha"),
        FieldDef(id=_FB, label="Beta"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="probe-worker",
        model=None,
        reasoning_effort=None,
        toolset_profile="default",
    ),
    transition_hooks=(),
    supports_prefix_reconciliation=False,
)


def _build_synthetic() -> None:
    # Validates the synthetic definition (never through the production bridge).
    build_registry(
        [SYNTHETIC],
        known_skills=frozenset({"probe-worker"}),
        known_toolset_profiles=frozenset({"default"}),
    )


# =====================================================================
# A. No lifecycle-constant reads in machine.py
# =====================================================================


def _machine_import_names() -> set[str]:
    tree = ast.parse(_MACHINE_PY.read_text(), filename=str(_MACHINE_PY))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


def test_machine_imports_no_lifecycle_order_constants() -> None:
    imported = _machine_import_names()
    for forbidden in (
        "CODING_STAGE_ORDER",
        "CODING_EMPLOYEE_STAGE_ORDER",
        "CODING_GATING_FIELD_BY_STAGE",
        "CODING_NEXT_STAGE_BY_STAGE",
    ):
        assert forbidden not in imported, f"machine.py must not import {forbidden}"
    assert "coding_bridge" in imported


def test_machine_reads_no_module_constant_for_decisions() -> None:
    # FIELD_GATES stays a module-level spec constant (t_tt00 golden test imports it),
    # but no function body may READ it. Assert it is defined exactly once (the
    # assignment) and referenced nowhere else in machine.py.
    tree = ast.parse(_MACHINE_PY.read_text(), filename=str(_MACHINE_PY))
    loads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "FIELD_GATES"
        and isinstance(node.ctx, ast.Load)
    ]
    assert loads == [], "machine.py functions must not read FIELD_GATES"


# =====================================================================
# B. Codec — definition-validated decode, LENIENT on unknown extras
# =====================================================================


def _six_slot_payload() -> dict[str, object]:
    return {
        "kickoff": {"value": "k", "proposal": None, "user_note": None},
        "success": {"value": "s", "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }


def test_codec_lenient_on_unknown_extra_top_level_keys() -> None:
    payload = _six_slot_payload()
    payload["result"] = "legacy value"  # the live-data legacy key
    payload["audit"] = {"who": "someone"}
    parsed = fields_codec.fields_from_json(json.dumps(payload), coding_bridge.coding_definition())
    assert fields_codec.get_slot(parsed, "kickoff").value == "k"
    assert fields_codec.get_slot(parsed, "success").value == "s"
    assert fields_codec.get_slot(parsed, "approach").value is None


def test_codec_requires_each_declared_field() -> None:
    payload = _six_slot_payload()
    del payload["closeout"]  # a declared field missing
    with pytest.raises(PlannerError) as exc:
        fields_codec.fields_from_json(json.dumps(payload), coding_bridge.coding_definition())
    assert exc.value.code == ErrorCode.validation


def test_codec_round_trips_declared_fields() -> None:
    fields = TicketFields.empty(coding_bridge.field_ids(coding_bridge.coding_definition()))
    fields = fields_codec.with_slot(fields, "kickoff", FieldSlot(value="k", user_note="note"))
    fields = fields_codec.with_slot(
        fields,
        "success",
        FieldSlot(value="s", proposal=Proposal(body="p", proposed_by="agent", created_at=7)),
    )
    raw = fields_codec.fields_to_json(fields)
    parsed = fields_codec.fields_from_json(raw, coding_bridge.coding_definition())
    assert fields_codec.get_slot(parsed, "kickoff").value == "k"
    assert fields_codec.get_slot(parsed, "kickoff").user_note == "note"
    assert fields_codec.get_slot(parsed, "success").proposal is not None
    assert fields_codec.get_slot(parsed, "success").proposal.body == "p"


def test_codec_without_definition_decodes_the_stored_field_map() -> None:
    fields = fields_codec.with_slot(
        TicketFields.empty(coding_bridge.field_ids(coding_bridge.coding_definition())),
        "kickoff",
        FieldSlot(value="k"),
    )
    payload = json.loads(fields_codec.fields_to_json(fields))
    payload["stored_extra"] = {
        "value": "preserved",
        "proposal": None,
        "user_note": None,
    }
    raw = json.dumps(payload)
    one_arg = fields_codec.fields_from_json(raw)
    explicit = fields_codec.fields_from_json(raw, coding_bridge.coding_definition())
    assert tuple(one_arg.slots) == (*tuple(explicit.slots), "stored_extra")
    assert fields_codec.get_slot(one_arg, "stored_extra").value == "preserved"
    assert "stored_extra" not in explicit.slots


def test_codec_decodes_each_declared_value_not_only_presence() -> None:
    # A malformed declared slot value must be rejected by _slot_from_obj, not skipped.
    payload = _six_slot_payload()
    payload["approach"] = 7  # not a slot dict
    with pytest.raises(PlannerError) as exc:
        fields_codec.fields_from_json(json.dumps(payload), coding_bridge.coding_definition())
    assert exc.value.code == ErrorCode.validation


def test_codec_decodes_non_coding_field_set() -> None:
    # t_tt02b lifts the coding-bound storage boundary: a registered non-coding field
    # set (kickoff/_FA/_FB) now DECODES into its own slots against the definition's
    # declared field ids, rather than raising a coding-bound rejection.
    _build_synthetic()
    payload = {
        "kickoff": {"value": "k", "proposal": None, "user_note": None},
        _FA: {"value": "a", "proposal": None, "user_note": None},
        _FB: {"value": "b", "proposal": None, "user_note": None},
    }
    parsed = fields_codec.fields_from_json(json.dumps(payload), SYNTHETIC)
    assert fields_codec.get_slot(parsed, "kickoff").value == "k"
    assert fields_codec.get_slot(parsed, _FA).value == "a"
    assert fields_codec.get_slot(parsed, _FB).value == "b"


# =====================================================================
# C. String-id capable (not enum-bound) + two definitions at once
# =====================================================================


def test_views_operate_on_non_enum_synthetic_ids() -> None:
    _build_synthetic()
    assert views.stage_index(SYNTHETIC, _A) == 1
    assert views.advance_target(SYNTHETIC, _A) == _B
    assert views.advance_target(SYNTHETIC, _B) == "done"
    assert views.gating_field(SYNTHETIC, _A) == _FA
    assert views.is_terminal(SYNTHETIC, "done") is True
    assert views.is_terminal(SYNTHETIC, _A) is False
    assert views.ceiling_range(SYNTHETIC) == ("needs_kickoff", _A, _B, "done")
    assert views.default_ceiling(SYNTHETIC) == "needs_kickoff"
    assert views.first_worker_stage(SYNTHETIC) == _A


def test_machine_advance_target_returns_bare_string_for_foreign_id() -> None:
    _build_synthetic()
    result = machine.advance_target(_A, definition=SYNTHETIC)
    # _as_state("needs_beta") raises CodingStage(...), so a bare str comes back.
    assert type(result) is str
    assert result == _B
    assert result != CodingStage.done


def test_machine_gating_field_returns_bare_string_for_foreign_id() -> None:
    _build_synthetic()
    result = machine.gating_field(_A, definition=SYNTHETIC)
    assert type(result) is str
    assert result == _FA


def test_machine_tier1_lookups_on_synthetic_ids() -> None:
    _build_synthetic()
    assert machine.is_terminal(_A, definition=SYNTHETIC) is False
    assert machine.is_terminal("done", definition=SYNTHETIC) is True
    assert machine.stage_index(_A, definition=SYNTHETIC) == 1
    assert machine.at_or_beyond_ceiling(_A, _A, definition=SYNTHETIC) is True


def test_auto_accept_target_survives_interning_on_synthetic_field() -> None:
    # The surviving-`is` regression guard for machine.py's `field != gating_field(...)`.
    # gating_field returns the definition's STORED _FA object; the call-site arg must
    # be an equal-but-DISTINCT object so a restored `field is not gating_field(...)`
    # would wrongly return None and fail this test. Same for the stage arg.
    _build_synthetic()
    call_state = "".join(("needs_", "alpha"))
    call_field = "".join(("al", "pha"))
    assert call_state == _A and call_state is not _A
    assert call_field == _FA and call_field is not _FA
    result = machine.auto_accept_target(call_state, _B, call_field, definition=SYNTHETIC)
    assert result == _B


def test_admission_accepts_foreign_gate_generically() -> None:
    # t_tt02b lifts the coding-bound gate boundary: admission for a foreign definition
    # whose gate is a non-FieldName id (_FA) now resolves gating_field(_A)=_FA and
    # validates against the definition's own gate, not the coding six. At its ceiling
    # with at_cap=propose, proposing the current gating field (_FA) is admitted (no
    # raise) — proving the generic path handles a bare-str gate without AttributeError.
    from planner.tickets.logic import admission

    _build_synthetic()
    admission.check_agent_proposal(_A, _A, AtCap.propose, _FA, definition=SYNTHETIC)

    # A non-gating field at the ceiling is still rejected — but against the type's own
    # gate (_FA), not coding's, and with a clean PlannerError, not an AttributeError.
    with pytest.raises(PlannerError) as exc:
        admission.check_agent_proposal(_A, _A, AtCap.propose, _FB, definition=SYNTHETIC)
    assert exc.value.code == ErrorCode.validation
    assert exc.value.detail == {"field": _FB, "gating_field": _FA, "stage": _A}


def test_has_pending_gating_proposal_reads_foreign_gate_generically() -> None:
    # t_tt02b: the machine reads the type's OWN gate slot generically. For SYNTHETIC at
    # stage _A the gate is _FA; a proposal parked on the _FA slot is seen as pending,
    # never silently mis-routed to a coding slot. A bare-str gate flows without error.
    _build_synthetic()
    fields = fields_codec.with_slot(
        TicketFields.empty(("kickoff", _FA, _FB)),
        _FA,
        FieldSlot(proposal=Proposal(body="p", proposed_by="agent", created_at=1)),
    )
    assert machine.has_pending_gating_proposal(_A, fields, definition=SYNTHETIC) is True

    empty = TicketFields.empty(("kickoff", _FA, _FB))
    assert machine.has_pending_gating_proposal(_A, empty, definition=SYNTHETIC) is False


def test_two_definitions_flow_through_engine_at_once() -> None:
    # The F1 concurrency guarantee: SYNTHETIC (explicit) and coding (default) both
    # resolve correctly in one test, without any monkeypatch — proving the engine is
    # genuinely N-ary, not a single global definition.
    _build_synthetic()
    assert machine.advance_target(_A, definition=SYNTHETIC) == _B
    assert machine.advance_target(CodingStage.needs_success) is CodingStage.needs_approach
    assert machine.advance_target(_B, definition=SYNTHETIC) == "done"
    assert machine.advance_target(CodingStage.needs_closeout) is CodingStage.done


# =====================================================================
# D. Single-writer structural test
# =====================================================================


_CANONICAL_COL_WRITE = re.compile(r"\b(stage|ceiling|at_cap)\s*=", re.IGNORECASE)


def test_only_apply_decision_writes_canonical_position() -> None:
    """stage/ceiling/at_cap columns are written only inside _apply_decision.

    Whitespace-insensitive: `SET stage=?` (no space) must not evade the guard."""
    tree = ast.parse(_DATA_PY.read_text(), filename=str(_DATA_PY))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name == "_apply_decision":
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                sql = sub.value
                if "UPDATE tickets" in sql and _CANONICAL_COL_WRITE.search(sql):
                    offenders.append(node.name)
    assert offenders == [], f"canonical-position writers outside _apply_decision: {offenders}"


# =====================================================================
# E. Note write-path field guard
# =====================================================================


@pytest.fixture
def tmp_db(tmp_path: Path) -> Connection:
    from planner.core.db import connect, create_schema

    conn = connect(str(tmp_path / "note-test.db"))
    create_schema(conn)
    return conn


@pytest.fixture
def fake_clock() -> TestClock:
    return TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())


def _seed_ticket_past_success(conn: Connection, now: int) -> str:
    ticket = tickets_data.create_ticket_from_external_work(
        conn,
        worker_type="coding",
        title="Note target",
        target_stage=CodingStage.needs_approach,
        provided_values={FieldName.success: "success value"},
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="kickoff note",
    )
    return ticket.id


def _event_count(conn: Connection, entity_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    return int(row["n"])


def test_note_on_undeclared_field_rejected_no_side_effect(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket_id = _seed_ticket_past_success(tmp_db, now)
    before = tickets_data.read_ticket(tmp_db, ticket_id)
    before_events = _event_count(tmp_db, ticket_id)

    with pytest.raises(PlannerError) as exc:
        tickets_data.set_field_user_note(
            tmp_db,
            ticket_id,
            field="not_a_declared_field",  # type: ignore[arg-type]
            user_note="x",
            actor="human",
            now=now,
        )
    assert exc.value.code == ErrorCode.validation
    assert exc.value.detail == {"field": "not_a_declared_field"}

    after = tickets_data.read_ticket(tmp_db, ticket_id)
    assert after.fields == before.fields
    assert after.stage == before.stage
    assert after.ceiling == before.ceiling
    assert after.at_cap == before.at_cap
    assert _event_count(tmp_db, ticket_id) == before_events


def test_valid_note_changes_only_user_note(tmp_db: Connection, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()
    ticket_id = _seed_ticket_past_success(tmp_db, now)
    before = tickets_data.read_ticket(tmp_db, ticket_id)
    before_events = _event_count(tmp_db, ticket_id)

    updated = tickets_data.set_field_user_note(
        tmp_db,
        ticket_id,
        field=FieldName.approach,
        user_note="guidance",
        actor="human",
        now=now,
    )
    up_approach = fields_codec.get_slot(updated.fields, "approach")
    be_approach = fields_codec.get_slot(before.fields, "approach")
    assert up_approach.user_note == "guidance"
    # every other slot's value/proposal preserved; approach's own value/proposal too
    assert up_approach.value == be_approach.value
    assert up_approach.proposal == be_approach.proposal
    assert fields_codec.get_slot(updated.fields, "kickoff") == fields_codec.get_slot(
        before.fields, "kickoff"
    )
    assert fields_codec.get_slot(updated.fields, "success") == fields_codec.get_slot(
        before.fields, "success"
    )
    assert updated.stage == before.stage
    assert updated.ceiling == before.ceiling
    assert updated.at_cap == before.at_cap
    assert _event_count(tmp_db, ticket_id) == before_events + 1


# =====================================================================
# F. Strict-mypy overload cases
# =====================================================================
# The overload narrowing cases live in tests/typing/tt01_overload_cases.py, which
# `./verify` type-checks with a dedicated strict-mypy invocation (scripts/verify.py).
# assert_type is a runtime no-op, so keeping the cases here would give false
# confidence; the dedicated mypy gate makes them genuinely enforced. Production
# caller-narrowing is additionally enforced by `mypy src/` (widening
# resolve_scope.new_stage or the enum overload would fail at the machine.py call
# sites).
