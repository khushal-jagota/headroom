"""Focused process-boundary risks for the portable Worker owner."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from planner.conversation.local_child_contracts import LocalHostBootIdentity
from planner.worker_supervision import (
    WorkerSupervisor,
    config,
    inspect_record,
    records,
    stop_record,
)
from planner.worker_supervision.contracts import WorkerProcess


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native priority and pidfd cleanup proof")
def test_launch_priority_and_recorded_detached_tool_cleanup(tmp_path: Path) -> None:
    """One real launch proves priority before exec and custody after a tool detaches."""

    async def run() -> None:
        hostile = tmp_path / "ticket-code"
        hostile.mkdir()
        marker = tmp_path / "hostile-imported"
        hostile_code = (
            f"from pathlib import Path; Path({str(marker)!r}).touch(); "
            "raise RuntimeError('hostile import')"
        )
        (hostile / "planner").mkdir()
        (hostile / "planner" / "__init__.py").write_text(hostile_code)
        (hostile / "json.py").write_text(hostile_code)
        (hostile / "logging.py").write_text(hostile_code)
        environment = dict(
            os.environ,
            PLAN_TICKET_ID="priority",
            PLAN_WORKER_PROCESS_ROOT=str(tmp_path),
            PYTHONPATH=str(hostile),
        )
        supervisor = await WorkerSupervisor.create(environment, tmp_path)
        assert supervisor is not None
        tool = "import os,time; time.sleep(.7); os.setsid(); time.sleep(60)"
        code = (
            "import os,subprocess,sys,time; "
            f"p=subprocess.Popen([sys.executable,'-c',{tool!r}]); "
            "print(p.pid,os.getpriority(os.PRIO_PROCESS,0),"
            "open('/proc/self/oom_score_adj').read().strip(),flush=True); "
            "time.sleep(60)"
        )
        process = await asyncio.create_subprocess_exec(
            *supervisor.argv([sys.executable, "-c", code]),
            env=supervisor.environment,
            stdout=asyncio.subprocess.PIPE,
            cwd=hostile,
        )
        tool_pid = None
        try:
            await supervisor.started(process.pid)
            assert process.stdout is not None
            line = await asyncio.wait_for(process.stdout.readline(), 5)
            tool_pid, nice, oom = map(int, line.split())
            assert nice >= 10 and oom >= 0
            assert not marker.exists()
            assert os.getpriority(os.PRIO_PROCESS, tool_pid) >= 10
            assert int(Path(f"/proc/{tool_pid}/oom_score_adj").read_text()) >= 0
            await asyncio.sleep(0.9)
            assert os.getsid(tool_pid) == tool_pid
            identity = supervisor.identity()
            assert identity is not None and identity.supervision_record == str(supervisor.path)

            async def failed_transport_stop() -> None:
                raise OSError("transport close failed")

            result = await supervisor.stop(native_stop=failed_transport_stop)
            assert result.complete, result.reason
            assert "transport close failed" in result.reason
            await process.wait()
            assert inspect_record(supervisor.path, identity=identity).complete
        finally:
            # Cleanup follows durable incarnation evidence, including an observed
            # tool that detached. A completed record never authorizes another signal.
            await supervisor.stop()
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(run())


def test_pre_isolation_root_never_adopts_home_group() -> None:
    """A bootstrap failure before setsid cannot authorize the home's process group."""
    from planner.worker_supervision import _discover

    root = WorkerProcess(20, "a", 10, 10, 10)
    home = WorkerProcess(10, "b", 1, 10, 10)
    sibling = WorkerProcess(30, "c", 10, 10, 10)
    assert _discover(
        {"processes": [asdict(root)], "isolated": False},
        {
            10: home,
            20: root,
            30: sibling,
        },
    ) == [root]


def _record(tmp_path: Path) -> Path:
    path = tmp_path / ("a" * 32 + ".json")
    records.write(
        path,
        {
            "version": 1,
            "ticket_id": "test",
            "host": "host",
            "boot": "boot",
            "root": asdict(WorkerProcess(20, "old", 10, 20, 20)),
            "processes": [asdict(WorkerProcess(20, "old", 10, 20, 20))],
            "state": "running",
            "isolated": True,
        },
    )
    return path


