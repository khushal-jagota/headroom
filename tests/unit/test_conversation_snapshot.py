"""What Panels can honestly say about the agent CLIs on a machine.

Every probe here runs against a scripted machine rather than the real one: no subprocess,
no network, no PATH. What is under test is the reading — how a version line, a login line,
and the place a binary lives turn into what a card says, and what an update is allowed to
claim afterwards.

The one test that does touch the real machine is off unless it is asked for by name.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from planner.conversation.backends.claude_model_catalog import (
    ClaudeModel,
    ClaudeModelCatalog,
    ClaudeModelCatalogUnavailable,
)
from planner.conversation.backends.codex_app_server.model_catalog import (
    CodexModel,
    CodexModelCatalog,
    CodexModelCatalogUnavailable,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import (
    BackendInstallMethod,
    CommandOutcome,
    SubprocessBackendProbeEnvironment,
    probe_backend,
)

CLAUDE_PATH = "/Users/someone/.local/bin/claude"
CLAUDE_REAL_PATH = "/Users/someone/.local/lib/node_modules/@anthropic-ai/claude-code/bin/claude"
CODEX_PATH = "/Users/someone/.local/bin/codex"
CODEX_REAL_PATH = "/Users/someone/.codex/packages/standalone/releases/0.145.0-arm64/bin/codex"
HERMES_PATH = "/Users/someone/.local/bin/hermes"
USER_LOCAL_NPM_PREFIX = "/Users/someone/.local"


@dataclass
class _FakeMachine:
    """The machine, answered from a script and never actually touched."""

    executables: dict[str, str] = field(default_factory=dict)
    configured_executables: dict[str, str] = field(default_factory=dict)
    real_paths: dict[str, str] = field(default_factory=dict)
    managed_npm_prefix: str = USER_LOCAL_NPM_PREFIX
    writable_prefixes: set[str] = field(
        default_factory=lambda: {USER_LOCAL_NPM_PREFIX}
    )
    outcomes: dict[tuple[str, ...], CommandOutcome] = field(default_factory=dict)
    registry_versions: dict[str, str] = field(default_factory=dict)
    after_run: dict[tuple[str, ...], Callable[[], None]] = field(default_factory=dict)
    run_commands: list[tuple[str, ...]] = field(default_factory=list)
    registry_lookups: list[str] = field(default_factory=list)
    # What any command this script does not name answers with. The hermes catalog probe is
    # run as a path this machine resolves for itself, so a test says what it answers
    # without having to predict how it was spelled.
    answers_any_other_command: CommandOutcome | None = None
    # Commands that wait to be let go, so a test can hold one in flight and see what
    # another caller does while it is.
    slow_commands: set[tuple[str, ...]] = field(default_factory=set)
    let_slow_commands_finish: asyncio.Event | None = None
    commands_in_flight: int = 0
    most_commands_at_once: int = 0

    def executable_path(self, executable_name: str) -> str | None:
        return self.executables.get(executable_name)

    def configured_executable_path(self, executable_name: str) -> str | None:
        return self.configured_executables.get(executable_name) or (
            self.executables.get(executable_name) if executable_name == "hermes" else None
        )

    def real_path(self, path: str) -> str:
        return self.real_paths.get(path, path)

    def user_local_npm_prefix(self) -> str:
        return self.managed_npm_prefix

    def prefix_is_owned_and_writable(self, prefix: str) -> bool:
        return prefix in self.writable_prefixes

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        del timeout_seconds, environment_overrides
        command = tuple(argv)
        self.run_commands.append(command)
        if command in self.slow_commands and self.let_slow_commands_finish is not None:
            self.commands_in_flight += 1
            self.most_commands_at_once = max(
                self.most_commands_at_once, self.commands_in_flight
            )
            await self.let_slow_commands_finish.wait()
            self.commands_in_flight -= 1
        outcome = self.outcomes.get(
            command,
            self.answers_any_other_command
            or CommandOutcome(
                exit_code=-1, standard_output="", standard_error="no such command"
            ),
        )
        happens_next = self.after_run.get(command)
        if happens_next is not None:
            happens_next()
        return outcome

    async def latest_released_version(
        self, package_name: str, *, timeout_seconds: float
    ) -> str | None:
        del timeout_seconds
        self.registry_lookups.append(package_name)
        return self.registry_versions.get(package_name)


def _codex_that_answers(
    *models: CodexModel,
) -> Callable[[str], Awaitable[CodexModelCatalog]]:
    async def probe(codex_executable: str) -> CodexModelCatalog:
        del codex_executable
        efforts: list[str] = []
        for model in models:
            for effort in model.reasoning_effort_options or ():
                if effort not in efforts:
                    efforts.append(effort)
        default = next((model for model in models if model.is_default), None)
        return CodexModelCatalog(
            models=models,
            reasoning_effort_options=tuple(efforts),
            default_model_id=None if default is None else default.model_id,
            default_reasoning_effort=(
                None if default is None else default.default_reasoning_effort
            ),
        )

    return probe


async def _codex_that_says_nothing(codex_executable: str) -> CodexModelCatalog:
    del codex_executable
    raise CodexModelCatalogUnavailable("codex app-server would not start")


_CLAUDE_HANDSHAKE_MODELS = (
    ClaudeModel(
        model_id="opus[1m]",
        display_name="Opus 5 (1M)",
        resolved_model_id="claude-opus-5[1m]",
        reasoning_effort_options=("low", "medium", "high", "xhigh", "max"),
    ),
    ClaudeModel(
        model_id="sonnet",
        display_name="Sonnet 5",
        resolved_model_id="claude-sonnet-5",
        reasoning_effort_options=("low", "medium", "high", "xhigh", "max"),
    ),
    ClaudeModel(
        model_id="haiku",
        display_name="Haiku 4.5",
        resolved_model_id="claude-haiku-4-5-20251001",
        reasoning_effort_options=(),
    ),
)


def _claude_that_answers(
    *models: ClaudeModel, default_model_id: str | None = None
) -> Callable[[str], Awaitable[ClaudeModelCatalog]]:
    answered = models or _CLAUDE_HANDSHAKE_MODELS

    async def probe(claude_executable: str) -> ClaudeModelCatalog:
        del claude_executable
        efforts: list[str] = []
        for model in answered:
            for effort in model.reasoning_effort_options or ():
                if effort not in efforts:
                    efforts.append(effort)
        return ClaudeModelCatalog(
            models=tuple(answered),
            reasoning_effort_options=tuple(efforts),
            default_model_id=default_model_id,
        )

    return probe


async def _claude_that_says_nothing(claude_executable: str) -> ClaudeModelCatalog:
    del claude_executable
    raise ClaudeModelCatalogUnavailable("claude would not start")


def _installed_codex() -> _FakeMachine:
    machine = _FakeMachine(
        executables={"codex": CODEX_PATH}, real_paths={CODEX_PATH: CODEX_REAL_PATH}
    )
    machine.outcomes[(CODEX_PATH, "--version")] = CommandOutcome(
        exit_code=0, standard_output="codex-cli 0.145.0\n", standard_error=""
    )
    machine.outcomes[(CODEX_PATH, "login", "status")] = CommandOutcome(
        exit_code=0, standard_output="Logged in using ChatGPT\n", standard_error=""
    )
    return machine


def _run(exercise: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(asyncio.wait_for(exercise(), 20.0))


def _installed_claude(
    *,
    version_output: str = "2.1.219 (Claude Code)\n",
    identity: CommandOutcome | None = None,
) -> _FakeMachine:
    machine = _FakeMachine(
        executables={"claude": CLAUDE_PATH},
        real_paths={CLAUDE_PATH: CLAUDE_REAL_PATH},
    )
    machine.outcomes[(CLAUDE_PATH, "--version")] = CommandOutcome(
        exit_code=0, standard_output=version_output, standard_error=""
    )
    machine.outcomes[(CLAUDE_PATH, "auth", "status", "--json")] = identity or CommandOutcome(
        exit_code=0,
        standard_output=json.dumps(
            {
                "loggedIn": True,
                "email": "owner@example.com",
                "authMethod": "claude.ai",
                "subscriptionType": "max",
            }
        ),
        standard_error="",
    )
    return machine


# --- reading a version line ----------------------------------------------------------------




# --- reading where a binary lives ------------------------------------------------------------


# --- what a card says --------------------------------------------------------------------


















# --- what a backend runs when nobody picks --------------------------------------------------






# --- the update advisory ------------------------------------------------------------------


def test_codex_user_local_npm_install_targets_the_same_resolved_prefix() -> None:
    async def exercise() -> None:
        machine = _FakeMachine(
            executables={"codex": CODEX_PATH},
            real_paths={
                CODEX_PATH: "/Users/someone/.local/lib/node_modules/@openai/codex/bin/codex.js"
            },
        )
        machine.outcomes[(CODEX_PATH, "--version")] = CommandOutcome(
            exit_code=0, standard_output="codex-cli 0.145.0\n", standard_error=""
        )
        machine.outcomes[(CODEX_PATH, "login", "status")] = CommandOutcome(
            exit_code=0, standard_output="Logged in using ChatGPT\n", standard_error=""
        )
        machine.registry_versions["@openai/codex"] = "0.146.0"

        card = await probe_backend(
            ConversationBackendKey.codex,
            machine,
            codex_model_catalog_probe=_codex_that_answers(),
        )

        assert card.update_advisory is not None
        assert card.update_advisory.install_method is BackendInstallMethod.npm_global
        assert card.update_advisory.update_command == (
            "npm",
            "install",
            "--global",
            "--prefix",
            USER_LOCAL_NPM_PREFIX,
            "@openai/codex@latest",
        )

    _run(exercise)








# --- running an update --------------------------------------------------------------------


def _claude_update_command() -> tuple[str, ...]:
    return (
        "npm",
        "install",
        "--global",
        "--prefix",
        USER_LOCAL_NPM_PREFIX,
        "@anthropic-ai/claude-code@latest",
    )












# --- what a command that runs out of time leaves behind ------------------------------------


def _still_alive(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_a_command_that_runs_out_of_time_takes_what_it_started_with_it(
    tmp_path: Path,
) -> None:
    """An installer is a script that runs other programs.

    Killing only the script would leave those still writing to this machine after Panels
    had already said the update failed, which is the one thing a failed update must not
    mean. So the command gets its own process group and the group is what is killed.
    """

    async def exercise() -> None:
        child_process_id_file = tmp_path / "the-child.pid"
        outcome = await SubprocessBackendProbeEnvironment().run(
            (
                "/bin/sh",
                "-c",
                f"sleep 60 & echo $! > {child_process_id_file}; wait",
            ),
            timeout_seconds=1.0,
        )

        assert outcome.exit_code == -1
        assert "did not answer within" in outcome.standard_error

        child_process_id = int(child_process_id_file.read_text().strip())
        for _ in range(100):
            if not _still_alive(child_process_id):
                break
            await asyncio.sleep(0.05)
        assert not _still_alive(child_process_id), (
            f"the command's own child ({child_process_id}) outlived it"
        )

    _run(exercise)


# --- keeping the answers ---------------------------------------------------------------------


def test_destination_snapshot_retains_complete_choices_and_rejects_late_owner() -> None:
    from dataclasses import replace

    from tests.unit.test_current_model_resolution import context, observation, row

    from planner.conversation.model_resolution.contracts import CatalogObservation
    from planner.conversation.snapshot import BackendSnapshotScope, BackendSnapshotService

    async def exercise() -> None:
        service = BackendSnapshotService()
        launch = context()
        scope = BackendSnapshotScope(
            launch.backend_key, launch.destination.value, launch.workspace,
            launch.executor_identity, launch.account_generation,
        )
        current = True
        calls = 0
        entered, release = asyncio.Event(), asyncio.Event()
        result = observation((row("gpt-6.1-terra"),), launch=launch)

        async def is_current() -> bool:
            return current

        async def observe() -> CatalogObservation:
            nonlocal calls
            calls += 1
            return result

        async def slow() -> CatalogObservation:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return result

        complete = await service.destination_snapshot(scope, observe=observe, is_current=is_current)
        assert complete.available_models[0].model_id == "gpt-6.1-terra"
        result = replace(result, rows=(), complete=False)
        pending = asyncio.create_task(service.destination_snapshot(
            scope, observe=slow, is_current=is_current, refresh=True,
        ))
        await entered.wait()
        assert await service.destination_snapshot(
            scope, observe=observe, is_current=is_current,
        ) is complete
        coalesced = asyncio.create_task(service.destination_snapshot(
            scope, observe=slow, is_current=is_current, refresh=True,
        ))
        await asyncio.sleep(0)
        release.set()
        assert await pending is complete
        assert await coalesced is complete
        assert calls == 2
        other_scope = replace(
            scope, workspace=replace(scope.workspace, workspace_folder=Path("/other"))
        )
        assert service.retained_destination(other_scope) is None
        # A complete empty list is a successful replacement, distinct from a partial list.
        result = replace(result, complete=True)
        empty = await service.destination_snapshot(
            scope, observe=observe, is_current=is_current, refresh=True,
        )
        assert empty.models_complete and empty.available_models == ()
        entered.clear()
        release.clear()
        result = observation((row("late-account-model"),), launch=launch)
        pending = asyncio.create_task(service.destination_snapshot(
            scope, observe=slow, is_current=is_current, refresh=True,
        ))
        await entered.wait()
        current = False
        release.set()
        assert (await pending).available_models == ()
        assert service.retained_destination(scope) is empty
        # Sign-in invalidation must also reject an already pending old response.
        current = True
        entered.clear()
        release.clear()
        pending = asyncio.create_task(service.destination_snapshot(
            scope, observe=slow, is_current=is_current, refresh=True,
        ))
        await entered.wait()
        service.clear_destination_snapshots(scope.backend_key)
        release.set()
        assert (await pending).available_models == ()
        assert service.retained_destination(scope) is None

    asyncio.run(exercise())
