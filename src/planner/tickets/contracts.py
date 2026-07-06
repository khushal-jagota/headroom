"""Ticket domain shapes: the state machine order, the four fields, the grant pair,
and the ticket row. Stdlib only; Priority/Project are imported from core (shared
vocabulary), never redeclared."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal, TypedDict

from planner.core.contracts import Priority, Project

# §3.3 ticket title length cap. The DDL carries the matching literal
# `CHECK (length(title) <= 200)` as the DB-level backstop; this constant is the
# single enforcement source the write paths pass to admission.validate_title.
TITLE_MAX_CHARS: Final = 200


class TicketState(StrEnum):        # §4.1, exact order
    needs_success = "needs_success"
    needs_approach = "needs_approach"
    needs_plan = "needs_plan"
    in_progress = "in_progress"
    needs_review = "needs_review"
    done = "done"
    dropped = "dropped"            # terminal, human-only, outside the linear order


# Linear pipeline order (dropped excluded). Index comparison implements "<= ceiling".
# ceiling and next_ceiling values are restricted to members of this tuple (never dropped).
STATE_ORDER: Final[tuple[TicketState, ...]] = (
    TicketState.needs_success, TicketState.needs_approach, TicketState.needs_plan,
    TicketState.in_progress, TicketState.needs_review, TicketState.done,
)


class FieldName(StrEnum):          # §4.2 — the exactly-four field keys
    success = "success"
    approach = "approach"
    plan = "plan"
    result = "result"


class AtCap(StrEnum):              # §4.3
    stop = "stop"
    propose = "propose"


# §4.2 table — gating field per pre-terminal state. needs_review has none (human approve).
GATING_FIELD: Final[dict[TicketState, FieldName]] = {
    TicketState.needs_success: FieldName.success,
    TicketState.needs_approach: FieldName.approach,
    TicketState.needs_plan: FieldName.plan,
    TicketState.in_progress: FieldName.result,
}

# §4.2 table — accepted proposal advances to. in_progress -> needs_review is the
# default route; the ceiling=done special case (§4.4.5) is resolution-engine logic,
# not a second table entry.
ADVANCE_TARGET: Final[dict[TicketState, TicketState]] = {
    TicketState.needs_success: TicketState.needs_approach,
    TicketState.needs_approach: TicketState.needs_plan,
    TicketState.needs_plan: TicketState.in_progress,
    TicketState.in_progress: TicketState.needs_review,
}


@dataclass(frozen=True)
class Proposal:                    # §4.2 proposal slot
    body: str
    proposed_by: str               # actor string: "agent", run id context, or PLAN_ACTOR
    created_at: int


@dataclass
class FieldSlot:                   # §4.2 — one of the four field objects
    value: str | None = None       # canonical; resolution engine is the only writer
    proposal: Proposal | None = None
    notes: str | None = None       # free guidance; result.notes = review notes (§4.2)


@dataclass
class TicketFields:                # tickets.fields JSON column, exactly four keys
    success: FieldSlot = field(default_factory=FieldSlot)
    approach: FieldSlot = field(default_factory=FieldSlot)
    plan: FieldSlot = field(default_factory=FieldSlot)
    result: FieldSlot = field(default_factory=FieldSlot)


# --- the grant pair (§4.4.7) ---
NO_FURTHER: Final = "none"                     # wire sentinel: ceiling = the newly entered state
NextCeiling = TicketState | Literal["none"]    # valid TicketState values are STATE_ORDER members


@dataclass(frozen=True)
class GrantPair:                   # required on every human accept/edit-accept
    next_ceiling: NextCeiling
    at_cap: AtCap


# --- request bodies (§9 wire shapes) ---
# Every key is optional on the wire: an absent key takes the documented default,
# unknown keys are ignored. The api layer marshals the raw JSON dict into these
# shapes; a null or wrong-typed value raises ErrorCode.validation. Enum-valued
# keys carry the string form and are parsed against the contract enums in api.


class CreateTicketBody(TypedDict, total=False):   # POST /tickets
    title: str                     # default ""
    priority: str | None           # Priority value; default P3
    deadline: str | None           # ISO date
    project: str | None            # Project value
    sprint_id: str | None
    sprint_item_id: str | None


class ProposeBody(TypedDict, total=False):        # POST /tickets/{id}/propose/{field}
    body: str                      # default ""


class AcceptBody(TypedDict, total=False):         # POST /tickets/{id}/accept/{field}
    edited_body: str | None        # human edit applied before resolution
    next_ceiling: str | None       # TicketState value or NO_FURTHER; grant pair (§4.4.7)
    at_cap: str | None             # AtCap value; grant pair (§4.4.7)


class NoteBody(TypedDict, total=False):           # PUT /tickets/{id}/notes/{field}
    note: str | None               # null clears the note


class RecapBody(TypedDict, total=False):          # PUT /tickets/{id}/recap
    body: str                      # default ""


class ValueEditBody(TypedDict, total=False):      # PUT /tickets/{id}/value/{field}
    body: str                      # default ""


class GrantBody(TypedDict, total=False):          # POST /tickets/{id}/grant
    ceiling: str | None            # TicketState value; route requires it (grant_missing)
    at_cap: str | None             # AtCap value; route requires it (grant_missing)


class StateBody(TypedDict, total=False):          # POST /tickets/{id}/state
    to: str                        # TicketState value; required (default "" is rejected)


class LinkBody(TypedDict, total=False):           # POST /links (ticket-anchored, homed here)
    from_id: str                   # required (default "" fails endpoint checks)
    to_id: str                     # required (default "" fails endpoint checks)
    kind: str                      # LinkKind value; required (default "" is rejected)


@dataclass
class Ticket:                      # §3.3 — column names match exactly
    id: str
    title: str                     # <= TITLE_MAX_CHARS (200), every write path
    state: TicketState
    priority: Priority             # default P3
    deadline: str | None           # ISO date
    project: Project | None        # NULL when parented (derived)
    sprint_item_id: str | None
    sprint_id: str | None          # writable only when sprint_item_id IS NULL
    recap: str                     # writable only past needs_success
    ceiling: TicketState           # default needs_success (R2); restricted to STATE_ORDER
    at_cap: AtCap                  # default propose (R2)
    auto_blocked: bool
    consecutive_failures: int      # §7.5
    chat_session_key: str | None
    alias: str | None              # migration "Ticket ID:" (§12), unique when present
    fields: TicketFields
    claim_lock: str | None         # §7.3 claim token; NULL = unclaimed
    claim_expires: int | None
    created_at: int
    updated_at: int
