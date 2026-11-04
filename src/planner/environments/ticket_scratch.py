"""Scratch that done and deleted Tickets leave on this host.

A Ticket's scratch is each top-level entry named for it in a scratch folder, and each
worktree of the Panels repository whose path or branch names it. Once a Ticket is done
or deleted, its scratch is removed, but never its work. An entry is kept and reported
when a checkout inside it has uncommitted changes, has commits that no remote has, or
belongs to a live Ticket. A branch is deleted only when the merge target contains it.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

SCRATCH_FOLDERS = ("task-tmp", "worktrees", "repository-worktrees")
# Older Workers put scratch in these home folders. They are cleaned the same way.
LEGACY_HOME_FOLDERS = ("pct", "panels-session-evidence")
MERGE_TARGET = "origin/staging"
# A Ticket's last Worker step can still be ending when the Ticket reaches done.
SETTLE_SECONDS = 600

_TICKET_ID = re.compile(r"t_[a-z0-9]{8}(?![a-z0-9])")
_NOT_DESCENDED = frozenset({".git", ".venv", "node_modules"})


@dataclass(frozen=True)
class ScratchLayout:
    repository: Path
    folders: tuple[Path, ...]

    @classmethod
    def under(cls, root: Path, home: Path | None = None) -> ScratchLayout:
        home = Path.home() if home is None else home
        return cls(
            repository=root / "repository",
            folders=tuple(root / name for name in SCRATCH_FOLDERS)
            + tuple(home / name for name in LEGACY_HOME_FOLDERS),
        )


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str | None


@dataclass(frozen=True)
class TicketScratch:
    ticket_id: str
    entries: tuple[Path, ...]
    branches: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "ticket_id": self.ticket_id,
            "entries": [str(path) for path in self.entries],
            "branches": list(self.branches),
        }


@dataclass
class ScratchResult:
    removed: list[str] = field(default_factory=list)
    deleted_branches: list[str] = field(default_factory=list)
    kept_branches: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "removed": self.removed,
            "deleted_branches": self.deleted_branches,
            "kept_branches": self.kept_branches,
            "skipped": self.skipped,
        }


def ticket_id_of_name(name: str) -> str | None:
    match = _TICKET_ID.match(name)
    return match.group(0) if match else None


def ticket_id_of_branch(branch: str | None) -> str | None:
    match = _TICKET_ID.search(branch or "")
    return match.group(0) if match else None


def read_ticket_states(conn: sqlite3.Connection) -> dict[str, tuple[str, int]]:
    rows = conn.execute("SELECT id, stage, updated_at FROM tickets").fetchall()
    return {str(row[0]): (str(row[1]), int(row[2])) for row in rows}


def read_ticket_states_from(db_path: str) -> dict[str, tuple[str, int]]:
    conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        return read_ticket_states(conn)
    finally:
        conn.close()


def finished(ticket_id: str, states: Mapping[str, tuple[str, int]], now: int) -> bool:
    """A deleted Ticket, or a Ticket that has been done for the settle period."""
    state = states.get(ticket_id)
    return state is None or (state[0] == "done" and state[1] <= now - SETTLE_SECONDS)


def list_worktrees(repository: Path) -> list[Worktree]:
    """Linked worktrees of the repository. The main checkout is never included."""
    output = _git(repository, "worktree", "list", "--porcelain")
    if output.returncode != 0:
        return []
    worktrees: list[Worktree] = []
    for block in output.stdout.strip().split("\n\n")[1:]:
        path: Path | None = None
        branch: str | None = None
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree "))
            elif line.startswith("branch "):
                branch = line.removeprefix("branch ").removeprefix("refs/heads/")
        if path is not None:
            worktrees.append(Worktree(path, branch))
    return worktrees


def collect_ticket_scratch(
    layout: ScratchLayout, states: Mapping[str, tuple[str, int]], now: int
) -> list[TicketScratch]:
    """Name the scratch of every finished Ticket. This changes nothing."""
    if not (layout.repository / ".git").exists():
        return []
    entries: dict[str, set[Path]] = {}
    for folder in layout.folders:
        if not folder.is_dir() or folder.is_symlink():
            continue
        for child in folder.iterdir():
            ticket_id = ticket_id_of_name(child.name)
            if ticket_id is not None:
                entries.setdefault(ticket_id, set()).add(child)
    branches: dict[str, set[str]] = {}
    for worktree in list_worktrees(layout.repository):
        if any(_inside(worktree.path, folder) for folder in layout.folders):
            continue
        ticket_id = _ticket_id_of_path(worktree.path) or ticket_id_of_branch(worktree.branch)
        if ticket_id is not None:
            entries.setdefault(ticket_id, set()).add(worktree.path)
    for branch in _local_branches(layout.repository):
        ticket_id = ticket_id_of_branch(branch)
        if ticket_id is not None:
            branches.setdefault(ticket_id, set()).add(branch)
    return [
        TicketScratch(
            ticket_id,
            tuple(sorted(entries.get(ticket_id, ()))),
            tuple(sorted(branches.get(ticket_id, ()))),
        )
        for ticket_id in sorted(set(entries) | set(branches))
        if finished(ticket_id, states, now)
    ]


def remove_ticket_scratch(
    layout: ScratchLayout,
    scratches: Iterable[TicketScratch],
    states: Mapping[str, tuple[str, int]],
    now: int,
) -> ScratchResult:
    """Remove the named scratch, checking each entry again just before removal."""
    result = ScratchResult()
    scratches = list(scratches)
    worktrees = list_worktrees(layout.repository)
    for scratch in scratches:
        if not finished(scratch.ticket_id, states, now):
            continue
        for entry in scratch.entries:
            if not entry.exists() and not entry.is_symlink():
                continue
            reason = _reason_to_keep(entry, worktrees, states, now)
            if reason is not None:
                result.skipped.append({"path": str(entry), "reason": reason})
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            result.removed.append(str(entry))
    _git(layout.repository, "worktree", "prune")
    checked_out = {worktree.branch for worktree in list_worktrees(layout.repository)}
    for scratch in scratches:
        for branch in scratch.branches:
            if branch in checked_out:
                continue
            merged = _git(layout.repository, "merge-base", "--is-ancestor", branch, MERGE_TARGET)
            if (
                merged.returncode == 0
                and _git(layout.repository, "branch", "-D", branch).returncode == 0
            ):
                result.deleted_branches.append(branch)
            else:
                result.kept_branches.append(branch)
    return result


def _reason_to_keep(
    entry: Path,
    worktrees: list[Worktree],
    states: Mapping[str, tuple[str, int]],
    now: int,
) -> str | None:
    if entry.is_symlink() or not entry.is_dir():
        return None
    registered = [worktree for worktree in worktrees if _inside(worktree.path, entry)]
    for worktree in registered:
        owner = ticket_id_of_branch(worktree.branch)
        if owner is not None and not finished(owner, states, now):
            return f"holds the worktree {worktree.path} of a live Ticket"
    checkouts = {worktree.path for worktree in registered} | set(_git_checkouts(entry))
    for checkout in sorted(checkouts):
        status = _git(checkout, "status", "--porcelain")
        if status.returncode != 0:
            return f"git cannot read the checkout {checkout}"
        if status.stdout.strip():
            return f"uncommitted changes in {checkout}"
        unpushed = _git(checkout, "rev-list", "-n", "1", "HEAD", "--not", "--remotes")
        if unpushed.returncode != 0:
            return f"git cannot compare {checkout} with its remotes"
        if unpushed.stdout.strip():
            return f"commits that no remote has in {checkout}"
    return None


def _git_checkouts(root: Path) -> Iterable[Path]:
    for directory, names, files in os.walk(root):
        if ".git" in names or ".git" in files:
            yield Path(directory)
        names[:] = [name for name in names if name not in _NOT_DESCENDED]


def _local_branches(repository: Path) -> list[str]:
    output = _git(repository, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return output.stdout.split() if output.returncode == 0 else []


def _ticket_id_of_path(path: Path) -> str | None:
    for part in path.parts:
        ticket_id = ticket_id_of_name(part)
        if ticket_id is not None:
            return ticket_id
    return None


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _git(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(directory), *args],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


CLEANUP_INTERVAL_SECONDS = 600


def clean_finished_ticket_scratch(
    layout: ScratchLayout, db_path: str, now: int, already_kept: set[str]
) -> ScratchResult:
    """One server pass. An entry kept once is not checked again until restart."""
    states = read_ticket_states_from(db_path)
    scratches = [
        TicketScratch(
            scratch.ticket_id,
            tuple(entry for entry in scratch.entries if str(entry) not in already_kept),
            scratch.branches,
        )
        for scratch in collect_ticket_scratch(layout, states, now)
    ]
    result = remove_ticket_scratch(layout, scratches, states, now)
    already_kept.update(item["path"] for item in result.skipped)
    return result
