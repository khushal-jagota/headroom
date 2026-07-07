"""Internal decision shapes: a Decision is what a resolution function returns —
the replacement fields/state/scope plus the ordered events to append. These are
logic-layer only, never exposed over the wire."""

from __future__ import annotations

from dataclasses import dataclass

from planner.core.contracts import EventKind, JsonDict
from planner.tickets.contracts import AtCap, TicketFields, TicketState


@dataclass(frozen=True)
class EventSpec:
    kind: EventKind
    payload: JsonDict


@dataclass(frozen=True)
class Decision:
    events: tuple[EventSpec, ...]
    new_fields: TicketFields | None = None    # replacement fields object; None = untouched
    new_state: TicketState | None = None      # None = no transition
    new_ceiling: TicketState | None = None    # None = scope untouched
    new_at_cap: AtCap | None = None           # None = scope untouched
