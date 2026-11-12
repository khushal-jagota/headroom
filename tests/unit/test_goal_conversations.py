"""Chief conversations retain their home across workspaces and resets."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection
from typing import cast
from unittest.mock import Mock

from tests.unit.test_conversation_system import _FakeBackendChild, _Harness

from planner.conversation.backends.contracts import BackendEventSink, RetainedBackendChildFactory
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.authctx import RequestContext
from planner.core.clock import TestClock as Clock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
)
from planner.executor.routing import LocaleBackendChildFactory
from planner.message_delivery.service import send_message
from planner.runtime.conversation_start import reset_agent_conversation
from planner.runtime.workspaces import register_workspace_root


class ChiefWorkspaceHarness(_Harness):
    def __init__(self, path: Path) -> None:
        self.starts: list[ResolvedConversationStart] = []
        remote = Mock(side_effect=AssertionError("Chief children must stay on the VPS"))
        remote.retained_runs.return_value = ()
        remote.recover.return_value = None
        super().__init__(
            path,
            factory_wrapper=lambda local: LocaleBackendChildFactory(
                str(path), local, cast(RetainedBackendChildFactory, remote)
            ),
        )

    def _make_child(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> _FakeBackendChild:
        self.starts.append(resolved_start)
        return super()._make_child(
            resolved_start=resolved_start, event_sink=event_sink, message_files=message_files
        )


def test_chief_keeps_its_home_folder_across_workspaces_and_reset(
    tmp_db: Connection, tmp_path: Path, fake_clock: Clock
) -> None:
    path = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    roots = [tmp_path / name for name in ("workspace-a", "workspace-b")]
    for root in roots:
        root.mkdir()
    harness = ChiefWorkspaceHarness(path)
    chief_home = path.parent / "files" / "chief"

    async def speak(text: str) -> str:
        result = await send_message(
            harness.system,
            tmp_db,
            fake_clock,
            RequestContext(OWNER_PRINCIPAL),
            CHIEF_PRINCIPAL,
            text,
        )
        assert result.conversation_id is not None
        await harness.settle()
        await harness.complete_turn(result.conversation_id)
        return result.conversation_id

    async def exercise() -> None:
        try:
            with register_workspace_root(str(path), roots[0]):
                original_id = await speak("Begin")
            with register_workspace_root(str(path), roots[1]):
                assert await speak("Continue") == original_id
                await reset_agent_conversation(harness.system, tmp_db, "chief_of_staff")
                assert await speak("Start after reset") != original_id
            assert [start.workspace_folder for start in harness.starts] == [chief_home, chief_home]
            old = await harness.store.read_conversation(original_id)
            assert old is not None and await harness.events(original_id)
        finally:
            await harness.system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), timeout=15))
