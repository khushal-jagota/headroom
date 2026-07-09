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
    ticket_deleted = "ticket_deleted"                # hard-delete audit + affected resources

    # --- supplemental: creation, one per entity ---
    ticket_created = "ticket_created"
    sprint_created = "sprint_created"
    sprint_item_created = "sprint_item_created"
    idea_created = "idea_created"
    day_created = "day_created"                      # §3.4 materialization
    project_created = "project_created"

    # --- supplemental: proposals and fields ---
    proposal_filed = "proposal_filed"                # {field, body, proposed_by}
    approval_returned = "approval_returned"          # {kind, field?} returned for revision
    note_updated = "note_updated"                    # field user_note slot {field}
    recap_updated = "recap_updated"                  # §3.3
    scope_changed = "scope_changed"                  # {ceiling, at_cap, cause}
    field_value_edited = "field_value_edited"        # {field, body}

    # --- supplemental: plain field updates (§3.2 "event-logged" updates) ---
    ticket_updated = "ticket_updated"                # {field, from, to} plain edits
    item_updated = "item_updated"
    sprint_updated = "sprint_updated"
    day_updated = "day_updated"                      # notes/brief manual edits
    project_updated = "project_updated"
    item_children_changed = "item_children_changed"  # {ticket_id, reason}

    # --- supplemental: day lifecycle ---
    day_ticket_added = "day_ticket_added"            # {ticket_id, position, cause}

    # --- supplemental: durable ticket runtime/parking status ---
    ticket_status_changed = "ticket_status_changed"  # {ticket_status, optional error}

    # --- supplemental: links, chat ---
    link_added = "link_added"                        # {from_id, to_id, kind}
    link_removed = "link_removed"
    chat_session_created = "chat_session_created"    # {session_key}
    chat_message_recorded = "chat_message_recorded"  # {message_id, turn_id, role}
    chat_turn_started = "chat_turn_started"          # {turn_id, origin, mode, phase}
    chat_turn_updated = "chat_turn_updated"          # {turn_id, phase/activity/session}
    chat_turn_finished = "chat_turn_finished"        # {turn_id, status, optional error}


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
    scope_missing = "scope_missing"                # §4.4.7 accept without the full pair
    scope_invalid = "scope_invalid"                # next_ceiling before the new state / unknown
    stale_claim = "stale_claim"                    # §7.6 stale/foreign claim; detail names it
    recap_too_early = "recap_too_early"            # §3.3 recap write at needs_success
    title_too_long = "title_too_long"              # §3.3 > title_max_chars
    sprint_overlap = "sprint_overlap"              # §3.1 overlapping date ranges
    link_cycle = "link_cycle"                      # §3.6 blocks/parent_child transitive cycle
    link_invalid = "link_invalid"                  # self-link, second belongs_to, bad endpoints
    sprint_derived = "sprint_derived"              # §3.3 sprint_id write on a parented ticket
    agent_forbidden = "agent_forbidden"            # claim/agent request hits a human-only action
    gateway_offline = "gateway_offline"            # §11
    already_running = "already_running"            # Hermes 4009 session busy
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
