"""A Linux Worker child and its descendants live outside the Panels service.

The backend owns this object for the lifetime of its child, not for one turn.
There is no uncontained fallback when a Linux Worker cannot start its unit.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

from planner.conversation.worker_unit.config import (
    CONTROL_TIMEOUT_SECONDS,
    SLICE_CPU_WEIGHT_MAX,
    WORKER_SLICE,
    WORKER_TEMP_ROOT,
)


class WorkerUnitError(RuntimeError):
    """The required Worker containment could not be established or stopped."""


async def _control(*arguments: str) -> tuple[int, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            "systemctl", "--user", *arguments,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise WorkerUnitError(f"Cannot contact the Worker service manager: {error}") from error
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), CONTROL_TIMEOUT_SECONDS,
        )
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return process.returncode or 0, (stdout or stderr).decode(errors="replace").strip()


def _properties(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def _environment_file(environment: Mapping[str, str]) -> str:
    """Quote EnvironmentFile values without shell or systemd variable expansion."""
    lines = []
    for name, value in environment.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or "\0" in value:
            raise WorkerUnitError(f"Invalid environment entry for Worker: {name!r}")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        escaped = escaped.replace("$", "\\$").replace("`", "\\`")
        lines.append(f'{name}="{escaped}"\n')
    return "".join(lines)


def _disk_directory(path: Path) -> None:
    """Reject memory filesystems, including a home directory mounted on tmpfs."""
    resolved = path.resolve()
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, after = line.split(" - ", 1)
        mount = re.sub(
            r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), before.split()[4],
        )
        mount_path = Path(mount)
        if resolved.is_relative_to(mount_path):
            mounts.append((len(mount_path.parts), after.split()[0]))
    if not mounts or max(mounts)[1] in {"tmpfs", "ramfs"}:
        raise WorkerUnitError(f"Worker temporary storage must use disk: {path}")


class WorkerUnit:
    """The transient service and private temporary files for one backend child."""

    def __init__(
        self, *, name: str, directory: Path, environment: dict[str, str],
        working_directory: Path, slice_name: str,
    ) -> None:
        self.name = name
        self.directory = directory
        self.environment = environment
        self.working_directory = working_directory
        self.slice_name = slice_name
        self._finished = False

    @classmethod
    async def create(
        cls, environment: Mapping[str, str], working_directory: Path, *,
        slice_name: str = WORKER_SLICE,
    ) -> WorkerUnit | None:
        if sys.platform != "linux" or not environment.get("PLAN_TICKET_ID"):
            return None
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.slice", slice_name):
            raise WorkerUnitError("Invalid Worker slice name")
        code, text = await _control(
            "show", slice_name, "--property=LoadState", "--property=CPUWeight",
        )
        properties = _properties(text)
        try:
            weight = int(properties.get("CPUWeight", "0"))
        except ValueError:
            weight = 0
        if (
            code or properties.get("LoadState") != "loaded"
            or not 0 < weight <= SLICE_CPU_WEIGHT_MAX
        ):
            raise WorkerUnitError(
                f"Worker slice {slice_name} is unavailable or lacks its lower CPU weight. "
                "Install the Worker slice before starting Workers."
            )
        # The worker slice is a child of panels.slice. Its weight alone does not
        # set priority relative to app.slice, which contains the live service.
        if slice_name == WORKER_SLICE:
            code, text = await _control(
                "show", "panels.slice", "--property=LoadState", "--property=CPUWeight",
            )
            parent = _properties(text)
            try:
                parent_weight = int(parent.get("CPUWeight", "0"))
            except ValueError:
                parent_weight = 0
            if (
                code or parent.get("LoadState") != "loaded"
                or not 0 < parent_weight <= SLICE_CPU_WEIGHT_MAX
            ):
                raise WorkerUnitError(
                    "Install panels.slice with its lower CPU weight before Workers."
                )
        # UNIX sockets have a short pathname limit. Keep the disk root short
        # enough for pytest and tool-created subdirectories beneath TMPDIR.
        # PrivateTmp also hides /var/tmp from the user manager. The real home
        # is shared with that manager, and a direct child keeps socket paths short.
        root = Path(WORKER_TEMP_ROOT).expanduser()
        _disk_directory(root)
        directory = Path(tempfile.mkdtemp(prefix=".pw-", dir=root))
        try:
            directory.chmod(0o700)
            if directory.stat().st_uid != os.getuid():
                raise WorkerUnitError("Worker temporary directory has a different owner")
            temporary = directory / "t"
            temporary.mkdir(mode=0o700)
            child_environment = dict(environment)
            child_environment.update(TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
            file = directory / "environment"
            with file.open("x", encoding="utf-8") as output:
                file.chmod(0o600)
                output.write(_environment_file(child_environment))
            return cls(
                name=f"panels-worker-{uuid.uuid4().hex}.service", directory=directory,
                environment=child_environment, working_directory=working_directory,
                slice_name=slice_name,
            )
        except BaseException:
            shutil.rmtree(directory)
            raise

    def argv(self, original: Sequence[str]) -> tuple[str, ...]:
        """Keep protocol streams attached while systemd owns the actual child."""
        if not original:
            raise WorkerUnitError("The Worker command is empty")
        executable = original[0]
        if "/" in executable:
            candidate = Path(executable)
            if not candidate.is_absolute():
                candidate = self.working_directory / candidate
            # Keep virtualenv and other executable symlinks intact.
            executable = str(candidate.absolute())
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                raise WorkerUnitError(f"Worker executable is unavailable: {original[0]}")
        else:
            path = self.environment.get("PATH", os.defpath)
            search = os.pathsep.join(
                str((self.working_directory / entry).absolute())
                for entry in path.split(os.pathsep)
            )
            found = shutil.which(executable, path=search)
            if found is None:
                raise WorkerUnitError(f"Worker executable is unavailable: {original[0]}")
            executable = found
        directory = str(self.directory).replace("%", "%%")
        return (
            "systemd-run", "--user", "--quiet", "--wait", "--pipe",
            "--service-type=exec", "--expand-environment=no", f"--unit={self.name}",
            # A process killed for memory ends alone; the agent and its turn go on.
            f"--slice={self.slice_name}",
            "--property=OOMPolicy=continue", "--property=KillMode=control-group",
            "--property=NoNewPrivileges=yes",
            f"--property=EnvironmentFile={directory}/environment",
            f"--property=WorkingDirectory={str(self.working_directory).replace('%', '%%')}",
            f"--property=ExecStopPost=/bin/rm -rf -- {shlex.quote(directory)}",
            "--", executable, *original[1:],
        )

    async def stop(self) -> None:
        """Stop every descendant before deleting temporary files."""
        if self._finished:
            return
        code, text = await _control("show", self.name, "--property=LoadState")
        if not code and _properties(text).get("LoadState") == "loaded":
            code, text = await _control("stop", self.name)
            if code:
                raise WorkerUnitError(f"Cannot stop Worker unit {self.name}: {text}")
            # A failed transient unit remains addressable until its result is reset.
            await _control("reset-failed", self.name)
        elif code and "not found" not in text and "not loaded" not in text:
            raise WorkerUnitError(f"Cannot inspect Worker unit {self.name}: {text}")
        shutil.rmtree(self.directory, ignore_errors=True)
        self._finished = True
