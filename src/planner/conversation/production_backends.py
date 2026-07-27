"""The three real backends, composed for the machine Panels is running on.

This is the one place that says where each agent actually lives. The adapters know how to
speak to their vendor and nothing about this machine; the core knows the rules and nothing
about either. What is left — which binary, which home, which source tree — is here.

**A backend that is not installed still composes.** Nothing here checks that a binary
exists, on purpose: a missing binary is discovered when a conversation first tries to spawn
one, and the adapter turns it into the refusal the contract already has for it
(``backend_did_not_start``). Checking up front would mean a Panels that will not start
because of an agent nobody was going to use, and a worse message than the one the operating
system gives for the path it actually tried.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from planner.conversation.backends.claude_agent_sdk import (
    ClaudeAgentSdkBackendChildFactory,
    ClaudeAgentSdkChildLaunch,
)
from planner.conversation.backends.codex_app_server.adapter import (
    CodexAppServerBackendChildFactory,
    CodexChildLaunch,
    codex_app_server_child_launch,
)
from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.backends.hermes_acp import (
    AcpChildLaunch,
    HermesAcpBackendChildFactory,
    hermes_acp_child_launch,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.environments.hermes_home import (
    hermes_src_root,
    resolve_hermes_python,
    resolve_planner_home,
)

# How a binary is found by name. Named as a parameter so a test can compose the real
# factories against a machine it describes rather than the one it is running on.
type ExecutablePathResolver = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class ProductionBackendLaunches:
    """Where each agent lives on this machine, and what it takes to run it.

    This is the whole of what this module decides. The factories below are wrappers around
    it, so the answer can be read — and checked — without starting anything.
    """

    hermes: AcpChildLaunch
    codex: CodexChildLaunch
    claude: ClaudeAgentSdkChildLaunch


def production_backend_launches(
    *,
    panels_server_url: str,
    executable_path: ExecutablePathResolver = shutil.which,
) -> ProductionBackendLaunches:
    """This machine's three agents, resolved.

    ``panels_server_url`` is where this server is answering, and every agent gets it: the
    ``panels`` CLI in an agent's shell talks to Panels over HTTP and has no other way to
    learn the port. It is passed in rather than stored with a conversation because it is a
    fact about the server that is running now — a conversation resumed after a restart must
    reach the server that resumed it, not the one it was started under.
    """
    return ProductionBackendLaunches(
        hermes=_hermes_launch(panels_server_url),
        codex=codex_app_server_child_launch(
            codex_executable=_on_path(executable_path, "codex"),
            panels_server_url=panels_server_url,
        ),
        claude=_claude_launch(executable_path, panels_server_url),
    )


def production_backend_child_factories(
    *,
    panels_server_url: str,
    executable_path: ExecutablePathResolver = shutil.which,
) -> Mapping[ConversationBackendKey, BackendChildFactory]:
    """One child factory per backend, pointed at this machine's copy of each agent."""
    launches = production_backend_launches(
        panels_server_url=panels_server_url, executable_path=executable_path
    )
    return {
        ConversationBackendKey.hermes: HermesAcpBackendChildFactory(launches.hermes),
        ConversationBackendKey.codex: CodexAppServerBackendChildFactory(launches.codex),
        ConversationBackendKey.claude: ClaudeAgentSdkBackendChildFactory(launches.claude),
    }


def _hermes_launch(panels_server_url: str) -> AcpChildLaunch:
    """Hermes as today's layer resolves it: from its interpreter, not from PATH.

    Hermes is a checkout with a virtualenv rather than a packaged binary, so the
    interpreter is the thing that is configured and everything else is derived from it —
    the executable beside it, and the source tree two levels up that the agent imports
    itself from. The home is the one Panels already uses for hermes.
    """
    hermes_python = resolve_hermes_python()
    return hermes_acp_child_launch(
        hermes_executable=hermes_python.with_name("hermes"),
        hermes_home=resolve_planner_home(),
        hermes_python_source_root=hermes_src_root(hermes_python),
        panels_server_url=panels_server_url,
    )


def _claude_launch(
    executable_path: ExecutablePathResolver, panels_server_url: str
) -> ClaudeAgentSdkChildLaunch:
    """Claude as the CLI this machine has, when it has one.

    Naming the installed CLI is what makes a conversation run on the agent the owner
    logged in — the SDK would otherwise use the copy it ships with, which knows nothing
    about this machine's login. When there is no CLI to name, that shipped copy is the
    honest fallback and the backend card already says the CLI is missing.
    """
    found = executable_path("claude")
    return ClaudeAgentSdkChildLaunch(
        claude_executable=None if found is None else Path(found),
        environment_overrides=(("PLAN_SERVER_URL", panels_server_url),),
    )


def _on_path(executable_path: ExecutablePathResolver, executable_name: str) -> Path:
    """Where this executable is, or its bare name when it is nowhere.

    The bare name is not a guess that it will work — it is what makes the failure say the
    name that was looked for, rather than a path this machine never had.
    """
    found = executable_path(executable_name)
    return Path(found) if found is not None else Path(executable_name)
