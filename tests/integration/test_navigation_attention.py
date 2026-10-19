"""Current attention projection on the actual producer and synthetic whole runtimes.

These SQL/runtime boundaries need a real producer but no server or provider process.
The fixture never supplies guessed attention or a replacement Signal policy.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from planner.conversation.contracts import SignalAttachment
from planner.conversation.publication import PlainFieldMutation, SignalRuntimeMutation
from planner.conversation.publication.reader import read_durable_facts
from planner.conversation.runtime_composition import SignalRuntimeComposition, bind_admission
from planner.navigation import data, journal, snapshot
from planner.navigation.signal_publication import SignalInstallationError, install, participate


@dataclass
class Turn:
    pending_permission_ask_ids: set[str] = field(default_factory=set)
    pending_user_input_questions: dict[str, object] = field(default_factory=dict)


@dataclass
class Runtime:
    running_turn: Turn | None = None


@dataclass
class Host:
    composition: SignalRuntimeComposition
    runtime: Runtime
    attachment: SignalAttachment | None = None
    connections: list[sqlite3.Connection] = field(default_factory=list)

    def connect(self) -> sqlite3.Connection:
        conn = self.composition.connect()
        self.connections.append(conn)
        return conn

    def attach(self) -> None:
        self.attachment = self.composition.coordinator.attach_signal_publication(
            install=install,
            participate=participate,
            unavailable=lambda invalidation: None,
        )
        self.composition.coordinator.admit_runtime()

    def mutation(self, turn: Turn | None, operation: str) -> sqlite3.Connection:
        conn = self.connect()
        coordinator = self.composition.coordinator
        admission = coordinator.prepare_mutation(
            conn,
            SignalRuntimeMutation(
                "c",
                coordinator.runtime_epoch,
                self.runtime.running_turn,
                (PlainFieldMutation(self.runtime, "running_turn", turn),),
            ),
            operation_id=operation,
        )
        with bind_admission(conn, admission):
            conn.execute("BEGIN IMMEDIATE")
        coordinator.apply_mutation(conn, admission)
        return conn


@pytest.fixture
def prepared(tmp_path: Path) -> Iterator[Host]:
    composition = SignalRuntimeComposition(str(tmp_path / "synthetic.db"), 1000)
    runtime = Runtime()
    composition.bootstrap()
    composition.coordinator.bind_runtime(
        lambda identity: runtime.running_turn if identity == "c" else None
    )
    conn = composition.connect()
    try:
        with bind_admission(conn, composition.initialization):
            conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,"
            "created_at) "
            "VALUES(?,'codex','/synthetic','full',0)",
            [("c",), ("h",)],
        )
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,field_values,"
            "created_at,updated_at,conversation_id) "
            "VALUES('t','Ticket','coding','codex','needs_plan','done','{}',0,0,'c')"
        )
        conn.execute("INSERT INTO ticket_conversations VALUES('h','t')")
        conn.commit()
    finally:
        conn.close()
    composition.initialize()
    host = Host(composition, runtime)
    try:
        yield host
    finally:
        for connection in host.connections:
            connection.close()
        if host.attachment is not None:
            host.attachment.close()
        composition.close()


def conversation(conn: sqlite3.Connection, identity: str = "c") -> dict[str, Any]:
    return next(
        dict(record["value"])
        for record in snapshot(conn)["records"]
        if record["kind"] == "conversation" and record["id"] == identity
    )


def test_actual_producer_replacements_change_each_bit_without_generic_counter_changes(
    prepared: Host,
) -> None:
    prepared.attach()
    conn = prepared.connect()
    baseline = conversation(conn)
    generic = {key: value for key, value in baseline.items() if key != "attention"}
    previous = snapshot(conn)
    expected = [
        {
            "signal": {"working": True, "message_for_owner": False},
            "awaiting_answer": False,
            "last_turn_failed": False,
        },
        {
            "signal": {"working": True, "message_for_owner": False},
            "awaiting_answer": True,
            "last_turn_failed": False,
        },
        {
            "signal": {"working": True, "message_for_owner": True},
            "awaiting_answer": True,
            "last_turn_failed": False,
        },
        {
            "signal": {"working": True, "message_for_owner": True},
            "awaiting_answer": True,
            "last_turn_failed": True,
        },
    ]
    for index, turn in enumerate((Turn(), Turn({"permission"}), None, None)):
        if index < 2:
            writer = prepared.mutation(turn, f"runtime-{index}")
        else:
            writer = prepared.connect()
            writer.execute("BEGIN IMMEDIATE")
            kind = "message_to_owner" if index == 2 else "turn_ended"
            payload = "{}" if index == 2 else '{"ending":"failed"}'
            writer.execute(
                "INSERT INTO conversation_events VALUES('c',?,?,?,0)", (index - 1, kind, payload)
            )
        writer.commit()
        writer.close()
        current = conversation(conn)
        assert {key: value for key, value in current.items() if key != "attention"} == generic
        knowledge = current["attention"]
        assert knowledge["current"] == expected[index]
        durable = read_durable_facts(conn, "c")
        assert durable is not None
        assert knowledge["durable"] == {
            "message_for_owner": durable.message_for_owner,
            "last_turn_failed": durable.last_turn_failed,
        }
        page = journal(conn, previous["epoch"], previous["revision"])
        assert len(page["batches"]) == 1
        assert page["batches"][0]["upserts"] == [
            {"kind": "conversation", "id": "c", "value": current}
        ]
        previous = snapshot(conn)
    writer = prepared.mutation(None, "rolled-back-runtime")
    writer.rollback()
    writer.close()
    assert prepared.runtime.running_turn is not None
    assert snapshot(conn) == previous
    conn.close()


def test_attention_alias_move_and_canonical_deletion_are_one_complete_boundary(
    prepared: Host,
) -> None:
    prepared.attach()
    conn = prepared.connect()
    old = snapshot(conn)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO ticket_conversations VALUES('c','t')")
    conn.execute("UPDATE tickets SET conversation_id='h' WHERE id='t'")
    conn.commit()
    page = journal(conn, old["epoch"], old["revision"])
    assert len(page["batches"]) == 1
    addresses = [
        record["value"] for record in snapshot(conn)["records"] if record["kind"] == "address"
    ]
    assert {(a["conversation_id"], a["current"]) for a in addresses} == {("c", False), ("h", True)}
    old = snapshot(conn)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM ticket_conversations WHERE conversation_id='c'")
    conn.execute("DELETE FROM conversations WHERE conversation_id='c'")
    conn.commit()
    page = journal(conn, old["epoch"], old["revision"])
    assert len(page["batches"]) == 1
    assert {"kind": "conversation", "id": "c"} in page["batches"][0]["deletes"]
    assert all(
        record["id"] != "c"
        for record in snapshot(conn)["records"]
        if record["kind"] == "conversation"
    )
    assert all(
        record["value"]["conversation_id"] != "c"
        for record in snapshot(conn)["records"]
        if record["kind"] == "address"
    )
    conn.close()


@pytest.mark.parametrize("corruption", ["missing", "tombstone", "epoch", "incomplete", "nonbool"])
def test_current_projection_rejects_missing_or_malformed_publication(
    prepared: Host, corruption: str
) -> None:
    prepared.attach()
    conn = prepared.connect()
    old = snapshot(conn)
    conn.execute("BEGIN IMMEDIATE")
    if corruption == "missing":
        conn.execute("DELETE FROM navigation_signal_publications WHERE conversation_id='c'")
    elif corruption == "tombstone":
        conn.execute(
            "UPDATE navigation_signal_publications SET attention=NULL WHERE conversation_id='c'"
        )
    elif corruption == "epoch":
        conn.execute(
            "UPDATE navigation_signal_publications SET runtime_epoch='obsolete' "
            "WHERE conversation_id='c'"
        )
    else:
        value = conversation(conn)["attention"]["current"]
        if corruption == "incomplete":
            del value["signal"]["working"]
        else:
            value["signal"]["working"] = 0
        conn.execute(
            "UPDATE navigation_signal_publications SET attention=? WHERE conversation_id='c'",
            (json.dumps(value),),
        )
    with pytest.raises(SignalInstallationError):
        data.rebuild(conn)
    conn.rollback()
    assert snapshot(conn) == old
    conn.close()


def test_physical_migration_waits_for_complete_install_and_failed_activation_rolls_back(
    prepared: Host,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = prepared.connect()
    assert conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM navigation_signal_publications").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM navigation_signal_installation").fetchone()[0] == 0
    assert all(
        "attention" not in json.loads(row[0])
        for row in conn.execute("SELECT value FROM navigation_records WHERE kind='conversation'")
    )
    before = list(conn.iterdump())
    rebuild = data.rebuild

    def fail_after_rebuild(connection: sqlite3.Connection) -> None:
        rebuild(connection)
        raise RuntimeError("injected activation failure")

    with monkeypatch.context() as patch:
        patch.setattr(data, "rebuild", fail_after_rebuild)
        with pytest.raises(RuntimeError, match="injected activation failure"):
            prepared.attach()
    assert list(conn.iterdump()) == before
    from tests.integration.test_navigation_current_workspace import assert_prepared_rejected

    assert_prepared_rejected(Path(prepared.composition.db_path))
    assert conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == 5
    with pytest.raises(RuntimeError, match="runtime admission is not open"):
        snapshot(conn)
    conn.close()
    prepared.composition.close()
    # A restart creates no authority from the retained physical projection alone.
    replacement = SignalRuntimeComposition(prepared.composition.db_path, 1000)
    replacement.bootstrap()
    replacement.coordinator.bind_runtime(lambda identity: None)
    replacement.initialize()
    resumed = Host(replacement, Runtime())
    check = replacement.connect()
    calls = 0

    def count_rebuild(connection: sqlite3.Connection) -> None:
        nonlocal calls
        calls += 1
        rebuild(connection)

    try:
        assert check.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == 5
        with monkeypatch.context() as patch:
            patch.setattr(data, "rebuild", count_rebuild)
            resumed.attach()
        assert calls == 1
        current = snapshot(check)
        assert current["schema_version"] == 6
        assert current["semantics"] == "attention-knowledge-v6"
        assert current["revision"] == "0"
        assert all(
            record["value"]["attention"]["current"] is not None
            for record in current["records"]
            if record["kind"] == "conversation"
        )
    finally:
        check.close()
        if resumed.attachment is not None:
            resumed.attachment.close()
        replacement.close()


def test_empty_physical_runtime_cannot_activate_attention_without_complete_install(
    tmp_path: Path,
) -> None:
    composition = SignalRuntimeComposition(str(tmp_path / "empty.db"), 1000)
    composition.bootstrap()
    composition.coordinator.bind_runtime(lambda identity: None)
    conn = composition.connect()
    attachment: SignalAttachment | None = None
    try:
        assert conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
        with bind_admission(conn, composition.initialization):
            conn.execute("BEGIN IMMEDIATE")
        data.rebuild(conn)
        assert conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == 5
        with pytest.raises(ValueError, match="unsupported navigation schema version"):
            data.create_schema(conn, schema_version=4)
        conn.commit()
        composition.initialize()
        attachment = composition.coordinator.attach_signal_publication(
            install=install,
            participate=participate,
            unavailable=lambda invalidation: None,
        )
        composition.coordinator.admit_runtime()
        current = snapshot(conn)
        assert current["schema_version"] == 6
        assert current["semantics"] == "attention-knowledge-v6"
        assert all(record["kind"] != "conversation" for record in current["records"])
        assert (
            conn.execute("SELECT COUNT(*) FROM navigation_signal_publications").fetchone()[0] == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM navigation_signal_installation").fetchone()[0] == 1
        )
    finally:
        conn.close()
        if attachment is not None:
            attachment.close()
        composition.close()
