"""Adapter protocols and their request/result shapes: the two external boundaries
(boundary agent, chat gateway). Stdlib only. The dependency arrow is core-adapters
-> domain-contracts, never the reverse.

(The old subprocess-spawn boundary — how the dispatcher launched a hermes worker —
is removed with the dispatcher; System B drives minds via the W1 gateway primitive
now, not this adapter.)"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from planner.chat.contracts import ChatSendResult, GatewayStatus


@dataclass(frozen=True)
class BoundaryInputs:              # §6.2 deterministic-pass outputs, DB-internal only (R4)
    planning_date: str             # ISO
    carryover: list[dict[str, Any]]      # ticket digests: {id, title, state, priority}
    overdue: list[dict[str, Any]]        # same digest shape, tickets and items
    approvals_digest: list[dict[str, Any]]   # {entity_id, kind, waiting_since}


@dataclass(frozen=True)
class BoundaryJudgment:              # §6.2 — the four overview fields the boundary fills
    focus: str                      # the one-line hero (plain text)
    brief_take: str                 # markdown
    watchout: str                   # markdown
    if_today_lands: str             # markdown


class BoundaryAdapter(Protocol):
    # Timeout (boundary_timeout_seconds, 60s) is owned by the CALLER — the boundary
    # scheduler wraps calls; adapters just do the work or raise.
    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment: ...


class GatewayAdapter(Protocol):
    def status(self) -> GatewayStatus: ...
    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult: ...
