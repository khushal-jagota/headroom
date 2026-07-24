"""Shared vocabulary used across every domain: cross-domain enums, the event
kinds, the link kinds, the two infrastructure shapes (event row, link row), and
the structured-error contract (ErrorCode, PlannerError) that pure logic raises.

Stdlib only. Nothing here imports another planner module."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

JsonDict = dict[str, Any]  # event payloads, adapter blobs
UnixTime = int             # unix seconds


class Priority(StrEnum):                     # SPEC §3.2/§3.3 — homed in core (shared vocabulary)
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class LinkKind(StrEnum):                     # the one explicit Ticket relationship
    blocks = "blocks"                        # ticket -> ticket | ticket -> sprint item


class EventKind(StrEnum):
    # --- named explicitly in SPEC ---
    stage_changed = "stage_changed"                  # {from_stage, to_stage, cause}
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
    kickoff_proposal_filed = "kickoff_proposal_filed"  # {title, kickoff_note, proposed_by}
    kickoff_accepted = "kickoff_accepted"            # {title, kickoff_note, resolved_by, edited}
    approval_returned = "approval_returned"          # {kind, field?} returned for revision
    note_updated = "note_updated"                    # field user_note slot {field}
    recap_updated = "recap_updated"                  # §3.3
    scope_changed = "scope_changed"                  # {ceiling, at_cap, cause}
    stage_ownership_changed = "stage_ownership_changed"  # ownership override set/clear
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
    employee_session_changed = "employee_session_changed"  # {employee_session_id}
    worker_settings_changed = "worker_settings_changed"  # Worker management settings changed
    ticket_conversation_projection_changed = "ticket_conversation_projection_changed"

    # --- supplemental: links and Employee execution ---
    link_added = "link_added"                        # {from_id, to_id, kind}
    link_removed = "link_removed"
    employee_step_started = "employee_step_started"  # {employee_step_id}


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


@dataclass(frozen=True)
class BlockedBySummaryRow:
    ticket_id: str
    title: str
    stage: str
    active: bool
    href: str


@dataclass(frozen=True)
class BlocksTargetSummaryRow:
    target_id: str
    target_kind: Literal["ticket", "sprint_item"]
    title: str
    active: bool
    href: str


@dataclass(frozen=True)
class BlockerSummary:
    blocked: bool
    blocked_by: tuple[BlockedBySummaryRow, ...]
    blocks: tuple[BlocksTargetSummaryRow, ...]


# --- structured errors (SPEC §14: pure logic imports these from contracts) ------
# Every domain rejection raises PlannerError with a stable code; the API renders
# it as {"error": {code, message, detail}} and the CLI keys its exit code off the
# presence of that envelope.


class ErrorCode(StrEnum):
    at_cap_stop = "at_cap_stop"                    # §4.3 agent proposal at ceiling with stop
    scope_missing = "scope_missing"                # §4.4.7 accept without the full pair
    scope_invalid = "scope_invalid"                # next_ceiling before the new Stage / unknown
    stale_claim = "stale_claim"                    # §7.6 stale/foreign claim; detail names it
    title_too_long = "title_too_long"              # §3.3 > title_max_chars
    sprint_overlap = "sprint_overlap"              # §3.1 overlapping date ranges
    link_cycle = "link_cycle"                      # blocks active-cycle rejection
    link_invalid = "link_invalid"                  # self-link, duplicate, or bad endpoints
    sprint_derived = "sprint_derived"              # §3.3 sprint_id write on a parented ticket
    agent_forbidden = "agent_forbidden"            # attributed agent hits a direct-only action
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
