"""The serializable effect vocabulary returned by plan-tree transforms. Effects
are DATA describing side effects; the data layer applies them. Frozen dataclasses
so the stage-4 runtime can store/serialize them (replan requests especially —
SPEC R5 latest-wins is applied later, not here). Stdlib + planner contract
modules only."""

from __future__ import annotations

from dataclasses import dataclass

from planner.core.contracts import EventKind, JsonDict


@dataclass(frozen=True)
class AddTicketToDay:
    """Append ticket_id to the day list if absent (§6.3). Application is
    idempotent — a ticket already on the list is not added twice (a17)."""

    ticket_id: str


@dataclass(frozen=True)
class EmitEvent:
    """Append one events row. kind is an EventKind; payload is JSON-ready."""

    kind: EventKind
    payload: JsonDict


@dataclass(frozen=True)
class ReplanRoot:
    """Request a full-tree replan (adapter.replan_root). Serialized/executed by
    the stage-4 runtime (R5 latest-wins); exposed here only as data."""


@dataclass(frozen=True)
class ReplanChild:
    """Request a single-node replan of the child at ``position``
    (adapter.replan_child). Same stage-4 handling as ReplanRoot."""

    position: int


Effect = AddTicketToDay | EmitEvent | ReplanRoot | ReplanChild
ReplanRequest = ReplanRoot | ReplanChild
