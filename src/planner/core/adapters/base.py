"""Adapter protocols and their request/result shapes: the three external
boundaries (spawn subprocess, boundary/replan agent, chat gateway). Stdlib only.
The dependency arrow is core-adapters -> domain-contracts, never the reverse."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from planner.chat.contracts import ChatSendResult, GatewayStatus
from planner.days.contracts import PlanNode, PlanTree


@dataclass(frozen=True)
class SpawnRequest:                # §7.4 — everything the subprocess boundary needs
    ticket_id: str
    run_id: str
    claim: str
    server_url: str
    log_path: str                  # per-run log file under logs_dir
    hermes_bin: str
    profile: str                   # R3: "default"
    skill: str                     # R3: "planning-worker"


@dataclass(frozen=True)
class SpawnResult:
    ok: bool
    pid: int | None = None         # set when ok
    error: str | None = None       # set when not ok -> run status spawn_failed


class SpawnAdapter(Protocol):
    def spawn(self, request: SpawnRequest) -> SpawnResult: ...


@dataclass(frozen=True)
class BoundaryInputs:              # §6.2 deterministic-pass outputs, DB-internal only (R4)
    planning_date: str             # ISO
    carryover: list[dict[str, Any]]      # ticket digests: {id, title, state, priority}
    overdue: list[dict[str, Any]]        # same digest shape, tickets and items
    approvals_digest: list[dict[str, Any]]   # {entity_id, kind, waiting_since}


@dataclass(frozen=True)
class BoundaryJudgment:
    brief_markdown: str
    plan_tree: PlanTree


class BoundaryAdapter(Protocol):
    # Timeout (boundary_timeout_seconds, 60s) is owned by the CALLER — the boundary
    # scheduler wraps calls; adapters just do the work or raise.
    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment: ...
    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree: ...
    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode: ...


class GatewayAdapter(Protocol):
    def status(self) -> GatewayStatus: ...
    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult: ...
