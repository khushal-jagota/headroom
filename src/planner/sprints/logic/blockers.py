"""blockers_cleared derivation (§3.2). True iff there are blockers and every one
reaches ticket-state 'done'. Pure; computed on read, never stored."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

_TICKET_STATE_DONE: Final = "done"


def blockers_cleared(
    blocked_by: Sequence[str],
    ticket_states: Mapping[str, str],
) -> bool:
    if not blocked_by:
        return False
    return all(ticket_states.get(tid) == _TICKET_STATE_DONE for tid in blocked_by)
