"""Workspace ownership shared by the server and local executors."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

RETENTION_SECONDS = 7 * 24 * 60 * 60
WORKTREE_DIRECTORY = "worktree"
OWNERSHIP_FILE = ".panels-workspace.json"


@dataclass(frozen=True)
class WorkspaceState:
    ticket_id: str
    ended_at: int | None
    deleted: bool


@dataclass(frozen=True)
class WorkspaceIdentity:
    machine_id: str
    workspace_id: str


@dataclass
class CleanupResult:
    removed: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


type WorkspaceRetirementOperation = Callable[
    [str, WorkspaceIdentity, Callable[[], Path | str]], Path | str
]
