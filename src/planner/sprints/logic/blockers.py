"""Legacy blockers_cleared helper. Current reads use core.links.blocker_summary;
this remains for callers that pass already-resolved ticket states."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

_CLEARED_TICKET_STATES: Final = {"done", "dropped"}


def blockers_cleared(
    blocked_by: Sequence[str],
    ticket_states: Mapping[str, str],
) -> bool:
    if not blocked_by:
        return False
    return all(ticket_states.get(tid) in _CLEARED_TICKET_STATES for tid in blocked_by)
