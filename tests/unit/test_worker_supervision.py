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
        environment = dict(
            os.environ, PLAN_TICKET_ID="priority", PLAN_WORKER_PROCESS_ROOT=str(tmp_path)
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
        )
        tool_pid = None
        try:
            await supervisor.started(process.pid)
            assert process.stdout is not None
            line = await asyncio.wait_for(process.stdout.readline(), 5)
            tool_pid, nice, oom = map(int, line.split())
            assert nice >= 10 and oom >= 0
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
            if process.returncode is None:
                process.kill()
                await process.wait()
            if tool_pid is not None:
                try:
                    os.kill(tool_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

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
    started = time.monotonic()
    result = stop_record(path)
    assert not result.complete and time.monotonic() - started < 0.3
    assert [sig for _, sig in native.signals] == [signal.SIGTERM, signal.SIGKILL]
    assert records.read(path)["state"] == "not_proven"
    assert not inspect_record(path).complete


def test_corrupt_and_pending_records_never_prove_cleanup(tmp_path: Path) -> None:
    path = tmp_path / ("b" * 32 + ".json")
    path.write_text("{")
    path.chmod(0o600)
    assert not stop_record(path).complete
    assert path.read_text() == "{"


def test_native_stop_cancellation_cannot_extend_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        supervisor = WorkerSupervisor(
            dict(os.environ, PLAN_TICKET_ID="deadline", PLAN_WORKER_PROCESS_ROOT=str(tmp_path)),
            tmp_path,
        )
        supervisor.cancel_unspawned()
        release = asyncio.Event()

        async def resistant() -> None:
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        monkeypatch.setattr(config, "STOP_SECONDS", 0.1)
        monkeypatch.setattr(config, "NATIVE_STOP_SECONDS", 0.01)
        started = time.monotonic()
        result = await supervisor.stop(native_stop=resistant)
        assert result.complete and time.monotonic() - started < 0.2
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
        try:
            os.kill(line["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass


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
    assert not stop_record(supervisor.path).complete
    assert records.read(supervisor.path)["root"] is None
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