class _Primitives:
    def __init__(self, table: dict[int, WorkerProcess]) -> None:
        self.table = table
        self.signals: list[tuple[int, int]] = []

    def host_boot(self, deadline: float) -> LocalHostBootIdentity:
        return LocalHostBootIdentity("host", "boot")

    def snapshot(self, deadline: float) -> dict[int, WorkerProcess]:
        return self.table

    def is_absent(self, process: WorkerProcess, deadline: float) -> bool:
        current = self.table.get(process.pid)
        return current is None or current.birth != process.birth or current.zombie

    def signal(self, process: WorkerProcess, signum: int, deadline: float) -> bool:
        self.signals.append((process.pid, signum))
        return True


class _Adapter:
    def __init__(self, primitives: _Primitives) -> None:
        self.primitives = primitives

    def worker_processes(self) -> _Primitives:
        return self.primitives


def test_reused_pid_is_absence_not_a_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _record(tmp_path)
    replacement = WorkerProcess(20, "replacement", 1, 20, 20)
    unrelated = WorkerProcess(30, "other", 20, 20, 20)
    native = _Primitives({20: replacement, 30: unrelated})
    original_snapshot = native.snapshot
    first = True

    def sequential_snapshot(deadline: float) -> dict[int, WorkerProcess]:
        nonlocal first
        if first:
            first = False
            return {20: WorkerProcess(20, "old", 10, 20, 20), 30: unrelated}
        return original_snapshot(deadline)

    monkeypatch.setattr(native, "snapshot", sequential_snapshot)
    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    assert stop_record(path).complete
    assert native.signals == []


def test_stop_deadline_retains_uncertainty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _record(tmp_path)
    native = _Primitives({20: WorkerProcess(20, "old", 10, 20, 20)})
    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    monkeypatch.setattr(config, "STOP_SECONDS", 0.06)
    monkeypatch.setattr(config, "TERM_SECONDS", 0.02)

    class Clock:
        now = 100.0

        def monotonic(self) -> float:
            return self.now

        def sleep(self, duration: float) -> None:
            self.now += duration

    clock = Clock()
    monkeypatch.setattr("planner.worker_supervision.time", clock)
    monkeypatch.setattr("planner.worker_supervision.records.time", clock)
    phase_times: list[float] = []
    original_signal = native.signal

    def observed_signal(process: WorkerProcess, signum: int, deadline: float) -> bool:
        phase_times.append(clock.monotonic())
        return original_signal(process, signum, deadline)

    monkeypatch.setattr(native, "signal", observed_signal)
    started = clock.monotonic()
    result = stop_record(path)
    assert not result.complete
    assert result.reason == "Worker stop deadline expired"
    assert clock.monotonic() - started == pytest.approx(config.STOP_SECONDS)
    assert [sig for _, sig in native.signals] == [signal.SIGTERM, signal.SIGKILL]
    assert phase_times == pytest.approx([started, started + config.TERM_SECONDS])
    assert records.read(path)["state"] == "not_proven"
    assert not inspect_record(path).complete
    native.table.clear()
    record = records.read(path)
    record["observation_error"] = "A previous observation failed"
    records.write(path, record)
    assert inspect_record(path).complete
    assert "observation_error" not in records.read(path)


def test_corrupt_and_pending_records_never_prove_cleanup(tmp_path: Path) -> None:
    path = tmp_path / ("b" * 32 + ".json")
    path.write_text("{")
    path.chmod(0o600)
    assert not stop_record(path).complete
    assert path.read_text() == "{"
    other = WorkerSupervisor(
        dict(os.environ, PLAN_TICKET_ID="other", PLAN_WORKER_PROCESS_ROOT=str(tmp_path)),
        tmp_path,
    )
    asyncio.run(other.cancel_unspawned())
    from planner.worker_supervision import cleanup_uncertainties

    unknown = cleanup_uncertainties(tmp_path)
    assert len(unknown) == 1 and unknown[0].ticket_id is None
    assert unknown[0].record_path == path


