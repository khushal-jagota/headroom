"""Source and credential operations respect update admission and cancellation."""

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest
from tests.support.measure_sources import NOW, configured_source
from tests.unit.test_conversation_api import _Harness
from tests.unit.test_measure_source_dispatcher import HeldRuntime

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.credentials.requests import service as credential_requests
from planner.measure_sources import data, run_actions
from planner.measure_sources.contracts import RunMode, RunStatus
from planner.measure_sources.dispatcher import SourceDispatcher


def test_source_execution_blocks_idle_update_and_closed_admission_keeps_runs_pending(
    tmp_db: sqlite3.Connection, fake_clock: FakeClock, tmp_path: Path
) -> None:
    source, _ = configured_source(tmp_db)
    assert source.active_script is not None
    path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    host = _Harness(path)
    runtime = HeldRuntime()
    dispatcher = SourceDispatcher(
        str(path), fake_clock, runtime, key_path=tmp_path / "synthetic-key",
        workspace_admission=host.system.workspace_admission,
    )

    script_revision = source.active_script.revision

    async def exercise() -> None:
        try:
            run = run_actions.request_run(
                tmp_db,
                source.id,
                mode=RunMode.collection,
                history=None,
                expected_script_revision=script_revision,
                principal=OWNER_PRINCIPAL,
                now=NOW,
            )
            dispatcher.start()
            try:
                assert runtime.started.wait(3)
                assert host.system.workspace_activity() == "busy"
                assert not await host.system.run_with_workspace_admission(
                    lambda: None, require_idle=True
                )
            finally:
                dispatcher.stop(deadline=time.monotonic() + 5)
            assert data.read_run(tmp_db, run.id).status is RunStatus.interrupted
            assert await host.system.run_with_workspace_admission(lambda: None, require_idle=True)
            pending = run_actions.request_run(
                tmp_db,
                source.id,
                mode=RunMode.collection,
                history=None,
                expected_script_revision=script_revision,
                principal=OWNER_PRINCIPAL,
                now=NOW + 1,
            )
            refused_runtime = HeldRuntime()
            refused = SourceDispatcher(
                str(path), fake_clock, refused_runtime, key_path=tmp_path / "synthetic-key",
                workspace_admission=host.system.workspace_admission,
            )
            refused.start()
            try:
                # Drive a poll deterministically rather than infer refusal from a sleep.
                refused._pump()
                assert data.read_run(tmp_db, pending.id).status is RunStatus.pending
                assert not refused_runtime.started.is_set()
            finally:
                refused.stop(deadline=time.monotonic() + 5)
        finally:
            await host.runtime.shutdown()

    asyncio.run(exercise())


def test_shielded_credential_work_blocks_idle_update_after_caller_leaves(
    tmp_db: sqlite3.Connection,
) -> None:
    path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    host = _Harness(path)

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        entered_closed_operation = False

        async def settle() -> None:
            started.set()
            await release.wait()

        async def refused_operation() -> None:
            nonlocal entered_closed_operation
            entered_closed_operation = True

        caller = asyncio.create_task(credential_requests.shield_operation(host.runtime, settle()))
        try:
            await asyncio.wait_for(started.wait(), timeout=3)
            assert host.system.workspace_activity() == "busy"
            assert not await host.system.run_with_workspace_admission(
                lambda: None, require_idle=True
            )
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert host.system.workspace_admission.active
            assert not await host.system.run_with_workspace_admission(
                lambda: None, require_idle=True
            )
            release.set()
            await credential_requests.finish_operations(host.runtime)
            assert await host.system.run_with_workspace_admission(lambda: None, require_idle=True)
            with pytest.raises(RuntimeError, match="Workspace admission is closed"):
                await credential_requests.shield_operation(host.runtime, refused_operation())
            assert not entered_closed_operation
        finally:
            release.set()
            await credential_requests.finish_operations(host.runtime)
            await host.runtime.shutdown()

    asyncio.run(exercise())
