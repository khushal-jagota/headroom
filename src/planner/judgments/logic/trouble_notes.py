"""Pure validation and normalization for worker trouble notes."""

from __future__ import annotations

from planner.core.errors import ErrorCode, PlannerError

TROUBLE_NOTE_MAX_CHARS = 500


def normalize_trouble_note(body: object) -> str:
    if not isinstance(body, str):
        raise PlannerError(
            ErrorCode.validation,
            "trouble note must be a string",
            {"body": body},
        )
    normalized = body.strip()
    if not normalized:
        raise PlannerError(ErrorCode.validation, "trouble note must not be empty", {})
    if "\n" in normalized or "\r" in normalized:
        raise PlannerError(
            ErrorCode.validation,
            "trouble note must fit on one line",
            {},
        )
    if len(normalized) > TROUBLE_NOTE_MAX_CHARS:
        raise PlannerError(
            ErrorCode.validation,
            f"trouble note must be {TROUBLE_NOTE_MAX_CHARS} characters or fewer",
            {"max_chars": TROUBLE_NOTE_MAX_CHARS},
        )
    return normalized
