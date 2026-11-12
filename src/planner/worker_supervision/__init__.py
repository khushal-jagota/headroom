"""One owner for Worker launch, durable process custody, and bounded cleanup."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import stat
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from planner.conversation.local_child_contracts import LocalChildIdentity
from planner.platform_runtime.selection import get_platform_adapter
from planner.worker_supervision import config, records
from planner.worker_supervision.contracts import CleanupResult, WorkerCleanupError, WorkerProcess

__all__ = [
    "CleanupResult",
    "WorkerCleanupError",
    "WorkerSupervisor",
    "inspect_record",
    "reconcile",
    "stop_record",
]


def _process(raw: Any) -> WorkerProcess:
    if not isinstance(raw, dict):
        raise ValueError("Invalid recorded process")
    process = WorkerProcess(**raw)
    if (
        type(process.pid) is not int
        or process.pid <= 0
        or not isinstance(process.birth, str)
        or not process.birth
        or type(process.parent) is not int
        or type(process.group) is not int
        or type(process.session) is not int
        or type(process.zombie) is not bool
    ):
        raise ValueError("Invalid recorded process identity")
    return process


def _alive(process: WorkerProcess, table: dict[int, WorkerProcess]) -> bool:
    current = table.get(process.pid)
    return current is not None and current.birth == process.birth and not current.zombie


def _discover(record: dict[str, Any], table: dict[int, WorkerProcess]) -> list[WorkerProcess]:
    known = {_process(raw) for raw in record["processes"]}
    roots = {p.pid for p in known if _alive(p, table)}
    # A surviving authenticated member anchors its group/session, never a number alone.
    groups = (
        {(table[pid].group, table[pid].session) for pid in roots}
        if record.get("isolated")
        else set()
    )
    changed = True
    while changed:
        changed = False
        for process in table.values():
            if process.pid in roots or process.zombie:
                continue
            if process.parent in roots or (process.group, process.session) in groups:
                roots.add(process.pid)
                known.add(process)
                if record.get("isolated"):
                    groups.add((process.group, process.session))
                changed = True
    return sorted(known, key=lambda p: (p.pid, p.birth))


def _observe_unlocked(path: Path, deadline: float) -> dict[str, Any]:
    record = records.read(path)
    primitives = get_platform_adapter().worker_processes()
    host = primitives.host_boot(deadline)
    if host.host != record.get("host"):
        raise ValueError("Worker process record belongs to another machine")
    if host.boot != record.get("boot"):
        record.update(state="complete", reason="The recorded boot ended")
    elif record.get("state") != "complete":
        first = primitives.snapshot(deadline)
        second = primitives.snapshot(deadline)
        # A sequential inventory is not atomic. Authenticate both ends of each
        # discovered relationship across two observations before custody expands.
        stable = {pid: process for pid, process in second.items() if first.get(pid) == process}
        record["processes"] = [asdict(p) for p in _discover(record, stable)]
    records.write(path, record)
    return record


def _observe(path: Path, deadline: float) -> dict[str, Any]:
    with records.locked(path, deadline):
        return _observe_unlocked(path, deadline)


def inspect_record(path: Path, *, identity: LocalChildIdentity | None = None) -> CleanupResult:
    deadline = time.monotonic() + config.INSPECTION_SECONDS
    try:
        with records.locked(path, deadline):
            return _inspect_record(path, identity=identity, deadline=deadline)
    except (OSError, ValueError, TimeoutError) as error:
        return CleanupResult(False, f"Worker cleanup evidence unavailable: {error}")


def stop_record(
    path: Path,
    *,
    identity: LocalChildIdentity | None = None,
    deadline: float | None = None,
) -> CleanupResult:
    deadline = time.monotonic() + config.STOP_SECONDS if deadline is None else deadline
    try:
        with records.locked(path, deadline):
            return _stop_record(path, identity=identity, deadline=deadline)
    except (OSError, ValueError, TimeoutError) as error:
        return CleanupResult(False, f"Worker cleanup not proven: {error}")


def _inspect_record(
    path: Path, *, identity: LocalChildIdentity | None = None, deadline: float
) -> CleanupResult:
    """Read proof without signals; absence or unreadable evidence never means complete."""
    try:
        record = _observe_unlocked(path, deadline)
        if identity is not None:
            root = record.get("root")
            if root is None or (root["pid"], root["birth"], record["boot"]) != (
                identity.pid,
                identity.birth,
                identity.boot,
            ):
                return CleanupResult(False, "Worker custody does not match its process record")
        if record.get("state") == "complete":
            return CleanupResult(True, str(record.get("reason", "Recorded processes ended")))
        if record.get("root") is None:
            return CleanupResult(False, "Worker launch has no exact process registration")
        if record.get("observation_error"):
            return CleanupResult(False, str(record["observation_error"]))
        table = get_platform_adapter().worker_processes().snapshot(deadline)
        if any(_alive(_process(raw), table) for raw in record["processes"]):
            return CleanupResult(False, "Recorded Worker processes remain")
        record.update(state="complete", reason="All identified Worker processes ended")
        records.write(path, record)
        return CleanupResult(True, record["reason"])
    except (OSError, ValueError, TypeError, KeyError, TimeoutError) as error:
        return CleanupResult(False, f"Worker cleanup evidence unavailable: {error}")


def _stop_record(
    path: Path,
    *,
    identity: LocalChildIdentity | None = None,
    deadline: float | None = None,
) -> CleanupResult:
    """Stop only authenticated incarnations, then persist positive scoped proof."""
    deadline = time.monotonic() + config.STOP_SECONDS if deadline is None else deadline
    try:
        record = _observe_unlocked(path, deadline)
        if identity is not None:
            root = record.get("root")
            if root is None or (root["pid"], root["birth"], record["boot"]) != (
                identity.pid,
                identity.birth,
                identity.boot,
            ):
                return CleanupResult(False, "Worker custody does not match its process record")
        if record.get("state") == "complete":
            return CleanupResult(True, str(record["reason"]))
        if record.get("root") is None:
            return CleanupResult(False, "Worker launch has no exact process registration")
        primitives = get_platform_adapter().worker_processes()
        term_deadline = min(deadline, time.monotonic() + config.TERM_SECONDS)
        for signum, phase_deadline in ((signal.SIGTERM, term_deadline), (signal.SIGKILL, deadline)):
            record = _observe_unlocked(path, deadline)
            table = primitives.snapshot(deadline)
            remaining = [
                _process(raw) for raw in record["processes"] if _alive(_process(raw), table)
            ]
            for process in remaining:
                if not primitives.signal(process, signum, deadline):
                    record.update(
                        state="not_proven", reason="No safe signal handle for a recorded process"
                    )
                    records.write(path, record)
                    return CleanupResult(False, record["reason"])
            while time.monotonic() < phase_deadline:
                record = _observe_unlocked(path, deadline)
                table = primitives.snapshot(deadline)
                if not any(_alive(_process(raw), table) for raw in record["processes"]):
                    if record.get("observation_error"):
                        return CleanupResult(False, str(record["observation_error"]))
                    record.update(state="complete", reason="All identified Worker processes ended")
                    records.write(path, record)
                    return CleanupResult(True, record["reason"])
                time.sleep(min(0.05, max(0, phase_deadline - time.monotonic())))
        record.update(state="not_proven", reason="Worker stop deadline expired")
        records.write(path, record)
        return CleanupResult(False, record["reason"])
    except (OSError, ValueError, TypeError, KeyError, TimeoutError) as error:
        return CleanupResult(False, f"Worker cleanup not proven: {error}")


def reconcile(root: Path, ticket_id: str | None = None) -> CleanupResult:
    """Reconcile durable old custody before another Worker starts."""
    deadline = time.monotonic() + config.STOP_SECONDS
    if not root.exists():
        return CleanupResult(True, "No recorded Worker launches")
    for path in sorted(root.glob("*.json")):
        try:
            record = records.read(path)
        except (OSError, ValueError) as error:
            return CleanupResult(False, f"Worker launch record is unreadable: {error}")
        if ticket_id is not None and record["ticket_id"] != ticket_id:
            continue
        if record.get("state") == "complete":
            continue
        try:
            primitives = get_platform_adapter().worker_processes()
            host = primitives.host_boot(deadline)
            if host.host != record.get("host"):
                return CleanupResult(False, "Prior Worker custody belongs to another machine")
            if host.boot != record.get("boot"):
                result = stop_record(path, deadline=deadline)
                if not result.complete:
                    return result
                continue
            table = primitives.snapshot(deadline)
            if record.get("owner") and _alive(_process(record["owner"]), table):
                return CleanupResult(False, "A live Worker supervisor still owns this record")
        except (OSError, ValueError, TimeoutError) as error:
            return CleanupResult(False, f"Worker supervisor identity is unknown: {error}")
        result = stop_record(path, deadline=deadline)
        if not result.complete:
            return result
    return CleanupResult(True, "All recorded Worker launches reconciled")


class WorkerSupervisor:
    def __init__(self, environment: Mapping[str, str], working_directory: Path) -> None:
        self.environment = dict(environment)
        self.working_directory = Path(working_directory)
        root_value = environment.get(config.RECORD_ROOT_ENV) or os.environ.get(
            config.RECORD_ROOT_ENV
        )
        db = environment.get("PLAN_DB_PATH")
        if not root_value and not db:
            raise WorkerCleanupError("Worker process record root is missing")
        root = (
            Path(root_value)
            if root_value
            else Path(str(db)).expanduser().resolve().parent / "worker-processes"
        )
        root = root.expanduser().absolute()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise WorkerCleanupError("Worker process record root must be private and owned")
        for prior in root.glob("*.json"):
            previous = records.read(prior)
            if (
                previous.get("ticket_id") != environment["PLAN_TICKET_ID"]
                or previous.get("state") == "complete"
            ):
                continue
            primitives = get_platform_adapter().worker_processes()
            deadline = time.monotonic() + config.INSPECTION_SECONDS
            host = primitives.host_boot(deadline)
            if host.host != previous.get("host"):
                raise WorkerCleanupError("Prior Worker custody belongs to another machine")
            if host.boot != previous.get("boot"):
                result = stop_record(prior)
                if not result.complete:
                    raise WorkerCleanupError(result.reason)
                continue
            table = primitives.snapshot(deadline)
            if previous.get("owner") and _alive(_process(previous["owner"]), table):
                raise WorkerCleanupError("Ticket already has retained Worker process custody")
            result = stop_record(prior)
            if not result.complete:
                raise WorkerCleanupError(result.reason)
        self.root = root
        self.environment[config.RECORD_ROOT_ENV] = str(root)
        self.path = root / f"{uuid4().hex}.json"
        self._lock = threading.Lock()
        self._end = threading.Event()
        self._observer: threading.Thread | None = None
        primitives = get_platform_adapter().worker_processes()
        deadline = time.monotonic() + config.INSPECTION_SECONDS
        host = primitives.host_boot(deadline)
        owner = primitives.snapshot(deadline).get(os.getpid())
        if owner is None:
            raise WorkerCleanupError("Worker supervisor identity is unavailable")
        records.write(
            self.path,
            {
                "version": 1,
                "ticket_id": environment["PLAN_TICKET_ID"],
                "host": host.host,
                "boot": host.boot,
                "owner": asdict(owner),
                "root": None,
                "processes": [],
                "isolated": False,
                "state": "pending",
                "conversation_id": environment.get("PLAN_CONVERSATION_ID"),
                "reason": "Launch pending",
            },
        )

    @classmethod
    async def create(
        cls, environment: Mapping[str, str], working_directory: Path
    ) -> WorkerSupervisor | None:
        if not environment.get("PLAN_TICKET_ID"):
            return None
        return await asyncio.to_thread(cls, environment, working_directory)

    def argv(self, original: Sequence[str]) -> tuple[str, ...]:
        if not original:
            raise ValueError("Worker command is empty")
        executable = original[0]
        if "/" in executable:
            candidate = Path(executable)
            if not candidate.is_absolute():
                candidate = self.working_directory / candidate
            executable = str(candidate.absolute())
        else:
            executable = (
                shutil.which(executable, path=self.environment.get("PATH", os.defpath)) or ""
            )
        if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            self.cancel_unspawned()
            raise FileNotFoundError(f"Worker executable is unavailable: {original[0]}")
        return (
            sys.executable,
            "-m",
            "planner.worker_supervision.launch",
            str(self.path),
            executable,
            *original[1:],
        )

    def cancel_unspawned(self) -> None:
        """Caller receipt: the OS refused spawn before any child existed."""
        with self._lock, records.locked(self.path, time.monotonic() + config.INSPECTION_SECONDS):
            record = records.read(self.path)
            if record.get("root") is not None:
                raise WorkerCleanupError("Cannot cancel a registered Worker process")
            record.update(state="complete", reason="The OS refused spawn before a child existed")
            records.write(self.path, record)

    async def started(self, pid: int) -> None:
        deadline = time.monotonic() + config.START_SECONDS
        while time.monotonic() < deadline:
            record = await asyncio.to_thread(records.read, self.path)
            root = record.get("root")
            if root is not None and record.get("state") in {"running", "bootstrap_failed"}:
                if root["pid"] != pid:
                    raise WorkerCleanupError("Worker launch registered a different root")
                if record.get("state") == "bootstrap_failed":
                    raise WorkerCleanupError(str(record.get("reason")))
                await asyncio.to_thread(self.observe)
                self._observer = threading.Thread(
                    target=self._watch, name="worker-process-custody", daemon=True
                )
                self._observer.start()
                return
            await asyncio.sleep(0.01)
        raise WorkerCleanupError("Worker process registration deadline expired")

    def identity(self) -> LocalChildIdentity | None:
        try:
            record = records.read(self.path)
            root = record.get("root")
            if root is None:
                return None
            return LocalChildIdentity(
                root["pid"], root["birth"], record["boot"], supervision_record=str(self.path)
            )
        except (OSError, ValueError, TypeError, KeyError):
            return None

    local_child_identity = identity

    def observe(self) -> None:
        with self._lock:
            if self._end.is_set():
                return
            _observe(self.path, time.monotonic() + config.INSPECTION_SECONDS)

    def _watch(self) -> None:
        while not self._end.wait(config.OBSERVATION_SECONDS):
            try:
                self.observe()
            except (OSError, ValueError, TypeError, KeyError, TimeoutError):
                with self._lock:
                    if self._end.is_set():
                        continue
                    try:
                        with records.locked(
                            self.path, time.monotonic() + config.INSPECTION_SECONDS
                        ):
                            record = records.read(self.path)
                            record["observation_error"] = "A process observation failed"
                            records.write(self.path, record)
                        continue
                    except (OSError, ValueError):
                        pass

    async def stop(self, native_stop: Callable[[], Awaitable[None]] | None = None) -> CleanupResult:
        self._end.set()
        deadline = time.monotonic() + config.STOP_SECONDS
        native_timed_out = False
        try:

            def capture() -> None:
                with self._lock:
                    _observe(self.path, deadline)

            await asyncio.to_thread(capture)
            if native_stop is not None:
                task: asyncio.Future[None] = asyncio.ensure_future(native_stop())
                self._native_stop_task = task
                done, _ = await asyncio.wait(
                    {task},
                    timeout=max(0, min(config.NATIVE_STOP_SECONDS, deadline - time.monotonic())),
                )
                if task not in done:
                    task.cancel()
                    task.add_done_callback(
                        lambda finished: None if finished.cancelled() else finished.exception()
                    )
                    native_timed_out = True
                else:
                    await task
        except (OSError, ValueError, WorkerCleanupError, TimeoutError) as error:
            try:
                with records.locked(self.path, deadline):
                    record = records.read(self.path)
                    record.update(state="not_proven", observation_error=str(error))
                    records.write(self.path, record)
            except (OSError, ValueError):
                pass
            return CleanupResult(False, f"Native Worker stop not proven: {error}")

        def cleanup() -> CleanupResult:
            with self._lock:
                return stop_record(self.path, deadline=deadline)

        result = await asyncio.to_thread(cleanup)
        if native_timed_out:
            return CleanupResult(
                False, "Native Worker stop deadline expired; process cleanup: " + result.reason
            )
        return result
