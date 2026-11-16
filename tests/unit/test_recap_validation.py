"""A recap is one short plain line: capped, and never names an internal ID."""

from __future__ import annotations

import pytest

from planner.core.contracts import PlannerError
from planner.core.ids import new_id
from planner.tickets.logic.recap import RECAP_MAX_CHARS, validate_recap

ACCEPTED = [
    "",
    "The rules await approval before the owner can publish them.",
    "Waiting on review of the defaced banner fix in 2026.",
    "Phone 5551234567 is not a commit.",
    "Source control and goal setting are settled.",
]
REFUSED_IDS = [
    f"Ticket {new_id('t')} awaits review.",
    f"Manager {new_id('si')} is blocked.",
    f"Run {new_id('run')} failed.",
    "Merged as 2f417385a today.",
    "Fixed in 2f417385a4b1c9d0e8f7a6b5c4d3e2f1a0b9c8d7.",
    "Chat 0b6e3c1e-6f0a-4a39-9d0e-3f6f5b7d8a21 stalled.",
]


@pytest.mark.parametrize("text", ACCEPTED)
def test_ordinary_text_passes(text: str) -> None:
    validate_recap(text)


@pytest.mark.parametrize("text", REFUSED_IDS)
def test_ids_are_refused_with_one_line_fix(text: str) -> None:
    with pytest.raises(
        PlannerError, match=r"^Recap names an ID \(.+\)\. Say what .* rewrite it\.$"
    ):
        validate_recap(text)


def test_cap_counts_trimmed_text() -> None:
    validate_recap(" " + "a" * RECAP_MAX_CHARS + " ")
    with pytest.raises(PlannerError, match=r"Recap is 81 characters\. Rewrite it in 80 or fewer\."):
        validate_recap("a" * (RECAP_MAX_CHARS + 1))
