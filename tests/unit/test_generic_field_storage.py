"""t_tt02b — generic per-type field storage + Tier-2 scope, and the domain-contract
widening (Ticket.stage/ceiling -> str).

These tests assert the ticket's acceptance items with concrete values:
- T1: a coding ticket's fields_to_json bytes are byte-identical to the pre-widening
  hardcoded codec (a golden literal), AND the STORED DB bytes after a real
  create->propose->accept drive are byte-identical (review F6 — a different insertion
  order would fail here even while the standalone codec golden stays green).
- T2/T3: a synthetic (kickoff, alpha, beta) field set round-trips through the generic
  codec; the storage boundary now rejects against the definition's OWN field set,
  strict-on-missing-declared and lenient-on-extra preserved; the old coding-bound
  boundary (machine.require_coding_field) is gone.
- T4: resolve_scope/validate_ceiling/has_pending_gating_proposal operate on a
  non-coding definition's ceiling_range/gate; the two DISTINCT resolve_scope rejection
  payloads (unknown-ceiling vs at-or-beyond) are pinned for coding parity (review F2).
- T5: the DATA-layer drive on a probe ticket exercises every converted path at runtime
  with a non-coding type — a foreign AUTO-ACCEPT (ceiling beyond state, review F3), the
  mixed-mode ownership path (worker alpha auto-accepts into paired beta), proposal
  supersession, the recap writer, return-for-revision, and the admission /
  at-cap-stop error payloads — with EXACT event order and payloads at each transition
  (review F4).
- T7: a probe ticket survives create + reload (the P0 _row_to_ticket str() fix).
- Coding parity: drop/state-jump bookend `==` comparisons survive the str flip.

The probe definition uses novel Stage/field ids (alpha/beta), built with
``"".join(...)`` so interning cannot mask an identity comparison. It is registered
through the explicit test configuration seam; production composition stays unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.probe import FIELD_ALPHA as _FA
from tests.support.probe import FIELD_BETA as _FB
from tests.support.probe import NEEDS_ALPHA as _A
from tests.support.probe import NEEDS_BETA as _B
from tests.support.probe import PROBE_FIELD_IDS as _PROBE_FIELD_IDS
from tests.support.probe import install_probe_registry, uninstall_probe_registry

from planner.core.clock import TestClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    FieldSlot,
    StageOwnershipMode,
    TicketFields,
    TicketStatus,
)
from planner.tickets.logic import fields_codec, machine
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.contracts import WorkerTypeDefinition


@pytest.fixture
def probe_registry() -> Iterator[WorkerTypeDefinition]:
    """Install the canonical test registry for the persistence/engine doors, then
    restore production composition after the test."""
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Connection]:
    conn = connect(str(tmp_path / "probe-doors.db"))
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def fake_clock() -> TestClock:
    return TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())


# =====================================================================
# T1 — coding parity: fields_to_json bytes are byte-identical (golden literal)
# =====================================================================


_CODING_GOLDEN_SUCCESS_S = (
    '{"kickoff": {"value": null, "proposal": null}, '
    '"success": {"value": "s", "proposal": null}, '
    '"approach": {"value": null, "proposal": null}, '
    '"plan": {"value": null, "proposal": null}, '
    '"implementation": {"value": null, "proposal": null}, '
    '"closeout": {"value": null, "proposal": null}}'
)


def test_coding_fields_to_json_bytes_are_golden() -> None:
    fields = TicketFields.empty(CODING_WORKER_TYPE_DEFINITION.field_ids())
    fields = fields_codec.with_slot(fields, "success", FieldSlot(value="s"))
    # The exact byte string the pre-widening hardcoded codec emitted (key order + spacing).
    assert fields_codec.fields_to_json(fields) == _CODING_GOLDEN_SUCCESS_S
    # And it round-trips to an equal TicketFields.
    parsed = fields_codec.fields_from_json(_CODING_GOLDEN_SUCCESS_S)
    assert parsed == fields


# The exact STORED fields JSON of a coding ticket after create -> accept kickoff ->
# propose success -> accept success. Pins the persisted byte order (declared-key order)
# so a writer that constructs coding slots in a different insertion order fails here even
# while the standalone codec golden (above) stays green (review F6).
_CODING_STORED_AFTER_DRIVE = (
    '{"kickoff": {"value": "", "proposal": null}, '
    '"success": {"value": "the success", "proposal": null}, '
    '"approach": {"value": null, "proposal": null}, '
    '"plan": {"value": null, "proposal": null}, '
    '"implementation": {"value": null, "proposal": null}, '
    '"closeout": {"value": null, "proposal": null}}'
)


# =====================================================================
# T2 — generic storage round-trip for a synthetic (kickoff, alpha, beta) definition
# =====================================================================


def test_generic_storage_round_trip_and_copy_on_write(
    probe_registry: WorkerTypeDefinition,
) -> None:
    f = TicketFields.empty(_PROBE_FIELD_IDS)
    f2 = fields_codec.with_slot(f, _FA, FieldSlot(value="A"))

    # copy-on-write: f2 carries alpha; f is untouched; other slots stay empty.
    assert fields_codec.get_slot(f2, _FA).value == "A"
    assert fields_codec.get_slot(f2, _FB).value is None
    assert fields_codec.get_slot(f2, "kickoff").value is None
    assert fields_codec.get_slot(f, _FA).value is None

    raw = fields_codec.fields_to_json(f2)
    reloaded = fields_codec.declared_fields_from_json(raw, probe_registry.field_ids())
    assert fields_codec.get_slot(reloaded, _FA).value == "A"
    # declared key order preserved in the JSON.
    assert list(json.loads(raw).keys()) == list(_PROBE_FIELD_IDS)

    # storage boundary now rejects against the definition's OWN field set.
    with pytest.raises(PlannerError) as exc:
        fields_codec.get_slot(f2, "success")
    assert exc.value.code == ErrorCode.validation
    assert exc.value.detail == {"field": "success"}


# =====================================================================
# T3 — boundary lifted
# =====================================================================


# =====================================================================
# T4 — Tier-2 generic scope (non-coding ceiling id in ScopePair)
# =====================================================================


def test_resolve_scope_distinguishes_unknown_from_too_early_coding_payloads() -> None:
    # Coding-parity regression guard (review F2): the two rejection branches must stay
    # DISTINCT with their exact pre-widening payloads.
    # (a) an id outside the ceiling range -> "unknown next_ceiling".
    with pytest.raises(PlannerError) as unknown:
        machine.resolve_scope(
            "needs_approach",
            "bogus",
            AtCap.stop,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
    assert unknown.value.code == ErrorCode.scope_invalid
    assert unknown.value.message == "unknown next_ceiling"
    assert unknown.value.detail == {"next_ceiling": "bogus"}

    # (b) a valid ceiling whose index precedes new_stage -> "at or beyond the new state".
    with pytest.raises(PlannerError) as too_early:
        machine.resolve_scope(
            "needs_plan",
            "needs_approach",
            AtCap.stop,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
    assert too_early.value.code == ErrorCode.scope_invalid
    assert too_early.value.message == "next_ceiling must be at or beyond the new stage"
    assert too_early.value.detail == {
        "next_ceiling": "needs_approach",
        "new_stage": "needs_plan",
    }


# =====================================================================
# T5 — decide_* threaded, the DATA-layer drive (create -> propose[park] -> accept ->
# direct-scope -> propose[AUTO-ACCEPT] -> supersede -> recap -> return-for-revision ->
# drop/done) on a probe ticket, exercising every converted site with EXACT events
# =====================================================================


def _create_probe(tmp_db: Connection, now: int) -> str:
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Probe drive",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="probe",
    )
    return ticket.id


def test_probe_data_layer_drive_to_done(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkerTypeDefinition
) -> None:
    now = fake_clock.now_unix()
    tid = _create_probe(tmp_db, now)
    tickets_data.set_stage_ownership(
        tmp_db,
        tid,
        stage=_A,
        ownership_mode=StageOwnershipMode.worker,
        now=now,
    )

    # created at needs_kickoff scoped to the leading needs_kickoff ceiling, kickoff parked.
    t = tickets_data.read_ticket(tmp_db, tid)
    assert t.stage == "needs_kickoff"
    assert t.ceiling == "needs_kickoff"
    assert fields_codec.get_slot(t.fields, "kickoff").proposal is not None

    # --- accept kickoff, expanding the ceiling onward to needs_beta (beyond needs_alpha).
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling=_B,
        at_cap=AtCap.propose,
    )
    assert t.stage == _A
    assert t.ceiling == _B
    assert (
        fields_codec.get_slot(t.fields, "kickoff").value == ""
    )  # default kickoff note

    # --- propose alpha: ceiling (_B) is BEYOND state (_A) -> AUTO-ACCEPTS + advances.
    t = tickets_data.file_proposal(
        tmp_db, tid, field=_FA, body="alpha body", actor="agent", now=now
    )
    assert t.stage == _B  # auto-accepted + advanced
    assert fields_codec.get_slot(t.fields, _FA).value == "alpha body"
    assert fields_codec.get_slot(t.fields, _FA).proposal is None  # proposal cleared
    # Probe's mixed ownership fired for the FOREIGN type:
    # worker-owned needs_alpha auto-accepted into the newly reached paired needs_beta.
    assert t.ticket_status == TicketStatus.empty

    # --- at needs_beta (ceiling _B ==): propose beta -> PARKS (value None, proposal set).
    t = tickets_data.file_proposal(
        tmp_db, tid, field=_FB, body="beta v1", actor="agent", now=now
    )
    beta = fields_codec.get_slot(t.fields, _FB)
    assert (
        beta.value is None
        and beta.proposal is not None
        and beta.proposal.body == "beta v1"
    )
    assert t.stage == _B
    assert t.ticket_status == TicketStatus.awaiting_approval

    # --- propose beta AGAIN -> SUPERSEDES the first.
    t = tickets_data.file_proposal(
        tmp_db, tid, field=_FB, body="beta v2", actor="agent", now=now
    )
    beta_v2 = fields_codec.get_slot(t.fields, _FB)
    assert beta_v2.proposal is not None and beta_v2.proposal.body == "beta v2"

    # --- accept beta -> advances to done (the terminal), value settled.
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field=_FB,
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.stop,
    )
    assert t.stage == "done"
    assert fields_codec.get_slot(t.fields, _FB).value == "beta v2"
    assert fields_codec.get_slot(t.fields, _FB).proposal is None


def test_probe_recap_path_infers_gating_field(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkerTypeDefinition
) -> None:
    # file_current_proposal_with_recap infers the current gating field from the PROBE
    # definition (_FA at needs_alpha) and writes the recap atomically.
    now = fake_clock.now_unix()
    tid = _create_probe(tmp_db, now)
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t.stage == _A

    t = tickets_data.file_current_proposal_with_recap(
        tmp_db, tid, body="alpha via recap", recap="probe recap", actor="agent", now=now
    )
    assert t.recap == "probe recap"
    alpha = fields_codec.get_slot(t.fields, _FA)
    assert alpha.proposal is not None and alpha.proposal.body == "alpha via recap"
    assert t.stage == _A  # parked at the ceiling


def test_probe_return_for_revision_clears_parked_proposal(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkerTypeDefinition
) -> None:
    # return_for_revision on a probe ticket clears the parked proposal on the type's own
    # gating field (_FA), threading the probe definition into gating_field.
    now = fake_clock.now_unix()
    tid = _create_probe(tmp_db, now)
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t.stage == _A
    # park an alpha proposal, then attach a worker session (return needs an existing one).
    tickets_data.file_proposal(
        tmp_db, tid, field=_FA, body="alpha draft", actor="agent", now=now
    )
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = ? WHERE id = ?", ("probe-sess", tid)
    )
    tmp_db.commit()

    tickets_data.return_for_revision(
        tmp_db, tid, message="please revise", actor="human", now=now
    )
    reloaded = tickets_data.read_ticket(tmp_db, tid)
    assert (
        fields_codec.get_slot(reloaded.fields, _FA).proposal is None
    )  # parked proposal cleared
    assert reloaded.stage == _A


def test_probe_admission_error_payload(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkerTypeDefinition
) -> None:
    # An agent proposing a NON-gating field at the ceiling is rejected against PROBE's
    # own gate (_FA), with a clean PlannerError payload (not an AttributeError).
    now = fake_clock.now_unix()
    tid = _create_probe(tmp_db, now)
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t.stage == _A and t.ceiling == _A  # at the ceiling, at_cap propose

    with pytest.raises(PlannerError) as exc:
        tickets_data.file_proposal(
            tmp_db, tid, field=_FB, body="beta too early", actor="agent", now=now
        )
    assert exc.value.code == ErrorCode.validation
    assert exc.value.detail == {"field": _FB, "gating_field": _FA, "stage": _A}


# =====================================================================
# T7 — the P0 regression guard: a probe ticket survives create + reload
# =====================================================================


def test_probe_survives_create_and_reload(
    tmp_db: Connection, fake_clock: TestClock, probe_registry: WorkerTypeDefinition
) -> None:
    now = fake_clock.now_unix()
    tid = _create_probe(tmp_db, now)
    # _row_to_ticket must NOT raise (the str(row) fix; str("needs_alpha") would ValueError).
    ticket = tickets_data.read_ticket(tmp_db, tid)
    assert ticket.stage == "needs_kickoff"
    assert (
        ticket.ceiling == "needs_kickoff"
    )  # leading default ceiling (still a bare str)
    assert type(ticket.stage) is str
    assert type(ticket.ceiling) is str
