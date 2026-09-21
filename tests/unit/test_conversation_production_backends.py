"""Where Panels looks for each agent on the machine it is running on.

Nothing here starts an agent. What is under test is the answer this composition gives —
which binary, which home, which source tree — because that answer is the whole of what the
module decides, and getting it wrong is a conversation that talks to the wrong copy of an
agent or to none at all.
"""

from __future__ import annotations

from collections.abc import Callable

from planner.conversation.production_backends import production_backend_launches

# Where the server under test is answering. Any local origin does; what the tests care
# about is that the same one comes back out of every backend.
SERVER_URL = "http://127.0.0.1:8811"


def _machine(**found: str) -> Callable[[str], str | None]:
    """A machine that has exactly the binaries a test names, and nothing else."""

    def executable_path(executable_name: str) -> str | None:
        return found.get(executable_name)

    return executable_path


def test_claude_runs_on_the_panels_owned_tested_cli() -> None:
    launches = production_backend_launches(
        panels_server_url=SERVER_URL,
        executable_path=_machine(claude="/Users/someone/.local/bin/claude")
    )

    assert launches.claude.claude_executable is not None
    assert launches.claude.claude_executable.parts[-6:] == (
        "agent_backends",
        "node_modules",
        "@anthropic-ai",
        "claude-code",
        "bin",
        "claude.exe",
    )


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
