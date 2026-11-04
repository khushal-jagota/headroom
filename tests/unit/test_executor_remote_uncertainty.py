"""An unproved native prompt cannot turn into refusal or an automatic retry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.executor_turns import MacRemoteChildFactory

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryUncertain,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.execution_identity import ChildReference
from planner.executor.protocol import (
    CanonicalSkillSnapshot,
    CanonicalSkillSource,
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    StartRun,
    encode_value,
)
from planner.executor.vps_remote_child import MacRemoteChild


def test_unknown_prompt_is_durable_and_quarantined_across_vps_restart(
    tmp_db: Connection, tmp_path: Path
) -> None:
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    commands: list[ChildCommand] = []

    async def native(command: ChildCommand) -> CommandOutcome:
        commands.append(command)
        disposition = (
            CommandDisposition.succeeded
            if command.operation is ChildOperation.start
            else CommandDisposition.uncertain
        )
        return CommandOutcome(command.command_id, command.child, disposition, {})

    factory = MacRemoteChildFactory(
        lambda start, sink: ChildReference("incarnation", "child", start.conversation_id),
        native,
        lambda start: CanonicalSkillSnapshot(
            tuple(
                CanonicalSkillSource(name, f"---\nname: {name}\ndescription: test\n---\nTest\n")
                for name in ("panels", "panels-worker", "panels-worker-coding")
            ),
            ("panels", "panels-worker", "panels-worker-coding"),
        ),
        db_path=db_path,
    )

    def core() -> SqliteProcessConversationSystem:
        return SqliteProcessConversationSystem(
            store=ConversationStore(db_path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(db_path),
        )

    async def run() -> None:
        system = core()
        await system.start_conversation(
            ConversationStartRequest("c_unknown", "scripted", workspace_folder=tmp_path)
        )
        result = await system.send(
            "c_unknown",
            text_message_content("Original"),
            sender_label="Test",
            sender_message_id="stable_sender",
        )
        assert isinstance(result, PromptDeliveryUncertain)
        await system.shutdown()
        restored = core()
        replay = await restored.send(
            "c_unknown",
            text_message_content("Original"),
            sender_label="Test",
            sender_message_id="stable_sender",
        )
        assert isinstance(replay, PromptDeliveryUncertain)
        assert not await restored.is_running("c_unknown")
        assert restored.workspace_activity() == "busy"
        assert not await restored.run_with_workspace_admission(
            lambda: pytest.fail("uncertain Mac delivery cannot admit an idle update"),
            require_idle=True,
        )
        assert [command.operation for command in commands] == [
            ChildOperation.start,
            ChildOperation.write_prompt,
        ]
        kinds = [
            row[0]
            for row in tmp_db.execute(
                "SELECT kind FROM conversation_events WHERE conversation_id='c_unknown'"
            )
        ]
        assert kinds == ["prompt_delivery_uncertain"]
        assert await restored.run_with_workspace_admission(lambda: None, require_idle=False)
        with pytest.raises(RuntimeError, match="Workspace admission is closed"):
            await restored.send("c_unknown", text_message_content("Blocked"), sender_label="Test")
        await restored.shutdown()

    asyncio.run(run())


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
        child = MacRemoteChild(
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

    asyncio.run(run())


def test_oversized_full_start_frame_refuses_before_dispatch(tmp_path: Path) -> None:
    import pytest

    from planner.conversation.backends.contracts import BackendSpawnFailed
    from planner.conversation.contracts import ConversationAccess, ResolvedConversationStart
    from planner.executor.configuration import MAX_ENVELOPE_BYTES

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
    commands: list[ChildCommand] = []

    async def send(command: ChildCommand) -> CommandOutcome:
        commands.append(command)
        return CommandOutcome(command.command_id, command.child, CommandDisposition.succeeded, {})

    async def run() -> None:
        child = MacRemoteChild(
            ChildReference("inc", "child", "c_large"), send, lambda start: snapshot
        )
        resolved = ResolvedConversationStart(
            "c_large",
            ConversationBackendKey.codex,
            "scripted",
            None,
            None,
            tmp_path,
            ConversationAccess.full,
        )
        with pytest.raises(BackendSpawnFailed, match="frame exceeds envelope limit"):
            await child.start(resolved, vendor_session_cursor=None)
        assert commands == []

    asyncio.run(run())
