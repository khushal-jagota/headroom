"""Shared vocabulary used across every domain: cross-domain enums,
the link kinds, the link row, and the
structured-error contract (ErrorCode, PlannerError) that pure logic raises.

Stdlib only. Nothing here imports another planner module."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal

JsonDict = dict[str, Any]  # structured errors and adapter data
UnixTime = int  # unix seconds


class PrincipalKind(StrEnum):
    """The complete set of people and work objects that can act in Panels."""

    owner = "owner"
    chief = "chief"
    sprint_item = "sprint_item"
    ticket = "ticket"


OWNER_PRINCIPAL_ID: Final = "owner"
CHIEF_PRINCIPAL_ID: Final = "chief"


@dataclass(frozen=True, slots=True)
class Principal:
    """One stable Panels identity, independent of an agent or conversation."""

    kind: PrincipalKind
    id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PrincipalKind):
            raise ValueError("principal kind must be a PrincipalKind")
        if not self.id or self.id != self.id.strip():
            raise ValueError("principal id must be non-empty and must not contain outer whitespace")
        singleton_id = {
            PrincipalKind.owner: OWNER_PRINCIPAL_ID,
            PrincipalKind.chief: CHIEF_PRINCIPAL_ID,
        }.get(self.kind)
        if singleton_id is not None and self.id != singleton_id:
            raise ValueError(f"{self.kind.value} principal id must be {singleton_id}")


OWNER_PRINCIPAL: Final = Principal(PrincipalKind.owner, OWNER_PRINCIPAL_ID)
CHIEF_PRINCIPAL: Final = Principal(PrincipalKind.chief, CHIEF_PRINCIPAL_ID)


def principal_legacy_actor(principal: Principal) -> str:
    """Serialize a Principal for unchanged audit rows and error payloads."""
    return {
        PrincipalKind.owner: "unattributed",
        PrincipalKind.chief: "chief",
        PrincipalKind.sprint_item: "sprint_item_supervisor",
        PrincipalKind.ticket: "worker",
    }[principal.kind]


class Priority(StrEnum):  # SPEC §3.2/§3.3 — homed in core (shared vocabulary)
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class LinkKind(StrEnum):  # the one explicit Ticket relationship
    blocks = "blocks"  # ticket -> ticket | ticket -> sprint item


@dataclass(frozen=True)
class Link:  # SPEC §3.6 links row
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
    scope_missing = "scope_missing"  # §4.4.7 accept without the full pair
    scope_invalid = "scope_invalid"  # next_ceiling before the new Stage / unknown
    stale_claim = "stale_claim"  # §7.6 stale/foreign claim; detail names it
    title_too_long = "title_too_long"  # §3.3 > title_max_chars
    sprint_overlap = "sprint_overlap"  # §3.1 overlapping date ranges
    link_cycle = "link_cycle"  # blocks active-cycle rejection
    link_invalid = "link_invalid"  # self-link, duplicate, or bad endpoints
    agent_forbidden = "agent_forbidden"  # attributed agent hits a direct-only action
    gateway_offline = "gateway_offline"  # §11
    already_running = "already_running"  # Hermes 4009 session busy
    not_found = "not_found"
    validation = "validation"  # generic input validation


class PlannerError(Exception):
    def __init__(self, code: ErrorCode, message: str, detail: JsonDict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail: JsonDict = detail if detail is not None else {}

    def to_payload(self) -> JsonDict:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "detail": self.detail,
            }
        }
