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
        "Recorded process incarnations and observed session members only. "
        "Unseen detached descendants and processes that change user before observation "
        "are outside this proof. Linux signals use pidfds. "
        "Mac signals use an immediate matching microsecond birth check, with an accepted "
        "check-to-signal race."
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


class WorkerProcessPrimitives(Protocol):
    owns_isolated_session_members: bool

    def host_boot(self, deadline: float) -> LocalHostBootIdentity: ...
    def snapshot(self, deadline: float) -> dict[int, WorkerProcess]: ...
    def is_absent(self, process: WorkerProcess, deadline: float) -> bool: ...
    def prepare_launch(self) -> None: ...
    def signal(self, process: WorkerProcess, signum: int, deadline: float) -> bool: ...
