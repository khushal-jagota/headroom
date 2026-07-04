"""§7.2 dispatch ordering key: priority band, then deadline (ascending, NULLs last),
then created_at ascending. Stdlib + contracts only; callers compose the sort themselves
(sorted(..., key=ordering_key)) so this module imports no sibling logic module."""

from __future__ import annotations

from typing import Final

from planner.core.contracts import Priority
from planner.dispatch.contracts import DispatchCandidate

_PRIORITY_RANK: Final[dict[Priority, int]] = {
    Priority.P0: 0,
    Priority.P1: 1,
    Priority.P2: 2,
    Priority.P3: 3,
}


def ordering_key(candidate: DispatchCandidate) -> tuple[int, int, str, int]:
    """§7.2 ordering: priority (P0 first), deadline ascending with NULLs last,
    created_at ascending. ISO date strings compare lexicographically ==
    chronologically; the NULL flag (0 dated / 1 undated) puts every dated ticket
    before every undated one regardless of the string component."""
    return (
        _PRIORITY_RANK[candidate.priority],
        0 if candidate.deadline is not None else 1,
        candidate.deadline or "",
        candidate.created_at,
    )
