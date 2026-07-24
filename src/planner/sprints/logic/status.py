"""Derived sprint-item status.

The sprint item itself stores no status. Its status is a pure rollup from open
blocking links and non-dropped child tickets.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

from planner.sprints.contracts import ItemStatus


class SprintItemChildStatus(NamedTuple):
    stage: str
    ticket_status: str
    blocked: bool
    stage_in_progress: bool


_DROPPED_STAGE = "dropped"
_DONE_STAGE = "done"
_IN_PROGRESS_TICKET_STATUSES = frozenset(
    {
        "agent_running_step",
        "awaiting_approval",
        "proposal_discussion",
        "user_takeover",
        "paired_work",
    }
)
_BLOCKED_TICKET_STATUS = "errored"


def derive_sprint_item_status(
    *,
    directly_blocked: bool,
    children: Iterable[SprintItemChildStatus],
) -> ItemStatus:
    non_dropped = [child for child in children if child.stage != _DROPPED_STAGE]
    if non_dropped and all(child.stage == _DONE_STAGE for child in non_dropped):
        return ItemStatus.done
    if any(
        child.ticket_status in _IN_PROGRESS_TICKET_STATUSES or child.stage_in_progress
        for child in non_dropped
    ):
        return ItemStatus.in_progress
    if directly_blocked:
        return ItemStatus.blocked
    if any(child.blocked or child.ticket_status == _BLOCKED_TICKET_STATUS for child in non_dropped):
        return ItemStatus.blocked
    return ItemStatus.todo
