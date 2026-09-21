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
    BackendIdentityStatus,
    BackendInstallMethod,
    BackendSnapshotService,
    BackendUpdateOutcome,
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
            for effort in model.reasoning_effort_options:
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
            for effort in model.reasoning_effort_options:
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




def test_claude_reports_its_account_its_models_and_its_effort_levels() -> None:
    async def exercise() -> None:
        machine = _installed_claude()

        card = await probe_backend(
            ConversationBackendKey.claude,
            machine,
            claude_model_catalog_probe=_claude_that_answers(),
        )

        assert card.installed is True
        assert card.version == "2.1.219"
        assert card.identity is not None
        assert card.identity.status is BackendIdentityStatus.authenticated
        assert card.identity.account_label == "owner@example.com"
        assert card.identity.detail == "max plan via claude.ai"
        assert card.identity.login_command == "claude auth login"
        # The catalog is the CLI's own handshake: the value --model takes, the versioned
        # name of what it reaches, and the resolution spelled out as the detail line.
        assert [model.model_id for model in card.available_models] == [
            "opus[1m]",
            "sonnet",
            "haiku",
        ]
        assert [model.display_name for model in card.available_models] == [
            "Opus 5 (1M)",
            "Sonnet 5",
            "Haiku 4.5",
        ]
        assert card.available_models[0].detail == "opus[1m] → claude-opus-5[1m]"
        assert card.reasoning_effort_options == ("low", "medium", "high", "xhigh", "max")
        assert card.diagnoses == ()

    _run(exercise)


def test_each_model_keeps_the_efforts_it_said_it_takes() -> None:
    """The backend-wide list is the union, so on its own it offers a model values it
    refuses. What each model said about itself survives the probe.

    Claude's handshake here is the honest case: two models take five efforts and one
    says nothing about efforts at all. The union is the same five either way, so only
    the per-model answer can tell the third one apart.
    """

    async def exercise() -> None:
        card = await probe_backend(
            ConversationBackendKey.claude,
            _installed_claude(),
            claude_model_catalog_probe=_claude_that_answers(),
        )

        efforts = {
            model.model_id: model.reasoning_effort_options
            for model in card.available_models
        }
        assert efforts["opus[1m]"] == ("low", "medium", "high", "xhigh", "max")
        assert efforts["sonnet"] == ("low", "medium", "high", "xhigh", "max")
        # Said nothing, so it carries nothing and the backend's own list applies to it.
        assert efforts["haiku"] == ()

    _run(exercise)


def test_a_codex_model_keeps_the_efforts_it_said_it_takes() -> None:
    """Two codex models that take different efforts stay different after the probe.

    The union offers all four, so a picker reading only the union would offer xhigh on
    a model that takes low, medium and high.
    """

    async def exercise() -> None:
        card = await probe_backend(
            ConversationBackendKey.codex,
            _installed_codex(),
            codex_model_catalog_probe=_codex_that_answers(
                CodexModel(
                    model_id="gpt-5.6-sol",
                    display_name="GPT-5.6-Sol",
                    reasoning_effort_options=("low", "medium", "high"),
                    default_reasoning_effort="low",
                    is_default=True,
                ),
                CodexModel(
                    model_id="gpt-5.5",
                    display_name="GPT-5.5",
                    reasoning_effort_options=("medium", "xhigh"),
                    default_reasoning_effort="medium",
                    is_default=False,
                ),
            ),
        )

        efforts = {
            model.model_id: model.reasoning_effort_options
            for model in card.available_models
        }
        assert efforts["gpt-5.6-sol"] == ("low", "medium", "high")
        assert efforts["gpt-5.5"] == ("medium", "xhigh")
        # And the backend-wide list is still the union of them, for a model that says
        # nothing and for a picker with no model chosen yet.
        assert card.reasoning_effort_options == ("low", "medium", "high", "xhigh")

    _run(exercise)


def test_a_signed_out_cli_is_told_which_command_to_run() -> None:
    async def exercise() -> None:
        machine = _installed_claude(
            identity=CommandOutcome(
                exit_code=0, standard_output=json.dumps({"loggedIn": False}), standard_error=""
            )
        )

        card = await probe_backend(
            ConversationBackendKey.claude,
            machine,
            claude_model_catalog_probe=_claude_that_answers(),
        )

        assert card.identity is not None
        assert card.identity.status is BackendIdentityStatus.unauthenticated
        assert card.identity.account_label is None
        assert card.diagnoses == (
            "`claude` is not signed in. Run `claude auth login` in a terminal.",
        )

    _run(exercise)




