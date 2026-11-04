"""Shared inputs and recorded facts for historical attention reads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class GroupBy(StrEnum):
    project = "project"
    manager = "manager"
    ticket = "ticket"
    goal = "goal"


class ScopeKind(StrEnum):
    all = "all"
    project = "project"
    manager = "manager"
    ticket = "ticket"
    goal = "goal"
    chief = "chief"


class MetricStatus(StrEnum):
    complete = "complete"
    partial = "partial"
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class Scope:
    kind: ScopeKind = ScopeKind.all
    id: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    first_date: date | None = None
    last_date: date | None = None
    group_by: GroupBy = GroupBy.project
    scope: Scope = field(default_factory=Scope)
    sort: str = "owner_messages"
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class Destination:
    kind: str
    id: str
    title: str
    url: str | None
    project_id: str | None = None
    manager_id: str | None = None
    goal_id: str | None = None
    attribution_basis: str = "recorded"


@dataclass(frozen=True, slots=True)
class AttentionEvent:
    identity: str
    occurred_at: int
    kind: str
    destination: Destination


@dataclass(frozen=True, slots=True)
class GateInterval:
    identity: str
    destination: Destination
    opened_at: int | None
    observed_since: int
    closed_at: int | None


@dataclass(frozen=True, slots=True)
class Evidence:
    events: tuple[AttentionEvent, ...]
    gates: tuple[GateInterval, ...]
    history_started_at: int
    limitations: tuple[str, ...] = ()
    # Relation labels are metadata, never independent activity or owner events.
    destinations: tuple[Destination, ...] = ()
    message_history_started_at: int | None = None
    unattributed_message_times: tuple[int, ...] = ()
