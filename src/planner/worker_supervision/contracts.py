"""Exact process evidence and scoped cleanup for a Worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from planner.conversation.local_child_contracts import LocalHostBootIdentity


@dataclass(frozen=True, slots=True)
class WorkerProcess:
    pid: int
    birth: str
    parent: int
    group: int
    session: int
    zombie: bool = False


@dataclass(frozen=True, slots=True)
class CleanupResult:
    complete: bool
    reason: str
    scope: str = (
        "Linux cleanup requires empty delegated cgroup custody for all inherited descendants. "
        "Legacy or unsupported Linux custody retains a cleanup fence. Processes launched "
        "through external brokers are outside descendant custody. Mac cleanup covers recorded "
        "process incarnations and observed session members only, with an immediate matching "
        "microsecond birth check and an accepted check-to-signal race."
    )


@dataclass(frozen=True, slots=True)
class CleanupUncertainty:
    ticket_id: str | None
    conversation_id: str | None
    record_path: Path
    reason: str


class WorkerCleanupError(RuntimeError):
    """The owned processes do not have positive cleanup proof."""


@dataclass(frozen=True, slots=True)
class ReconciliationRecord:
    record_path: Path
    ticket_id: str | None
    conversation_id: str | None
    result: CleanupResult


@dataclass(frozen=True, slots=True)
class ReconciliationResult(CleanupResult):
    records: tuple[ReconciliationRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class InspectionContext:
    primitives: WorkerProcessPrimitives
    host: LocalHostBootIdentity


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    stable: dict[int, WorkerProcess]
    children: dict[int, list[WorkerProcess]]
    sessions: dict[int, list[WorkerProcess]]
    latest_sessions: dict[int, list[WorkerProcess]]


class WorkerProcessPrimitives(Protocol):
    owns_isolated_session_members: bool

    def host_boot(self, deadline: float) -> LocalHostBootIdentity: ...
    def snapshot(self, deadline: float) -> dict[int, WorkerProcess]: ...
    def is_absent(self, process: WorkerProcess, deadline: float) -> bool: ...
    def prepare_launch(self) -> None: ...
    def signal(self, process: WorkerProcess, signum: int, deadline: float) -> bool: ...
