"""Adapter protocols and their request/result shapes: the three external
boundaries (spawn subprocess, boundary agent, chat gateway). Stdlib only.
The dependency arrow is core-adapters -> domain-contracts, never the reverse."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from planner.chat.contracts import ChatSendResult, GatewayStatus


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
    # Dispatcher dead-worker probe (§7.1/§7.3): the reclaim sweep's per-pid liveness
    # check. A real adapter can reap its own exited children within one tick.
    def is_pid_alive(self, pid: int) -> bool: ...
    # Per-tick housekeeping: reap/drop any exited children the sweep will not probe (runs
    # that closed by any path), so the server holds no zombies or stale handles.
    def reap_finished_children(self) -> None: ...


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
