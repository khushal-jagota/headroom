"""What a Mac start carries is decided on the VPS, before any row is committed.

Each start reads the canonical skill rows of that moment, and a start too large for an
envelope is refused at put, so nothing reaches the mailbox.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.executor_presence import mac_identity, make_current
from tests.support.executor_turns import scripted_remote_child

from planner.conversation.contracts import ConversationBackendKey
from planner.execution_identity import ChildReference
from planner.executor.protocol import (
    CanonicalSkillSnapshot,
    CanonicalSkillSource,
    ChildCommand,
    CommandDisposition,
    CommandOutcome,
    StartRun,
    encode_value,
)
from planner.executor.vps_remote_child import MacRemoteChild


def test_each_native_start_reads_current_database_snapshot(
    tmp_db: Connection, tmp_path: Path
) -> None:
    from dataclasses import replace

    from pydantic import TypeAdapter

    from planner.conversation.contracts import (
        ConversationAccess,
        ConversationRoleMaterials,
        ResolvedConversationStart,
    )
    from planner.core.clock import RealClock
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.executor.vps_transport import VpsExecutor
    from planner.managed_skills import read_all_skill_sources, write_skill_source
    from planner.tickets import data

    ticket = data.create_ticket(
        tmp_db,
        title="Canonical native launch",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        employee_backend="codex",
        employee_launch_model="scripted",
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    endpoint = VpsExecutor(path, RealClock())
    start = ResolvedConversationStart(
        "c_snapshot",
        ConversationBackendKey.codex,
        "scripted",
        None,
        ConversationRoleMaterials(
            "Worker", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket.id))
        ),
        tmp_path,
        ConversationAccess.full,
    )
    commands: list[ChildCommand] = []

    async def send(command: ChildCommand) -> CommandOutcome:
        commands.append(command)
        return CommandOutcome(command.command_id, command.child, CommandDisposition.succeeded, {})

    async def run() -> None:
        first_text = read_all_skill_sources(tmp_db)["panels"] + "\nDatabase owner text α.\r\n"
        write_skill_source(tmp_db, "panels", first_text, now=2)
        child = scripted_remote_child(
            ChildReference("incarnation", "one", "c_snapshot"),
            send,
            endpoint.runs._read_skill_snapshot,
        )
        await child.start(start, vendor_session_cursor=None)
        first = commands[0]
        assert isinstance(first.arguments, StartRun)
        snapshot = TypeAdapter(CanonicalSkillSnapshot).validate_python(
            encode_value(first.arguments.canonical_skill_snapshot)
        )
        assert (
            next(row.source_text for row in snapshot.sources if row.skill_name == "panels")
            == first_text
        )
        second_text = first_text + "Later canonical rows.\n"
        write_skill_source(tmp_db, "panels", second_text, now=3)
        await child.start(replace(start), vendor_session_cursor="retained-provider-session")
        assert isinstance(commands[1].arguments, StartRun)
        resumed = TypeAdapter(CanonicalSkillSnapshot).validate_python(
            encode_value(commands[1].arguments.canonical_skill_snapshot)
        )
        assert (
            next(row.source_text for row in resumed.sources if row.skill_name == "panels")
            == second_text
        )
        assert isinstance(commands[1].arguments, StartRun)
        assert (
            encode_value(commands[1].arguments.vendor_session_cursor) == "retained-provider-session"
        )
        assert commands[1].command_id != first.command_id
        await endpoint.close()

    asyncio.run(run())


def test_oversized_full_start_refuses_before_its_row_is_committed(
    tmp_db: Connection, tmp_path: Path
) -> None:
    from planner.conversation.backends.contracts import BackendSpawnFailed
    from planner.conversation.contracts import ConversationAccess, ResolvedConversationStart
    from planner.core.clock import RealClock
    from planner.executor.configuration import MAX_ENVELOPE_BYTES
    from planner.executor.store import ExecutorStore
    from planner.executor.vps_transport import VpsExecutor

    names = ("panels", "panels-worker", "panels-worker-coding")
    # JSON escaping makes the encoded command larger than the raw skill sources.
    snapshot = CanonicalSkillSnapshot(
        tuple(
            CanonicalSkillSource(
                name,
                f"---\nname: {name}\ndescription: test\n---\n" + '"' * (MAX_ENVELOPE_BYTES // 5),
            )
            for name in names
        ),
        names,
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES ('c_large','codex','scripted','/tmp','full',1)"
    )
    tmp_db.commit()
    identity = mac_identity()
    make_current(path, identity)
    reference = ChildReference(identity.executor_incarnation, "child", "c_large")
    ExecutorStore(path).register_child(reference, "ticket", "test-ticket", 1)

    def stored() -> tuple[int, tuple[int, int]]:
        commands = tmp_db.execute("SELECT COUNT(*) FROM executor_commands").fetchone()[0]
        run = tmp_db.execute(
            "SELECT turn_number,delivery_uncertain FROM mac_runs WHERE conversation_id='c_large'"
        ).fetchone()
        return commands, (run[0], run[1])

    before = stored()

    async def run() -> None:
        endpoint = VpsExecutor(path, RealClock())
        try:
            child = MacRemoteChild(reference, endpoint.runs, lambda start: snapshot)
            resolved = ResolvedConversationStart(
                "c_large",
                ConversationBackendKey.codex,
                "scripted",
                None,
                None,
                tmp_path,
                ConversationAccess.full,
            )
            with pytest.raises(BackendSpawnFailed, match="exceeds its encoded size limit"):
                await child.start(resolved, vendor_session_cursor=None)
        finally:
            await endpoint.close()

    asyncio.run(run())
    # The refused start left neither a row nor run bookkeeping behind.
    assert stored() == before == (0, (0, 0))
