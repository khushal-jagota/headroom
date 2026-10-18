"""Bounded launchd adapter: maintenance observes exit and never kills a process."""

from __future__ import annotations

import math
import os
import plistlib
import re
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from planner.environments.resident.contracts import (
    PreparedResidentPackage,
    ResidentError,
    ServiceConfiguration,
)
from planner.environments.resident.control import OwnerExecutorControl
from planner.environments.resident.launch_agent import render_launch_agent
from planner.environments.resident.logic.update import safe_quiescence
from planner.environments.resident.paths import data_paths
from planner.executor.contracts import QuiescenceReceipt, QuiesceRequest

CommandRunner = Callable[[tuple[str, ...], float], subprocess.CompletedProcess[str]]


def run_launchctl(command: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


@dataclass(frozen=True)
class _Job:
    process_id: int | None
    runs: int
    last_exit_code: int | None


def _job(output: str) -> _Job:
    def number(name: str) -> int | None:
        matches = re.findall(rf"^\s*{re.escape(name)} = (-?[0-9]+)\s*$", output, re.MULTILINE)
        if len(matches) > 1:
            raise ResidentError(f"Ambiguous launchd {name}")
        return int(matches[0]) if matches else None

    runs = number("runs")
    pid = number("pid")
    if runs is None or runs < 1 or (pid is not None and pid <= 0):
        raise ResidentError("Launchd observation lacks a valid process run count")
    return _Job(pid, runs, number("last exit code"))


class LaunchAgentService:
    """Requires a receipt-bound pre-quiesce checkpoint to detect unseen restarts.

    The installed plist must use only RunAtLoad and SuccessfulExit=false.
    A successful maintenance exit therefore stays stopped; abnormal exits restart.
    No method sends kill, bootout, disable, or a kickstart -k operation.
    """

    def __init__(
        self,
        configuration: ServiceConfiguration,
        control: OwnerExecutorControl,
        *,
        timeout_seconds: float,
        poll_seconds: float,
        run: CommandRunner = run_launchctl,
    ) -> None:
        if configuration.launch is None:
            raise ResidentError("Production service requires explicit executor transport arguments")
        render_launch_agent(configuration)
        if (
            not math.isfinite(timeout_seconds)
            or not math.isfinite(poll_seconds)
            or timeout_seconds <= 0
            or poll_seconds <= 0
        ):
            raise ResidentError("Service deadlines must be positive")
        self.configuration = configuration
        self.control = control
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.run = run
        self._checkpoint: tuple[QuiesceRequest, int] | None = None

    @property
    def target(self) -> str:
        return f"gui/{self.configuration.user_id}/{self.configuration.label}"

    def _print(self, timeout: float) -> _Job:
        result = self.run(("launchctl", "print", self.target), timeout)
        if result.returncode:
            raise ResidentError(f"Launchd job cannot be observed: {result.stderr}")
        return _job(result.stdout)

    def prepare_quiesce(self, request: QuiesceRequest) -> None:
        """Called immediately before IPC request; baseline is not a stop authority."""
        self._checkpoint = None
        job = self._print(self.timeout_seconds)
        if job.process_id != request.expected_process_id:
            raise ResidentError("Launchd process changed before quiesce")
        self._checkpoint = (request, job.runs)

    def stop(self, expected: QuiescenceReceipt) -> None:
        checkpoint, self._checkpoint = self._checkpoint, None
        if (
            checkpoint is None
            or expected.identity != checkpoint[0].expected_identity
            or expected.process_id != checkpoint[0].expected_process_id
            or not checkpoint[0].exit_when_ready
            or not expected.exit_committed
            or not safe_quiescence(expected.quiescence)
        ):
            raise ResidentError("Exit receipt lacks its exact launchd quiesce checkpoint")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResidentError("Receipt process did not exit within maintenance deadline")
            job = self._print(remaining)
            if job.runs != checkpoint[1]:
                raise ResidentError("Launchd restarted; old receipt cannot authorize cutover")
            if job.process_id is None:
                if job.last_exit_code != 0:
                    raise ResidentError("Maintenance exit was not successful")
                return
            if job.process_id != expected.process_id:
                raise ResidentError("Replacement process cannot be stopped by an earlier receipt")
            time.sleep(min(self.poll_seconds, max(0, deadline - time.monotonic())))

    def _configured_job(self, output: str) -> None:
        expected = plistlib.loads(render_launch_agent(self.configuration))
        matches = re.findall(r"^\s*arguments = \{\n(.*?)^\s*\}", output, re.MULTILINE | re.DOTALL)
        if len(matches) != 1:
            raise ResidentError("Launchd arguments cannot be verified")
        actual = [line.strip() for line in matches[0].splitlines() if line.strip()]
        if actual != expected["ProgramArguments"]:
            raise ResidentError(
                "Loaded LaunchAgent arguments differ; explicit configuration action needed"
            )
        for name, value in expected["EnvironmentVariables"].items():
            values = re.findall(rf"^\s*{re.escape(name)} => (.*?)\s*$", output, re.MULTILINE)
            if values != [value]:
                raise ResidentError(f"Loaded LaunchAgent environment differs: {name}")

    def install(self, prepared: PreparedResidentPackage) -> None:
        """Explicit first install only: no running job replacement or privileged fallback."""
        if prepared.app.resolve() != self.configuration.paths.app.resolve():
            raise ResidentError("Service install must use installed source")
        deadline = time.monotonic() + self.timeout_seconds
        result = self.run(("launchctl", "print", self.target), self.timeout_seconds)
        if result.returncode != 113 or "Could not find service" not in result.stderr:
            raise ResidentError("First install requires a positively absent launchd job")
        directory = self.configuration.paths.home / "Library" / "LaunchAgents"
        for path in (directory, *directory.parents):
            if path.is_symlink():
                raise ResidentError("Symbolic link in LaunchAgent installation path")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o022:
            raise ResidentError("LaunchAgents directory must be owned and writable only by account")
        data = data_paths(self.configuration.paths)
        for path in (data.root, data.logs, data.run):
            if any(item.is_symlink() for item in (path, *path.parents)):
                raise ResidentError("Symbolic link in executor data path")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != 0o700:
                raise ResidentError("Executor data directories must be owner-only")
        plist = directory / f"{self.configuration.label}.plist"
        content = render_launch_agent(self.configuration)
        if plist.is_symlink():
            raise ResidentError("LaunchAgent plist must not be a symbolic link")
        if plist.exists():
            if (
                not plist.is_file()
                or plist.read_bytes() != content
                or plist.stat().st_uid != os.getuid()
                or stat.S_IMODE(plist.stat().st_mode) != 0o600
            ):
                raise ResidentError(
                    "Existing LaunchAgent plist differs from requested installation"
                )
        else:
            temporary = directory / f".panels-{uuid4().hex}.plist"
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, plist)
            finally:
                temporary.unlink(missing_ok=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResidentError("Installation deadline expired before bootstrap")
        started_at = time.time_ns() // 1_000_000
        result = self.run(
            ("launchctl", "bootstrap", f"gui/{self.configuration.user_id}", str(plist)), remaining
        )
        if result.returncode:
            raise ResidentError(f"LaunchAgent bootstrap failed: {result.stderr}")
        self.control.wait_for_health(
            prepared.package,
            not_before_ms=started_at,
            timeout_seconds=deadline - time.monotonic(),
            poll_seconds=self.poll_seconds,
        )

    def start(self, prepared: PreparedResidentPackage) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        if prepared.app.resolve() != self.configuration.paths.app.resolve():
            raise ResidentError("Service start must use the installed source path")
        result = self.run(("launchctl", "print", self.target), self.timeout_seconds)
        if result.returncode:
            raise ResidentError(f"Launchd job cannot be observed: {result.stderr}")
        self._configured_job(result.stdout)
        before = _job(result.stdout)
        if before.process_id is not None:
            raise ResidentError("Refusing to start over a running executor")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResidentError("Startup deadline expired before kickstart")
        started_at = time.time_ns() // 1_000_000
        result = self.run(("launchctl", "kickstart", self.target), remaining)
        if result.returncode:
            raise ResidentError(f"Launchd start failed: {result.stderr}")
        self.control.wait_for_health(
            prepared.package,
            not_before_ms=started_at,
            timeout_seconds=deadline - time.monotonic(),
            poll_seconds=self.poll_seconds,
        )