def test_native_stop_cancellation_cannot_extend_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        supervisor = WorkerSupervisor(
            dict(os.environ, PLAN_TICKET_ID="deadline", PLAN_WORKER_PROCESS_ROOT=str(tmp_path)),
            tmp_path,
        )
        await supervisor.cancel_unspawned()
        release = asyncio.Event()

        async def resistant() -> None:
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        from planner.worker_supervision.contracts import CleanupResult

        monkeypatch.setattr("planner.worker_supervision._observe", lambda path, deadline: {})
        monkeypatch.setattr(
            "planner.worker_supervision.stop_record",
            lambda path, *, deadline: CleanupResult(True, "The OS refused spawn"),
        )

        async def reserved_budget_expired(
            tasks: set[asyncio.Future[None]],
            *,
            timeout: float,
        ) -> tuple[set[asyncio.Future[None]], set[asyncio.Future[None]]]:
            assert 0 < timeout <= config.NATIVE_STOP_SECONDS
            await asyncio.sleep(0)
            return set(), tasks

        monkeypatch.setattr("planner.worker_supervision.asyncio.wait", reserved_budget_expired)
        result = await supervisor.stop(native_stop=resistant)
        assert result.complete
        assert not supervisor._native_stop_task.done()
        assert "Native Worker stop deadline expired" in result.reason
        release.set()
        await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native orphan pidfd cleanup proof")
def test_restart_reconciles_worker_after_its_parent_crashes(tmp_path: Path) -> None:
    """A new supervisor uses durable evidence after the original owner dies."""
    import json
    import subprocess

    from planner.worker_supervision import reconcile

    code = """
import asyncio, json, os, subprocess, sys
from pathlib import Path
from planner.worker_supervision import WorkerSupervisor
async def run():
    owner = await WorkerSupervisor.create(os.environ, Path.cwd())
    argv = owner.argv([sys.executable, '-c', 'import time; time.sleep(60)'])
    child = subprocess.Popen(argv, env=owner.environment)
    await owner.started(child.pid)
    print(json.dumps({'record': str(owner.path), 'pid': child.pid}), flush=True)
    os._exit(0)
asyncio.run(run())
"""
    environment = dict(os.environ, PLAN_TICKET_ID="crash", PLAN_WORKER_PROCESS_ROOT=str(tmp_path))
    parent = subprocess.Popen([sys.executable, "-c", code], env=environment, stdout=subprocess.PIPE)
    assert parent.stdout is not None
    line = json.loads(parent.stdout.readline())
    parent.wait(timeout=5)
    try:
        result = reconcile(tmp_path)
        assert result.complete, result.reason
        assert records.read(Path(line["record"]))["state"] == "complete"
    finally:
        stop_record(Path(line["record"]))


