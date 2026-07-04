"""Structured errors. Every domain rejection raises PlannerError with a stable
code; the API renders it as {"error": {code, message, detail}} and the CLI keys
its exit code off the presence of that envelope."""

from __future__ import annotations

from enum import StrEnum

from planner.core.contracts import JsonDict


class ErrorCode(StrEnum):
    frozen_write = "frozen_write"                  # §5 kickoff/review writes after freeze
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
    db_not_empty = "db_not_empty"                  # §12 seed --demo on a non-empty DB
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
