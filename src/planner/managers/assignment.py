"""Normalize issued assignment input into the current Manager contract."""

from __future__ import annotations

from planner.core.contracts import ErrorCode, JsonDict, PlannerError


def normalize_assignment(raw: JsonDict) -> JsonDict:
    result = dict(raw)
    has_current, has_previous = "manager" in result, "orchestrator" in result
    current = result.get("manager")
    previous = result.pop("orchestrator", None)

    def canonical(value: object) -> object:
        if isinstance(value, dict):
            return {
                **value,
                "kind": "manager" if value.get("kind") == "sprint_item" else value.get("kind"),
            }
        return value

    if has_current and has_previous and canonical(current) != canonical(previous):
        raise PlannerError(ErrorCode.validation, "conflicting Manager assignments")
    if "manager" in result or previous is not None:
        result["manager"] = canonical(current if current is not None else previous)
    return result