def test_pending_launch_receipt_stays_uncertain_across_restart(tmp_path: Path) -> None:
    import sqlite3

    from planner.conversation import child_custody

    supervisor = WorkerSupervisor(
        dict(os.environ, PLAN_TICKET_ID="pending", PLAN_WORKER_PROCESS_ROOT=str(tmp_path)),
        tmp_path,
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE custody(state,supervision_record,host,boot)")
    conn.execute(
        "INSERT INTO custody VALUES('launch_pending',?,'host','boot')", (str(supervisor.path),)
    )
    row = conn.execute("SELECT * FROM custody").fetchone()
    assert not child_custody.retirement_row_proven(row)
    result = stop_record(supervisor.path)
    assert result.complete and "authorization revoked" in result.reason
    assert child_custody.retirement_row_proven(row)
    assert records.read(supervisor.path)["root"] is None
    from planner.worker_supervision.launch import _launch

    with records.locked(supervisor.path, time.monotonic() + 1):
        with pytest.raises(RuntimeError, match="authorization was revoked"):
            _launch(supervisor.path, [sys.executable, "-c", "raise AssertionError('executed')"])
    assert records.read(supervisor.path)["state"] == "complete"
    malformed = records.read(supervisor.path)
    malformed.update(state="running", launch_revoked=False, processes=[malformed["owner"]])
    records.write(supervisor.path, malformed)
    assert not inspect_record(supervisor.path).complete
    assert not stop_record(supervisor.path).complete
    malformed.update(state="complete", launch_revoked=True)
    records.write(supervisor.path, malformed)
    from planner.worker_supervision import WorkerCleanupError, cleanup_uncertainties

    unknown = cleanup_uncertainties(tmp_path)
    assert len(unknown) == 1 and unknown[0].ticket_id == "pending"
    with pytest.raises(WorkerCleanupError, match="without an exact root"):
        WorkerSupervisor(
            dict(os.environ, PLAN_TICKET_ID="pending", PLAN_WORKER_PROCESS_ROOT=str(tmp_path)),
            tmp_path,
        )
    unrelated = WorkerSupervisor(
        dict(os.environ, PLAN_TICKET_ID="unrelated", PLAN_WORKER_PROCESS_ROOT=str(tmp_path)),
        tmp_path,
    )
    asyncio.run(unrelated.cancel_unspawned())
    malformed.update(state="pending", processes=[], launch_phase="pending", launch_revoked=False)
    records.write(supervisor.path, malformed)
    assert stop_record(supervisor.path).complete
    conn.close()


def test_journal_transactions_cannot_lose_a_recorded_detached_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inspection waits for the writer, so an older snapshot cannot erase custody."""
    from concurrent.futures import ThreadPoolExecutor

    path = _record(tmp_path)
    detached = WorkerProcess(30, "tool", 1, 30, 30)
    native = _Primitives({30: detached})
    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with records.locked(path, time.monotonic() + 1):
            future = pool.submit(inspect_record, path)
            time.sleep(0.03)
            assert not future.done()
            record = records.read(path)
            record["processes"].append(asdict(detached))
            records.write(path, record)
        assert not future.result(timeout=1).complete
    assert any(item["pid"] == 30 for item in records.read(path)["processes"])


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native pre-bootstrap process evidence")
def test_parent_captures_wrapper_that_exits_before_registration(tmp_path: Path) -> None:
    async def run() -> None:
        supervisor = WorkerSupervisor(
            dict(os.environ, PLAN_TICKET_ID="bootstrap", PLAN_WORKER_PROCESS_ROOT=str(tmp_path)),
            tmp_path,
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            "import time; time.sleep(60)",
        )
        try:
            await supervisor.spawned(process.pid)
            assert records.read(supervisor.path)["state"] == "spawned"
            process.kill()
            await process.wait()
            assert inspect_record(supervisor.path).complete
        finally:
            await supervisor.stop()
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(run())


def test_session_survivor_without_a_known_ancestor_blocks_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _record(tmp_path)
    native = _Primitives({})
    original_absent = native.is_absent
    injected = False

    def late_tool_after_root_absence(process: WorkerProcess, deadline: float) -> bool:
        nonlocal injected
        if not injected:
            injected = True
            native.table[30] = WorkerProcess(30, "late tool", 1, 30, 20)
        return original_absent(process, deadline)

    monkeypatch.setattr(native, "is_absent", late_tool_after_root_absence)
    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    assert not inspect_record(path).complete
    monkeypatch.setattr(config, "STOP_SECONDS", 0.04)
    monkeypatch.setattr(config, "TERM_SECONDS", 0.01)
    result = stop_record(path)
    assert not result.complete
    assert native.signals == []
    native.table.clear()
    assert inspect_record(path).complete


def test_mac_uid_discovery_gap_does_not_prove_a_recorded_pid_ended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from planner.platform_runtime.darwin.worker_processes import DarwinWorkerProcesses

    path = _record(tmp_path)
    native = _Primitives({})

    def denied(pid: int, signum: int) -> None:
        raise PermissionError("recorded process changed owner")

    monkeypatch.setattr(
        "planner.platform_runtime.darwin.worker_processes.os",
        SimpleNamespace(kill=denied),
    )
    monkeypatch.setattr(native, "is_absent", DarwinWorkerProcesses().is_absent)
    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    assert not inspect_record(path).complete

    def absent(pid: int, signum: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(
        "planner.platform_runtime.darwin.worker_processes.os",
        SimpleNamespace(kill=absent),
    )
    assert inspect_record(path).complete


def test_async_receipt_and_observation_failure_leave_the_event_loop_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    async def run() -> None:
        supervisor = WorkerSupervisor(
            dict(os.environ, PLAN_TICKET_ID="disk", PLAN_WORKER_PROCESS_ROOT=str(tmp_path)),
            tmp_path,
        )
        entered = threading.Event()
        release = threading.Event()
        original_write = records.write

        def held_write(path: Path, record: dict[str, object]) -> None:
            entered.set()
            assert release.wait(1), "Journal I/O blocked the event loop"
            original_write(path, record)

        monkeypatch.setattr(records, "write", held_write)
        from collections.abc import Awaitable

        async def complete_without_blocking(operation: Awaitable[object]) -> None:
            task = asyncio.ensure_future(operation)
            assert await asyncio.to_thread(entered.wait, 1)
            release.set()
            await task

        await complete_without_blocking(supervisor.cancel_unspawned())
        entered.clear()
        release.clear()

        def failed_observation(path: Path, deadline: float) -> None:
            raise OSError("temporary process inspection error")

        monkeypatch.setattr("planner.worker_supervision._observe", failed_observation)
        await complete_without_blocking(supervisor.stop())

    asyncio.run(run())


def test_refused_signal_waits_for_fresh_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    path = _record(tmp_path)
    native = _Primitives({20: WorkerProcess(20, "old", 10, 20, 20)})
    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    monkeypatch.setattr(native, "signal", lambda process, signum, deadline: False)
    clock = [100.0]

    def settles(duration: float) -> None:
        clock[0] += duration
        native.table.clear()

    timer = SimpleNamespace(monotonic=lambda: clock[0], sleep=settles)
    monkeypatch.setattr("planner.worker_supervision.time", timer)
    monkeypatch.setattr("planner.worker_supervision.records.time", timer)
    result = stop_record(path)
    assert result.complete
    assert clock[0] > 100.0


def test_mac_signal_requires_immediate_microsecond_birth_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    import errno
    from types import SimpleNamespace
    from typing import Any

    from planner.platform_runtime.darwin import worker_processes as darwin

    birth = [10, 20]
    readable = [True]

    def pidinfo(pid: int, flavor: int, arg: int, buffer: Any, size: int) -> int:
        if not readable[0]:
            ctypes.set_errno(errno.EPERM)
            return 0
        info = ctypes.cast(buffer, ctypes.POINTER(darwin._BSDInfo)).contents
        info.start_sec, info.start_usec = birth
        return ctypes.sizeof(info)

    monkeypatch.setattr(
        ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(proc_pidinfo=pidinfo)
    )
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        darwin, "os", SimpleNamespace(kill=lambda pid, sig: signals.append((pid, sig)))
    )
    native = darwin.DarwinWorkerProcesses()
    target = WorkerProcess(42, "10:20", 1, 42, 42)
    assert native.signal(target, signal.SIGTERM, time.monotonic() + 1)
    assert signals == [(42, signal.SIGTERM)]
    birth[1] = 21
    assert not native.signal(target, signal.SIGKILL, time.monotonic() + 1)
    assert not native.is_absent(target, time.monotonic() + 1)
    readable[0] = False
    assert not native.signal(target, signal.SIGKILL, time.monotonic() + 1)
    assert [sig for _, sig in signals if sig] == [signal.SIGTERM]


def test_mac_host_command_failures_remain_structured_cleanup_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from planner.platform_runtime.darwin.worker_processes import DarwinWorkerProcesses
    from planner.worker_supervision import WorkerCleanupError

    path = _record(tmp_path)
    native = _Primitives({})
    monkeypatch.setattr(native, "host_boot", DarwinWorkerProcesses().host_boot)
    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    for error in (
        subprocess.TimeoutExpired("sysctl", 1),
        subprocess.CalledProcessError(1, "ioreg"),
    ):

        def fails(
            *args: object,
            failure: subprocess.SubprocessError = error,
            **kwargs: object,
        ) -> object:
            raise failure

        monkeypatch.setattr(
            "planner.platform_runtime.darwin.worker_processes.subprocess.run", fails
        )
        assert not inspect_record(path).complete
        result = stop_record(path)
        assert not result.complete and "Mac host identity command failed" in result.reason
        with pytest.raises(WorkerCleanupError, match="Mac host identity command failed"):
            asyncio.run(
                WorkerSupervisor.create(
                    dict(os.environ, PLAN_TICKET_ID="test", PLAN_WORKER_PROCESS_ROOT=str(tmp_path)),
                    tmp_path,
                )
            )
