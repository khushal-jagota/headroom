"""Status publication through ordinary SQLite saves, without save admission fences."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from planner.conversation.contracts import SignalBatch
from planner.conversation.publication import persistence
from planner.conversation.runtime_composition import SignalRuntimeComposition


@dataclass
class Turn:
    pending_permission_ask_ids: set[str] = field(default_factory=set)
    pending_user_input_questions: dict[str, object] = field(default_factory=dict)


@dataclass
class Host:
    composition: SignalRuntimeComposition
    turns: dict[str, Turn]
    batches: list[SignalBatch]

    def value(self, identity: str = "c") -> dict[str, object]:
        with self.composition.connect() as conn:
            row = conn.execute(
                "SELECT value FROM signal_publication_values WHERE conversation_id=?", (identity,)
            ).fetchone()
            return json.loads(row[0])["attention"]


@pytest.fixture
def host(tmp_path: Path) -> Iterator[Host]:
    composition = SignalRuntimeComposition(str(tmp_path / "signals.db"), 1000)
    turns: dict[str, Turn] = {}
    batches: list[SignalBatch] = []
    composition.bootstrap()
    composition.coordinator.bind_runtime(turns.get)
    conn = composition.connect()
    conn.execute(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES('c','codex','/synthetic','full',0)"
    )
    conn.close()
    composition.initialize()
    attachment = composition.coordinator.attach_signal_publication(
        install=lambda connection, snapshot: None,
        participate=lambda connection, batch: batches.append(batch),
        unavailable=lambda invalidation: None,
    )
    try:
        yield Host(composition, turns, batches)
    finally:
        attachment.close()
        composition.close()


def test_status_facts_change_independently_after_plain_application_save(host: Host) -> None:
    conn = host.composition.connect()
    try:
        host.turns["c"] = Turn({"ask"})
        conn.execute("UPDATE conversations SET latest_sequence=1 WHERE conversation_id='c'")
        assert host.value() == {
            "signal": {"working": True, "message_for_owner": False},
            "awaiting_answer": True,
            "last_turn_failed": False,
        }
        conn.execute("INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}',0)")
        assert host.value()["signal"] == {"working": True, "message_for_owner": True}
        host.turns.clear()
        conn.execute(
            "INSERT INTO conversation_events VALUES('c',2,'turn_ended',?,0)",
            ('{"ending":"failed"}',),
        )
        assert host.value() == {
            "signal": {"working": False, "message_for_owner": True},
            "awaiting_answer": False,
            "last_turn_failed": True,
        }
        conn.execute(
            "UPDATE conversations SET owner_read_through_sequence=2 WHERE conversation_id='c'"
        )
        assert host.value()["signal"] == {"working": False, "message_for_owner": False}
        assert len(host.batches) == 4
    finally:
        conn.close()


def test_raw_sqlite_save_marks_changes_without_runtime_functions(host: Host) -> None:
    with sqlite3.connect(host.composition.db_path) as raw:
        raw.execute("INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}',0)")
        assert raw.execute("SELECT conversation_id FROM signal_publication_dirty").fetchall() == [
            ("c",)
        ]
        assert not raw.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'signal_private_%' "
            "OR name IN ('signal_publication_operations','signal_publication_batches')"
        ).fetchall()
    # A raw edit does not invoke Python publication. The next app save consumes its mark.
    conn = host.composition.connect()
    try:
        conn.execute("UPDATE conversations SET latest_sequence=1 WHERE conversation_id='c'")
        assert host.value()["signal"] == {"working": False, "message_for_owner": True}
    finally:
        conn.close()


def test_startup_recomputes_all_statuses_after_unpublished_raw_edit(host: Host) -> None:
    with sqlite3.connect(host.composition.db_path) as raw:
        raw.execute("INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}',0)")
    host.composition.close()
    replacement = SignalRuntimeComposition(host.composition.db_path, 1000)
    try:
        replacement.bootstrap()
        replacement.coordinator.bind_runtime(lambda identity: None)
        replacement.initialize()
        conn = replacement.connect()
        try:
            value = persistence.values(conn)
            assert len(value) == 1
            assert value[0].attention.signal == {"working": False, "message_for_owner": True}
            assert conn.execute("SELECT * FROM signal_publication_dirty").fetchall() == []
        finally:
            conn.close()
    finally:
        replacement.close()


def test_source_rollback_does_not_publish_or_leave_marks(host: Host) -> None:
    conn = host.composition.connect()
    try:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}',0)")
        conn.rollback()
        assert host.batches == []
        assert conn.execute("SELECT * FROM signal_publication_dirty").fetchall() == []
        assert host.value()["signal"] == {"working": False, "message_for_owner": False}
    finally:
        conn.close()


def test_upgrade_removes_installed_fences_and_evidence(host: Host) -> None:
    conn = host.composition.connect()
    try:
        conn.execute("BEGIN")
        conn.execute("CREATE TABLE signal_publication_operations(operation_id TEXT)")
        conn.execute("CREATE TABLE signal_publication_batches(batch_id TEXT)")
        conn.execute(
            "CREATE TRIGGER signal_private_values_update "
            "BEFORE UPDATE ON signal_publication_values "
            "BEGIN SELECT RAISE(ABORT,'old fence'); END"
        )
        conn.execute("DROP TRIGGER signal_source_conversations_update")
        conn.execute(
            "CREATE TRIGGER signal_source_conversations_update BEFORE UPDATE ON conversations "
            "BEGIN SELECT signal_publication_admitted(); END"
        )
        persistence.install_source_triggers(conn)
        conn.commit()
        conn.execute("UPDATE conversations SET latest_sequence=1 WHERE conversation_id='c'")
        assert len(host.batches) == 1
    finally:
        conn.close()


def test_bootstrap_removes_old_fences_before_startup_writes(host: Host) -> None:
    host.composition.close()
    with sqlite3.connect(host.composition.db_path) as raw:
        raw.execute("DROP TRIGGER signal_source_conversations_update")
        raw.execute(
            "CREATE TRIGGER signal_source_conversations_update BEFORE UPDATE ON conversations "
            "BEGIN SELECT signal_publication_admitted(); END"
        )
    replacement = SignalRuntimeComposition(host.composition.db_path, 1000)
    try:

        def audit(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE conversations SET latest_sequence=1 WHERE conversation_id='c'")

        replacement.bootstrap(startup_audit=audit)
        with replacement.connect() as conn:
            assert (
                conn.execute(
                    "SELECT latest_sequence FROM conversations WHERE conversation_id='c'"
                ).fetchone()[0]
                == 1
            )
    finally:
        replacement.close()