def test_codex_reads_its_login_line_and_what_its_app_server_says_it_runs() -> None:
    """Codex is the one backend whose catalog is asked rather than written down."""

    async def exercise() -> None:
        machine = _installed_codex()

        card = await probe_backend(
            ConversationBackendKey.codex,
            machine,
            codex_model_catalog_probe=_codex_that_answers(
                CodexModel(
                    model_id="gpt-5.6-sol",
                    display_name="GPT-5.6-Sol",
                    reasoning_effort_options=("low", "medium", "high"),
                    default_reasoning_effort="low",
                    is_default=True,
                ),
                CodexModel(
                    model_id="gpt-5.5",
                    display_name="GPT-5.5",
                    reasoning_effort_options=("medium", "xhigh"),
                    default_reasoning_effort="medium",
                    is_default=False,
                ),
            ),
        )

        assert card.version == "0.145.0"
        assert card.identity is not None
        assert card.identity.status is BackendIdentityStatus.authenticated
        assert card.identity.account_label == "Logged in using ChatGPT"
        assert card.identity.login_command == "codex login"
        assert [model.model_id for model in card.available_models] == [
            "gpt-5.6-sol",
            "gpt-5.5",
        ]
        # The union across the models, in the order codex listed them: a picker shows one
        # list, and an effort no model takes is one no turn could run under.
        assert card.reasoning_effort_options == ("low", "medium", "high", "xhigh")
        assert card.diagnoses == ()

    _run(exercise)


def test_a_codex_that_will_not_say_what_it_runs_lists_nothing_and_says_why() -> None:
    async def exercise() -> None:
        card = await probe_backend(
            ConversationBackendKey.codex,
            _installed_codex(),
            codex_model_catalog_probe=_codex_that_says_nothing,
        )

        assert card.installed is True
        assert card.available_models == ()
        assert card.reasoning_effort_options == ()
        assert card.diagnoses == (
            "Codex is installed but did not answer when asked what it can run, so no "
            "models are listed. Check that `codex app-server` starts from a terminal.",
        )

    _run(exercise)


# --- what a backend runs when nobody picks --------------------------------------------------


def test_configured_hermes_is_probed_when_it_is_absent_from_path() -> None:
    async def exercise() -> None:
        configured_path = "/opt/hermes/venv/bin/hermes"
        machine = _FakeMachine(
            executables={"hermes": "/usr/local/bin/unrelated-hermes"},
            configured_executables={"hermes": configured_path},
            answers_any_other_command=CommandOutcome(
                exit_code=0,
                standard_output=json.dumps(
                    {
                        "schemaVersion": 1,
                        "status": "not_configured",
                        "defaultModelId": None,
                        "providers": [],
                    }
                ),
                standard_error="",
            ),
        )
        machine.outcomes[(configured_path, "--version")] = CommandOutcome(
            exit_code=0,
            standard_output="Hermes Agent v0.18.2\n",
            standard_error="",
        )

        card = await probe_backend(ConversationBackendKey.hermes, machine)

        assert card.installed is True
        assert card.executable_path == configured_path
        assert card.diagnoses == (
            "Hermes is installed but has no default provider and model configured. "
            "Run `hermes model` in a terminal.",
        )

    _run(exercise)


def test_hermes_default_must_be_present_in_its_returned_inventory() -> None:
    async def exercise() -> None:
        machine = _FakeMachine(executables={"hermes": HERMES_PATH})
        machine.outcomes[(HERMES_PATH, "--version")] = CommandOutcome(
            exit_code=0,
            standard_output="Hermes Agent v0.18.2\n",
            standard_error="",
        )
        machine.answers_any_other_command = CommandOutcome(
            exit_code=0,
            standard_output=json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "default_unavailable",
                    "defaultModelId": "openai:missing",
                    "providers": [
                        {
                            "id": "openai",
                            "displayName": "OpenAI",
                            "models": [
                                {
                                    "id": "openai:available",
                                    "displayName": "Available",
                                    "detail": "OpenAI",
                                }
                            ],
                        }
                    ],
                }
            ),
            standard_error="",
        )

        card = await probe_backend(ConversationBackendKey.hermes, machine)

        assert card.default_model_id is None
        assert [model.model_id for model in card.available_models] == [
            "openai:available"
        ]
        assert "does not have usable credentials" in card.diagnoses[0]

    _run(exercise)


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






