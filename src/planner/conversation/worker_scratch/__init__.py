"""Short, disk-backed scratch owned by one Worker backend process."""

from __future__ import annotations

import errno
import fcntl
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path


class WorkerScratchError(OSError):
    """The Worker's scratch storage or ownership could not be established."""


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


@contextmanager
def _locked_root() -> Iterator[Path]:
    # This lock file stays in place so unlink/recreate cannot split ownership.
    root = _scratch_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise WorkerScratchError(f"Worker scratch root must be a private real directory: {root}")
    descriptor = os.open(root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise WorkerScratchError("Invalid scratch root lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield root
    finally:
        os.close(descriptor)


def _remove_unowned(directory: Path) -> bool:
    if not directory.exists() and not directory.is_symlink():
        return True
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        return False
    try:
        descriptor = os.open(directory / ".owner", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        shutil.rmtree(directory)
        return True
    finally:
        os.close(descriptor)


def _prune(root: Path, ticket_id: str) -> bool:
    complete = True
    for directory in root.iterdir():
        if re.fullmatch(re.escape(ticket_id) + r"-[0-9a-f]{6}", directory.name):
            if not _remove_unowned(directory):
                complete = False
    return complete


def prune_ticket_scratch(ticket_id: str) -> bool:
    """Remove only this Ticket's unlocked runs; retain live or unknown ownership."""
    _ticket_name(ticket_id)
    if not _scratch_root().exists() and not _scratch_root().is_symlink():
        return True
    with _locked_root() as root:
        return _prune(root, ticket_id)


class WorkerScratch:
    def __init__(self, environment: Mapping[str, str]) -> None:
        ticket_id = environment["PLAN_TICKET_ID"]
        _ticket_name(ticket_id)
        self._descriptor: int | None = None
        with _locked_root() as root:
            _disk_directory(root)
            _prune(root, ticket_id)
            while True:
                self.directory = root / f"{ticket_id}-{secrets.token_hex(3)}"
                try:
                    self.directory.mkdir(mode=0o700)
                    break
                except FileExistsError:
                    continue
            try:
                descriptor = os.open(
                    self.directory / ".owner", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600,
                )
                self._descriptor = descriptor
                fcntl.flock(descriptor, fcntl.LOCK_SH)
                info = os.fstat(descriptor)
                self._owner_identity = (info.st_dev, info.st_ino)
            except BaseException:
                if self._descriptor is not None:
                    os.close(self._descriptor)
                    self._descriptor = None
                shutil.rmtree(self.directory)
                raise
        self.environment = dict(environment)
        self.environment.update(dict.fromkeys(("TMPDIR", "TMP", "TEMP"), str(self.directory)))

    def argv(self, command: Sequence[str], working_directory: Path) -> tuple[str, ...]:
        """Exec the provider with a second ownership lock, without another process."""
        if not command:
            raise WorkerScratchError("The Worker command is empty")
        executable = command[0]
        found: str | None
        if "/" in executable:
            candidate = Path(executable)
            if not candidate.is_absolute():
                candidate = working_directory / candidate
            found = str(candidate.absolute())
            if not candidate.exists():
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), executable)
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), executable)
        else:
            path = os.pathsep.join(
                str((working_directory / item).absolute())
                for item in self.environment.get("PATH", os.defpath).split(os.pathsep)
            )
            found = shutil.which(executable, path=path)
            if found is None:
                for folder in path.split(os.pathsep):
                    try:
                        (Path(folder) / executable).stat()
                    except FileNotFoundError:
                        continue
                    except PermissionError:
                        pass
                    raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), executable)
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), executable)
        return (
            sys.executable, str(Path(__file__).with_name("exec.py")), str(self.directory),
            str(self._owner_identity[0]), str(self._owner_identity[1]), "--", found, *command[1:],
        )

    def close(self) -> None:
        with _locked_root():
            if self._descriptor is not None:
                os.close(self._descriptor)
                self._descriptor = None
            _remove_unowned(self.directory)
