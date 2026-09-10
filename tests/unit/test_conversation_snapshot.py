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

import pytest

from planner.conversation.backends.claude_model_catalog import (
    ClaudeModel,
    ClaudeModelCatalog,
    ClaudeModelCatalogUnavailable,
    versioned_display_name,
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
    classify_install_method,
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


def test_where_a_binary_really_lives_is_what_says_how_to_update_it() -> None:
    """The resolved path is usually a link; the thing it points at is the evidence."""
    assert (
        classify_install_method((CLAUDE_PATH, CLAUDE_REAL_PATH), native_path_marker=None)
        is BackendInstallMethod.npm_global
    )
    assert (
        classify_install_method(
            ("/opt/homebrew/bin/codex", "/opt/homebrew/Cellar/codex/0.145.0/bin/codex"),
            native_path_marker=None,
        )
        is BackendInstallMethod.homebrew
    )
    assert (
        classify_install_method(
            ("/Users/someone/.bun/bin/codex", "/Users/someone/.bun/bin/codex"),
            native_path_marker=None,
        )
        is BackendInstallMethod.bun_global
    )
    assert (
        classify_install_method(
            ("/Users/someone/.local/share/pnpm/claude", "/Users/someone/.local/share/pnpm/claude"),
            native_path_marker=None,
        )
        is BackendInstallMethod.pnpm_global
    )
    # Nothing recognisable: a real answer, and one that offers no button.
    assert (
        classify_install_method((HERMES_PATH, HERMES_PATH), native_path_marker=None)
        is BackendInstallMethod.manual_only
    )




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


def test_a_resolved_model_id_reads_as_its_versioned_name() -> None:
    assert versioned_display_name("claude-opus-5[1m]") == "Opus 5 (1M)"
    assert versioned_display_name("claude-fable-5") == "Fable 5"
    assert versioned_display_name("claude-sonnet-5") == "Sonnet 5"
    assert versioned_display_name("claude-haiku-4-5-20251001") == "Haiku 4.5"
    # An id this cannot make sense of is shown as itself — honest over pretty.
    assert versioned_display_name("someday-a-new-shape") == "someday-a-new-shape"




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


def test_hermes_has_no_account_to_report_and_no_effort_to_offer() -> None:
    """Two different absences: hermes has no login at all, and no reasoning setting."""

    async def exercise() -> None:
        machine = _FakeMachine(executables={"hermes": HERMES_PATH})
        machine.outcomes[(HERMES_PATH, "--version")] = CommandOutcome(
            exit_code=0, standard_output="Hermes Agent v0.18.2 (2026.7.7.2)\n", standard_error=""
        )
        card = await probe_backend(ConversationBackendKey.hermes, machine)

        assert card.version == "0.18.2"
        assert card.identity is None
        assert card.reasoning_effort_options == ()
        assert card.update_advisory is None
        # Nothing was asked of a registry for a backend no registry publishes.
        assert machine.registry_lookups == []

    _run(exercise)




# --- what a backend runs when nobody picks --------------------------------------------------


def test_claude_names_the_concrete_model_its_default_reaches() -> None:
    """"Default" is not a model anybody can be shown as running: the model it reaches is."""

    async def exercise() -> None:
        card = await probe_backend(
            ConversationBackendKey.claude,
            _installed_claude(),
            claude_model_catalog_probe=_claude_that_answers(default_model_id="opus[1m]"),
        )

        assert card.default_model_id == "opus[1m]"
        assert card.default_model_id in [model.model_id for model in card.available_models]
        # Claude's handshake says which efforts a model takes and nothing about where it
        # starts, so there is no default effort to report and none is invented.
        assert card.default_reasoning_effort is None

    _run(exercise)


def test_codex_names_the_model_and_the_effort_it_flags_as_its_own() -> None:
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
                    reasoning_effort_options=("medium",),
                    default_reasoning_effort="medium",
                    is_default=False,
                ),
            ),
        )

        assert card.default_model_id == "gpt-5.6-sol"
        assert card.default_reasoning_effort == "low"

    _run(exercise)