def test_a_backend_that_installed_itself_is_updated_by_its_own_command() -> None:
    async def exercise() -> None:
        machine = _FakeMachine(
            executables={"codex": CODEX_PATH}, real_paths={CODEX_PATH: CODEX_REAL_PATH}
        )
        machine.outcomes[(CODEX_PATH, "--version")] = CommandOutcome(
            exit_code=0, standard_output="codex-cli 0.145.0\n", standard_error=""
        )
        machine.outcomes[(CODEX_PATH, "login", "status")] = CommandOutcome(
            exit_code=0, standard_output="Logged in using ChatGPT\n", standard_error=""
        )

        card = await probe_backend(ConversationBackendKey.codex, machine)

        assert card.update_advisory is not None
        assert card.update_advisory.install_method is BackendInstallMethod.native
        assert card.update_advisory.update_command == ("codex", "update")
        # A vendor's own updater is not a published package, so nothing is looked up.
        assert machine.registry_lookups == []

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


def test_hermes_update_action_is_unavailable_without_running_an_update_command() -> None:
    async def exercise() -> None:
        machine = _FakeMachine(executables={"hermes": HERMES_PATH})
        machine.outcomes[(HERMES_PATH, "--version")] = CommandOutcome(
            exit_code=0, standard_output="Hermes Agent v0.18.2\n", standard_error=""
        )
        machine.answers_any_other_command = CommandOutcome(
            exit_code=0,
            standard_output=json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "not_configured",
                    "defaultModelId": None,
                    "providers": [],
                }
            ),
            standard_error="",
        )

        result = await BackendSnapshotService(machine).update_backend(
            ConversationBackendKey.hermes
        )

        assert result.outcome is BackendUpdateOutcome.failed
        assert result.detail == "There is no update Panels can run for this backend."
        assert all("update" not in command for command in machine.run_commands)

    _run(exercise)


def test_an_update_that_failed_carries_the_end_of_what_it_printed() -> None:
    async def exercise() -> None:
        machine = _installed_claude()
        machine.registry_versions["@anthropic-ai/claude-code"] = "2.1.230"
        machine.outcomes[_claude_update_command()] = CommandOutcome(
            exit_code=1, standard_output="", standard_error="x" * 20_000 + "EACCES at the end"
        )

        result = await BackendSnapshotService(
            machine, claude_model_catalog_probe=_claude_that_answers()
        ).update_backend(
            ConversationBackendKey.claude
        )

        assert result.outcome is BackendUpdateOutcome.failed
        assert result.detail == "The update command exited with code 1."
        assert result.output_tail.endswith("EACCES at the end")
        assert len(result.output_tail) <= 10_000

    _run(exercise)






def test_an_update_on_one_backend_does_not_hold_up_reading_another_ones_card() -> None:
    """An install is slow; looking at a card is not, and must not wait behind one."""

    async def exercise() -> None:
        machine = _installed_claude()
        machine.registry_versions["@anthropic-ai/claude-code"] = "2.1.230"
        machine.outcomes[_claude_update_command()] = CommandOutcome(
            exit_code=0, standard_output="", standard_error=""
        )
        machine.slow_commands.add(_claude_update_command())
        machine.let_slow_commands_finish = asyncio.Event()
        service = BackendSnapshotService(
            machine, claude_model_catalog_probe=_claude_that_answers()
        )

        updating = asyncio.create_task(service.update_backend(ConversationBackendKey.claude))
        for _ in range(50):
            await asyncio.sleep(0)

        # The codex card comes back while claude is still installing.
        card = await asyncio.wait_for(
            service.snapshot(ConversationBackendKey.codex), timeout=5.0
        )
        assert card.backend_key is ConversationBackendKey.codex

        assert machine.let_slow_commands_finish is not None
        machine.let_slow_commands_finish.set()
        await updating

    _run(exercise)


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


