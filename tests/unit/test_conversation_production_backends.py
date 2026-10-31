"""Where Panels looks for each agent on the machine it is running on.

Nothing here starts an agent. What is under test is the answer this composition gives —
which binary, which home, which source tree — because that answer is the whole of what the
module decides, and getting it wrong is a conversation that talks to the wrong copy of an
agent or to none at all.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from planner.conversation.production_backends import production_backend_launches

# Where the server under test is answering. Any local origin does; what the tests care
# about is that the same one comes back out of every backend.
SERVER_URL = "http://127.0.0.1:8811"


def _machine(**found: str) -> Callable[[str], str | None]:
    """A machine that has exactly the binaries a test names, and nothing else."""

    def executable_path(executable_name: str) -> str | None:
        return found.get(executable_name)

    return executable_path




def test_every_agent_is_told_where_panels_is_answering() -> None:
    """The `panels` CLI in an agent's shell talks to Panels over HTTP.

    It has no other way to learn the port, and getting it wrong is an agent that cannot
    read its own Ticket. All three carry it, because a Ticket's worker can be any of them.
    """
    launches = production_backend_launches(
        panels_server_url=SERVER_URL,
        executable_path=_machine(codex="/usr/local/bin/codex", claude="/usr/local/bin/claude"),
    )

    assert dict(launches.hermes.environment_overrides)["PLAN_SERVER_URL"] == SERVER_URL
    assert dict(launches.codex.environment_overrides)["PLAN_SERVER_URL"] == SERVER_URL
    assert dict(launches.claude.environment_overrides)["PLAN_SERVER_URL"] == SERVER_URL


def test_local_providers_refuse_unresolved_executor_workspace(tmp_path: Path) -> None:
    import asyncio
    from typing import cast

    import pytest

    from planner.conversation.backends.contracts import BackendEventSink, BackendSpawnFailed
    from planner.conversation.contracts import (
        ConversationAccess,
        ResolvedConversationStart,
        WorkspacePathPolicy,
    )
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.conversation.production_backends import production_backend_child_factories

    async def run() -> None:
        factories = production_backend_child_factories(panels_server_url=SERVER_URL)
        for key, factory in factories.items():
            resolved = ResolvedConversationStart(
                "unresolved-default", key, "test", None, None, Path("/"),
                ConversationAccess.full, workspace_path_policy=WorkspacePathPolicy.executor_default,
            )
            child = factory(resolved_start=resolved, event_sink=cast(BackendEventSink, object()),
                            message_files=ConversationMessageFiles(tmp_path / "files"))
            with pytest.raises(BackendSpawnFailed, match="requires native resolution"):
                await child.start(resolved, vendor_session_cursor=None)
    asyncio.run(run())
