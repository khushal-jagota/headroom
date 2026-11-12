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
def test_launch_priority_and_recorded_detached_tool_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One real launch proves priority before exec and custody after a tool detaches."""

    monkeypatch.setattr(config, "OBSERVATION_SECONDS", 0.1)

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
    owns_isolated_session_members = False

    def __init__(self, table: dict[int, WorkerProcess]) -> None:
        self.table = table
        self.signals: list[tuple[int, int]] = []

    def host_boot(self, deadline: float) -> LocalHostBootIdentity:
        return LocalHostBootIdentity("host", "boot")

    def snapshot(self, deadline: float) -> dict[int, WorkerProcess]:
        return self.table

    def prepare_launch(self) -> None:
        pass

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
    monkeypatch.setattr("planner.worker_supervision.observation.time", clock)
    monkeypatch.setattr(config, "STOP_OBSERVATION_BATCH_SECONDS", 0)
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

        monkeypatch.setattr(
            "planner.worker_supervision._observe", lambda path, deadline, **kwargs: {}
        )
        monkeypatch.setattr(
            "planner.worker_supervision.get_platform_adapter", lambda: _Adapter(_Primitives({}))
        )
        monkeypatch.setattr(
            "planner.worker_supervision.stop_record",
            lambda path, *, deadline, **kwargs: CleanupResult(True, "The OS refused spawn"),
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

        def failed_observation(path: Path, deadline: float, **kwargs: object) -> None:
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
    monkeypatch.setattr("planner.worker_supervision.observation.time", timer)
    monkeypatch.setattr(config, "STOP_OBSERVATION_BATCH_SECONDS", 0)
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
    assert native.is_absent(target, time.monotonic() + 1)
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


def test_reconciliation_finishes_later_ticket_after_first_ticket_stays_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from planner.worker_supervision import reconcile

    first = _record(tmp_path)
    second = tmp_path / ("b" * 32 + ".json")
    later = records.read(first)
    later.update(
        ticket_id="later",
        root=asdict(WorkerProcess(30, "later", 1, 30, 30)),
        processes=[asdict(WorkerProcess(30, "later", 1, 30, 30))],
    )
    records.write(second, later)
    native = _Primitives(
        {20: WorkerProcess(20, "old", 10, 20, 20), 30: WorkerProcess(30, "later", 1, 30, 30)}
    )
    hosts: list[float] = []
    clock = [100.0]

    def host(deadline: float) -> LocalHostBootIdentity:
        hosts.append(deadline)
        return LocalHostBootIdentity("host", "boot")

    def signal_one(process: WorkerProcess, signum: int, deadline: float) -> bool:
        if process.pid == 30:
            native.table.pop(30, None)
            return True
        return False

    timer = SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr("planner.worker_supervision.time", timer)
    monkeypatch.setattr("planner.worker_supervision.observation.time", timer)
    monkeypatch.setattr(config, "STOP_OBSERVATION_BATCH_SECONDS", 0)
    monkeypatch.setattr("planner.worker_supervision.records.time", timer)
    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    monkeypatch.setattr(native, "host_boot", host)
    monkeypatch.setattr(native, "signal", signal_one)
    monkeypatch.setattr(config, "STOP_SECONDS", 0.06)
    monkeypatch.setattr(config, "TERM_SECONDS", 0.02)
    result = reconcile(tmp_path)
    assert not result.complete
    assert [record.result.complete for record in result.records] == [False, True]
    assert records.read(second)["state"] == "complete"
    assert len(hosts) == 1


def test_batch_filters_healthy_sources_and_gives_each_record_a_fresh_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from planner.worker_supervision import cleanup_uncertainties

    first = _record(tmp_path)
    active = records.read(first)
    active.update(ticket_id="healthy", source_incarnation="inc", source_child_id="child")
    records.write(first, active)
    second = tmp_path / ("b" * 32 + ".json")
    later = records.read(first)
    later.update(
        ticket_id="other",
        source_child_id="other",
        root=asdict(WorkerProcess(30, "later", 1, 30, 30)),
        processes=[asdict(WorkerProcess(30, "later", 1, 30, 30))],
    )
    records.write(second, later)
    native = _Primitives({20: WorkerProcess(20, "old", 10, 20, 20)})
    hosts: list[float] = []
    clock = [100.0]
    original_snapshot = native.snapshot
    exhausted = False

    def host(deadline: float) -> LocalHostBootIdentity:
        hosts.append(deadline)
        return LocalHostBootIdentity("host", "boot")

    def slow_first(deadline: float) -> dict[int, WorkerProcess]:
        nonlocal exhausted
        if not exhausted:
            exhausted = True
            clock[0] = deadline + 0.1
            raise TimeoutError("first record spent its inspection budget")
        assert clock[0] < deadline
        return original_snapshot(deadline)

    timer = SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: None)
    monkeypatch.setattr("planner.worker_supervision.time", timer)
    monkeypatch.setattr("planner.worker_supervision.observation.time", timer)
    monkeypatch.setattr(config, "STOP_OBSERVATION_BATCH_SECONDS", 0)
    monkeypatch.setattr("planner.worker_supervision.records.time", timer)
    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    monkeypatch.setattr(native, "host_boot", host)
    monkeypatch.setattr(native, "snapshot", slow_first)
    assert (
        cleanup_uncertainties(
            tmp_path, ticket_id="healthy", healthy_sources=frozenset({("inc", "child")})
        )
        == ()
    )
    assert hosts == []
    uncertain = cleanup_uncertainties(tmp_path)
    assert [item.ticket_id for item in uncertain] == ["healthy"]
    assert len(hosts) == 1
    assert records.read(second)["state"] == "complete"


def test_linux_claims_late_isolated_session_member_and_signals_exact_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _record(tmp_path)
    native = _Primitives({30: WorkerProcess(30, "late", 1, 30, 20)})
    native.owns_isolated_session_members = True

    def signal_member(process: WorkerProcess, signum: int, deadline: float) -> bool:
        native.signals.append((process.pid, signum))
        native.table.pop(process.pid, None)
        return True

    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    monkeypatch.setattr(native, "signal", signal_member)
    result = stop_record(path)
    assert result.complete
    assert native.signals == [(30, signal.SIGTERM)]
    assert any(
        item["pid"] == 30 and item["birth"] == "late" for item in records.read(path)["processes"]
    )


def test_shared_round_indexes_once_and_retains_only_changed_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """More Workers cannot multiply inventories, indexes, or unchanged journal writes."""
    import threading

    from planner.worker_supervision import observation
    from planner.worker_supervision.contracts import InspectionContext

    table = {pid: WorkerProcess(pid, str(pid), 1, pid, pid) for pid in range(20, 1520)}
    native = _Primitives(table)
    context = InspectionContext(native, native.host_boot(time.monotonic() + 1))
    snapshots = 0
    indexes = 0
    writes = 0
    original_index = observation.index_processes
    original_write = records.write

    def snapshot(deadline: float) -> dict[int, WorkerProcess]:
        nonlocal snapshots
        snapshots += 1
        return table

    def index(
        table: dict[int, WorkerProcess],
    ) -> tuple[dict[int, list[WorkerProcess]], dict[int, list[WorkerProcess]]]:
        nonlocal indexes
        indexes += 1
        return original_index(table)

    def write(path: Path, value: dict[str, object]) -> None:
        nonlocal writes
        writes += 1
        original_write(path, value)

    supervisors = []
    for number in range(20):
        path = tmp_path / (f"{number:032x}.json")
        root = table[20 + number]
        records.write(
            path,
            {
                "version": 1,
                "ticket_id": str(number),
                "host": "host",
                "boot": "boot",
                "root": asdict(root),
                "processes": [asdict(root)],
                "isolated": True,
                "state": "running",
                "unattributed_session_members": [],
            },
        )
        supervisor = object.__new__(WorkerSupervisor)
        supervisor.path = path
        supervisor._lock = threading.Lock()
        supervisor._end = threading.Event()
        supervisors.append(supervisor)
    monkeypatch.setattr(native, "snapshot", snapshot)
    monkeypatch.setattr(observation, "index_processes", index)
    monkeypatch.setattr("planner.worker_supervision.index_processes", index)
    monkeypatch.setattr(records, "write", write)
    monkeypatch.setattr(config, "OBSERVATION_SECONDS", 0.01)
    observer = observation.SharedProcessObserver(lambda deadline: context)
    finished = threading.Event()
    delivered = 0

    def callback(supervisor: WorkerSupervisor) -> observation.ObservationCallback:
        def apply(context: InspectionContext | None, pair: object) -> None:
            nonlocal delivered
            from planner.worker_supervision.contracts import ProcessObservation

            assert isinstance(pair, ProcessObservation)
            supervisor._observe_periodic(context, pair)
            delivered += 1
            if delivered == len(supervisors):
                for member in supervisors:
                    observer.unregister(member.path)
                finished.set()

        return apply

    # A busy journal must not prevent the other nineteen records from observation.
    with records.locked(supervisors[0].path, time.monotonic() + 1):
        for supervisor in supervisors:
            observer.register(supervisor.path, callback(supervisor))
        thread = observer._thread
        assert thread is not None
        try:
            assert finished.wait(1)
        finally:
            for supervisor in supervisors:
                observer.unregister(supervisor.path)
            thread.join(1)
    assert not thread.is_alive()
    assert snapshots == 2 and indexes == 2 and writes == 0
    child = WorkerProcess(1600, "child", 21, 21, 21)
    table[child.pid] = child
    pair = observation.capture_observation(context, time.monotonic() + 1)
    for supervisor in supervisors:
        supervisor._observe_periodic(context, pair)
    assert snapshots == 4 and indexes == 4 and writes == 1
    assert records.read(supervisors[1].path)["processes"][-1] == asdict(child)


def test_shared_observer_retires_and_restarts_without_repeated_host_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last stop releases the thread, while each active lifetime shares host evidence."""
    import threading

    from planner.worker_supervision import observation
    from planner.worker_supervision.contracts import InspectionContext

    monkeypatch.setattr(config, "OBSERVATION_SECONDS", 0.01)
    context = InspectionContext(_Primitives({}), LocalHostBootIdentity("host", "boot"))
    host_calls = 0
    rounds = 0
    observed = threading.Event()

    def factory(deadline: float) -> InspectionContext:
        nonlocal host_calls
        host_calls += 1
        return context

    def callback(context: InspectionContext | None, pair: object) -> None:
        nonlocal rounds
        rounds += 1
        if rounds >= 2:
            observed.set()

    observer = observation.SharedProcessObserver(factory)
    path = tmp_path / "worker"
    observer.register(path, callback)
    thread = observer._thread
    assert thread is not None
    try:
        assert observed.wait(1)
        assert host_calls == 1
    finally:
        observer.unregister(path)
        thread.join(1)
    assert not thread.is_alive() and observer._thread is None
    observed.clear()
    observer.register(path, callback)
    replacement = observer._thread
    assert replacement is not None and replacement is not thread
    try:
        assert observed.wait(1)
        assert host_calls == 2
    finally:
        observer.unregister(path)
        replacement.join(1)
    assert not replacement.is_alive()


