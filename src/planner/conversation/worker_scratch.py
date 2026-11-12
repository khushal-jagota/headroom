"""Short, disk-backed disposable files for one current Worker backend process."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path


class WorkerScratchError(OSError):
    """The Worker's scratch storage could not be established."""


def _scratch_root() -> Path:
    return Path.home() / ".pw"


def _disk_directory(path: Path) -> None:
    resolved = path.resolve()
    mounts: list[tuple[int, str]] = []
    if sys.platform == "linux":
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            before, after = line.split(" - ", 1)
            mount = re.sub(
                r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), before.split()[4],
            )
            if resolved.is_relative_to(Path(mount)):
                mounts.append((len(Path(mount).parts), after.split()[0]))
    elif sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/sbin/mount"], capture_output=True, text=True, check=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise WorkerScratchError("Cannot inspect the Mac scratch filesystem") from error
        for line in result.stdout.splitlines():
            match = re.fullmatch(r".+ on (.+) \(([^,)]+).+\)", line)
            if match and resolved.is_relative_to(Path(match[1])):
                mounts.append((len(Path(match[1]).parts), match[2]))
    if not mounts or max(mounts)[1] in {"tmpfs", "ramfs", "devfs", "mfs"}:
        raise WorkerScratchError(f"Worker temporary storage must use disk: {path}")



def _ticket_name(ticket_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", ticket_id):
        raise WorkerScratchError("Invalid Ticket identity for scratch")


def _prepare_root() -> Path:
    root = _scratch_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise WorkerScratchError(f"Worker scratch root must be a private real directory: {root}")
    return root


def _prune(root: Path, ticket_id: str, keep: Path | None = None) -> bool:
    complete = True
    for directory in root.iterdir():
        if directory == keep:
            continue
        if re.fullmatch(re.escape(ticket_id) + r"-[0-9a-f]{6}", directory.name):
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                complete = False
                continue
            shutil.rmtree(directory)
    return complete


def prune_ticket_scratch(ticket_id: str) -> bool:
    """Remove this Ticket's disposable folders when its workspace retires."""
    _ticket_name(ticket_id)
    if not _scratch_root().exists() and not _scratch_root().is_symlink():
        return True
    return _prune(_prepare_root(), ticket_id)


class WorkerScratch:
    def __init__(self, environment: Mapping[str, str]) -> None:
        ticket_id = environment["PLAN_TICKET_ID"]
        _ticket_name(ticket_id)
        root = _prepare_root()
        _disk_directory(root)
        while True:
            self.directory = root / f"{ticket_id}-{secrets.token_hex(3)}"
            try:
                self.directory.mkdir(mode=0o700)
                break
            except FileExistsError:
                continue
        try:
            _prune(root, ticket_id, keep=self.directory)
        except BaseException:
            self.close()
            raise
        self.environment = dict(environment)
        self.environment.update(dict.fromkeys(("TMPDIR", "TMP", "TEMP"), str(self.directory)))

    def close(self) -> None:
        if self.directory.exists() and not self.directory.is_symlink():
            shutil.rmtree(self.directory)
