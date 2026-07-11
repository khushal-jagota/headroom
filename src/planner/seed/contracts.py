"""Seed/migration shapes: the §12 mapping tables (targeting the real enums), the
parsed intermediates the parser emits, and the migration report. Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from planner.core.contracts import Priority
from planner.tickets.contracts import TicketState

# §12 mapping tables — exact source strings on the left.
TRACKING_ITEM_SECTIONS: Final[frozenset[str]] = frozenset(
    {"Todo", "In Progress", "Done", "Blocked", "Deferred"}
)

READINESS_MAP: Final[dict[str, TicketState]] = {
    "Concepts": TicketState.needs_success,
    "Needs Shaping": TicketState.needs_approach,
    "Ready": TicketState.needs_plan,
    "In Progress": TicketState.needs_implementation,
}

# `Priority:` labels are literal P0..P3 in the source (verified in the snapshot) — identity map.
PRIORITY_MAP: Final[dict[str, Priority]] = {p.value: p for p in Priority}

# `Urgency:` is present-but-empty throughout the snapshot. Mapped as a fallback only:
# consulted when a Priority label is absent; empty/unknown values are ignored (P3 default applies).
URGENCY_MAP: Final[dict[str, Priority]] = {
    "critical": Priority.P0, "high": Priority.P1, "medium": Priority.P2, "low": Priority.P3,
}   # keys compared case-insensitively

# Legacy markdown project headings/labels from the v1 planning files.
PROJECT_NAMES: Final[tuple[str, ...]] = ("Vylo", "Tribe", "Learning", "Other")
PROJECT_MAP: Final[dict[str, str]] = {name: name for name in PROJECT_NAMES}
DEFAULT_PROJECT_NAME: Final = "Other"

# `Mode:` is dropped (§12); the parser records it nowhere but it is NOT a skipped
# section (it is a recognised, deliberately-discarded field).


# --- parsed intermediates (parser output, importer input; no DB types) ---

@dataclass
class ParsedSprint:
    name: str
    date_start: str
    date_end: str
    limiting_factor: str = ""
    primary_bet: str = ""
    supports: str = ""
    premortem: str = ""
    outcomes: str = ""
    solo_reflection: str = ""
    joint_discussion: str = ""
    updates_to_thinking: str = ""
    carry_forward: str = ""


@dataclass
class ParsedItem:                  # sprint-tracking.md item or deferred.md item
    title: str
    priority: Priority
    project: str
    body: str = ""
    deadline: str | None = None
    deferred: bool = False         # True -> sprint_id stays NULL


@dataclass
class ParsedTicket:                # workspace.md ticket
    title: str
    state: TicketState
    priority: Priority
    alias: str | None = None       # "Ticket ID:"
    chat_session_key: str | None = None   # "Chat ID:"
    body: str = ""                 # -> ticket.kickoff_note intake context
    success: str | None = None
    approach: str | None = None
    item_title: str | None = None  # link target for unambiguous title match


@dataclass
class ParsedIdea:
    title: str
    body: str = ""
    project: str | None = None


@dataclass(frozen=True)
class SkippedSection:              # §12: silent drops forbidden — every skip is enumerated
    source_file: str               # path relative to the seed source dir
    heading: str | None            # nearest heading, if any
    reason: str                    # human-readable why it could not be parsed
    excerpt: str                   # first ~120 chars of the skipped text


@dataclass
class MigrationReport:             # printed, and structured under --json
    sprints: int = 0
    sprint_items: int = 0          # items with a sprint
    deferred_items: int = 0        # items imported with sprint_id NULL
    tickets: int = 0
    ideas: int = 0
    links: int = 0                 # belongs_to links made by title match
    duplicates_skipped: int = 0    # idempotent re-run hits (alias/title match)
    skipped: list[SkippedSection] = field(default_factory=list)
