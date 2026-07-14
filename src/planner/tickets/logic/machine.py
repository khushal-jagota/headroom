"""State-machine math for §4.2/§4.3/§4.4: linear order indexing, gating and
advance tables, the auto-accept condition, ceiling comparison, and onward-scope
resolution.

Definition-driven (t_tt01): the linear-order lookups read the workflow from a
``WorkflowDefinition`` through the registry's derived views (reached via the
``coding_bridge`` seam), never from the module-global lifecycle constants. The
Tier-1 lookups are string-id-native — a foreign stage id (e.g. ``"needs_alpha"``)
that is not a ``CodingStage`` member flows through them; existing callers pass a
``CodingStage`` (already a ``str``) and omit ``definition`` to get ``coding``
(parity). The Tier-2 scope + field-storage ops stay ``CodingStage``/``FieldName``
-typed and coding-resolved (genericizing them is deferred to t_tt02x/t_tt02).

Pure: contracts + fields_codec + the coding_bridge seam only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, overload

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
    CodingStage,
    FieldName,
    Implementer,
    NextCeiling,
    ScopePair,
    TicketFields,
    TicketStatus,
)
from planner.tickets.logic import coding_bridge, fields_codec

if TYPE_CHECKING:
    from planner.tickets.contracts import Ticket
    from planner.tickets.logic.coding_bridge import WorkflowDefinition

# The inverse gate table (field -> the stage it gates). Retained as the reference
# spec the t_tt00 golden parity test proves the coding definition equal to; the
# engine no longer READS it — field_is_passed derives the gated stage from the
# definition via the views. (BRIEF: keep FIELD_GATES in the machine namespace,
# stop reading it.)
FIELD_GATES: Final[dict[FieldName, CodingStage]] = {
    FieldName.kickoff: CodingStage.needs_kickoff,
    FieldName.success: CodingStage.needs_success,
    FieldName.approach: CodingStage.needs_approach,
    FieldName.plan: CodingStage.needs_plan,
    FieldName.implementation: CodingStage.needs_implementation,
    FieldName.closeout: CodingStage.needs_closeout,
}


# --- enum re-wrap helpers (the enum-vs-string resolution) -----------------------
# A coding id round-trips to its StrEnum singleton; a foreign id stays a bare str,
# keeping the engine N-ary. The overloads on the public functions narrow a
# CodingStage/coding arg to the enum return so strict mypy type-checks callers.


def _as_stage(stage_id: str) -> CodingStage | str:
    try:
        return CodingStage(stage_id)
    except ValueError:
        return stage_id


def _as_field(field_id: str) -> FieldName | str:
    try:
        return FieldName(field_id)
    except ValueError:
        return field_id


# --- Tier 1: string-id-native linear-order lookups -----------------------------


def stage_index(stage: str, *, definition: WorkflowDefinition | None = None) -> int:
    defn = definition or coding_bridge.coding_definition()
    return coding_bridge.views.stage_index(defn, str(stage))


def is_terminal(stage: str, *, definition: WorkflowDefinition | None = None) -> bool:
    defn = definition or coding_bridge.coding_definition()
    return coding_bridge.views.is_terminal(defn, str(stage))


@overload
def gating_field(
    stage: CodingStage, *, definition: WorkflowDefinition | None = None
) -> FieldName | None: ...
@overload
def gating_field(
    stage: str, *, definition: WorkflowDefinition | None = None
) -> FieldName | str | None: ...
def gating_field(
    stage: str, *, definition: WorkflowDefinition | None = None
) -> FieldName | str | None:
    defn = definition or coding_bridge.coding_definition()
    gf = coding_bridge.views.gating_field(defn, str(stage))
    return _as_field(gf) if gf is not None else None


def field_is_passed(
    field: str, stage: str, *, definition: WorkflowDefinition | None = None
) -> bool:
    """A field is *passed* iff the stage it gates strictly precedes the current
    stage in the linear order. dropped is not linear, so stage_index(dropped)
    raises validation; the decide_edit_value check order rejects dropped explicitly
    before this is reached."""
    defn = definition or coding_bridge.coding_definition()
    gated = coding_bridge.views.gated_stage(defn, str(field))
    return coding_bridge.views.stage_index(defn, str(stage)) > coding_bridge.views.stage_index(
        defn, gated
    )


@overload
def advance_target(
    stage: CodingStage, *, definition: WorkflowDefinition | None = None
) -> CodingStage: ...
@overload
def advance_target(
    stage: str, *, definition: WorkflowDefinition | None = None
) -> CodingStage | str: ...
def advance_target(
    stage: str, *, definition: WorkflowDefinition | None = None
) -> CodingStage | str:
    """The single linear step from a non-terminal stage. Advancing never depends on
    the ceiling; the ceiling only decides whether a proposal auto-accepts."""
    defn = definition or coding_bridge.coding_definition()
    tgt = coding_bridge.views.advance_target(defn, str(stage))
    if tgt is None:
        raise PlannerError(
            ErrorCode.validation, "stage has no advance target", {"stage": str(stage)}
        )
    return _as_stage(tgt)


@overload
def auto_accept_target(
    stage: CodingStage,
    ceiling: str,
    field: str,
    *,
    definition: WorkflowDefinition | None = None,
) -> CodingStage | None: ...
@overload
def auto_accept_target(
    stage: str,
    ceiling: str,
    field: str,
    *,
    definition: WorkflowDefinition | None = None,
) -> CodingStage | str | None: ...
def auto_accept_target(
    stage: str,
    ceiling: str,
    field: str,
    *,
    definition: WorkflowDefinition | None = None,
) -> CodingStage | str | None:
    defn = definition or coding_bridge.coding_definition()
    if is_terminal(stage, definition=defn):
        return None
    if field != gating_field(stage, definition=defn):
        return None
    target = advance_target(stage, definition=defn)
    if stage_index(target, definition=defn) > stage_index(ceiling, definition=defn):
        return None
    return target


def at_or_beyond_ceiling(
    stage: str, ceiling: str, *, definition: WorkflowDefinition | None = None
) -> bool:
    defn = definition or coding_bridge.coding_definition()
    return stage_index(stage, definition=defn) >= stage_index(ceiling, definition=defn)


# --- Tier 2: coding-bound scope + field-storage (enum-typed) --------------------


def validate_ceiling(
    ceiling: str, *, definition: WorkflowDefinition | None = None
) -> None:
    defn = definition or coding_bridge.coding_definition()
    if str(ceiling) not in coding_bridge.views.ceiling_range(defn):
        raise PlannerError(
            ErrorCode.scope_invalid, "ceiling must be a linear stage", {"ceiling": str(ceiling)}
        )


def resolve_scope(
    new_stage: str,
    next_ceiling: NextCeiling | None,
    at_cap: AtCap | None,
    *,
    definition: WorkflowDefinition | None = None,
) -> ScopePair:
    defn = definition or coding_bridge.coding_definition()
    if next_ceiling is None or at_cap is None:
        missing: list[str] = []
        if next_ceiling is None:
            missing.append("next_ceiling")
        if at_cap is None:
            missing.append("at_cap")
        raise PlannerError(
            ErrorCode.scope_missing, "accept requires the onward scope pair", {"missing": missing}
        )
    if next_ceiling == NO_FURTHER:
        # the ceiling becomes exactly the newly entered stage
        return ScopePair(next_ceiling=str(new_stage), at_cap=at_cap)
    ceiling_id = str(next_ceiling)
    # Two DISTINCT rejections (coding-parity): an id outside the type's ceiling range is
    # "unknown next_ceiling"; a valid ceiling whose index precedes new_stage is
    # "next_ceiling must be at or beyond the new stage".
    if ceiling_id not in coding_bridge.views.ceiling_range(defn):
        raise PlannerError(
            ErrorCode.scope_invalid, "unknown next_ceiling", {"next_ceiling": ceiling_id}
        )
    if stage_index(ceiling_id, definition=defn) < stage_index(str(new_stage), definition=defn):
        raise PlannerError(
            ErrorCode.scope_invalid,
            "next_ceiling must be at or beyond the new stage",
            {"next_ceiling": ceiling_id, "new_stage": str(new_stage)},
        )
    return ScopePair(next_ceiling=ceiling_id, at_cap=at_cap)


def has_pending_gating_proposal(
    stage: str, fields: TicketFields, *, definition: WorkflowDefinition | None = None
) -> bool:
    field = gating_field(stage, definition=definition)
    if field is None:
        return False
    return fields_codec.get_slot(fields, str(field)).proposal is not None


def has_pending_parked_proposal(
    ticket: Ticket, *, definition: WorkflowDefinition | None = None
) -> bool:
    return has_pending_gating_proposal(ticket.stage, ticket.fields, definition=definition)


def plan_handoff_status(
    implementer: Implementer | None,
    old_stage: str,
    new_stage: str | None,
    *,
    definition: WorkflowDefinition | None = None,
) -> TicketStatus | None:
    """The definition's matching transition hook maps an implementer + (old_stage,
    new_stage) pair to a durable status override. For coding this reproduces the exact
    plan-handoff rule (an accepted Plan handed to needs_implementation under khushal
    becomes a durable user_takeover); every other implementer/transition returns None.
    None means this transition carries no status override."""
    if new_stage is None or implementer is None:
        return None
    defn = definition or coding_bridge.coding_definition()
    effect = coding_bridge.views.transition_effect(
        defn, str(implementer), str(old_stage), str(new_stage)
    )
    return TicketStatus(effect) if effect is not None else None
