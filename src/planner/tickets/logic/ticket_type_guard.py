"""The single registry-validation door shared by row load, pre-persist, creation,
seed, and the startup audit (t_tt02).

``resolve_and_validate`` is the one place that decides whether a stored
``(worker_type, stage, ceiling)`` triple is registry-valid. Every persistence door
and the startup audit call it, so "valid" has exactly one definition and cannot
drift between the read path and the audit.

Field-set / slot validation is NOT done here: the existing
``fields_codec.fields_from_json`` is the single field door, and it is STRICT on a
missing declared field or a malformed slot and LENIENT on extra top-level keys
(live rows carry a legacy ``result`` key). Callers that need the fields validated
decode through the codec with the definition this function returns, so the two
share one policy by construction.

F6-safe: imports only the ``coding_bridge`` seam, never ``worker_types``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.logic import coding_bridge

if TYPE_CHECKING:
    from planner.tickets.logic.coding_bridge import WorkflowDefinition


def resolve_and_validate(
    worker_type: str, *, stage: str, ceiling: str
) -> WorkflowDefinition:
    """Resolve the type's definition and validate the row's ``stage`` and ``ceiling``
    against it. Returns the definition for the caller to thread onward (codec + engine).

    Raises ``PlannerError`` on:
    - unknown ``worker_type`` (``not_found``) — the registry door;
    - ``stage`` that is neither a linear stage nor the reserved ``dropped``
      (``validation``, "stage outside the linear order");
    - ``ceiling`` outside the type's ceiling range (``scope_invalid``)."""
    defn = coding_bridge.require(worker_type)
    # A linear stage resolves an index; the exceptional terminal ``dropped`` is
    # accepted explicitly (it is outside the linear order). Any other id raises
    # "stage outside the linear order" from the views.
    if stage != defn.dropped_stage.id:
        coding_bridge.views.stage_index(defn, stage)
    if ceiling not in coding_bridge.views.ceiling_range(defn):
        raise PlannerError(
            ErrorCode.scope_invalid,
            "ceiling outside the type's range",
            {"worker_type": worker_type, "ceiling": ceiling},
        )
    return defn
