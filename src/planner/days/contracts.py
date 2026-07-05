"""Day domain shapes: the day row, the day-plan tree (one level of children in
v1), and the planning-date function signature. Stdlib only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import TypedDict


class NodeStatus(StrEnum):         # §6.3
    proposed = "proposed"
    accepted = "accepted"
    invalidated = "invalidated"


@dataclass
class PlanRoot:                    # §6.3 root node
    focus: str
    status: NodeStatus = NodeStatus.proposed


@dataclass
class PlanNode:                    # §6.3 child node
    ticket_id: str | None
    note: str
    status: NodeStatus
    position: int


@dataclass
class PlanTree:                    # days.plan JSON column: {root, children}
    root: PlanRoot
    children: list[PlanNode] = field(default_factory=list)   # one level in v1


@dataclass
class Day:                         # §3.4
    id: str                        # day_YYYY-MM-DD (planning date)
    brief: str
    notes: str
    plan: PlanTree | None
    chat_session_key: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class DayTicket:                   # day_tickets row (§3.4)
    day_id: str
    ticket_id: str
    position: int                  # contiguous from 0


# --- request bodies (§9 wire shapes) ---
# Every key is optional on the wire: an absent key takes the documented default,
# unknown keys are ignored. The api layer marshals the raw JSON dict into these
# shapes; a null or wrong-typed value raises ErrorCode.validation.


class DayPatchBody(TypedDict, total=False):       # PATCH /day/{date}
    brief: str | None              # absent/null = leave unchanged
    notes: str | None              # absent/null = leave unchanged


class AddDayTicketBody(TypedDict, total=False):   # POST /day/{date}/tickets
    ticket_id: str                 # required (default "" fails the existence guard)


class PlanNodeBody(TypedDict, total=False):       # POST /day/{date}/plan/{accept,invalidate}
    node: int | str | None         # "root" or a child position; route requires it


# Planning-date math (§6.1): implemented in days/logic/dates.py at stage 3.
# Signature is the contract: planning_date(now, boundary_hour) -> calendar date of
# (now - boundary_hour hours).
PlanningDateFn = Callable[[datetime, int], date]
