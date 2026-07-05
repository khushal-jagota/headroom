"""Dispatch domain shapes: run rows, run statuses, and the pure input row for the
eligibility + ordering functions (§7.2). Stdlib only; enums imported from their
home domains."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypedDict

from planner.core.contracts import Priority
from planner.tickets.contracts import AtCap, TicketState


class RunStatus(StrEnum):          # §7.3 six + spawn_failed (§7.5)
    running = "running"
    done = "done"
    blocked = "blocked"
    crashed = "crashed"
    timed_out = "timed_out"
    reclaimed = "reclaimed"
    spawn_failed = "spawn_failed"


# Statuses that count as failures for the circuit breaker (§7.5).
FAILURE_STATUSES: Final[frozenset[RunStatus]] = frozenset({
    RunStatus.crashed, RunStatus.timed_out, RunStatus.spawn_failed,
})

# Outcomes an agent may pass to `plan run close --outcome` (terminal, agent-reportable).
AGENT_CLOSE_OUTCOMES: Final[frozenset[RunStatus]] = frozenset({
    RunStatus.done, RunStatus.blocked,
})


# --- request bodies (§9 wire shapes) ---
# Every key is optional on the wire: an absent key takes the documented default,
# unknown keys are ignored. The api layer marshals the raw JSON dict into these
# shapes; a null or wrong-typed value raises ErrorCode.validation.


class CloseRunBody(TypedDict, total=False):       # POST /runs/{id}/close
    outcome: str                   # RunStatus value in AGENT_CLOSE_OUTCOMES; required
    summary: str | None


@dataclass
class Run:                         # §7.3 runs row — column names match
    id: str                        # run_<slug>
    ticket_id: str
    status: RunStatus
    started_at: int
    ended_at: int | None
    summary: str | None
    error: str | None
    pid: int | None


@dataclass(frozen=True)
class DispatchCandidate:
    """Input row for the pure eligibility + ordering functions (§7.2).
    Assembled by the data layer; logic never touches the DB."""

    ticket_id: str
    state: TicketState
    priority: Priority
    deadline: str | None           # ISO date, NULLs ordered last
    created_at: int
    ceiling: TicketState
    at_cap: AtCap
    auto_blocked: bool
    has_active_claim: bool         # claim_lock set and unexpired
    is_blocked: bool               # derived from links (§3.6)
    gating_pending: bool           # current gating field has a pending proposal
