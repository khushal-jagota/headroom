"""Stop-window evidence must not invent certainty or extend release authority."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from planner.conversation.deploy_interruptions.files import manifest_root, read_manifests
from planner.conversation.lifecycle_storage import acquire_runtime_ownership


def capture_after_stop(database: Path, attempt_id: str) -> subprocess.CompletedProcess[str]:
    """Run the helper the way the installer does after it stops the service."""
    return subprocess.run(
        [sys.executable, "-I", "-m", "planner.environments.interruption_capture",
         str(database), attempt_id, uuid4().hex],
        check=False, timeout=30, capture_output=True, text=True,
    )


@pytest.fixture
def capture_database(tmp_path: Path) -> Path:
    database = tmp_path / "planning.db"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE conversation_runtime_epoch(singleton INTEGER PRIMARY KEY,epoch TEXT);
            INSERT INTO conversation_runtime_epoch VALUES(1,'source-epoch');
            CREATE TABLE conversation_turn_occurrences(
                conversation_id TEXT,turn_number INTEGER,occurrence_id TEXT,runtime_epoch TEXT,
                PRIMARY KEY(conversation_id,turn_number));
            INSERT INTO conversation_turn_occurrences VALUES('worker',1,'nonce','source-epoch');
            CREATE TABLE conversation_lifecycle_facts(
                conversation_id TEXT,kind TEXT,turn_number INTEGER,status TEXT);
            CREATE INDEX unresolved ON conversation_lifecycle_facts(kind,status);
            INSERT INTO conversation_lifecycle_facts VALUES('worker','turn',1,'running');
            INSERT INTO conversation_lifecycle_facts VALUES('completed','turn',1,'completed');
            INSERT INTO conversation_lifecycle_facts VALUES('legacy','turn',2,'intent');
            CREATE TABLE tickets(id TEXT,title TEXT,conversation_id TEXT,
                worker_step_claim_revision INTEGER,worker_step_claim TEXT);
            CREATE INDEX ticket_conversation ON tickets(conversation_id);
            INSERT INTO tickets VALUES('ticket','Ship the change','worker',7,'out');
            CREATE TABLE conversation_history_catalog(
                conversation_id TEXT,owner_kind TEXT,owner_id TEXT);
            CREATE TABLE managers(id TEXT,title TEXT);
            CREATE TABLE agents(agent_key TEXT,conversation_id TEXT);
            CREATE TABLE mac_runs(conversation_id TEXT,stop_state TEXT);
            INSERT INTO agents VALUES('chief_of_staff','legacy');
        """)
    return database


def test_real_capture_is_read_only_exact_and_preserves_unavailable_when_lease_is_owned(
    capture_database: Path,
) -> None:
    start = time.monotonic()
    capture_after_stop(capture_database, "release-one")
    duration = time.monotonic() - start
    manifests = read_manifests(capture_database)
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.state == "captured"
    assert [(turn.conversation_id, turn.original_status) for turn in manifest.turns] == [
        ("legacy", "intent"),
        ("worker", "running"),
    ]
    worker = manifest.turns[1]
    assert (
        worker.occurrence_id,
        worker.runtime_epoch,
        worker.owner_kind,
        worker.claim_revision,
        worker.claim,
    ) == ("nonce", "source-epoch", "ticket", 7, "out")
    assert manifest.turns[0].occurrence_id is None
    assert manifest.turns[0].runtime_epoch == "source-epoch"
    with acquire_runtime_ownership(str(capture_database)):
        capture_after_stop(capture_database, "release-one")
    manifests = read_manifests(capture_database)
    assert len({item.capture_id for item in manifests}) == 2
    unavailable = next(item for item in manifests if item.state == "unavailable")
    assert unavailable.turns == ()
    assert "ConversationRuntimeAlreadyOwned" in (unavailable.detail or "")
    with sqlite3.connect(capture_database) as conn:
        assert conn.execute("SELECT worker_step_claim FROM tickets").fetchone() == ("out",)
        assert conn.execute(
            "SELECT status FROM conversation_lifecycle_facts WHERE conversation_id='worker'"
        ).fetchone() == ("running",)
    print(f"isolated capture duration={duration:.6f}s")


def test_invalid_manifests_are_unavailable_and_temporary_files_are_ignored(tmp_path: Path) -> None:
    database = tmp_path / "db"
    directory = manifest_root(database) / "release"
    directory.mkdir(parents=True)
    (directory / ".capture-unfinished").write_text("incomplete")
    (directory / "invalid.json").write_text(json.dumps({"version": 1, "turns": []}))
    (directory / "linked.json").symlink_to(directory / "invalid.json")
    os.mkfifo(directory / "pipe.json")
    manifests = read_manifests(database)
    assert len(manifests) == 3
    assert all(item.state == "unavailable" and not item.turns for item in manifests)


def test_missing_schema_and_diagnostic_write_failure_remain_unavailable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database):
        pass
    result = capture_after_stop(database, "missing-schema")
    manifests = read_manifests(database)
    assert len(manifests) == 1
    assert manifests[0].state == "unavailable"
    assert "OperationalError" in (manifests[0].detail or "")
    assert "OperationalError" in result.stdout
    blocked = manifest_root(database) / "blocked-release"
    blocked.write_text("not a directory")
    assert capture_after_stop(database, "blocked-release").returncode != 0
    assert all(item.state == "unavailable" for item in read_manifests(database))


def test_capture_keeps_historical_ticket_name_without_current_claim_authority(
    capture_database: Path,
) -> None:
    with sqlite3.connect(capture_database) as conn:
        conn.execute("UPDATE tickets SET conversation_id='replacement'")
        conn.execute("INSERT INTO conversation_history_catalog VALUES('worker','ticket','ticket')")
    capture_after_stop(capture_database, "changed-link:legacy")
    turn = next(
        turn
        for turn in read_manifests(capture_database)[0].turns
        if turn.conversation_id == "worker"
    )
    assert (turn.owner_kind, turn.owner_id, turn.display_name) == (
        "ticket",
        "ticket",
        "Ship the change",
    )
    assert turn.claim is None and turn.claim_revision is None


@pytest.mark.parametrize("evidence", ["stopping", "closed"])
def test_vps_stop_requires_native_evidence_before_excluding_local_recovery(
    capture_database: Path, evidence: str
) -> None:
    # Existing local fixture also proves that the exclusion does not suppress
    # ordinary local recovery. No native connection or provider is involved.
    with sqlite3.connect(capture_database) as conn:
        conn.execute("INSERT INTO mac_runs VALUES('worker',?)", (evidence,))
    capture_after_stop(capture_database, "native-survives")
    manifest = read_manifests(capture_database)[0]
    assert manifest.state == "captured"
    assert [turn.conversation_id for turn in manifest.turns] == (
        ["legacy", "worker"] if evidence == "closed" else ["legacy"]
    )
    with sqlite3.connect(capture_database) as conn:
        assert conn.execute(
            "SELECT status FROM conversation_lifecycle_facts WHERE conversation_id='worker'"
        ).fetchone() == ("running",)
