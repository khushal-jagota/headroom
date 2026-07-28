"""t_tt03 — the external-work prefix derivation is gate-based and per-type.

The settled-prefix order comes from the NON-TERMINAL stages' ``gating_field`` (in
stage order), NOT from ``field_ids`` — the registry does not order-align the two,
so a type could declare them differently (the t_tt02b field-order bug). These
tests pin:

- T-EW-golden (PERMANENT): coding's derived field order and prefix counts equal the
  canonical map (needs_success:1 … done:6) — the drift guard.
- probe's derivation (needs_alpha:1, needs_beta:2, done:3).
- a SYNTHETIC type whose field-declaration order is DELIBERATELY misaligned with its
  gating order — proving the derivation follows the GATE, not the field list (the
  fix isn't a probe-happens-to-align accident).
- a type declining ``supports_prefix_reconciliation`` is rejected loudly.
"""

from __future__ import annotations

import pytest
from tests.support.probe import PROBE_WORKER_TYPE_DEFINITION

from planner.core.contracts import ErrorCode, PlannerError, Priority
from planner.tickets.contracts import (
    AtCap,
    FieldSlot,
    Proposal,
    StageOwnershipMode,
    Ticket,
    TicketFields,
    TicketStatus,
)
from planner.tickets.logic.external_work import (
    _prefix_count,
    decide_external_work,
)
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)
from planner.worker_types.registry import WorkerTypeRegistry

CODING = CODING_WORKER_TYPE_DEFINITION


def test_coding_gate_field_order_is_golden() -> None:  # T-EW-golden (PERMANENT)
    assert CODING.reconciliation_field_order() == (
        "kickoff",
        "success",
        "approach",
        "plan",
        "implementation",
        "closeout",
    )


def test_coding_prefix_counts_are_golden() -> None:  # T-EW-golden (PERMANENT)
    expected = {
        "needs_success": 1,
        "needs_approach": 2,
        "needs_plan": 3,
        "needs_implementation": 4,
        "needs_closeout": 5,
        "done": 6,
    }
    assert {state: _prefix_count(CODING, state) for state in expected} == expected


def test_probe_derivation() -> None:
    assert PROBE_WORKER_TYPE_DEFINITION.reconciliation_field_order() == ("kickoff", "alpha", "beta")
    assert {
        state: _prefix_count(PROBE_WORKER_TYPE_DEFINITION, state)
        for state in ("needs_alpha", "needs_beta", "done")
    } == {"needs_alpha": 1, "needs_beta": 2, "done": 3}


# A synthetic type whose DECLARED field order (kickoff, second, first) is deliberately
# NOT the gating order (kickoff -> first -> second). The derivation must follow the
# gates, proving it isn't accidentally correct because probe/coding happen to align.
_MISALIGNED = WorkerTypeDefinition(
    worker_type="misaligned",
    label="Misaligned",
    stages=(
        StageDefinition(
            id="needs_kickoff",
            label="Kickoff",
            gating_field="kickoff",
            is_terminal=False,
            default_ownership_mode=StageOwnershipMode.worker,
        ),
        StageDefinition(
            id="needs_first",
            label="First",
            gating_field="first",
            is_terminal=False,
            default_ownership_mode=StageOwnershipMode.worker,
        ),
        StageDefinition(
            id="needs_second",
            label="Second",
            gating_field="second",
            is_terminal=False,
            default_ownership_mode=StageOwnershipMode.worker,
        ),
        StageDefinition(
            id="done",
            label="Done",
            gating_field=None,
            is_terminal=True,
            default_ownership_mode=None,
        ),
    ),
    dropped_stage=StageDefinition(
        id="dropped",
        label="Dropped",
        gating_field=None,
        is_terminal=True,
        default_ownership_mode=None,
    ),
    # Declared field order intentionally scrambled relative to the gate order.
    fields=(
        FieldDefinition(id="kickoff", label="Kickoff"),
        FieldDefinition(id="second", label="Second"),
        FieldDefinition(id="first", label="First"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker",
        default_model="a-model",
        default_reasoning_effort=None,
        toolset_profile="default",
        default_backend="hermes",
    ),
    supports_prefix_reconciliation=True,
)

_NO_PREFIX = WorkerTypeDefinition(
    worker_type="noprefix",
    label="No prefix",
    stages=(
        StageDefinition(
            id="needs_kickoff",
            label="Kickoff",
            gating_field="kickoff",
            is_terminal=False,
            default_ownership_mode=StageOwnershipMode.worker,
        ),
        StageDefinition(
            id="needs_one",
            label="One",
            gating_field="one",
            is_terminal=False,
            default_ownership_mode=StageOwnershipMode.worker,
        ),
        StageDefinition(
            id="done",
            label="Done",
            gating_field=None,
            is_terminal=True,
            default_ownership_mode=None,
        ),
    ),
    dropped_stage=StageDefinition(
        id="dropped",
        label="Dropped",
        gating_field=None,
        is_terminal=True,
        default_ownership_mode=None,
    ),
    fields=(FieldDefinition(id="kickoff", label="Kickoff"), FieldDefinition(id="one", label="One")),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker",
        default_model="a-model",
        default_reasoning_effort=None,
        toolset_profile="default",
        default_backend="hermes",
    ),
    supports_prefix_reconciliation=False,
)


def test_derivation_follows_gate_order_not_field_declaration() -> None:
    # field_ids order is (kickoff, second, first) but the GATE order is
    # (kickoff, first, second) — the derivation must use the gate order.
    assert _MISALIGNED.field_ids() == ("kickoff", "second", "first")
    assert _MISALIGNED.reconciliation_field_order() == ("kickoff", "first", "second")


def _needs_kickoff_ticket(defn: WorkerTypeDefinition) -> Ticket:
    slots = {fid: FieldSlot() for fid in defn.field_ids()}
    slots["kickoff"] = FieldSlot(
        value=None,
        proposal=Proposal(body="kickoff note", proposed_by="chief", created_at=1),
    )
    return Ticket(
        id="t_x",
        title="x",
        worker_type=defn.worker_type,
        stage="needs_kickoff",
        priority=Priority.P3,
        deadline=None,
        project_id=None,
        project_name=None,
        sprint_item_id=None,
        sprint_id=None,
        recap="",
        ceiling=defn.default_ceiling(),
        at_cap=AtCap.propose,
        ticket_status=TicketStatus.empty,
        ticket_status_changed_at=0,
        backend_error=None,
        stage_ownership_overrides={},
        default_stage_ownership_mode=StageOwnershipMode.worker,
        effective_stage_ownership_mode=StageOwnershipMode.worker,
        conversation_id=None,
        employee_backend="hermes",
        employee_launch_model=None,
        employee_launch_reasoning_effort=None,
        alias=None,
        fields=TicketFields(slots),
        created_at=1,
        updated_at=1,
    )


def test_type_declining_prefix_reconciliation_is_rejected() -> None:
    # Build a validated registry so the definition is well-formed, then drive it.
    WorkerTypeRegistry(
        (_NO_PREFIX,),
        known_skills=frozenset({"panels-worker"}),
        known_toolset_profiles=frozenset({"default"}),
    )
    ticket = _needs_kickoff_ticket(_NO_PREFIX)
    with pytest.raises(PlannerError) as exc:
        decide_external_work(
            ticket,
            "needs_one",
            {"kickoff": "kn"},
            worker_type_definition=_NO_PREFIX,
        )
    assert exc.value.code == ErrorCode.validation
    assert exc.value.message == "type does not support external-work prefix reconciliation"
    assert exc.value.detail == {"worker_type": "noprefix"}
