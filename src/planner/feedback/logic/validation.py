"""Framework-free validation for feedback-note commands."""

from __future__ import annotations

from planner.core.errors import ErrorCode, PlannerError


def require_feedback_text(text: str) -> str:
    if not text.strip():
        raise PlannerError(ErrorCode.validation, "feedback text cannot be empty", {})
    return text


def require_feedback_ids(feedback_ids: list[str]) -> tuple[str, ...]:
    if not feedback_ids:
        raise PlannerError(ErrorCode.validation, "feedback use requires at least one note", {})
    if any(not feedback_id.strip() for feedback_id in feedback_ids):
        raise PlannerError(ErrorCode.validation, "feedback ids cannot be empty", {})
    if len(set(feedback_ids)) != len(feedback_ids):
        raise PlannerError(ErrorCode.validation, "feedback ids must be unique", {})
    return tuple(feedback_ids)


def require_page_context(
    page_address: str | None, page_label: str | None
) -> tuple[str | None, str | None]:
    both_absent = page_address is None and page_label is None
    both_present = (
        page_address is not None
        and bool(page_address.strip())
        and page_label is not None
        and bool(page_label.strip())
    )
    if not both_absent and not both_present:
        raise PlannerError(
            ErrorCode.validation,
            "feedback page address and label must be provided together",
            {},
        )
    if page_address is not None and (
        not page_address.startswith("#/")
        or len(page_address) <= 2
        or page_address != page_address.strip()
        or "#" in page_address[1:]
        or any(ord(character) < 32 for character in page_address)
    ):
        raise PlannerError(
            ErrorCode.validation,
            "feedback page address must be an internal Panels hash route",
            {},
        )
    return page_address, page_label
