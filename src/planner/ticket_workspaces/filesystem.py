"""Prepare and retire local Ticket folders without owning the Worker's Git workflow."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from planner.ticket_workspaces.contracts import (
    OWNERSHIP_FILE,
    RETENTION_SECONDS,
    TICKET_ID_PATTERN,
    WORKTREE_DIRECTORY,
    CleanupResult,
    WorkspaceIdentity,
    WorkspaceState,
)

_TICKET_ID = re.compile(rf"^({TICKET_ID_PATTERN})(?:-|$)")
_SKIP_DIRECTORIES = frozenset({".git", ".venv", "node_modules"})
_CHECKOUT_TIMEOUT_SECONDS = 30 * 60
_PANELS_ORIGIN = "https://github.com/khushal-jagota/panels.git"


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=_CHECKOUT_TIMEOUT_SECONDS
        if args[:2] == ("worktree", "add") or args[:1] == ("clone",)
        else 60,
        env=environment,
    )


def _clone_source(repository: Path | None, panels_project: bool) -> tuple[str, str | None]:
    if panels_project:
        return _PANELS_ORIGIN, "staging"
    assert repository is not None
    remote = _git(repository, "remote", "get-url", "origin")
    if remote.returncode or not remote.stdout.strip():
        raise RuntimeError("Cannot find the Project repository's origin remote")
    source = remote.stdout.strip()
    # A relative local Git remote is relative to its source checkout, not to the Ticket.
    if not re.match(r"(?:[a-zA-Z][a-zA-Z0-9+.-]*://|[^/]+:|/)", source):
        source = str((repository / source).resolve())
    return source, None


def _validate_clone(destination: Path, branch: str | None = None) -> None:
    if not (destination / ".git").is_dir() or (destination / ".git").is_symlink():
        raise RuntimeError("Ticket workspace is not its independent Git checkout")
    actual = _git(destination, "rev-parse", "--show-toplevel")
    current = _git(destination, "symbolic-ref", "--short", "HEAD")
    if actual.returncode or Path(actual.stdout.strip()).resolve() != destination:
        raise RuntimeError("Ticket workspace belongs to another repository or path")
    if branch is not None and (current.returncode or current.stdout.strip() != branch):
        raise RuntimeError("Interrupted Ticket checkout branch changed; owner review required")


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
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
    if len(children) > 1:
        raise RuntimeError(
            "Project folder contains repositories; select the repository folder explicitly"
        )
    if children:
        result = _git(children[0], "rev-parse", "--show-toplevel")
        if result.returncode:
            raise RuntimeError(f"Cannot inspect Project repository: {children[0]}")
        return Path(result.stdout.strip()).resolve()
    return None


def workspace_identity(ticket_root: Path, *, create: bool = True) -> WorkspaceIdentity:
    """Allocate one physical token even when cleanup and startup arrive together."""
    if ticket_root.is_symlink() or ticket_root.parent.is_symlink():
        raise RuntimeError("Ticket workspace must not be a symlink")
    if create:
        ticket_root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(ticket_root, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        opened = os.fstat(descriptor)
        attached = ticket_root.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (attached.st_dev, attached.st_ino):
            raise RuntimeError("Ticket workspace changed before ownership inspection")
        return _allocate_workspace_identity(ticket_root)
    finally:
        os.close(descriptor)


def read_workspace_identity(ticket_root: Path) -> WorkspaceIdentity:
    """Read ownership without recreating a concurrently detached folder."""
    if ticket_root.is_symlink() or ticket_root.parent.is_symlink():
        raise RuntimeError("Ticket workspace must not be a symlink")
    marker_path = ticket_root / OWNERSHIP_FILE
    if marker_path.is_symlink():
        raise RuntimeError("Ticket ownership must not be a symlink")
    marker = json.loads(marker_path.read_text())
    return WorkspaceIdentity(UUID(marker["machine_id"]).hex, UUID(marker["workspace_id"]).hex)


def _allocate_workspace_identity(ticket_root: Path) -> WorkspaceIdentity:
    ticket_root = ticket_root.resolve()
    machine_path = ticket_root.parent.parent / ".workspace-machine-id"
    descriptor = os.open(machine_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        value = stream.read().strip()
        if not value:
            value = uuid4().hex
            stream.write(value)
            stream.flush()
        machine_id = UUID(value).hex
    marker_path = ticket_root / OWNERSHIP_FILE
    if marker_path.is_symlink():
        raise RuntimeError("Ticket ownership must not be a symlink")
    marker = json.loads(marker_path.read_text()) if marker_path.exists() else {}
    match = _TICKET_ID.match(ticket_root.name)
    ticket_id = match.group(1) if match else ticket_root.name
    if marker.get("ticket_id", ticket_id) != ticket_id:
        raise RuntimeError("Ticket workspace ownership does not match")
    if marker.get("machine_id", machine_id) != machine_id:
        raise RuntimeError("Ticket workspace belongs to another machine")
    workspace_id = UUID(marker["workspace_id"]).hex if "workspace_id" in marker else uuid4().hex
    if not {"ticket_id", "machine_id", "workspace_id"}.issubset(marker):
        marker.update(ticket_id=ticket_id, machine_id=machine_id, workspace_id=workspace_id)
        _write_json(marker_path, marker)
    return WorkspaceIdentity(machine_id, workspace_id)


def _registered_worktree(repository: Path, destination: Path, branch: str | None = None) -> bool:
    listing = _git(repository, "worktree", "list", "--porcelain")
    if listing.returncode:
        raise RuntimeError("Cannot inspect registered Ticket worktrees")
    for block in listing.stdout.strip().split("\n\n"):
        lines = block.splitlines()
        if f"worktree {destination}" in lines:
            return branch is None or f"branch refs/heads/{branch}" in lines
    return False


def _validate_checkout(repository: Path, destination: Path, branch: str | None = None) -> None:
    if not (destination / ".git").is_file() or not _registered_worktree(
        repository, destination, branch
    ):
        raise RuntimeError("Ticket workspace is not its registered Git worktree")
    expected = _git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
    current = _git(destination, "rev-parse", "--path-format=absolute", "--git-common-dir")
    actual = _git(destination, "rev-parse", "--show-toplevel")
    if (
        expected.returncode
        or current.returncode
        or actual.returncode
        or expected.stdout != current.stdout
        or Path(actual.stdout.strip()).resolve() != destination
    ):
        raise RuntimeError("Ticket workspace belongs to another repository or path")


def _prove_checkout_complete(destination: Path) -> None:
    """A registered worktree can still lack its index or part of its checkout."""
    metadata = _git(destination, "rev-parse", "--absolute-git-dir")
    if metadata.returncode:
        raise RuntimeError("Cannot verify the interrupted Ticket Git metadata")
    # Git holds this lock across checkout, including file contents. An abrupt stop
    # can leave every path present but a partially written file. Do not unlock it.
    if os.path.lexists(Path(metadata.stdout.strip()) / "locked"):
        raise RuntimeError(
            "Interrupted Ticket checkout is incomplete (Git initialization lock remains)"
        )
    head = _git(destination, "ls-tree", "-r", "-z", "HEAD")
    index = _git(destination, "ls-files", "-z")
    if head.returncode or index.returncode:
        raise RuntimeError("Cannot verify the interrupted Ticket checkout")
    required = {
        entry.split("\t", 1)[1]
        for entry in head.stdout.split("\0")
        if entry and not entry.startswith("160000 ")
    }
    indexed = set(index.stdout.split("\0"))
    if not required.issubset(indexed) or any(
        not os.path.lexists(destination / path) for path in required
    ):
        raise RuntimeError(
            "Interrupted Ticket checkout is incomplete; complete the checkout before retry"
        )


def _repair_previous_worktree(ticket_root: Path, repository: Path, ticket_id: str) -> None:
    """Repair only recorded retired checkouts before their former path is reused."""
    retired_root = ticket_root.parent.parent / ".ticket-retired"
    if not retired_root.exists():
        return
    if retired_root.is_symlink():
        raise RuntimeError("Retirement directory must not be a symlink")
    removed_checkout = False
    for record_path in retired_root.glob("*.json"):
        record = json.loads(record_path.read_text())
        if record.get("source") != str(ticket_root) or record.get("ticket_id") != ticket_id:
            continue
        target = Path(record["target"])
        if target.parent != retired_root or target.is_symlink():
            raise RuntimeError("Invalid recorded retirement target")
        if not target.exists():
            continue
        marker_path = target / OWNERSHIP_FILE
        if marker_path.is_symlink() or not marker_path.is_file():
            raise RuntimeError("Retired checkout ownership is unavailable")
        marker = json.loads(marker_path.read_text())
        if marker.get("ticket_id") != ticket_id:
            raise RuntimeError("Retired checkout ownership does not match")
        if "worktree" not in marker:
            continue  # Artifact-only and older session folders have no Git backlink to repair.
        if marker["worktree"] != str(ticket_root / WORKTREE_DIRECTORY):
            raise RuntimeError("Retired checkout ownership does not match")
        if marker.get("repository") != str(repository):
            continue
        checkout = target / WORKTREE_DIRECTORY
        if not checkout.exists():
            removed_checkout = True
            continue
        if checkout.is_symlink() or not (checkout / ".git").is_file():
            raise RuntimeError("Retired checkout is not its recorded linked worktree")
        expected = _git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
        actual = _git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if expected.returncode or actual.returncode or expected.stdout != actual.stdout:
            raise RuntimeError("Retired checkout belongs to another repository")
        repaired = _git(repository, "worktree", "repair", str(checkout))
        if repaired.returncode:
            raise RuntimeError(f"Cannot repair retired Ticket worktree: {repaired.stderr.strip()}")
        _validate_checkout(repository, checkout)
    if removed_checkout and _registered_worktree(repository, ticket_root / WORKTREE_DIRECTORY):
        raise RuntimeError("Retired checkout is missing but its former path remains registered")


def prepare_workspace(
    ticket_root: Path,
    repository: Path | None,
    ticket_id: str,
    *,
    panels_project: bool = False,
) -> Path:
    """Resume the same locally owned checkout, including interrupted preparation."""
    identity = workspace_identity(ticket_root)
    ticket_root = ticket_root.resolve()
    destination = ticket_root / WORKTREE_DIRECTORY
    marker_path = ticket_root / OWNERSHIP_FILE
    if destination.is_symlink():
        raise RuntimeError("Ticket workspace must not be a symlink")
    marker = json.loads(marker_path.read_text())
    if marker.get("ticket_id") != ticket_id:
        raise RuntimeError("Ticket workspace ownership does not match")
    selected = (
        repository_root(repository)
        if not panels_project or ("worktree" in marker and marker.get("checkout_kind") != "clone")
        else None
    )
    if "worktree" in marker:
        if marker["worktree"] != str(destination):
            raise RuntimeError("Ticket workspace path changed; owner review required")
        if marker.get("checkout_kind") == "clone":
            if not destination.exists() and marker.get("preparing"):
                temporary = ticket_root / marker.get("temporary", "")
                if temporary.parent != ticket_root or not temporary.name.startswith(".clone-"):
                    raise RuntimeError("Interrupted Ticket clone ownership is invalid")
                _validate_clone(temporary, marker["branch"])
                _prove_checkout_complete(temporary)
                temporary.rename(destination)
            if not destination.is_dir():
                raise RuntimeError("The managed Ticket workspace is missing or incomplete")
            _validate_clone(destination, marker["branch"] if marker.get("preparing") else None)
            if marker.get("preparing"):
                _prove_checkout_complete(destination)
        else:
            # Legacy linked worktrees remain attached to their original repository.
            recorded_repository = marker.get("repository")
            if recorded_repository != (str(selected) if selected else None):
                raise RuntimeError("Ticket workspace repository changed; owner review required")
            if not marker.get("preparing", False):
                if not destination.is_dir():
                    raise RuntimeError("The managed Ticket workspace is missing")
                if selected is not None:
                    _validate_checkout(selected, destination)
                return destination
            if selected is None:
                destination.mkdir(exist_ok=True)
            elif destination.exists():
                _validate_checkout(selected, destination, marker["branch"])
                _prove_checkout_complete(destination)
            else:
                _repair_previous_worktree(ticket_root, selected, ticket_id)
                branch = marker["branch"]
                existing = _git(
                    selected, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"
                )
                if existing.returncode == 0:
                    if existing.stdout.strip() != marker["initial_commit"]:
                        raise RuntimeError(
                            "Interrupted Ticket branch changed; owner review required"
                        )
                    result = _git(selected, "worktree", "add", str(destination), branch)
                elif existing.returncode == 1:
                    result = _git(
                        selected,
                        "worktree",
                        "add",
                        "-b",
                        branch,
                        str(destination),
                        marker["initial_commit"],
                    )
                else:
                    raise RuntimeError("Cannot inspect the interrupted Ticket branch")
                if result.returncode:
                    raise RuntimeError(f"Cannot prepare Ticket worktree: {result.stderr.strip()}")
                _validate_checkout(selected, destination, branch)
    else:
        if destination.exists():
            raise RuntimeError("Ticket worktree exists without preparation ownership evidence")
        if panels_project or selected is not None:
            source, base = _clone_source(selected, panels_project)
            branch = f"ticket/{ticket_id}-workspace-{identity.workspace_id[:12]}"
            # Clone into a temporary sibling; an interrupted clone is never exposed as
            # a usable checkout and cannot be mistaken for user work on retry.
            temporary = ticket_root / f".clone-{uuid4().hex}"
            args = ["clone", "--filter=blob:none", "--single-branch", "--no-local"]
            if base:
                args.extend(["--branch", base])
            args.extend(["--", source, str(temporary)])
            recorded = False
            try:
                result = _git(ticket_root, *args)
                if result.returncode:
                    raise RuntimeError(
                        "Cannot clone the Project origin; check remote access and branch"
                    )
                switched = _git(temporary, "switch", "-c", branch)
                if switched.returncode:
                    raise RuntimeError("Cannot create the Ticket branch")
                _validate_clone(temporary, branch)
                marker.update(
                    repository=str(selected) if selected else None,
                    branch=branch,
                    worktree=str(destination),
                    preparing=True,
                    checkout_kind="clone",
                    temporary=temporary.name,
                )
                _write_json(marker_path, marker)
                recorded = True
                temporary.rename(destination)
            finally:
                if not recorded and temporary.exists():
                    shutil.rmtree(temporary)
        else:
            marker.update(repository=None, branch=None, worktree=str(destination), preparing=True)
            _write_json(marker_path, marker)
            destination.mkdir()
    (ticket_root / "artifacts").mkdir(exist_ok=True)
    marker["preparing"] = False
    _write_json(marker_path, marker)
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
        for part in (root.name, *checkout.relative_to(root).parts):
            match = _TICKET_ID.match(part)
            if match:
                owners.add(match.group(1))
        branch = _git(checkout, "symbolic-ref", "--short", "HEAD")
        owners.update(re.findall(r"t_[0-9abcdefghjkmnpqrstuvwxyz]{8}(?![a-z0-9])", branch.stdout))
    return tuple(sorted(owners))


def _recorded_checkout_problem(ticket_root: Path) -> str | None:
    """A recorded Git checkout cannot become a plain folder if its metadata disappears."""
    marker_path = ticket_root / OWNERSHIP_FILE
    if not os.path.lexists(marker_path):
        return None  # Artifact-only and legacy folders can lack a workspace marker.
    try:
        if marker_path.is_symlink() or not marker_path.is_file():
            return "workspace ownership evidence is invalid"
        marker = json.loads(marker_path.read_text())
        is_git = marker.get("checkout_kind") == "clone" or marker.get("repository") is not None
        if not is_git or "worktree" not in marker:
            return None
        checkout = ticket_root / WORKTREE_DIRECTORY
        metadata = checkout / ".git"
        if checkout.is_symlink() or metadata.is_symlink() or not metadata.exists():
            return "recorded Git checkout or its metadata is missing or invalid"
        result = _git(checkout, "rev-parse", "--show-toplevel")
        if result.returncode or Path(result.stdout.strip()).resolve() != checkout.resolve():
            return "recorded Git checkout or its metadata is missing or invalid"
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return "cannot inspect recorded Git checkout ownership"
    return None


def reason_to_keep(
    root: Path,
    states: Mapping[str, WorkspaceState] | None = None,
    active_ticket_ids: frozenset[str] = frozenset(),
) -> str | None:
    if root.is_symlink() or root.parent.is_symlink():
        return "workspace path is a symlink"
    recorded_problem = _recorded_checkout_problem(root)
    if recorded_problem:
        return recorded_problem
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
            tracked = _git(checkout, "ls-files", "--stage")
            if tracked.returncode:
                return f"Git cannot inspect tracked work in {checkout}"
            if any(line.startswith("160000 ") for line in tracked.stdout.splitlines()):
                return f"submodules require owner review before cleanup: {checkout}"
            if states is not None:
                owners = []
                for part in (root.name, *checkout.relative_to(root).parts):
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
            if remotes.returncode or "origin" not in remotes.stdout.splitlines():
                return f"no origin copy can be verified for {checkout}"
            # Only refs advertised by origin can prove preservation.
            remote_hashes: set[str] = set()
            fetched = _git(checkout, "fetch", "--prune", "origin")
            advertised = _git(checkout, "ls-remote", "--heads", "--tags", "origin")
            if fetched.returncode or advertised.returncode:
                return f"remote verification failed for {checkout}"
            for line in advertised.stdout.splitlines():
                commit = line.split()[0]
                # Single-branch clones need not have objects from other advertised
                # branches. Only commits present locally can prove preservation.
                if _git(checkout, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0:
                    remote_hashes.add(commit)
            if not remote_hashes:
                return f"no remote copy can be verified for {checkout}"
            refs = ["HEAD"]
            marker_root = root.parent if root.name == WORKTREE_DIRECTORY else root
            marker_path = marker_root / OWNERSHIP_FILE
            if checkout == marker_root / WORKTREE_DIRECTORY and marker_path.is_file():
                marker = json.loads(marker_path.read_text())
                branch = marker.get("branch")
                if branch:
                    refs.append(f"refs/heads/{branch}")
            unpublished = _git(
                checkout, "rev-list", "-n", "1", *refs, "--not", *sorted(remote_hashes)
            )
            if unpublished.returncode:
                return f"Git cannot compare remote work for {checkout}"
            if unpublished.stdout.strip():
                return f"unpushed work in {checkout}"
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
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
    measure_size: bool = False,
    before_remove: Callable[[Path], str | None] | None = None,
    authorize_retirement: Callable[[WorkspaceState, WorkspaceIdentity], bool] | None = None,
    retire: Callable[[Path, WorkspaceState, WorkspaceIdentity, bool], Path | str] | None = None,
    retirement_is_committed: Callable[[WorkspaceState, WorkspaceIdentity], bool] | None = None,
) -> CleanupResult:
    """Serialize server and CLI passes without locking unrelated workspace starts."""
    tickets_root.parent.mkdir(parents=True, exist_ok=True)
    lock_path = tickets_root.parent / ".ticket-workspace-cleanup.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _cleanup_local_workspaces(
            tickets_root,
            states,
            now,
            extra_roots=extra_roots,
            active_ticket_ids=active_ticket_ids,
            apply=apply,
            measure_size=measure_size,
            before_remove=before_remove,
            authorize_retirement=authorize_retirement,
            retire=retire,
            retirement_is_committed=retirement_is_committed,
        )
    finally:
        os.close(descriptor)


def _cleanup_local_workspaces(
    tickets_root: Path,
    states: Mapping[str, WorkspaceState],
    now: int,
    *,
    extra_roots: tuple[Path, ...] = (),
    active_ticket_ids: frozenset[str] = frozenset(),
    apply: bool = True,
    measure_size: bool = False,
    before_remove: Callable[[Path], str | None] | None = None,
    authorize_retirement: Callable[[WorkspaceState, WorkspaceIdentity], bool] | None = None,
    retire: Callable[[Path, WorkspaceState, WorkspaceIdentity, bool], Path | str] | None = None,
    retirement_is_committed: Callable[[WorkspaceState, WorkspaceIdentity], bool] | None = None,
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
    eligible: list[tuple[Path, bool]] = []
    reason: str | None
    for path, ticket_id in candidates:
        state = states.get(ticket_id or "")
        if path.parent != tickets_root:
            reason = "legacy scratch outside Ticket files requires one-time owner review"
        elif state is None:
            reason = "Ticket owner or end date is unknown; owner review required"
        elif ticket_id in active_ticket_ids or state.ended_at is None:
            reason = "Ticket is active"
        else:
            checkout = path / WORKTREE_DIRECTORY
            checkout_only = now < state.ended_at + RETENTION_SECONDS
            recorded_problem = _recorded_checkout_problem(path)
            if recorded_problem:
                reason = recorded_problem
            elif checkout_only and not (checkout / ".git").exists():
                reason = "Ticket artifacts remain for seven days"
            else:
                reason = None
                if checkout_only and reason is None:
                    try:
                        if set(workspace_ticket_owners(checkout)) - {ticket_id}:
                            reason = "mixed Ticket ownership requires owner review"
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        reason = f"cannot inspect workspace ownership: {exc}"
                if reason is None:
                    reason = reason_to_keep(
                        checkout if checkout_only else path, states, active_ticket_ids
                    )
        manifest.append(
            {
                "path": str(path),
                "ticket_id": ticket_id,
                "ended_at": state.ended_at if state else None,
                "eligible_at": state.ended_at + RETENTION_SECONDS
                if state and state.ended_at is not None
                else None,
                "allocated_bytes": _allocated_bytes(path) if measure_size else None,
                "preservation_reason": reason,
            }
        )
        if reason:
            result.skipped.append({"path": str(path), "reason": reason})
        else:
            eligible.append((path, checkout_only))
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
    for path, checkout_only in eligible:
        try:
            match = _TICKET_ID.match(path.name)
            assert match is not None
            state = states[match.group(1)]
            identity = workspace_identity(path, create=False)
            if before_remove is not None:
                reason = before_remove(path)
                if reason:
                    result.skipped.append({"path": str(path), "reason": reason})
                    continue
            if authorize_retirement is not None and not authorize_retirement(state, identity):
                result.skipped.append(
                    {"path": str(path), "reason": "retirement authorization refused"}
                )
                continue
            target = (
                retire(path, state, identity, checkout_only)
                if retire is not None
                else retire_workspace(
                    path,
                    state,
                    identity=identity,
                    retired_root=tickets_root.parent / ".ticket-retired",
                    checkout_only=checkout_only,
                )
            )
            if isinstance(target, str):
                result.skipped.append({"path": str(path), "reason": target})
                continue
            reason = _remove_retired(target)
            if reason:
                result.skipped.append({"path": str(target), "reason": reason})
            else:
                result.removed.append(
                    str(path / WORKTREE_DIRECTORY) if checkout_only else str(path)
                )
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            result.skipped.append({"path": str(path), "reason": f"cleanup failed: {exc}"})
    return result


def retire_workspace(
    path: Path,
    state: WorkspaceState,
    *,
    retired_root: Path | None = None,
    identity: WorkspaceIdentity | None = None,
    checkout_only: bool = False,
) -> Path | str:
    """Detach this exact physical workspace before any destructive operation.

    The caller holds either the server's short write transaction or the executor's
    local start lock after canonical retirement authorization.
    """
    target = prepare_workspace_retirement(
        path, state, retired_root=retired_root, identity=identity, checkout_only=checkout_only
    )
    if isinstance(target, Path):
        try:
            detach_workspace(path, target, checkout_only=checkout_only)
        except BaseException:
            if checkout_only and target.is_dir() and not (target / WORKTREE_DIRECTORY).exists():
                (target / OWNERSHIP_FILE).unlink(missing_ok=True)
                target.rmdir()
            (target.parent / f"{target.name.rsplit('-', 1)[1]}.json").unlink(missing_ok=True)
            raise
    return target


def prepare_workspace_retirement(
    path: Path,
    state: WorkspaceState,
    *,
    retired_root: Path | None = None,
    identity: WorkspaceIdentity | None = None,
    checkout_only: bool = False,
) -> Path | str:
    """Prepare the exact retirement record before acquiring the database writer."""
    if path.is_symlink() or path.parent.is_symlink():
        return "workspace path is a symlink"
    identity = identity or read_workspace_identity(path)
    if read_workspace_identity(path) != identity:
        return "physical workspace identity changed"
    retired_root = retired_root or path.parent.parent / ".ticket-retired"
    if retired_root.is_symlink():
        return "retirement directory is a symlink"
    retired_root.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4

    token = uuid4().hex
    target = retired_root / f"{state.ticket_id}-{identity.workspace_id}-{token}"
    record_path = retired_root / f"{token}.json"
    from planner.conversation.worker_scratch import snapshot_ticket_scratch

    scratch_names = snapshot_ticket_scratch(state.ticket_id)
    # Write ahead: a crash after rename still leaves an exact owned target to retry.
    if checkout_only:
        target.mkdir()
        _write_json(target / OWNERSHIP_FILE, json.loads((path / OWNERSHIP_FILE).read_text()))
    _write_json(
        record_path,
        {
            "source": str(path),
            "target": str(target),
            "ticket_id": state.ticket_id,
            "machine_id": identity.machine_id,
            "workspace_id": identity.workspace_id,
            "ended_at": state.ended_at,
            "deleted": state.deleted,
            "scratch_names": scratch_names,
            "checkout_only": checkout_only,
        },
    )
    return target


def detach_workspace(path: Path, target: Path, *, checkout_only: bool) -> None:
    if not checkout_only:
        path.rename(target)
        return
    (path / WORKTREE_DIRECTORY).rename(target / WORKTREE_DIRECTORY)
    try:
        marker_path = path / OWNERSHIP_FILE
        marker = json.loads(marker_path.read_text())
        for key in (
            "worktree",
            "repository",
            "branch",
            "preparing",
            "initial_commit",
            "checkout_kind",
            "temporary",
        ):
            marker.pop(key, None)
        marker["workspace_id"] = uuid4().hex
        _write_json(marker_path, marker)
    except BaseException:
        (target / WORKTREE_DIRECTORY).rename(path / WORKTREE_DIRECTORY)
        raise


def _remove_retired(path: Path) -> str | None:
    """Repair Git's backlink after atomic folder rename, then remove safe work."""
    for checkout in _checkouts(path):
        if (checkout / ".git").is_file():
            result = _git(checkout, "worktree", "repair", str(checkout))
            if result.returncode:
                return f"cannot repair retired worktree: {result.stderr.strip()}"
    from planner.conversation.worker_scratch import prune_ticket_scratch

    token = path.name.rsplit("-", 1)[-1]
    record = json.loads((path.parent / f"{token}.json").read_text())
    if record["target"] != str(path):
        return "retirement target differs from its record"
    if record.get("checkout_only"):
        source = Path(record["source"])
        marker_path = source / OWNERSHIP_FILE
        if source.is_dir() and not (source / WORKTREE_DIRECTORY).exists():
            try:
                identity = read_workspace_identity(source)
                if identity == WorkspaceIdentity(record["machine_id"], record["workspace_id"]):
                    marker = json.loads(marker_path.read_text())
                    for key in (
                        "worktree",
                        "repository",
                        "branch",
                        "preparing",
                        "initial_commit",
                        "checkout_kind",
                        "temporary",
                    ):
                        marker.pop(key, None)
                    marker["workspace_id"] = uuid4().hex
                    _write_json(marker_path, marker)
            except (OSError, ValueError, KeyError):
                return "cannot recover retained Ticket folder ownership"
    names = record.get("scratch_names", [])
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        return "invalid retirement scratch snapshot"
    if not prune_ticket_scratch(record["ticket_id"], names):
        return "Worker scratch path is not an owned real directory"
    return cleanup_ticket_folder(path)


def retry_retired_workspaces(
    retired_root: Path,
    *,
    authorize_retirement: Callable[[WorkspaceState, WorkspaceIdentity], bool] | None = None,
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
            )
            identity = WorkspaceIdentity(record["machine_id"], record["workspace_id"])
            if authorize_retirement is not None and not authorize_retirement(state, identity):
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
