"""An unproved native prompt cannot turn into refusal or an automatic retry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryUncertain,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.executor.contracts import (
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
)
from planner.executor.remote import RemoteBackendChildFactory


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

    factory = RemoteBackendChildFactory(
        lambda start, sink: ChildReference("incarnation", "child", start.conversation_id), native
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
        assert system._conversations["c_unknown"].child_is_quarantined
        assert system._conversations["c_unknown"].reserved_turn is not None
        restored = core()
        replay = await restored.send(
            "c_unknown",
            text_message_content("Original"),
            sender_label="Test",
            sender_message_id="stable_sender",
        )
        assert isinstance(replay, PromptDeliveryUncertain)
        assert restored._conversations["c_unknown"].child_is_quarantined
        assert restored._conversations["c_unknown"].reserved_turn is not None
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
        await system.shutdown()
        await restored.shutdown()

    asyncio.run(run())
