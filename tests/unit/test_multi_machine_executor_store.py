"""Machine boundaries absent from the single-executor tests.

Risk/gap: one machine replaces, polls, or reads another's retained authority; an idle
machine takes the SQLite writer; a forged duplicate returns a foreign receipt. Reuse
real temporary SQLite stores and lease fixtures. No listener or new browser host.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from tests.support.executor_presence import lease_request, mac_identity

from planner.core.clock import RealClock
from planner.core.contracts import PlannerError
from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.execution_identity import ChildReference
from planner.executor.protocol import (
    EnvelopeKind,
    JsonObject,
    ModelListRequest,
    QueuedEnvelope,
)
from planner.executor.vps_inbox import InboxStore
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence_store import (
    accept_lease_in_transaction,
    current_incarnation,
)
from planner.executor.vps_transport import VpsExecutor
from planner.machines.contracts import ORIGINAL_MAC_MACHINE_ID

OTHER = "2ac2e524-89cf-4493-bd0d-23cb36ca8f0c"


def database(tmp_path: Path) -> str:
    path = str(tmp_path / "home.db")
    conn = connect(path)
    try:
        create_schema(conn)
        conn.execute(
            "INSERT INTO machines VALUES (?, 'Other', 'desktop', 'sand', 0)", (OTHER,)
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_replacement_and_incarnation_claim_stay_with_their_machine(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    first, other, replacement = mac_identity(), mac_identity(), mac_identity()
    with connect(path) as conn:
        accept_lease_in_transaction(
            conn, lease_request(first), 100, ORIGINAL_MAC_MACHINE_ID
        )
        accept_lease_in_transaction(conn, lease_request(other), 100, OTHER)
        accept_lease_in_transaction(
            conn, lease_request(replacement), 200, ORIGINAL_MAC_MACHINE_ID
        )
        assert current_incarnation(conn, OTHER) == other.executor_incarnation
        assert current_incarnation(conn) == replacement.executor_incarnation
        with pytest.raises(PlannerError):
            accept_lease_in_transaction(
                conn, lease_request(other), 300, ORIGINAL_MAC_MACHINE_ID
            )
        assert current_incarnation(conn, OTHER) == other.executor_incarnation


def test_idle_poll_leaves_the_writer_free_even_with_other_machine_work(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    first, other = mac_identity(), mac_identity()
    with connect(path) as conn:
        accept_lease_in_transaction(conn, lease_request(first), 100)
        accept_lease_in_transaction(conn, lease_request(other), 100, OTHER)
    original = SqliteCommandMailbox(path)
    asyncio.run(original.put_model_list_request(ModelListRequest("codex", False)))
    mailbox = SqliteCommandMailbox(path, machine_id=OTHER)
    # Hold a real writer throughout the empty poll. A BEGIN IMMEDIATE on the poll
    # path would block or fail even though no work belongs to this machine.
    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        reply, settled = mailbox._fetch(other.executor_incarnation, 0, 100, 100)
        assert reply is not None and not reply.commands and not settled
        writer.rollback()
    assert mailbox.high_sequence() == 0


def test_duplicate_receipt_cannot_cross_machine_after_conversation_reuse(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    first, other = mac_identity(), mac_identity()
    with connect(path) as conn:
        accept_lease_in_transaction(conn, lease_request(first), 100)
        accept_lease_in_transaction(conn, lease_request(other), 100, OTHER)
    envelope = QueuedEnvelope(
        "retained-sender",
        "reused-conversation",
        1,
        "submission",
        EnvelopeKind.cli_write,
        {},
    )
    original = InboxStore(path)

    def writer(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        return {"private": "original"}

    receipt = original.accept(envelope, writer, 110, identity=first)
    assert receipt.result == {"private": "original"}
    # Even a valid current sender cannot obtain a receipt from a former owner.
    with pytest.raises(PlannerError, match="receipt belongs to another machine"):
        InboxStore(path, machine_id=OTHER).preflight(envelope, identity=other)
    assert original.preflight(envelope, identity=first) == receipt


def test_machine_owner_checks_precede_live_reads(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    first, other = mac_identity(), mac_identity()
    with connect(path) as conn:
        accept_lease_in_transaction(conn, lease_request(first), 100)
        accept_lease_in_transaction(conn, lease_request(other), 100, OTHER)
    foreign = VpsExecutor(path, RealClock(), machine_id=OTHER)
    assert (
        foreign.inbox.store.upload_staging_root == tmp_path / "executor-uploads" / OTHER
    )
    from planner.executor.protocol import LiveReadRequest

    request = LiveReadRequest(
        ChildReference(first.executor_incarnation, "child", "conversation"),
        "/api/tickets",
        {},
    )
    with pytest.raises(PlannerError, match="another machine"):
        asyncio.run(foreign.read(other, request))

    # A valid child must not turn an allowed raw prefix into a private file URL.
    canary = tmp_path / "foreign-canary.txt"
    canary.write_text("private command-file bytes")
    app = FastAPI()
    reached: list[str] = []

    @app.get("/api/tickets")
    @app.get("/api/tickets/shared-ticket")
    async def permitted(request: Request) -> dict[str, bool]:
        reached.append(request.url.path)
        return {"shared_record": True}

    @app.get("/files/tickets/foreign/canary.txt")
    @app.get("/api/tickets-unsafe")
    async def private(request: Request) -> dict[str, str]:
        reached.append(request.url.path)
        return {"canary": canary.read_text()}

    foreign.app = app
    child = ChildReference(other.executor_incarnation, "own-child", "own-conversation")
    foreign.runs.store.register_child(child, "ticket", "own-ticket", 1)

    async def check_reads() -> None:
        for forbidden in (
            "/api/tickets/../../files/tickets/foreign/canary.txt",
            "/api/tickets%2f%2e%2e%2f%2e%2e%2ffiles%2ftickets%2fforeign%2fcanary.txt",
            "/api/tickets-unsafe",
        ):
            with pytest.raises(PlannerError, match="unsupported executor read"):
                await foreign.read(other, LiveReadRequest(child, forbidden, {}))
        assert reached == []
        for permitted_path in ("/api/tickets", "/api/tickets/shared-ticket"):
            assert await foreign.read(other, LiveReadRequest(child, permitted_path, {})) == {
                "shared_record": True
            }
        assert reached == ["/api/tickets", "/api/tickets/shared-ticket"]
        await foreign.close()

    asyncio.run(check_reads())


def test_populated_migration_preserves_command_and_receipt_identity(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "legacy.db")
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, "merge_home_setup_maintenance")
    incarnation = str(uuid4())
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO executor_incarnations VALUES (?,1,NULL)", (incarnation,)
        )
        conn.execute(
            "INSERT INTO mac_executor_presence(singleton,executor_incarnation) VALUES (1,?)",
            (incarnation,),
        )
        conn.execute(
            "INSERT INTO executor_commands(command_sequence,command_id,executor_incarnation,"
            "operation,command_json,state,created_at_ms) "
            "VALUES (73,'command',?,'read_model_list','{}','waiting',1)",
            (incarnation,),
        )
        conn.execute(
            "INSERT INTO mac_inbox_positions VALUES "
            "('sender','conversation',8,'submission','accepted','{}')"
        )
        conn.commit()
        create_schema(conn)
        assert tuple(
            conn.execute(
                "SELECT command_sequence,command_id,executor_incarnation,machine_id "
                "FROM executor_commands"
            ).fetchone()
        ) == (73, "command", incarnation, ORIGINAL_MAC_MACHINE_ID)
        assert tuple(
            conn.execute(
                "SELECT sender_id,sequence,submission_id,machine_id FROM mac_inbox_positions"
            ).fetchone()
        ) == ("sender", 8, "submission", ORIGINAL_MAC_MACHINE_ID)
        assert current_incarnation(conn) == incarnation
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_refuses_orphan_presence_without_erasing_evidence(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "orphan.db")
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, "merge_home_setup_maintenance")
    conn = connect(path)
    try:
        conn.execute(
            "INSERT INTO mac_executor_presence(singleton,executor_incarnation) VALUES(1,'orphan')"
        )
        conn.commit()
        with pytest.raises(ValueError, match="orphan in presence"):
            create_schema(conn)
        assert (
            conn.execute(
                "SELECT executor_incarnation FROM mac_executor_presence WHERE singleton=1"
            ).fetchone()[0]
            == "orphan"
        )
    finally:
        conn.close()


def test_foreign_lease_telemetry_cannot_borrow_another_machine_stall_identity(
    tmp_path: Path,
) -> None:
    from planner.execution_identity import TurnToken
    from planner.executor.protocol import WaitingConversation, WorkingTurn

    path = database(tmp_path)

    async def exercise() -> None:
        first, other = mac_identity(), mac_identity()
        original = VpsExecutor(path, RealClock())
        foreign = VpsExecutor(path, RealClock(), machine_id=OTHER)
        await original.lease(lease_request(first))
        await foreign.lease(lease_request(other))
        child = ChildReference(
            other.executor_incarnation, "other-child", "other-conversation"
        )
        foreign.runs.store.register_child(child, "ticket", "other-ticket", 1)
        with connect(path) as conn:
            conn.execute(
                "UPDATE mac_runs SET turn_number=1 WHERE conversation_id=?",
                (child.conversation_id,),
            )
        working = WorkingTurn(child, TurnToken(child.conversation_id, 1), False)
        await foreign.lease(lease_request(other, working=(working,)))
        before = foreign.presence.record()
        own_child = ChildReference(
            first.executor_incarnation, "own-child", "own-conversation"
        )
        original.runs.store.register_child(own_child, "ticket", "own-ticket", 1)
        with connect(path) as conn:
            conn.execute(
                "UPDATE mac_runs SET turn_number=1 WHERE conversation_id=?",
                (own_child.conversation_id,),
            )
        await original.lease(
            lease_request(
                first,
                working=(
                    WorkingTurn(
                        own_child, TurnToken(own_child.conversation_id, 1), False
                    ),
                ),
            )
        )
        from planner.executor.protocol import (
            BackendEvent,
            BackendEventMethod,
            LiveEventBatch,
        )

        activity_before = dict(original.presence._activity_ms)
        await original.live(
            first,
            own_child.conversation_id,
            LiveEventBatch(
                (
                    BackendEvent(
                        child,
                        BackendEventMethod.agent_message_delta,
                        working.turn,
                        {"text": "forged"},
                    ),
                )
            ),
        )
        assert original.presence._activity_ms == activity_before
        for request in (
            lease_request(first, working=(working,)),
            lease_request(
                first, waiting=(WaitingConversation(child.conversation_id, 1),)
            ),
        ):
            with pytest.raises(PlannerError, match="another machine"):
                await original.lease(request)
        assert foreign.presence.record() == before
        assert {
            wait.conversation_id for wait in original.presence._tracked_waits(0)
        } == {own_child.conversation_id}
        assert foreign.presence._tracked_waits(0)
        original.presence.note_conversation_activity(own_child.conversation_id)
        await original.lease(lease_request(first))
        assert not original.presence._activity_ms
        assert not original.presence._tracked_waits(0)
        await original.close()
        await foreign.close()

    asyncio.run(exercise())
