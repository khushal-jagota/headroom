"""Pure ticket-type logic: the definition validator, the derived views over a
WorkflowDefinition, and the manifest serializer. Stdlib + the two leaf modules
(tickets/contracts, core/contracts) only; zero side effects."""

from __future__ import annotations

from planner.ticket_types.logic.manifest import serialize_definition
from planner.ticket_types.logic.validation import validate_definition
from planner.ticket_types.logic.views import (
    advance_map,
    advance_target,
    ceiling_range,
    default_ceiling,
    field_ids,
    first_worker_stage,
    gate_map,
    gated_state,
    gating_field,
    has_field,
    is_terminal,
    linear_terminal_stage_id,
    require_stage,
    stage_ids,
    state_index,
    transition_effect,
)

__all__ = [
    "advance_map",
    "advance_target",
    "ceiling_range",
    "default_ceiling",
    "field_ids",
    "first_worker_stage",
    "gate_map",
    "gated_state",
    "gating_field",
    "has_field",
    "is_terminal",
    "linear_terminal_stage_id",
    "require_stage",
    "serialize_definition",
    "stage_ids",
    "state_index",
    "transition_effect",
    "validate_definition",
]
