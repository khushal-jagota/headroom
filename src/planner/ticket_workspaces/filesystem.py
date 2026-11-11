"""Prepare and retire local Ticket folders without owning the Worker's Git workflow."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from planner.ticket_workspaces.contracts import (
    OWNERSHIP_FILE,
    RETENTION_SECONDS,
    WORKTREE_DIRECTORY,
    CleanupResult,
    WorkspaceState,
)

_TICKET_ID = re.compile(r"^(t_[0-9abcdefghjkmnpqrstuvwxyz]{8})(?:-|$)")
_SKIP_DIRECTORIES = frozenset({".git", ".venv", "node_modules"})


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
        env=environment,
    )


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def repository_root(folder: Path | None) -> Path | None:
    if folder is None or not folder.is_dir():
        return None
    result = _git(folder, "rev-parse", "--show-toplevel")
    if result.returncode == 0:
        return Path(result.stdout.strip()).resolve()
    conventional = folder / "repository"
    if conventional.is_dir():
        result = _git(conventional, "rev-parse", "--show-toplevel")
        if result.returncode == 0:
            return Path(result.stdout.strip()).resolve()
    # Do not choose among multiple repositories hidden below a Project folder.
    children = [child for child in folder.iterdir() if child.is_dir() and (child / ".git").exists()]
    if children:
        raise RuntimeError(
            "Project folder contains repositories; select the repository folder explicitly"
        )
    return None


def prepare_workspace(
    ticket_root: Path,
    repository: Path | None,
    ticket_id: str,
    *,
    generation: int = 0,
) -> Path:
    """Make one checkout, or an ordinary workspace for a project without Git."""
    if ticket_root.is_symlink() or ticket_root.parent.is_symlink():
        raise RuntimeError("Ticket workspace must not be a symlink")
    ticket_root.mkdir(parents=True, exist_ok=True)
    ticket_root = ticket_root.resolve()
    destination = ticket_root / WORKTREE_DIRECTORY
    marker_path = ticket_root / OWNERSHIP_FILE
    if destination.is_symlink() or marker_path.is_symlink():
        raise RuntimeError("Ticket workspace must not be a symlink")
    if marker_path.exists():
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if marker.get("ticket_id") != ticket_id or marker.get("worktree") != str(destination):
            raise RuntimeError("Ticket workspace ownership does not match")
        old_generation = int(marker.get("generation", 0))
        if old_generation != generation:
            if old_generation > generation:
                raise RuntimeError("The Ticket workspace is newer than its start request")
            # A new canonical generation means retirement was authorized previously.
            # Preserve an old folder left by a crash before creating the replacement.
            moved = retire_workspace(
                ticket_root, WorkspaceState(ticket_id, None, False, old_generation)
            )
            if isinstance(moved, str):
                raise RuntimeError(moved)
            return prepare_workspace(ticket_root, repository, ticket_id, generation=generation)
        if not destination.is_dir():
            raise RuntimeError("The managed Ticket workspace is missing")
        selected = repository_root(repository)
        if marker.get("repository") != (str(selected) if selected else None):
            raise RuntimeError("Ticket workspace repository changed; owner review required")
        if selected is not None:
            actual = repository_root(destination)
            if actual != destination or not (destination / ".git").is_file():
                raise RuntimeError("Ticket workspace is not its registered Git worktree")
            expected = _git(selected, "rev-parse", "--path-format=absolute", "--git-common-dir")
            current = _git(destination, "rev-parse", "--path-format=absolute", "--git-common-dir")
            if expected.returncode or current.returncode or expected.stdout != current.stdout:
                raise RuntimeError("Ticket workspace belongs to another repository")
        return destination
    if destination.exists():
        raise RuntimeError("Ticket worktree exists without ownership evidence")
    selected_repository = repository_root(repository)
    branch = (
        f"ticket/{ticket_id}-workspace-g{generation}" if selected_repository is not None else None
    )
    if selected_repository is not None:
        result = _git(
            selected_repository, "worktree", "add", "-b", str(branch), str(destination), "HEAD"
        )
        if result.returncode:
            raise RuntimeError(f"Cannot prepare Ticket worktree: {result.stderr.strip()}")
    else:
        destination.mkdir()
    (ticket_root / "artifacts").mkdir(exist_ok=True)
    _write_json(
        marker_path,
        {
            "ticket_id": ticket_id,
            "repository": str(selected_repository) if selected_repository else None,
            "branch": branch,
            "worktree": str(destination),
            "generation": generation,
        },
    )
    return destination


def _checkouts(root: Path) -> list[Path]:
    checkouts = []

    def unreadable(error: OSError) -> None:
        raise error

    for directory, names, files in os.walk(root, followlinks=False, onerror=unreadable):
        if ".git" in names or ".git" in files:
            checkouts.append(Path(directory))
        names[:] = [name for name in names if name not in _SKIP_DIRECTORIES]
    return checkouts


def workspace_ticket_owners(root: Path) -> tuple[str, ...]:
    owners: set[str] = set()
    for checkout in _checkouts(root):
        for part in checkout.parts:
            match = _TICKET_ID.match(part)
            if match:
                owners.add(match.group(1))
        branch = _git(checkout, "symbolic-ref", "--short", "HEAD")
        owners.update(re.findall(r"t_[0-9abcdefghjkmnpqrstuvwxyz]{8}(?![a-z0-9])", branch.stdout))
    return tuple(sorted(owners))


def reason_to_keep(
    root: Path,
    states: Mapping[str, WorkspaceState] | None = None,
    active_ticket_ids: frozenset[str] = frozenset(),
) -> str | None:
    if root.is_symlink() or root.parent.is_symlink():
        return "workspace path is a symlink"
    try:
        checkouts = _checkouts(root)
    except OSError as exc:
        return f"cannot inspect workspace: {exc}"
    match = _TICKET_ID.match(root.name)
    if match is not None:
        try:
            if set(workspace_ticket_owners(root)) - {match.group(1)}:
                return "mixed Ticket ownership requires owner review"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"cannot inspect workspace ownership: {exc}"
    for checkout in checkouts:
        try:
            if states is not None:
                owners = []
                for part in checkout.parts:
                    match = _TICKET_ID.match(part)
                    if match:
                        owners.append(match.group(1))
                branch = _git(checkout, "symbolic-ref", "--short", "HEAD")
                owners.extend(
                    re.findall(r"t_[0-9abcdefghjkmnpqrstuvwxyz]{8}(?![a-z0-9])", branch.stdout)
                )
                if any(
                    owner in active_ticket_ids
                    or (owner in states and states[owner].ended_at is None)
                    for owner in owners
                ):
                    return f"holds a live Ticket worktree in {checkout}"
            status = _git(
                checkout,
                "status",
                "--porcelain",
                "--untracked-files=normal",
                "--ignore-submodules=none",
            )
            if status.returncode:
                return f"Git cannot read {checkout}"
            if status.stdout.strip():
                return f"uncommitted work in {checkout}"
            if (checkout / ".git").is_dir():
                linked = _git(checkout, "worktree", "list", "--porcelain")
                if linked.returncode:
                    return f"Git cannot inspect linked worktrees for {checkout}"
                for line in linked.stdout.splitlines():
                    if line.startswith("worktree "):
                        linked_path = Path(line[len("worktree ") :])
                        if not linked_path.is_relative_to(root):
                            return f"repository still owns an external worktree: {linked_path}"
            remotes = _git(checkout, "remote")
            if remotes.returncode or not remotes.stdout.strip():
                return f"no remote copy can be verified for {checkout}"
            # Only refs advertised by the actual remotes can prove preservation.
            remote_hashes: set[str] = set()
            for remote in remotes.stdout.splitlines():
                fetched = _git(checkout, "fetch", "--prune", remote)
                advertised = _git(checkout, "ls-remote", "--heads", "--tags", remote)
                if fetched.returncode or advertised.returncode:
                    return f"remote verification failed for {checkout}"
                remote_hashes.update(line.split()[0] for line in advertised.stdout.splitlines())
            if not remote_hashes:
                return f"no remote copy can be verified for {checkout}"
            refs = "--all" if (checkout / ".git").is_dir() else "HEAD"
            unpublished = _git(
                checkout, "rev-list", "-n", "1", refs, "--not", *sorted(remote_hashes)
            )
            if unpublished.returncode:
                return f"Git cannot compare remote work for {checkout}"
            if unpublished.stdout.strip():
                return f"unpushed work in {checkout}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"Git inspection failed for {checkout}: {exc}"
    return None


def cleanup_ticket_folder(
    ticket_root: Path,
    *,
    states: Mapping[str, WorkspaceState] | None = None,
    active_ticket_ids: frozenset[str] = frozenset(),
    before_remove: Callable[[Path], str | None] | None = None,
) -> str | None:
    """Return a preservation reason, or remove a folder with verified remote work."""
    reason = reason_to_keep(ticket_root, states, active_ticket_ids)
    if reason is not None:
        return reason
    if before_remove is not None:
        reason = before_remove(ticket_root)
        if reason is not None:
            return reason
    # Deregister linked worktrees before removing artifacts and ignored build output.
    for checkout in reversed(_checkouts(ticket_root)):
        if (checkout / ".git").is_file():
            common = _git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir")
            if common.returncode:
                return f"Git cannot locate the repository for {checkout}"
            removed = _git(
                Path(common.stdout.strip()), "worktree", "remove", "--force", str(checkout)
            )
            if removed.returncode:
                return f"Git cannot remove the worktree {checkout}: {removed.stderr.strip()}"
    if ticket_root.is_dir():
        shutil.rmtree(ticket_root)
    else:
        ticket_root.unlink()
    return None


def _allocated_bytes(root: Path) -> int:
    if root.is_file():
        return root.lstat().st_blocks * 512
    total = 0
    for directory, _, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                total += (Path(directory) / name).lstat().st_blocks * 512
            except FileNotFoundError:
                continue
    return total


def cleanup_local_workspaces(
    tickets_root: Path,
    states: Mapping[str, WorkspaceState],
    now: int,
    *,
    extra_roots: tuple[Path, ...] = (),
    active_ticket_ids: frozenset[str] = frozenset(),
    apply: bool = True,
    before_remove: Callable[[Path], str | None] | None = None,
    authorize_retirement: Callable[[WorkspaceState], bool] | None = None,
    retire: Callable[[Path, WorkspaceState], Path | str] | None = None,
    retirement_is_committed: Callable[[WorkspaceState], bool] | None = None,
) -> CleanupResult:
    """Inventory before removal; unknown ownership and active Tickets always stay."""
    result = CleanupResult()
    candidates: list[tuple[Path, str | None]] = []
    for root in (tickets_root, *extra_roots):
        if not root.is_dir() or root.is_symlink():
            continue
        for child in sorted(root.iterdir()):
            if child.is_symlink():
                continue
            match = _TICKET_ID.match(child.name)
            candidates.append((child, match.group(1) if match else None))
    manifest = []
    eligible: list[Path] = []
    reason: str | None
    for path, ticket_id in candidates:
        state = states.get(ticket_id or "")
        if state is None:
            reason = "Ticket owner or end date is unknown; owner review required"
        elif ticket_id in active_ticket_ids or state.ended_at is None:
            reason = "Ticket is active"
        elif now < state.ended_at + RETENTION_SECONDS:
            reason = "seven-day retention period has not ended"
        else:
            reason = reason_to_keep(path, states, active_ticket_ids)
        manifest.append(
            {
                "path": str(path),
                "ticket_id": ticket_id,
                "ended_at": state.ended_at if state else None,
                "eligible_at": state.ended_at + RETENTION_SECONDS
                if state and state.ended_at is not None
                else None,
                "allocated_bytes": _allocated_bytes(path),
                "preservation_reason": reason,
            }
        )
        if reason:
            result.skipped.append({"path": str(path), "reason": reason})
        else:
            eligible.append(path)
    tickets_root.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        tickets_root.parent / "ticket-workspaces-cleanup.json", {"at": now, "folders": manifest}
    )
    if not apply:
        return result
    retried = retry_retired_workspaces(
        tickets_root.parent / ".ticket-retired",
        authorize_retirement=retirement_is_committed or authorize_retirement,
    )
    result.removed.extend(retried.removed)
    result.skipped.extend(retried.skipped)
    for path in eligible:
        try:
            match = _TICKET_ID.match(path.name)
            assert match is not None
            state = states[match.group(1)]
            if before_remove is not None:
                reason = before_remove(path)
                if reason:
                    result.skipped.append({"path": str(path), "reason": reason})
                    continue
            if authorize_retirement is not None and not authorize_retirement(state):
                result.skipped.append(
                    {"path": str(path), "reason": "retirement authorization refused"}
                )
                continue
            target = (
                retire(path, state)
                if retire is not None
                else retire_workspace(
                    path, state, retired_root=tickets_root.parent / ".ticket-retired"
                )
            )
            if isinstance(target, str):
                result.skipped.append({"path": str(path), "reason": target})
                continue
            reason = _remove_retired(target)
            if reason:
                result.skipped.append({"path": str(target), "reason": reason})
            else:
                result.removed.append(str(path))
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            result.skipped.append({"path": str(path), "reason": f"cleanup failed: {exc}"})
    return result


def retire_workspace(
    path: Path,
    state: WorkspaceState,
    *,
    retired_root: Path | None = None,
) -> Path | str:
    """Detach this exact generation before any destructive operation.

    The caller holds either the server's short write transaction or the executor's
    local start lock after canonical retirement authorization.
    """
    if path.is_symlink() or path.parent.is_symlink():
        return "workspace path is a symlink"
    marker_path = path / OWNERSHIP_FILE
    if marker_path.exists():
        marker = json.loads(marker_path.read_text())
        if int(marker.get("generation", 0)) != state.generation:
            return "workspace generation changed"
    retired_root = retired_root or path.parent.parent / ".ticket-retired"
    if retired_root.is_symlink():
        return "retirement directory is a symlink"
    retired_root.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4

    token = uuid4().hex
    target = retired_root / f"{state.ticket_id}-g{state.generation}-{token}"
    record_path = retired_root / f"{token}.json"
    # Write ahead: a crash after rename still leaves an exact owned target to retry.
    _write_json(
        record_path,
        {
            "source": str(path),
            "target": str(target),
            "ticket_id": state.ticket_id,
            "generation": state.generation,
            "ended_at": state.ended_at,
            "deleted": state.deleted,
        },
    )
    path.rename(target)
    return target


def _remove_retired(path: Path) -> str | None:
    """Repair Git's backlink after atomic folder rename, then remove safe work."""
    for checkout in _checkouts(path):
        if (checkout / ".git").is_file():
            result = _git(checkout, "worktree", "repair", str(checkout))
            if result.returncode:
                return f"cannot repair retired worktree: {result.stderr.strip()}"
    return cleanup_ticket_folder(path)


def retry_retired_workspaces(
    retired_root: Path,
    *,
    authorize_retirement: Callable[[WorkspaceState], bool] | None = None,
) -> CleanupResult:
    result = CleanupResult()
    if not retired_root.is_dir() or retired_root.is_symlink():
        return result
    for record_path in sorted(retired_root.glob("*.json")):
        try:
            record = json.loads(record_path.read_text())
            target = Path(record["target"])
            if target.parent != retired_root or target.is_symlink():
                raise ValueError("invalid retirement target")
            if not target.exists():
                record_path.unlink()
                continue
            state = WorkspaceState(
                record["ticket_id"],
                record.get("ended_at"),
                bool(record.get("deleted", False)),
                int(record["generation"]),
            )
            if authorize_retirement is not None and not authorize_retirement(state):
                result.skipped.append(
                    {
                        "path": str(target),
                        "reason": "retirement grant is not committed; owner review required",
                    }
                )
                continue
            reason = _remove_retired(target)
            if reason is None:
                result.removed.append(str(target))
                record_path.unlink()
            else:
                result.skipped.append({"path": str(target), "reason": reason})
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            result.skipped.append({"path": str(record_path), "reason": str(exc)})
    return result
