"""Linux evidence associates observed processes with a worktree, not authorship.

Discovery requires exactly one readable same-UID listener owner. Linux may hide
non-dumpable processes, so this cannot exclude additional hidden socket holders.
The pinned process and its accepted reverse TCP tuple must always be readable.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from planner.previews import config
from planner.previews.contracts import PreviewError, ProcessIdentity


class LinuxProcessInspector:
    def __init__(self, proc: Path = Path("/proc")) -> None:
        self.proc = proc

    def _deadline(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise PreviewError("Process inspection exceeded its deadline.", 503)

    def _read(self, path: Path, deadline: float) -> str:
        self._deadline(deadline)
        try:
            result = path.read_text()
        except OSError as exc:
            raise PreviewError("Process evidence is missing or unreadable.", 410) from exc
        self._deadline(deadline)
        return result

    def _identity(
        self, pid: int, inode: int, port: int, worktree: Path, deadline: float
    ) -> ProcessIdentity:
        root = self.proc / str(pid)
        stat = self._read(root / "stat", deadline)
        try:
            ticks = int(stat[stat.rindex(")") + 2 :].split()[19])
            cwd = (root / "cwd").resolve(strict=True)
            cwd.relative_to(worktree)
        except (OSError, ValueError, IndexError) as exc:
            raise PreviewError("Listener cwd is outside the Ticket preview worktree.", 410) from exc
        return ProcessIdentity(
            self._read(self.proc / "sys/kernel/random/boot_id", deadline).strip(),
            pid,
            ticks,
            inode,
            port,
            str(worktree),
        )

    def _fds(self, pid: int, deadline: float, budget: list[int]) -> set[int]:
        inodes: set[int] = set()
        try:
            with os.scandir(self.proc / str(pid) / "fd") as entries:
                for entry in entries:
                    self._deadline(deadline)
                    budget[0] += 1
                    if budget[0] > config.DISCOVERY_FDS:
                        raise PreviewError("Process descriptor inspection bound exceeded.", 503)
                    try:
                        target = os.readlink(entry.path)
                    except FileNotFoundError:
                        continue
                    if target.startswith("socket:[") and target.endswith("]"):
                        inodes.add(int(target[8:-1]))
        except FileNotFoundError:
            return set()
        except PermissionError as exc:
            raise PreviewError("Cannot inspect process socket ownership.", 410) from exc
        return inodes

    def _tcp(self, deadline: float) -> list[tuple[str, str, str, int]]:
        result = []
        for name in ("tcp", "tcp6"):
            for line in self._read(self.proc / "net" / name, deadline).splitlines()[1:]:
                self._deadline(deadline)
                columns = line.split()
                if len(columns) < 10:
                    raise PreviewError("Malformed kernel socket evidence.", 410)
                result.append((columns[1], columns[2], columns[3], int(columns[9])))
        return result

    def discover(self, port: int, worktree: Path) -> ProcessIdentity:
        deadline = time.monotonic() + config.INSPECTION_SECONDS
        listeners = [
            row
            for row in self._tcp(deadline)
            if row[2] == "0A" and int(row[0].split(":")[1], 16) == port
        ]
        if len(listeners) != 1 or listeners[0][0].split(":")[0] != "0100007F":
            raise PreviewError("Require exactly one listener bound to 127.0.0.1.")
        inode = listeners[0][3]
        owners = []
        budget = [0]
        try:
            with os.scandir(self.proc) as entries:
                pids = [int(entry.name) for entry in entries if entry.name.isdecimal()]
        except OSError as exc:
            raise PreviewError("Linux process evidence is unavailable.") from exc
        if len(pids) > config.DISCOVERY_PIDS:
            raise PreviewError("Process discovery bound exceeded.", 503)
        for pid in pids:
            self._deadline(deadline)
            try:
                if (self.proc / str(pid)).stat().st_uid != os.getuid():
                    continue
            except FileNotFoundError:
                continue
            try:
                owned = self._fds(pid, deadline, budget)
            except PreviewError as exc:
                if isinstance(exc.__cause__, PermissionError):
                    # Linux can hide non-dumpable same-UID processes. We prove one
                    # readable owner; hidden holders are outside this evidence.
                    continue
                raise
            if inode in owned:
                owners.append(pid)
        if len(owners) != 1:
            raise PreviewError("Listener ownership is missing or shared.")
        identity = self._identity(owners[0], inode, port, worktree, deadline)
        self.check(identity, deadline=deadline)
        return identity

    def check(self, identity: ProcessIdentity, *, deadline: float | None = None) -> None:
        deadline = (
            deadline if deadline is not None else time.monotonic() + config.INSPECTION_SECONDS
        )
        worktree = Path(identity.worktree)
        if (
            self._identity(identity.pid, identity.listener_inode, identity.port, worktree, deadline)
            != identity
        ):
            raise PreviewError("Preview process identity changed.", 410)
        listeners = [
            row
            for row in self._tcp(deadline)
            if row[2] == "0A" and int(row[0].split(":")[1], 16) == identity.port
        ]
        if (
            len(listeners) != 1
            or listeners[0][0] != f"0100007F:{identity.port:04X}"
            or listeners[0][3] != identity.listener_inode
        ):
            raise PreviewError("Preview listener ended or changed.", 410)
        if identity.listener_inode not in self._fds(identity.pid, deadline, [0]):
            raise PreviewError("Preview process no longer owns its listener.", 410)

    def connected(self, identity: ProcessIdentity, local_port: int) -> None:
        deadline = time.monotonic() + config.INSPECTION_SECONDS
        while True:
            self.check(identity, deadline=deadline)
            rows = self._tcp(deadline)
            reverse = [
                row[3]
                for row in rows
                if row[0] == f"0100007F:{identity.port:04X}"
                and row[1] == f"0100007F:{local_port:04X}"
                and row[2] == "01"
            ]
            if len(reverse) == 1 and reverse[0] in self._fds(identity.pid, deadline, [0]):
                self.check(identity, deadline=deadline)
                return
            self._deadline(deadline)
            time.sleep(min(config.ACCEPT_RETRY_SECONDS, max(0, deadline - time.monotonic())))


def validate_worktree(project_folder: Path, ticket_id: str) -> Path:
    """Require the exact reserved directory, with no symlink components or bare repo."""
    deadline = time.monotonic() + config.INSPECTION_SECONDS
    folder = project_folder.absolute()
    candidate = folder / "worktrees" / f"{ticket_id}-preview"
    try:
        if folder.resolve(strict=True) != folder or candidate.resolve(strict=True) != candidate:
            raise PreviewError("Preview worktree path must not contain symlink components.")
        result = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=max(0.001, deadline - time.monotonic()),
            check=False,
        )
        if result.returncode or Path(result.stdout.strip()) != candidate:
            raise PreviewError("The reserved path must be a Git worktree root.")
        listed = subprocess.run(
            ["git", "-C", str(candidate), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=max(0.001, deadline - time.monotonic()),
            check=False,
        )
        if listed.returncode or f"worktree {candidate}\n" not in listed.stdout:
            raise PreviewError("The reserved directory is not a registered Git worktree.")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreviewError("Cannot validate the Ticket preview Git worktree.") from exc
    return candidate
