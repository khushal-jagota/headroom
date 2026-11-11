"""Local ownership needs positive, exact process evidence, never just elapsed time."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from planner.conversation.child_custody import (
    child_is_absent,
    process_identity,
    retirement_proven,
    write_pending,
    write_retired,
    write_started,
)


def test_exact_generation_and_real_process_exit(tmp_path: Path) -> None:
    if sys.platform != "linux":
        pytest.skip("Linux process birth evidence")
    # This process belongs to the test. No backend, service, or live data is involved.
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        identity = process_identity(process.pid)
        assert identity is not None
        assert not child_is_absent(identity)
        conn = sqlite3.connect(tmp_path / "custody.db")
        conn.execute(
            "CREATE TABLE conversation_child_custody(conversation_id TEXT PRIMARY KEY,"
            "generation TEXT,state TEXT,pid INTEGER,birth TEXT,boot TEXT,unit TEXT,"
            "runtime_epoch TEXT,occurrence_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE conversation_turn_occurrences(conversation_id TEXT, turn_number INTEGER,"
            "occurrence_id TEXT,runtime_epoch TEXT)"
        )
        conn.execute("INSERT INTO conversation_turn_occurrences VALUES ('c',1,'turn','runtime')")
        assert not retirement_proven(conn, "c")
        write_pending(conn, "c", "old")
        assert not retirement_proven(conn, "c")
        write_started(conn, "c", "old", identity)
        assert not retirement_proven(conn, "c")
        write_pending(conn, "c", "replacement")
        write_retired(conn, "c", "old")
        write_started(conn, "c", "old", identity)
        assert not retirement_proven(conn, "c")
        write_started(conn, "c", "replacement", identity)
        process.terminate()
        process.wait(timeout=5)
        assert child_is_absent(identity)
        assert retirement_proven(conn, "c")
        conn.close()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_live_source_keeps_its_claim_then_dead_source_recovers_once(tmp_path: Path) -> None:
    from tests.unit.test_conversation_system import _Harness, _run, _start
    from tests.unit.test_deploy_interruption_delivery import _ticket

    from planner.conversation.deploy_interruptions.coordinator import run_automatic_recovery
    from planner.core.db import connect, create_schema

    if sys.platform != "linux":
        pytest.skip("Linux process birth evidence")
    path = tmp_path / "home.db"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    original = _Harness(path)

    async def exercise() -> None:
        await _start(original)
        await original.store.reserve_turn("c")
        _ticket(path)
        # Model a protocol-confirmed source whose process then dies. The backend
        # harness owns no process; the exact source below belongs only to this test.
        source = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            identity = process_identity(source.pid)
            assert identity is not None
            with connect(str(path)) as db:
                write_pending(db, "c", "source")
                write_started(db, "c", "source", identity)
                db.commit()
            await original.system.shutdown()
            live_restart = _Harness(path)
            try:
                assert live_restart.store.blocked_local_conversations == {"c"}
                assert live_restart.system.workspace_activity() == "unknown"
                await run_automatic_recovery(live_restart.system, live_restart.store)
                assert live_restart.backend("c").writes == []
                with connect(str(path)) as db:
                    assert tuple(db.execute(
                        "SELECT worker_step_claim,worker_step_claim_revision "
                        "FROM tickets WHERE id='t'"
                    ).fetchone()) == ("out", 7)
            finally:
                await live_restart.system.shutdown()
            source.terminate()
            source.wait(timeout=5)
            restarted = _Harness(path)
            try:
                assert not restarted.store.blocked_local_conversations
                await run_automatic_recovery(restarted.system, restarted.store)
                writes = restarted.backend("c").written_texts()
                assert len(writes) == 1
                assert "Inspect the interrupted work" in writes[0]
                assert "Do not replay unknown prompts" in writes[0]
                await run_automatic_recovery(restarted.system, restarted.store)
                assert len(restarted.backend("c").writes) == 1
            finally:
                await restarted.system.shutdown()
        finally:
            if source.poll() is None:
                source.terminate()
                source.wait(timeout=5)
            await original.system.shutdown()

    _run(exercise)


def test_mac_identity_uses_fixed_locale_and_rejects_unknown_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from planner.conversation import child_custody

    valid_boot = True
    calls: list[dict[str, str]] = []

    def run(arguments: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        environment = options["env"]
        assert isinstance(environment, dict)
        calls.append(environment)
        if arguments[0].endswith("sysctl"):
            stdout = "{ sec = 1730000000, usec = 123 } date in any locale" if valid_boot else "unknown"
        else:
            stdout = "Wed Nov 11 10:00:00 2026"
        return subprocess.CompletedProcess(arguments, 0, stdout, "")

    monkeypatch.setattr(child_custody, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(subprocess, "run", run)
    identity = child_custody.process_identity(123)
    assert identity is not None and identity.boot == "darwin:1730000000:123"
    assert all(call["LC_ALL"] == "C" for call in calls)
    valid_boot = False
    assert child_custody.process_identity(123) is None
    assert not child_custody.child_is_absent(identity)
