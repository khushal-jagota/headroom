"""Where Panels looks for each agent on the machine it is running on.

Nothing here starts an agent. What is under test is the answer this composition gives —
which binary, which home, which source tree — because that answer is the whole of what the
module decides, and getting it wrong is a conversation that talks to the wrong copy of an
agent or to none at all.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from planner.conversation.production_backends import production_backend_launches

# Where the server under test is answering. Any local origin does; what the tests care
# about is that the same one comes back out of every backend.
SERVER_URL = "http://127.0.0.1:8811"


def _machine(**found: str) -> Callable[[str], str | None]:
    """A machine that has exactly the binaries a test names, and nothing else."""

    def executable_path(executable_name: str) -> str | None:
        return found.get(executable_name)

    return executable_path




def test_every_agent_is_told_where_panels_is_answering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `panels` CLI in an agent's shell talks to Panels over HTTP.

    It has no other way to learn the port, and getting it wrong is an agent that cannot
    read its own Ticket. All three carry it, because a Ticket's worker can be any of them.
    """
    monkeypatch.delenv("PLAN_DB_PATH", raising=False)
    monkeypatch.delenv("PLAN_WORKER_PROCESS_ROOT", raising=False)
    configured_root = tmp_path / "configured-home" / "worker-processes"
    launches = production_backend_launches(
        panels_server_url=SERVER_URL, worker_process_root=configured_root,
        executable_path=_machine(codex="/usr/local/bin/codex", claude="/usr/local/bin/claude"),
    )

    assert dict(launches.hermes.environment_overrides)["PLAN_SERVER_URL"] == SERVER_URL
    assert dict(launches.codex.environment_overrides)["PLAN_SERVER_URL"] == SERVER_URL
    assert dict(launches.claude.environment_overrides)["PLAN_SERVER_URL"] == SERVER_URL
    for launch in (launches.hermes, launches.codex, launches.claude):
        assert dict(launch.environment_overrides)["PLAN_WORKER_PROCESS_ROOT"] == str(
            configured_root
        )


def test_release_changes_do_not_replace_external_cli_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = "/owner/.local/bin/claude"
    resolver = _machine(codex="/owner/.local/bin/codex", claude=external)
    monkeypatch.delenv("PLAN_CLAUDE_EXECUTABLE", raising=False)
    for root in ("/release/old", "/release/new"):
        monkeypatch.setenv("PLAN_APP_ROOT", root)
        launches = production_backend_launches(
            panels_server_url=SERVER_URL, executable_path=resolver
        )
        assert launches.claude.claude_executable == Path(external)
        assert launches.codex.argv[0] == "/owner/.local/bin/codex"
        # An old permanent Mac supervisor supplies this generated override on handoff.
        monkeypatch.setenv(
            "PLAN_CLAUDE_EXECUTABLE",
            root + "/agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
        )
        assert production_backend_launches(
            panels_server_url=SERVER_URL, executable_path=resolver
        ).claude.claude_executable == Path(external)
        monkeypatch.delenv("PLAN_CLAUDE_EXECUTABLE")
    monkeypatch.setenv("PLAN_CLAUDE_EXECUTABLE", "/operator/claude")
    assert production_backend_launches(
        panels_server_url=SERVER_URL, executable_path=resolver
    ).claude.claude_executable == Path("/operator/claude")
    monkeypatch.delenv("PLAN_CLAUDE_EXECUTABLE")
    assert production_backend_launches(
        panels_server_url=SERVER_URL, executable_path=_machine()
    ).claude.claude_executable == Path("claude")


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
