"""Expected revision metadata is separate from editable document fields."""

from typing import Any

from planner.core.contracts import ErrorCode, PlannerError


def document_edit(body: dict[str, Any], current_revision: int) -> dict[str, Any]:
    """Validate and compare supplied metadata before a canonical document change."""
    if "expected_revision" not in body:
        return body
    revision = body["expected_revision"]
    if type(revision) is not int or revision < 0:
        raise PlannerError(ErrorCode.validation, "expected_revision must be a nonnegative integer")
    if revision != current_revision:
        raise PlannerError(
            ErrorCode.revision_conflict,
            "this document changed; read it again before saving",
            {"expected_revision": revision, "current_revision": current_revision},
        )
    return {key: value for key, value in body.items() if key != "expected_revision"}