def test_hermes_names_the_model_its_own_configuration_runs_on() -> None:
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
                    "status": "runnable",
                    "defaultModelId": "openai:gpt-5.6-sol",
                    "providers": [
                        {
                            "id": "openai",
                            "displayName": "OpenAI",
                            "models": [
                                {
                                    "id": "openai:gpt-5.6-sol",
                                    "displayName": "gpt-5.6-sol",
                                    "detail": "OpenAI",
                                },
                                {
                                    "id": "openai:gpt-5.5",
                                    "displayName": "gpt-5.5",
                                    "detail": "OpenAI",
                                },
                            ],
                        }
                    ],
                }
            ),
            standard_error="",
        )

        card = await probe_backend(ConversationBackendKey.hermes, machine)

        assert card.default_model_id == "openai:gpt-5.6-sol"
        assert [model.model_id for model in card.available_models] == [
            "openai:gpt-5.6-sol",
            "openai:gpt-5.5",
        ]
        # Hermes has no reasoning effort at all, so it has no default one either.
        assert card.reasoning_effort_options == ()
        assert card.default_reasoning_effort is None

    _run(exercise)


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


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {
                "schemaVersion": 1,
                "status": "runnable",
                "defaultModelId": "openai-codex:gpt-5.6-sol",
                "providers": [
                    {
                        "id": "openai-codex",
                        "displayName": "OpenAI Codex",
                        "models": [
                            {
                                "id": "openai-codex:gpt-5.6-sol",
                                "displayName": "GPT-5.6 Sol",
                            }
                        ],
                    }
                ],
            },
            id="partial-model-row",
        ),
        pytest.param(
            {
                "schemaVersion": 1,
                "status": "not_configured",
                "defaultModelId": None,
                "providers": [
                    {
                        "id": "openai-codex",
                        "displayName": "OpenAI Codex",
                        "models": [
                            {
                                "id": "openai-codex:gpt-5.6-sol",
                                "displayName": "GPT-5.6 Sol",
                                "detail": "OpenAI Codex",
                            }
                        ],
                    },
                    {
                        "id": "openai-codex",
                        "displayName": "Duplicate",
                        "models": [
                            {
                                "id": "openai-codex:gpt-5.5",
                                "displayName": "GPT-5.5",
                                "detail": "Duplicate",
                            }
                        ],
                    },
                ],
            },
            id="duplicate-provider",
        ),
        pytest.param(
            {
                "schemaVersion": 1,
                "status": "runnable",
                "defaultModelId": None,
                "providers": [],
            },
            id="runnable-without-default",
        ),
    ],
)
def test_a_malformed_hermes_inventory_is_rejected_whole(payload: dict[str, Any]) -> None:
    async def exercise() -> None:
        machine = _FakeMachine(executables={"hermes": HERMES_PATH})
        machine.outcomes[(HERMES_PATH, "--version")] = CommandOutcome(
            exit_code=0,
            standard_output="Hermes Agent v0.18.2\n",
            standard_error="",
        )
        machine.answers_any_other_command = CommandOutcome(
            exit_code=0,
            standard_output=json.dumps(payload),
            standard_error="",
        )

        card = await probe_backend(ConversationBackendKey.hermes, machine)

        assert card.available_models == ()
        assert card.default_model_id is None
        assert card.diagnoses == (
            "Hermes' model inventory returned an invalid answer, so no models are listed.",
        )

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


def test_cancelling_a_command_takes_what_it_started_with_it(tmp_path: Path) -> None:
    """A disconnected update caller cannot leave an installer behind its released lease."""

    async def exercise() -> None:
        child_process_id_file = tmp_path / "the-cancelled-child.pid"
        running = asyncio.create_task(
            SubprocessBackendProbeEnvironment().run(
                (
                    "/bin/sh",
                    "-c",
                    f"sleep 60 & echo $! > {child_process_id_file}; wait",
                ),
                timeout_seconds=60.0,
            )
        )
        for _ in range(100):
            if child_process_id_file.exists():
                break
            await asyncio.sleep(0.01)
        assert child_process_id_file.exists()

        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        child_process_id = int(child_process_id_file.read_text().strip())
        for _ in range(100):
            if not _still_alive(child_process_id):
                break
            await asyncio.sleep(0.05)
        assert not _still_alive(child_process_id), (
            f"the cancelled command's child ({child_process_id}) outlived it"
        )

    _run(exercise)


# --- keeping the answers ---------------------------------------------------------------------


def test_a_probe_is_run_once_and_again_only_when_asked() -> None:
    async def exercise() -> None:
        machine = _installed_claude()
        service = BackendSnapshotService(
            machine, claude_model_catalog_probe=_claude_that_answers()
        )

        first = await service.snapshot(ConversationBackendKey.claude)
        commands_after_the_first = len(machine.run_commands)
        again = await service.snapshot(ConversationBackendKey.claude)

        assert again == first
        assert len(machine.run_commands) == commands_after_the_first

        await service.snapshot(ConversationBackendKey.claude, refresh=True)
        assert len(machine.run_commands) > commands_after_the_first

    _run(exercise)


