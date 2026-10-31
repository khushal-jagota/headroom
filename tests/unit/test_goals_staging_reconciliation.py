"""Prove that the combined migration DAG preserves either accepted branch's state."""

import asyncio
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
from tests.support.measure_sources import NOW, configured_source
from tests.unit.test_conversation_api import _Harness
from tests.unit.test_db import _migration_tree_through
from tests.unit.test_measure_source_dispatcher import HeldRuntime

from planner.core import db
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.credentials.requests import service as credential_requests
from planner.measure_sources import data, run_actions
from planner.measure_sources.contracts import RunMode, RunStatus
from planner.measure_sources.dispatcher import SourceDispatcher


def _seed_goals(conn: sqlite3.Connection) -> tuple[str, ...]:
    conn.execute("""INSERT INTO goals(id,project_id,title,why,kind,active,completed,page,
        revision,created_at,updated_at,employee_backend,employee_launch_model)
        VALUES('retained-goal','retained-project','Retained','Evidence','binary',1,0,
        '{"blocks":[]}',1,1,1,'codex','fixture')""")
    conn.execute("""INSERT INTO measure_sources(id,name,project_id,account,responsible_kind,
        responsible_goal_id,responsibility_revision,source_revision,created_at,updated_at)
        VALUES('retained-source','Retained','retained-project','fixture','goal',
        'retained-goal',3,2,1,1)""")
    conn.execute("""INSERT INTO measures(id,project_id,name,unit,origin,kind,timezone,
        source_id,definition_revision,created_at,updated_at)
        VALUES('retained-measure','retained-project','Retained','items','taken','reading',
        'UTC','retained-source',1,1,1)""")
    conn.execute("""INSERT INTO measure_source_runs(id,source_id,mode,trigger,status,
        source_revision,script_revision,script_sha256,frozen_at,required_outputs,created_at)
        VALUES('retained-run','retained-source','collection','manual','succeeded',
        2,1,'fixture-script',1,'[]',1)""")
    conn.execute("""INSERT INTO measure_observations(measure_id,date,value,run_id,observed_at,
        definition_revision,query_start_at,query_end_at,limitation)
        VALUES('retained-measure','2026-10-30',-2.5,'retained-run',1,1,10,20,'partial fixture')""")
    conn.execute(
        """INSERT INTO credentials(id,scheme,name,auth_kind,encryption_version,
        encryption_context,nonce,ciphertext,revision,created_at,updated_at)
        VALUES('retained-credential','local','Fixture','bearer',2,?,?,?,4,1,1)""",
        (b"fixture-context", b"fixture-nonce", b"opaque-fixture-ciphertext"),
    )
    return (
        "goals",
        "measure_sources",
        "measures",
        "measure_source_runs",
        "measure_observations",
        "credentials",
    )


def _seed_staging(conn: sqlite3.Connection) -> tuple[str, ...]:
    conn.execute("""INSERT INTO ticket_application_previews(generation,ticket_id,worktree,
        public_origin,server_epoch,created_at,state,ended_reason)
        VALUES('retained-preview','fixture-ticket','/fixture/worktree',
        'https://fixture.invalid','fixture-epoch',1,'ended','fixture')""")
    conn.execute("""INSERT INTO conversation_video_uploads(upload_id,destination_json,
        filename,byte_count,received_bytes,state,expires_at,stored_file_id,outcome)
        VALUES('retained-upload','{}','fixture.mp4',42,42,'expired',1,'fixture-file','retained')""")
    return ("ticket_application_previews", "conversation_video_uploads")


@pytest.mark.parametrize(
    "previous_head", [
        None, "ticket_application_previews", "scalar_observation_coverage",
        "goals_staging_reconciliation",
    ]
)
def test_combined_schema_preserves_fresh_and_each_accepted_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_head: str | None,
) -> None:
    shipped = db.MIGRATIONS_DIRECTORY
    if previous_head is not None:
        _migration_tree_through(tmp_path, monkeypatch, previous_head)
    with closing(db.connect(str(tmp_path / "synthetic-reconciliation.db"))) as conn:
        db.create_schema(conn)
        conn.execute("""INSERT INTO projects(id,name,created_at,updated_at)
            VALUES('retained-project','Retained project',1,1)""")
        tables: tuple[str, ...] = ("projects",)
        if previous_head in {"scalar_observation_coverage", "goals_staging_reconciliation"}:
            tables += _seed_goals(conn)
        if previous_head in {"ticket_application_previews", "goals_staging_reconciliation"}:
            tables += _seed_staging(conn)
        before = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in tables
        }
        conn.commit()
        monkeypatch.setattr(db, "MIGRATIONS_DIRECTORY", shipped)
        db.create_schema(conn)
        assert {
            str(row[0]) for row in conn.execute("SELECT version_num FROM alembic_version")
        } == {"mac_vps_execution"}
        assert {
            "goals",
            "measure_sources",
            "credentials",
            "credential_requests",
            "ticket_application_previews",
            "conversation_video_uploads",
        } <= {
            str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, rows in before.items():
            assert [dict(row) for row in conn.execute(f"SELECT * FROM {table}")] == rows
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # A restart must not replay either branch or lose the merged version marker.
        db.create_schema(conn)
        assert conn.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0] == 1


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
