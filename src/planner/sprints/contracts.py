"""Sprint domain shapes: sprints, sprint items, ideas, and the item status
proposal. Stdlib only; Priority/Project imported from core."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from planner.core.contracts import Priority, Project


class ItemStatus(StrEnum):         # §3.2
    todo = "todo"
    active = "active"
    done = "done"
    blocked = "blocked"
    deferred_next_sprint = "deferred_next_sprint"


# Agent-permitted direct transitions (§3.2): todo<->active, and -> blocked (with blockers).
AGENT_ITEM_TRANSITIONS: Final[frozenset[tuple[ItemStatus, ItemStatus]]] = frozenset({
    (ItemStatus.todo, ItemStatus.active),
    (ItemStatus.active, ItemStatus.todo),
    (ItemStatus.todo, ItemStatus.blocked),
    (ItemStatus.active, ItemStatus.blocked),
})

# Statuses reachable only via human-accepted proposal (§3.2).
PROPOSAL_ONLY_STATUSES: Final[frozenset[ItemStatus]] = frozenset({
    ItemStatus.done, ItemStatus.deferred_next_sprint,
})

# Freeze groups (§3.1): frozen-write checks compare against these tuples.
KICKOFF_FIELDS: Final[tuple[str, ...]] = ("limiting_factor", "primary_bet", "supports", "premortem")
REVIEW_FIELDS: Final[tuple[str, ...]] = (
    "outcomes", "solo_reflection", "joint_discussion", "updates_to_thinking", "carry_forward",
)


@dataclass(frozen=True)
class Addendum:                    # §3.1 weekly_addenda entry
    date: str                      # ISO date
    text: str


@dataclass(frozen=True)
class ItemStatusProposal:          # §3.2 status proposal — the item's single gating field
    to_status: ItemStatus          # must be in PROPOSAL_ONLY_STATUSES
    note: str | None               # optional rationale shown in Review
    proposed_by: str
    created_at: int


@dataclass
class Sprint:                      # §3.1
    id: str
    name: str
    date_start: str                # ISO, inclusive
    date_end: str                  # ISO, inclusive
    limiting_factor: str
    primary_bet: str
    supports: str
    premortem: str
    weekly_addenda: list[Addendum] = field(default_factory=list)   # append-only
    kickoff_frozen_at: int | None = None
    outcomes: str = ""
    solo_reflection: str = ""
    joint_discussion: str = ""
    updates_to_thinking: str = ""
    carry_forward: str = ""
    review_frozen_at: int | None = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class SprintItem:                  # §3.2
    id: str
    title: str
    body: str
    status: ItemStatus
    priority: Priority
    deadline: str | None
    project: Project
    current_state_note: str
    sprint_id: str | None          # NULL = backlog/deferred
    blocked_by: list[str] = field(default_factory=list)   # ticket ids; non-empty iff blocked
    status_proposal: ItemStatusProposal | None = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class Idea:                        # §3.5
    id: str
    title: str
    body: str
    project: Project | None
    created_at: int
    updated_at: int
