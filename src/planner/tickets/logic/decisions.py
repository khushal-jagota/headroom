"""Internal decision shapes: a Decision is what a resolution function returns —
the replacement fields/stage/scope plus the engine's own ordered statement of what
the write does, which the write path reads to settle the consequences. These are
logic-layer only, never exposed over the wire."""

from __future__ import annotations

from dataclasses import dataclass

from planner.core.contracts import EventKind, JsonDict
from planner.tickets.contracts import AtCap, TicketFields


@dataclass(frozen=True)
class EventSpec:
    kind: EventKind
    payload: JsonDict


@dataclass(frozen=True)
class Decision:
    events: tuple[EventSpec, ...]
    new_fields: TicketFields | None = None  # replacement fields object; None = untouched
    new_stage: str | None = None  # Stage id; None = no transition
    new_ceiling: str | None = None  # ceiling id; None = scope untouched
    new_at_cap: AtCap | None = None  # None = scope untouched
