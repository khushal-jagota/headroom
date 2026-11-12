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
from planner.worker_supervision.contracts import (
    CleanupResult,
    CleanupUncertainty,
    WorkerCleanupError,
    WorkerProcess,
)

__all__ = [
    "CleanupResult",
    "CleanupUncertainty",
    "cleanup_uncertainties",
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
    sessions = {table[pid].session for pid in roots} if record.get("isolated") else set()
    changed = True
    while changed:
        changed = False
        for process in table.values():
            if process.pid in roots or process.zombie:
                continue
            if process.parent in roots or process.session in sessions:
                roots.add(process.pid)
                known.add(process)
                if record.get("isolated"):
                    sessions.add(process.session)
                changed = True
    return sorted(known, key=lambda p: (p.pid, p.birth))


def _validate_pending_shape(record: dict[str, Any]) -> None:
    if record.get("root") is not None:
        return
    if record["processes"]:
        raise ValueError("Worker record has process custody without an exact root")
    if not record.get("host") or not record.get("boot") or record.get("owner") is None:
        raise ValueError("Pending Worker launch ownership is unknown")
    _process(record["owner"])
    state = record.get("state")
    if state == "complete":
        if not record.get("launch_revoked") and not record.get("boot_ended"):
            raise ValueError("Rootless completion lacks a pre-execution revocation receipt")
        return
    if state == "pending":
        return
    if state in {"not_proven", "bootstrap_failed"} and record.get("launch_phase") == "pending":
        return
    raise ValueError("Worker record lacks valid pre-execution launch evidence")


def _observe_unlocked(path: Path, deadline: float) -> dict[str, Any]:
    record = records.read(path)
    _validate_pending_shape(record)
    primitives = get_platform_adapter().worker_processes()
    host = primitives.host_boot(deadline)
    if host.host != record.get("host"):
        raise ValueError("Worker process record belongs to another machine")
    if host.boot != record.get("boot"):
        record.update(state="complete", boot_ended=True, reason="The recorded boot ended")
    elif record.get("state") != "complete":
        first = primitives.snapshot(deadline)
        second = primitives.snapshot(deadline)
        # A sequential inventory is not atomic. Authenticate both ends of each
        # discovered relationship across two observations before custody expands.
        stable = {pid: process for pid, process in second.items() if first.get(pid) == process}
        discovered = _discover(record, stable)
        record["processes"] = [asdict(p) for p in discovered]
        known = {(process.pid, process.birth) for process in discovered}
        root = record.get("root")
        session_members: list[dict[str, Any]] = []
        if root is not None and record.get("isolated") and root["session"] == root["pid"]:
            leader = stable.get(root["pid"])
            # A replacement leader proves that the old session identifier drained.
            # Otherwise an unattributed survivor prevents cleanup proof, but its
            # session number alone never authorizes a signal.
            if leader is None or leader.birth == root["birth"]:
                session_members = [
                    asdict(process)
                    for process in second.values()
                    if process.session == root["session"]
                    and not process.zombie
                    and (process.pid, process.birth) not in known
                ]
        record["unattributed_session_members"] = session_members
        record.pop("observation_error", None)
    records.write(path, record)
    return record


def _observe(path: Path, deadline: float) -> dict[str, Any]:
    with records.locked(path, deadline):
        return _observe_unlocked(path, deadline)


def inspect_record(
    path: Path,
    *,
    identity: LocalChildIdentity | None = None,
    revoke_pending: bool = False,
    deadline: float | None = None,
) -> CleanupResult:
    deadline = time.monotonic() + config.INSPECTION_SECONDS if deadline is None else deadline
    try:
        with records.locked(path, deadline):
            return _inspect_record(
                path, identity=identity, deadline=deadline, revoke_pending=revoke_pending
            )
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


def _revoke_pending(path: Path, record: dict[str, Any]) -> CleanupResult:
    # The isolated bootstrap must acquire the same lock before it registers or
    # executes. Revocation is a no-backend-execution proof, not bootstrap death.
    _validate_pending_shape(record)
    if record.get("owner") is None or not record.get("host") or not record.get("boot"):
        return CleanupResult(False, "Pending Worker launch ownership is unknown")
    reason = "Launch authorization revoked before backend execution"
    record.update(state="complete", launch_revoked=True, reason=reason)
    records.write(path, record)
    return CleanupResult(True, reason)


def _inspect_record(
    path: Path,
    *,
    identity: LocalChildIdentity | None = None,
    deadline: float,
    revoke_pending: bool = False,
) -> CleanupResult:
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
        primitives = get_platform_adapter().worker_processes()
        if record.get("root") is None:
            owner = record.get("owner")
            if (
                not revoke_pending
                and not record.get("launch_failed")
                and owner is not None
                and not primitives.is_absent(_process(owner), deadline)
            ):
                return CleanupResult(False, "Worker launch is still pending with its live owner")
            return _revoke_pending(path, record)
        if any(not primitives.is_absent(_process(raw), deadline) for raw in record["processes"]):
            return CleanupResult(False, "Recorded Worker processes remain")
        # The root can exit after its previous snapshot and leave a late tool.
        # Inspect the isolated session again after exact root/child absence.
        record = _observe_unlocked(path, deadline)
        if any(not primitives.is_absent(_process(raw), deadline) for raw in record["processes"]):
            return CleanupResult(False, "Recorded Worker processes remain")
        if record.get("unattributed_session_members"):
            return CleanupResult(False, "Unattributed isolated-session processes remain")
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
            return _revoke_pending(path, record)
        primitives = get_platform_adapter().worker_processes()
        term_deadline = min(deadline, time.monotonic() + config.TERM_SECONDS)
        for signum, phase_deadline in ((signal.SIGTERM, term_deadline), (signal.SIGKILL, deadline)):
            record = _observe_unlocked(path, deadline)
            remaining = [
                _process(raw)
                for raw in record["processes"]
                if not primitives.is_absent(_process(raw), deadline)
            ]
            for process in remaining:
                # A refused signal does not prove survival. A process can exit
                # naturally while its native transport finishes teardown.
                primitives.signal(process, signum, deadline)
            while time.monotonic() < phase_deadline:
                result = _inspect_record(path, identity=identity, deadline=deadline)
                if result.complete:
                    return result
                time.sleep(min(0.05, max(0, phase_deadline - time.monotonic())))
        record = records.read(path)
        reason = "Worker stop deadline expired"
        if record.get("unattributed_session_members"):
            reason += "; unattributed isolated-session processes remain"
        record.update(state="not_proven", reason=reason)
        records.write(path, record)
        return CleanupResult(False, record["reason"])
    except (OSError, ValueError, TypeError, KeyError, TimeoutError) as error:
        return CleanupResult(False, f"Worker cleanup not proven: {error}")


def cleanup_uncertainties(
    root: Path, *, revoke_pending: bool = False
) -> tuple[CleanupUncertainty, ...]:
    """Fresh observation clears each Ticket independently; it never stops a process."""
    deadline = time.monotonic() + config.STOP_SECONDS
    uncertain: list[CleanupUncertainty] = []
    if not root.exists():
        return ()
    for path in sorted(root.glob("*.json")):
        try:
            record = records.read(path)
        except (OSError, ValueError) as error:
            uncertain.append(CleanupUncertainty(None, None, path, str(error)))
            continue
        if record.get("state") == "complete":
            try:
                _validate_pending_shape(record)
            except (ValueError, TypeError, KeyError) as error:
                uncertain.append(
                    CleanupUncertainty(
                        record.get("ticket_id"),
                        record.get("conversation_id"),
                        path,
                        str(error),
                    )
                )
            continue
        result = inspect_record(path, revoke_pending=revoke_pending, deadline=deadline)
        if not result.complete:
            uncertain.append(
                CleanupUncertainty(
                    record.get("ticket_id"),
                    record.get("conversation_id"),
                    path,
                    result.reason,
                )
            )
    return tuple(uncertain)


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
            try:
                _validate_pending_shape(record)
            except (ValueError, TypeError, KeyError) as error:
                return CleanupResult(False, str(error))
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
            try:
                previous = records.read(prior)
            except (OSError, ValueError):
                # The uncertainty scan reports unassignable evidence. Canonical
                # custody retains its own source fence, never every Ticket's.
                continue
            if previous.get("ticket_id") != environment["PLAN_TICKET_ID"]:
                continue
            if previous.get("state") == "complete":
                try:
                    _validate_pending_shape(previous)
                except (ValueError, TypeError, KeyError) as error:
                    raise WorkerCleanupError(str(error)) from error
                continue
            inspection = inspect_record(prior)
            if inspection.complete:
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
                "launch_phase": "pending",
                "conversation_id": environment.get("PLAN_CONVERSATION_ID"),
                "source_child_id": environment.get("PLAN_EXECUTOR_CHILD_ID"),
                "source_incarnation": environment.get("PLAN_EXECUTOR_SOURCE_INCARNATION"),
                "reason": "Launch pending",
            },
        )

    @classmethod
    async def create(
        cls, environment: Mapping[str, str], working_directory: Path
    ) -> WorkerSupervisor | None:
        if not environment.get("PLAN_TICKET_ID"):
            return None
        try:
            return await asyncio.to_thread(cls, environment, working_directory)
        except (OSError, ValueError) as error:
            raise WorkerCleanupError(f"Worker launch evidence is unavailable: {error}") from error

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
            raise FileNotFoundError(f"Worker executable is unavailable: {original[0]}")
        return (
            sys.executable,
            "-I",
            "-m",
            "planner.worker_supervision.launch",
            str(self.path),
            executable,
            *original[1:],
        )

    async def cancel_unspawned(self) -> None:
        """Caller receipt: the OS refused spawn before any child existed."""
        await asyncio.to_thread(self._cancel_unspawned)

    def _cancel_unspawned(self) -> None:
        with self._lock, records.locked(self.path, time.monotonic() + config.INSPECTION_SECONDS):
            record = records.read(self.path)
            _validate_pending_shape(record)
            if record.get("root") is not None:
                raise WorkerCleanupError("Cannot cancel a registered Worker process")
            record.update(
                state="complete",
                launch_revoked=True,
                reason="The OS refused spawn before a child existed",
            )
            records.write(self.path, record)

    async def spawned(self, pid: int) -> None:
        """Capture the OS child before bootstrap registration can fail."""
        await asyncio.to_thread(self._spawned, pid)

    def _spawned(self, pid: int) -> None:
        deadline = time.monotonic() + config.INSPECTION_SECONDS
        with self._lock, records.locked(self.path, deadline):
            record = records.read(self.path)
            if record.get("launch_revoked") or record.get("state") == "complete":
                raise WorkerCleanupError("Worker launch authorization was revoked")
            root = record.get("root")
            _validate_pending_shape(record)
            if root is not None:
                if root["pid"] != pid:
                    raise WorkerCleanupError("Worker launch registered a different root")
                return
            primitives = get_platform_adapter().worker_processes()
            first = primitives.snapshot(deadline).get(pid)
            second = primitives.snapshot(deadline).get(pid)
            if first is not None and first == second and first.parent == os.getpid():
                record.update(root=asdict(first), processes=[asdict(first)], state="spawned")
                records.write(self.path, record)

    async def started(self, pid: int) -> None:
        await self.spawned(pid)
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
        await asyncio.to_thread(self._mark_launch_failed)
        raise WorkerCleanupError("Worker process registration deadline expired")

    def _mark_launch_failed(self) -> None:
        with self._lock, records.locked(self.path, time.monotonic() + config.INSPECTION_SECONDS):
            record = records.read(self.path)
            record["launch_failed"] = True
            records.write(self.path, record)

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
        diagnostics: list[str] = []

        def capture() -> None:
            with self._lock:
                _observe(self.path, deadline)

        try:
            await asyncio.to_thread(capture)
        except (OSError, ValueError, TypeError, KeyError, WorkerCleanupError) as error:
            diagnostics.append(f"Process observation failed: {error}")

            def retain_observation_error(message: str) -> None:
                try:
                    with records.locked(self.path, deadline):
                        record = records.read(self.path)
                        record.update(state="not_proven", observation_error=message)
                        records.write(self.path, record)
                except (OSError, ValueError):
                    pass

            await asyncio.to_thread(retain_observation_error, str(error))
            return CleanupResult(False, "; ".join(diagnostics))

        if native_stop is not None:
            try:
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
                    diagnostics.append("Native Worker stop deadline expired")
                elif task.cancelled():
                    diagnostics.append("Native Worker stop task was canceled")
                else:
                    await task
            except Exception as error:
                # Transport teardown failure does not prevent process escalation.
                # It is not a gap in process observation or durable custody.
                diagnostics.append(f"Native Worker stop failed: {error}")

        def cleanup() -> CleanupResult:
            with self._lock:
                return stop_record(self.path, deadline=deadline)

        result = await asyncio.to_thread(cleanup)
        if diagnostics:
            return CleanupResult(result.complete, result.reason + "; " + "; ".join(diagnostics))
        return result