def test_independent_backend_snapshots_start_together() -> None:
    async def exercise() -> None:
        machine = _FakeMachine(
            executables={"codex": CODEX_PATH, "claude": CLAUDE_PATH},
            real_paths={CODEX_PATH: CODEX_REAL_PATH, CLAUDE_PATH: CLAUDE_REAL_PATH},
        )
        machine.outcomes[(CODEX_PATH, "--version")] = CommandOutcome(
            exit_code=0, standard_output="codex-cli 0.145.0\n", standard_error=""
        )
        machine.outcomes[(CODEX_PATH, "login", "status")] = CommandOutcome(
            exit_code=0, standard_output="Logged in using ChatGPT\n", standard_error=""
        )
        machine.outcomes[(CLAUDE_PATH, "--version")] = CommandOutcome(
            exit_code=0, standard_output="2.1.219 (Claude Code)\n", standard_error=""
        )
        machine.outcomes[(CLAUDE_PATH, "auth", "status", "--json")] = CommandOutcome(
            exit_code=0,
            standard_output=json.dumps({"loggedIn": True}),
            standard_error="",
        )
        release_catalogues = asyncio.Event()
        started_catalogues: set[ConversationBackendKey] = set()

        async def codex_catalog(codex_executable: str) -> CodexModelCatalog:
            del codex_executable
            started_catalogues.add(ConversationBackendKey.codex)
            await release_catalogues.wait()
            return await _codex_that_answers()(CODEX_PATH)

        async def claude_catalog(claude_executable: str) -> ClaudeModelCatalog:
            del claude_executable
            started_catalogues.add(ConversationBackendKey.claude)
            await release_catalogues.wait()
            return await _claude_that_answers()(CLAUDE_PATH)

        service = BackendSnapshotService(
            machine,
            codex_model_catalog_probe=codex_catalog,
            claude_model_catalog_probe=claude_catalog,
        )
        reading = asyncio.create_task(service.snapshots())
        try:
            for _ in range(20):
                if len(started_catalogues) == 2:
                    break
                await asyncio.sleep(0)
            assert started_catalogues == {
                ConversationBackendKey.codex,
                ConversationBackendKey.claude,
            }
        finally:
            release_catalogues.set()
        snapshots = await reading

        assert tuple(snapshot.backend_key for snapshot in snapshots) == tuple(
            ConversationBackendKey
        )

    _run(exercise)


def test_concurrent_reads_of_one_backend_share_the_first_probe() -> None:
    async def exercise() -> None:
        machine = _installed_claude()
        release_catalogue = asyncio.Event()
        catalogue_probe_count = 0

        async def claude_catalog(claude_executable: str) -> ClaudeModelCatalog:
            nonlocal catalogue_probe_count
            catalogue_probe_count += 1
            await release_catalogue.wait()
            return await _claude_that_answers()(claude_executable)

        service = BackendSnapshotService(
            machine, claude_model_catalog_probe=claude_catalog
        )
        reads = tuple(
            asyncio.create_task(service.snapshot(ConversationBackendKey.claude))
            for _ in range(2)
        )
        try:
            for _ in range(20):
                await asyncio.sleep(0)
            assert catalogue_probe_count == 1
        finally:
            release_catalogue.set()
        first, second = await asyncio.gather(*reads)

        assert first is second
        assert machine.run_commands.count((CLAUDE_PATH, "--version")) == 1

    _run(exercise)


def test_forced_hermes_snapshot_refreshes_inventory_without_probing_for_updates() -> None:
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
                    "status": "not_configured",
                    "defaultModelId": None,
                    "providers": [],
                }
            ),
            standard_error="",
        )
        service = BackendSnapshotService(machine)

        await service.snapshot(ConversationBackendKey.hermes)
        first_inventory_command = next(
            command
            for command in machine.run_commands
            if "hermes_model_catalog.py" in " ".join(command)
        )
        assert "--refresh" not in first_inventory_command

        await service.snapshot(ConversationBackendKey.hermes)
        assert machine.run_commands.count(first_inventory_command) == 1

        refreshed = await service.snapshot(ConversationBackendKey.hermes, refresh=True)

        assert refreshed.version == "0.18.2"
        assert refreshed.update_advisory is None
        assert any(
            "hermes_model_catalog.py" in " ".join(command) and "--refresh" in command
            for command in machine.run_commands
        )
        assert (HERMES_PATH, "update", "--check") not in machine.run_commands

    _run(exercise)
