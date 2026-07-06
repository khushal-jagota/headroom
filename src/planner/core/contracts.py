"""Shared vocabulary used across every domain: cross-domain enums, the event
kinds, the link kinds, the two infrastructure shapes (event row, link row), and
the structured-error contract (ErrorCode, PlannerError) that pure logic raises.

Stdlib only. Nothing here imports another planner module."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

JsonDict = dict[str, Any]  # event payloads, adapter blobs
UnixTime = int             # unix seconds


class Project(StrEnum):                     # SPEC §3.2, exact strings (capitalised as in SPEC)
    Vylo = "Vylo"
    Tribe = "Tribe"
    Learning = "Learning"
    Other = "Other"


class Priority(StrEnum):                     # SPEC §3.2/§3.3 — homed in core (shared vocabulary)
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class LinkKind(StrEnum):                     # SPEC §3.6, exact strings
    belongs_to = "belongs_to"                # ticket -> sprint item
    parent_child = "parent_child"            # ticket -> ticket
    blocks = "blocks"                        # ticket -> ticket | ticket -> sprint item
    relates = "relates"                      # any -> any


class EventKind(StrEnum):
    # --- named explicitly in SPEC ---
    state_changed = "state_changed"                  # §4.4.6 {from, to, cause}
    proposal_accepted = "proposal_accepted"          # §4.4.2/4 {field, body, resolved_by, edited}
    proposal_superseded = "proposal_superseded"      # §4.4.1 {field, replaced_body}
    day_ticket_removed = "day_ticket_removed"        # §3.4 {ticket_id}
    day_closed = "day_closed"                        # §6.2 {done_count, not_done_count}
    auto_blocked = "auto_blocked"                    # §7.5 {consecutive_failures}

    # --- supplemental: creation, one per entity ---
    ticket_created = "ticket_created"
    sprint_created = "sprint_created"
    sprint_item_created = "sprint_item_created"
    idea_created = "idea_created"
    day_created = "day_created"                      # §3.4 materialization

    # --- supplemental: proposals and fields ---
    proposal_filed = "proposal_filed"                # {field, body, proposed_by}
    note_updated = "note_updated"                    # §4.2 notes slot {field}
    recap_updated = "recap_updated"                  # §3.3
    grant_changed = "grant_changed"                  # {ceiling, at_cap, cause}
    field_value_edited = "field_value_edited"        # {field, body}

    # --- supplemental: plain field updates (§3.2 "event-logged" updates) ---
    ticket_updated = "ticket_updated"                # {field, from, to} plain edits
    item_updated = "item_updated"
    sprint_updated = "sprint_updated"
    day_updated = "day_updated"                      # notes/brief manual edits
    item_status_changed = "item_status_changed"      # {from, to, cause} incl. agent todo<->active

    # --- supplemental: day lifecycle ---
    day_ticket_added = "day_ticket_added"            # {ticket_id, position, cause}
    boundary_failed = "boundary_failed"              # §6.2 adapter failure/timeout {error}

    # --- supplemental: runs and claims ---
    run_started = "run_started"                      # {run_id, pid}
    run_closed = "run_closed"                        # {run_id, status, summary}
    claim_heartbeat = "claim_heartbeat"              # {run_id, claim_expires}
    claim_reclaimed = "claim_reclaimed"              # {run_id, reason: "expired"|"dead_pid"}
    auto_block_cleared = "auto_block_cleared"        # human unblock action

    # --- supplemental: links, chat ---
    link_added = "link_added"                        # {from_id, to_id, kind}
    link_removed = "link_removed"
    chat_session_created = "chat_session_created"    # {session_key}


@dataclass(frozen=True)
class EventRow:                              # a row read back from the append-only events table
    id: int
    entity_id: str
    kind: str
    payload: JsonDict
    created_at: int


@dataclass(frozen=True)
class Link:                                  # SPEC §3.6 links row
    from_id: str
    to_id: str
    kind: LinkKind


# --- structured errors (SPEC §14: pure logic imports these from contracts) ------
# Every domain rejection raises PlannerError with a stable code; the API renders
# it as {"error": {code, message, detail}} and the CLI keys its exit code off the
# presence of that envelope.


class ErrorCode(StrEnum):
    at_cap_stop = "at_cap_stop"                    # §4.3 agent proposal at ceiling with stop
    grant_missing = "grant_missing"                # §4.4.7 accept without the full pair
    grant_invalid = "grant_invalid"                # next_ceiling before the new state / unknown
    stale_claim = "stale_claim"                    # §7.6 stale/foreign claim; detail names it
    recap_too_early = "recap_too_early"            # §3.3 recap write at needs_success
    title_too_long = "title_too_long"              # §3.3 > title_max_chars
    sprint_overlap = "sprint_overlap"              # §3.1 overlapping date ranges
    link_cycle = "link_cycle"                      # §3.6 blocks/parent_child transitive cycle
    link_invalid = "link_invalid"                  # self-link, second belongs_to, bad endpoints
    sprint_derived = "sprint_derived"              # §3.3 sprint_id write on a parented ticket
    item_transition_forbidden = "item_transition_forbidden"  # §3.2 agent direct done/deferred
    agent_forbidden = "agent_forbidden"            # claim/agent request hits a human-only action
    gateway_offline = "gateway_offline"            # §11
    not_found = "not_found"
    validation = "validation"                      # generic input validation


class PlannerError(Exception):
    def __init__(self, code: ErrorCode, message: str, detail: JsonDict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail: JsonDict = detail if detail is not None else {}

    def to_payload(self) -> JsonDict:
        return {"error": {"code": self.code.value, "message": self.message, "detail": self.detail}}
