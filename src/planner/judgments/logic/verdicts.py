"""Pure validation and normalization for optional Ticket verdicts."""

from __future__ import annotations

from planner.core.errors import ErrorCode, PlannerError
from planner.judgments.contracts import Verdict


def require_finished_ticket(stage: str) -> None:
    if stage != "done":
        raise PlannerError(
            ErrorCode.validation,
            "a verdict can be changed only when the ticket is done",
            {"stage": stage},
        )


def normalize_verdict(rating: object, text: object) -> Verdict:
    if rating is not None and (
        isinstance(rating, bool)
        or not isinstance(rating, int)
        or rating not in range(1, 6)
    ):
        raise PlannerError(
            ErrorCode.validation,
            "verdict rating must be an integer from 1 to 5",
            {"rating": rating},
        )
    if text is not None and not isinstance(text, str):
        raise PlannerError(
            ErrorCode.validation,
            "verdict text must be a string",
            {"text": text},
        )
    normalized_text = text.strip() if text is not None else None
    return Verdict(rating=rating, text=normalized_text or None)
