"""Item status-transition permissions (§3.2, §4.4.7). Pure: returns a verdict enum;
never raises, never writes. The data layer maps each verdict to an error or a write."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from planner.sprints.contracts import (
    AGENT_ITEM_TRANSITIONS,
    PROPOSAL_ONLY_STATUSES,
    ItemStatus,
)


class TransitionVerdict(StrEnum):
    allowed = "allowed"
    forbidden = "forbidden"               # not an agent/human edge, or into a proposal-only status
    missing_blockers = "missing_blockers"  # -> blocked with an empty blocked_by list


def classify_agent_transition(
    from_status: ItemStatus,
    to_status: ItemStatus,
    blocked_by: Sequence[str],
) -> TransitionVerdict:
    if to_status in PROPOSAL_ONLY_STATUSES:
        return TransitionVerdict.forbidden
    if (from_status, to_status) not in AGENT_ITEM_TRANSITIONS:
        return TransitionVerdict.forbidden
    if to_status is ItemStatus.blocked and not blocked_by:
        return TransitionVerdict.missing_blockers
    return TransitionVerdict.allowed


def classify_human_transition(
    from_status: ItemStatus,
    to_status: ItemStatus,
    blocked_by: Sequence[str],
) -> TransitionVerdict:
    if to_status in PROPOSAL_ONLY_STATUSES:
        return TransitionVerdict.forbidden
    # done is terminal: entered only via accepted proposal (§4.4.7), no exit edge.
    # deferred_next_sprint stays human-resumable, or deferral would be a dead end.
    if from_status is ItemStatus.done:
        return TransitionVerdict.forbidden
    if from_status == to_status:
        return TransitionVerdict.forbidden
    if to_status is ItemStatus.blocked and not blocked_by:
        return TransitionVerdict.missing_blockers
    return TransitionVerdict.allowed
