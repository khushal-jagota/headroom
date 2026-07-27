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

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.production_backends import (
    production_backend_child_factories,
    production_backend_launches,
)

# Where the server under test is answering. Any local origin does; what the tests care
# about is that the same one comes back out of every backend.
SERVER_URL = "http://127.0.0.1:8811"


def _machine(**found: str) -> Callable[[str], str | None]:
    """A machine that has exactly the binaries a test names, and nothing else."""

    def executable_path(executable_name: str) -> str | None:
        return found.get(executable_name)

    return executable_path


def test_every_backend_has_a_factory_and_making_one_starts_nothing() -> None:
    factories = production_backend_child_factories(
        panels_server_url=SERVER_URL,
        executable_path=_machine(codex="/usr/local/bin/codex", claude="/usr/local/bin/claude"),
    )

    assert set(factories) == set(ConversationBackendKey)


def test_codex_is_taken_from_the_path_and_run_as_an_app_server() -> None:
    launches = production_backend_launches(
        panels_server_url=SERVER_URL,
        executable_path=_machine(codex="/opt/homebrew/bin/codex")
    )

    assert launches.codex.argv == ("/opt/homebrew/bin/codex", "app-server")


def test_a_codex_that_is_nowhere_is_still_composed_under_its_own_name() -> None:
    """Nothing refuses to start: the failure belongs to the first send, and names codex."""
    launches = production_backend_launches(
        panels_server_url=SERVER_URL,
        executable_path=_machine())

    assert launches.codex.argv == ("codex", "app-server")


def test_claude_runs_on_the_cli_this_machine_has() -> None:
    launches = production_backend_launches(
        panels_server_url=SERVER_URL,
        executable_path=_machine(claude="/Users/someone/.local/bin/claude")
    )

    assert launches.claude.claude_executable == Path("/Users/someone/.local/bin/claude")


def test_a_claude_that_is_nowhere_leaves_the_sdk_its_own_copy() -> None:
    launches = production_backend_launches(
        panels_server_url=SERVER_URL,
        executable_path=_machine())

    assert launches.claude.claude_executable is None


def test_hermes_is_derived_from_its_interpreter_rather_than_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hermes is a checkout, so the interpreter is what is configured and the rest follows."""
    monkeypatch.setenv(
        "PLAN_HERMES_PYTHON", "/tmp/hermes-install/hermes-agent/venv/bin/python"
    )
    monkeypatch.setenv("PLAN_HERMES_HOME", "/tmp/hermes-home")

    launches = production_backend_launches(
        panels_server_url=SERVER_URL,
        executable_path=_machine())

    assert launches.hermes.argv == ("/tmp/hermes-install/hermes-agent/venv/bin/hermes", "acp")
    environment = dict(launches.hermes.environment_overrides)
    assert environment["HERMES_HOME"] == "/tmp/hermes-home"
    # Two levels up from the interpreter: the source tree the agent imports itself from.
    assert environment["HERMES_PYTHON_SRC_ROOT"] == "/tmp/hermes-install/hermes-agent"


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
