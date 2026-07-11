"""State-machine math for §4.2/§4.3/§4.4: linear order indexing, gating and
advance tables, the auto-accept condition, ceiling comparison, and onward-scope
resolution.

Definition-driven (t_tt01): the linear-order lookups read the workflow from a
``WorkflowDefinition`` through the registry's derived views (reached via the
``coding_bridge`` seam), never from the module-global lifecycle constants. The
Tier-1 lookups are string-id-native — a foreign stage id (e.g. ``"needs_alpha"``)
that is not a ``TicketState`` member flows through them; existing callers pass a
``TicketState`` (already a ``str``) and omit ``definition`` to get ``coding``
(parity). The Tier-2 scope + field-storage ops stay ``TicketState``/``FieldName``
-typed and coding-resolved (genericizing them is deferred to t_tt02x/t_tt02).

Pure: contracts + fields_codec + the coding_bridge seam only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, overload

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
    FieldName,
    Implementer,
    NextCeiling,
    ScopePair,
    TicketFields,
    TicketState,
    TicketStatus,
)
from planner.tickets.logic import coding_bridge, fields_codec

if TYPE_CHECKING:
    from planner.tickets.contracts import Ticket
    from planner.tickets.logic.coding_bridge import WorkflowDefinition

# The inverse gate table (field -> the state it gates). Retained as the reference
# spec the t_tt00 golden parity test proves the coding definition equal to; the
# engine no longer READS it — field_is_passed derives the gated state from the
# definition via the views. (BRIEF: keep FIELD_GATES in the machine namespace,
# stop reading it.)
FIELD_GATES: Final[dict[FieldName, TicketState]] = {
    FieldName.kickoff: TicketState.needs_kickoff,
    FieldName.success: TicketState.needs_success,
    FieldName.approach: TicketState.needs_approach,
    FieldName.plan: TicketState.needs_plan,
    FieldName.implementation: TicketState.needs_implementation,
    FieldName.closeout: TicketState.needs_closeout,
}


# --- enum re-wrap helpers (the enum-vs-string resolution) -----------------------
# A coding id round-trips to its StrEnum singleton; a foreign id stays a bare str,
# keeping the engine N-ary. The overloads on the public functions narrow a
# TicketState/coding arg to the enum return so strict mypy type-checks callers.


def _as_state(state_id: str) -> TicketState | str:
    try:
        return TicketState(state_id)
    except ValueError:
        return state_id


def _as_field(field_id: str) -> FieldName | str:
    try:
        return FieldName(field_id)
    except ValueError:
        return field_id


# --- Tier 1: string-id-native linear-order lookups -----------------------------


def state_index(state: str, *, definition: WorkflowDefinition | None = None) -> int:
    defn = definition or coding_bridge.coding_definition()
    return coding_bridge.views.state_index(defn, str(state))


def is_terminal(state: str, *, definition: WorkflowDefinition | None = None) -> bool:
    defn = definition or coding_bridge.coding_definition()
    return coding_bridge.views.is_terminal(defn, str(state))


@overload
def gating_field(
    state: TicketState, *, definition: WorkflowDefinition | None = None
) -> FieldName | None: ...
@overload
def gating_field(
    state: str, *, definition: WorkflowDefinition | None = None
) -> FieldName | str | None: ...
def gating_field(
    state: str, *, definition: WorkflowDefinition | None = None
) -> FieldName | str | None:
    defn = definition or coding_bridge.coding_definition()
    gf = coding_bridge.views.gating_field(defn, str(state))
    return _as_field(gf) if gf is not None else None


def field_is_passed(
    field: str, state: str, *, definition: WorkflowDefinition | None = None
) -> bool:
    """A field is *passed* iff the state it gates strictly precedes the current
    state in the linear order. dropped is not linear, so state_index(dropped)
    raises validation; the decide_edit_value check order rejects dropped explicitly
    before this is reached."""
    defn = definition or coding_bridge.coding_definition()
    gated = coding_bridge.views.gated_state(defn, str(field))
    return coding_bridge.views.state_index(defn, str(state)) > coding_bridge.views.state_index(
        defn, gated
    )


@overload
def advance_target(
    state: TicketState, *, definition: WorkflowDefinition | None = None
) -> TicketState: ...
@overload
def advance_target(
    state: str, *, definition: WorkflowDefinition | None = None
) -> TicketState | str: ...
def advance_target(
    state: str, *, definition: WorkflowDefinition | None = None
) -> TicketState | str:
    """The single linear step from a non-terminal state. Advancing never depends on
    the ceiling; the ceiling only decides whether a proposal auto-accepts."""
    defn = definition or coding_bridge.coding_definition()
    tgt = coding_bridge.views.advance_target(defn, str(state))
    if tgt is None:
        raise PlannerError(
            ErrorCode.validation, "state has no advance target", {"state": str(state)}
        )
    return _as_state(tgt)


@overload
def auto_accept_target(
    state: TicketState,
    ceiling: str,
    field: str,
    *,
    definition: WorkflowDefinition | None = None,
) -> TicketState | None: ...
@overload
def auto_accept_target(
    state: str,
    ceiling: str,
    field: str,
    *,
    definition: WorkflowDefinition | None = None,
) -> TicketState | str | None: ...
def auto_accept_target(
    state: str,
    ceiling: str,
    field: str,
    *,
    definition: WorkflowDefinition | None = None,
) -> TicketState | str | None:
    defn = definition or coding_bridge.coding_definition()
    if is_terminal(state, definition=defn):
        return None
    if field != gating_field(state, definition=defn):
        return None
    target = advance_target(state, definition=defn)
    if state_index(target, definition=defn) > state_index(ceiling, definition=defn):
        return None
    return target


def at_or_beyond_ceiling(
    state: str, ceiling: str, *, definition: WorkflowDefinition | None = None
) -> bool:
    defn = definition or coding_bridge.coding_definition()
    return state_index(state, definition=defn) >= state_index(ceiling, definition=defn)


# --- Tier 2: coding-bound scope + field-storage (enum-typed) --------------------


def validate_ceiling(
    ceiling: TicketState, *, definition: WorkflowDefinition | None = None
) -> None:
    defn = definition or coding_bridge.coding_definition()
    if str(ceiling) not in coding_bridge.views.ceiling_range(defn):
        raise PlannerError(
            ErrorCode.scope_invalid, "ceiling must be a linear state", {"ceiling": ceiling.value}
        )


def resolve_scope(
    new_state: TicketState,
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
        # the ceiling becomes exactly the newly entered state
        return ScopePair(next_ceiling=new_state, at_cap=at_cap)
    if not isinstance(next_ceiling, TicketState):
        raise PlannerError(
            ErrorCode.scope_invalid, "unknown next_ceiling", {"next_ceiling": str(next_ceiling)}
        )
    if str(next_ceiling) not in coding_bridge.views.ceiling_range(defn) or state_index(
        next_ceiling, definition=defn
    ) < state_index(new_state, definition=defn):
        raise PlannerError(
            ErrorCode.scope_invalid,
            "next_ceiling must be at or beyond the new state",
            {"next_ceiling": next_ceiling.value, "new_state": new_state.value},
        )
    return ScopePair(next_ceiling=next_ceiling, at_cap=at_cap)


def require_coding_field(field: FieldName | str) -> FieldName:
    """The Tier-2 field-storage boundary: the fixed 6-slot ``TicketFields`` struct can
    only hold coding fields. A foreign field id (a bare ``str`` from ``_as_field`` on a
    non-coding definition) must fail LOUDLY here, never silently mis-route to a slot.
    Genericizing storage is deferred to t_tt02x/t_tt02."""
    if isinstance(field, FieldName):
        return field
    raise PlannerError(
        ErrorCode.validation,
        "field storage is coding-bound; a foreign field id cannot be stored yet",
        {"field": str(field)},
    )


def has_pending_gating_proposal(
    state: TicketState, fields: TicketFields, *, definition: WorkflowDefinition | None = None
) -> bool:
    field = gating_field(state, definition=definition)
    if field is None:
        return False
    return fields_codec.get_slot(fields, require_coding_field(field)).proposal is not None


def has_pending_parked_proposal(
    ticket: Ticket, *, definition: WorkflowDefinition | None = None
) -> bool:
    return has_pending_gating_proposal(ticket.state, ticket.fields, definition=definition)


def plan_handoff_status(
    implementer: Implementer | None, old_state: TicketState, new_state: TicketState | None
) -> TicketStatus | None:
    """An accepted Plan (direct or auto-accepted) that hands off to needs_implementation
    routes to Khushal as a durable user_takeover; every other implementer and NULL keep
    the existing status path. None means this transition carries no status override."""
    if old_state == TicketState.needs_plan and new_state == TicketState.needs_implementation:
        if implementer == Implementer.khushal:
            return TicketStatus.user_takeover
    return None