def test_stop_cannot_leave_a_registration_after_concurrent_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop and the first observation share a fence against a retained dead callback."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    supervisor = object.__new__(WorkerSupervisor)
    supervisor.path = tmp_path / "worker"
    supervisor._lock = threading.Lock()
    supervisor._end = threading.Event()
    entered = threading.Event()
    release = threading.Event()
    registered: set[Path] = set()

    def observe(path: Path, deadline: float) -> None:
        entered.set()
        assert release.wait(1)

    monkeypatch.setattr("planner.worker_supervision._observe", observe)
    monkeypatch.setattr(
        "planner.worker_supervision._process_observer",
        SimpleNamespace(
            register=lambda path, callback: registered.add(path),
            unregister=lambda path: registered.discard(path),
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        start = pool.submit(supervisor._start_observing)
        assert entered.wait(1)
        stop = pool.submit(supervisor._stop_observing)
        release.set()
        start.result(timeout=1)
        stop.result(timeout=1)
    assert supervisor._end.is_set() and not registered
    supervisor._start_observing()
    assert not registered


def test_shared_observer_isolates_callback_failures_and_limits_record_error_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupt record cannot end shared observation or flood the server log."""
    import logging
    import threading

    from planner.worker_supervision import observation
    from planner.worker_supervision.contracts import InspectionContext

    monkeypatch.setattr(config, "OBSERVATION_SECONDS", 0.01)
    context = InspectionContext(_Primitives({}), LocalHostBootIdentity("host", "boot"))
    observer = observation.SharedProcessObserver(lambda deadline: context)
    paths = [tmp_path / name for name in ("bad-index.json", "bad-depth.json", "healthy.json")]
    calls = [0, 0, 0]
    finished = threading.Event()

    def bad_index(context: InspectionContext | None, pair: object) -> None:
        calls[0] += 1
        raise IndexError("invalid record index")

    def bad_depth(context: InspectionContext | None, pair: object) -> None:
        calls[1] += 1
        raise RecursionError("invalid record depth")

    def healthy(context: InspectionContext | None, pair: object) -> None:
        calls[2] += 1
        if calls[2] == 3:
            for path in paths:
                observer.unregister(path)
            finished.set()

    with caplog.at_level(logging.ERROR, logger=observation.__name__):
        with observer._condition:
            for path, callback in zip(paths, (bad_index, bad_depth, healthy), strict=True):
                observer.register(path, callback)
            thread = observer._thread
        assert thread is not None
        try:
            assert finished.wait(1)
        finally:
            for path in paths:
                observer.unregister(path)
            thread.join(1)
    assert calls == [3, 3, 3]
    assert not thread.is_alive()
    errors = [record for record in caplog.records if record.name == observation.__name__]
    assert len(errors) == 2
    assert str(paths[0]) in errors[0].getMessage()
    assert str(paths[1]) in errors[1].getMessage()
    assert errors[0].exc_info is not None and errors[0].exc_info[0] is IndexError
    assert errors[1].exc_info is not None and errors[1].exc_info[0] is RecursionError


def test_register_restarts_a_retained_dead_observer_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retained thread handle cannot permanently suppress observation of new Workers."""
    import threading

    from planner.worker_supervision import observation
    from planner.worker_supervision.contracts import InspectionContext

    monkeypatch.setattr(config, "OBSERVATION_SECONDS", 0.01)
    context = InspectionContext(_Primitives({}), LocalHostBootIdentity("host", "boot"))
    observer = observation.SharedProcessObserver(lambda deadline: context)
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    calls: set[Path] = set()
    finished = threading.Event()

    def callback(path: Path) -> observation.ObservationCallback:
        def apply(context: InspectionContext | None, pair: object) -> None:
            calls.add(path)
            if len(calls) == 2:
                observer.unregister(old_path)
                observer.unregister(new_path)
                finished.set()

        return apply

    dead_thread = threading.Thread(target=lambda: None)
    dead_thread.start()
    dead_thread.join(1)
    assert not dead_thread.is_alive()
    observer._thread = dead_thread
    observer._callbacks[old_path] = callback(old_path)
    observer.register(new_path, callback(new_path))
    replacement = observer._thread
    assert replacement is not None and replacement is not dead_thread
    try:
        assert finished.wait(1)
    finally:
        observer.unregister(old_path)
        observer.unregister(new_path)
        replacement.join(1)
    assert not replacement.is_alive()


def test_stop_observations_share_only_upcoming_pairs_and_preserve_each_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late cleanup request needs new evidence, and a short leader cannot cancel its peer."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from planner.worker_supervision import observation
    from planner.worker_supervision.contracts import InspectionContext

    monkeypatch.setattr(config, "STOP_OBSERVATION_BATCH_SECONDS", 0.1)
    native = _Primitives({})
    context = InspectionContext(native, LocalHostBootIdentity("cohort-host", "boot"))
    broker = observation.FreshProcessObservations()
    key = (type(native), context.host)
    entered = threading.Event()
    release = threading.Event()
    snapshots = 0
    expire_next = False

    def snapshot(deadline: float) -> dict[int, WorkerProcess]:
        nonlocal snapshots, expire_next
        snapshots += 1
        result = native.table.copy()
        if snapshots == 1:
            entered.set()
            assert release.wait(1)
        if expire_next:
            expire_next = False
            time.sleep(max(0, deadline - time.monotonic()))
            raise TimeoutError("The leader's inventory budget expired")
        return result

    monkeypatch.setattr(native, "snapshot", snapshot)

    def await_pending(count: int) -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with broker._condition:
                if len(broker._pending.get(key, ())) == count:
                    return
            time.sleep(0.001)
        pytest.fail("Fresh observation requests did not reach their cohort")

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(broker.capture, context, time.monotonic() + 2)
        await_pending(1)
        second = pool.submit(broker.capture, context, time.monotonic() + 2)
        await_pending(2)
        assert entered.wait(1)
        late = pool.submit(broker.capture, context, time.monotonic() + 2)
        await_pending(1)
        child = WorkerProcess(30, "late-child", 1, 30, 20)
        native.table[child.pid] = child
        release.set()
        first_pair = first.result(timeout=1)
        assert second.result(timeout=1) is first_pair
        late_pair = late.result(timeout=1)
        assert late_pair is not first_pair
        assert child.pid not in first_pair.stable
        assert late_pair.stable[child.pid] == child
        assert snapshots == 4

        expire_next = True
        short = pool.submit(broker.capture, context, time.monotonic() + 0.3)
        await_pending(1)
        expired_peer = pool.submit(broker.capture, context, time.monotonic() + 0.15)
        await_pending(2)
        longer = pool.submit(broker.capture, context, time.monotonic() + 2)
        await_pending(3)
        with pytest.raises(TimeoutError, match="inspection deadline"):
            expired_peer.result(timeout=1)
        with pytest.raises(TimeoutError, match="leader's inventory budget"):
            short.result(timeout=1)
        assert longer.result(timeout=1).stable[child.pid] == child
        assert snapshots == 7
    assert not broker._pending and not broker._capturing


def test_concurrent_stops_leave_the_default_executor_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Long cleanup cannot queue another Stop or database work behind its TERM grace."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from planner.worker_supervision.contracts import CleanupResult

    native = _Primitives({20: WorkerProcess(20, "old", 10, 20, 20)})
    monkeypatch.setattr("planner.worker_supervision.get_platform_adapter", lambda: _Adapter(native))
    monkeypatch.setattr(config, "STOP_OBSERVATION_BATCH_SECONDS", 0)
    original = _record(tmp_path)
    entered: set[Path] = set()
    entered_lock = threading.Lock()
    both_entered = threading.Event()
    release = threading.Event()

    def cleanup(path: Path, **kwargs: object) -> CleanupResult:
        with entered_lock:
            entered.add(path)
            if len(entered) == 2:
                both_entered.set()
        assert release.wait(1), "Long cleanup used the shared executor"
        return CleanupResult(True, "Fake recorded processes ended")

    monkeypatch.setattr("planner.worker_supervision.stop_record", cleanup)

    async def run() -> None:
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        supervisors = []
        for number in range(2):
            path = tmp_path / f"{number:032x}.json"
            records.write(path, records.read(original))
            supervisor = object.__new__(WorkerSupervisor)
            supervisor.path = path
            supervisor._lock = threading.Lock()
            supervisor._end = threading.Event()
            supervisors.append(supervisor)
        tasks = [asyncio.create_task(supervisor.stop()) for supervisor in supervisors]
        try:
            # This probe also needs the shared executor. It must run while both
            # long cleanup calls remain held in their own transient executors.
            async def wait_for_cleanups() -> None:
                while not both_entered.is_set():
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(wait_for_cleanups(), 0.5)
            assert (
                await asyncio.wait_for(asyncio.to_thread(lambda: "available"), 0.5) == "available"
            )
        finally:
            release.set()
            results = await asyncio.gather(*tasks)
        assert all(result.complete for result in results)

    asyncio.run(run())
