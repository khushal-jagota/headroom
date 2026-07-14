"""Derived views over a WorkflowDefinition: order/indexing, terminal recognition,
the forward gate map, the inverse gate map, the advance map, the ceiling range,
and the transition effect.

Everything is derived from ``defn.stages`` (and ``defn.fields`` / hooks) — there
is never a second hand-written table. Each function is a drop-in for the
corresponding operation in ``tickets/logic/machine.py``.

Unknown-stage safety: for a KNOWN terminal these return None (parity with the
machine's ``.get`` semantics); for a stage id NOT in the definition's linear
order they RAISE ``PlannerError(validation, ...)`` — matching ``machine.stage_index``.
A typo or a foreign-type stage must never masquerade as ``done``.

Pure: contracts + core error contract only."""

from __future__ import annotations

from planner.core.contracts import ErrorCode, PlannerError
from planner.ticket_types.contracts import Stage, WorkflowDefinition


def _raise_unknown_state(stage_id: str) -> PlannerError:
    return PlannerError(
        ErrorCode.validation, "stage outside the linear order", {"stage": stage_id}
    )


# --- order / indexing (replaces machine.stage_index) ---------------------------


def stage_ids(defn: WorkflowDefinition) -> tuple[str, ...]:
    """Every linear stage id, full order (== CODING_STAGE_ORDER values)."""
    return tuple(s.id for s in defn.stages)


def stage_index(defn: WorkflowDefinition, stage_id: str) -> int:
    """Position in the linear order; raises validation if the id is not linear."""
    ids = stage_ids(defn)
    try:
        return ids.index(stage_id)
    except ValueError as exc:
        raise _raise_unknown_state(stage_id) from exc


def require_stage(defn: WorkflowDefinition, stage_id: str) -> Stage:
    """The Stage for a linear stage id; raises validation if unknown."""
    for stage in defn.stages:
        if stage.id == stage_id:
            return stage
    raise _raise_unknown_state(stage_id)


# --- terminal recognition (replaces machine.is_terminal, incl. dropped) --------


def is_terminal(defn: WorkflowDefinition, stage_id: str) -> bool:
    """True for the linear terminal (done) AND the exceptional dropped; False for a
    known non-terminal; RAISES for an unknown id."""
    if stage_id == defn.dropped_stage.id:
        return True
    return require_stage(defn, stage_id).is_terminal


# --- forward gate (replaces machine.gating_field) ------------------------------


def gating_field(defn: WorkflowDefinition, stage_id: str) -> str | None:
    """Gate of a KNOWN stage; None for a known terminal (linear done AND the exceptional
    dropped). UNKNOWN stage RAISES."""
    if stage_id == defn.dropped_stage.id:
        return None
    return require_stage(defn, stage_id).gating_field


def gate_map(defn: WorkflowDefinition) -> dict[str, str]:
    """Non-terminal stage -> its gating field (== CODING_GATING_FIELD_BY_STAGE values)."""
    result: dict[str, str] = {}
    for stage in defn.stages:
        if not stage.is_terminal and stage.gating_field is not None:
            result[stage.id] = stage.gating_field
    return result


# --- inverse gate (replaces the FIELD_GATES table) -----------------------------


def gated_stage(defn: WorkflowDefinition, field_id: str) -> str:
    """The one stage a field gates; raises validation if the field gates nothing.
    Validation R11 guarantees the inverse is one-to-one, so this is unambiguous."""
    for stage_id, gated_field in gate_map(defn).items():
        if gated_field == field_id:
            return stage_id
    raise PlannerError(
        ErrorCode.validation, "field gates no stage", {"field": field_id}
    )


def has_field(defn: WorkflowDefinition, field_id: str) -> bool:
    """field_id is a declared FieldDef of this definition."""
    return any(f.id == field_id for f in defn.fields)


def field_ids(defn: WorkflowDefinition) -> tuple[str, ...]:
    """Every declared field id, full order (== FieldName values)."""
    return tuple(f.id for f in defn.fields)


# --- advance (replaces machine.advance_target) ---------------------------------


def advance_target(defn: WorkflowDefinition, stage_id: str) -> str | None:
    """Next stage of a KNOWN stage; None for a known terminal (linear done AND the
    exceptional dropped). UNKNOWN stage RAISES."""
    if stage_id == defn.dropped_stage.id:
        return None
    ids = stage_ids(defn)
    index = stage_index(defn, stage_id)   # raises on unknown
    if defn.stages[index].is_terminal:
        return None
    return ids[index + 1]


def advance_map(defn: WorkflowDefinition) -> dict[str, str]:
    """Non-terminal stage -> next stage (== CODING_NEXT_STAGE_BY_STAGE values)."""
    ids = stage_ids(defn)
    result: dict[str, str] = {}
    for i, stage in enumerate(defn.stages):
        if not stage.is_terminal and i + 1 < len(ids):
            result[stage.id] = ids[i + 1]
    return result


# --- ceiling range (invariant 9) -----------------------------------------------


def ceiling_range(defn: WorkflowDefinition) -> tuple[str, ...]:
    """The full linear stage order — every stage is a selectable ceiling, INCLUDING
    the leading needs_kickoff. A fresh ticket's default ceiling is needs_kickoff, so it
    must be a member of this validity set (validate_ceiling / resolve_scope read this)."""
    return stage_ids(defn)


def default_ceiling(defn: WorkflowDefinition) -> str:
    """First entry of the ceiling range — the leading needs_kickoff. A fresh ticket
    starts scoped exactly to kickoff (needs_kickoff, propose); the human expands it
    onward at kickoff acceptance."""
    return ceiling_range(defn)[0]


def first_worker_stage(defn: WorkflowDefinition) -> str:
    """The first non-kickoff stage — the first stage that does real work
    (== "needs_success" for coding, "needs_stages" for new_worker). This is the
    threshold the recap gate and the sprint-in-progress test key off: distinct from
    default_ceiling, which is now the leading needs_kickoff."""
    return stage_ids(defn)[1]


def linear_terminal_stage_id(defn: WorkflowDefinition) -> str:
    """The linear terminal stage id ("done"). Returns a stage id string."""
    for stage in defn.stages:
        if stage.is_terminal:
            return stage.id
    # Unreachable for a validated definition (R5/R6 guarantee a linear terminal).
    raise PlannerError(
        ErrorCode.validation, "linear order has no terminal", {"worker_type": defn.type_id}
    )


# --- transition effect (replaces machine.plan_handoff_status) ------------------


def transition_effect(
    defn: WorkflowDefinition, implementer: str, old_stage: str, new_stage: str
) -> str | None:
    """The matching hook's effect, keyed on (old_stage, new_stage, implementer);
    None when no hook matches (parity with plan_handoff_status returning None)."""
    for hook in defn.transition_hooks:
        if (
            hook.old_stage == old_stage
            and hook.new_stage == new_stage
            and hook.implementer == implementer
        ):
            return hook.effect
    return None
