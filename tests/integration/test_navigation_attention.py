"""Current attention projection on the actual producer and synthetic whole runtimes.

These SQL/runtime boundaries need a real producer but no server or provider process.
The fixture never supplies guessed attention or a replacement Signal policy.
"""

from __future__ import annotations

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
from planner.navigation import journal, snapshot
from planner.navigation.signal_publication import install, participate


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
