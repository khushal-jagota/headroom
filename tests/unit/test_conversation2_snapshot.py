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
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from planner.conversation2.backends.codex_app_server.model_catalog import (
    CodexModel,
    CodexModelCatalog,
    CodexModelCatalogUnavailable,
)
from planner.conversation2.contracts import ConversationBackendKey
from planner.conversation2.snapshot import (
    BackendIdentityStatus,
    BackendInstallMethod,
    BackendSnapshotService,
    BackendUpdateOutcome,
    CommandOutcome,
    classify_install_method,
    parse_version,
    probe_backend,
)

CLAUDE_PATH = "/Users/someone/.local/bin/claude"
CLAUDE_REAL_PATH = "/Users/someone/.local/lib/node_modules/@anthropic-ai/claude-code/bin/claude"
CODEX_PATH = "/Users/someone/.local/bin/codex"
CODEX_REAL_PATH = "/Users/someone/.codex/packages/standalone/releases/0.145.0-arm64/bin/codex"
HERMES_PATH = "/Users/someone/.local/bin/hermes"


@dataclass
class _FakeMachine:
    """The machine, answered from a script and never actually touched."""

    executables: dict[str, str] = field(default_factory=dict)
    real_paths: dict[str, str] = field(default_factory=dict)
    outcomes: dict[tuple[str, ...], CommandOutcome] = field(default_factory=dict)
    registry_versions: dict[str, str] = field(default_factory=dict)
    after_run: dict[tuple[str, ...], Callable[[], None]] = field(default_factory=dict)
    run_commands: list[tuple[str, ...]] = field(default_factory=list)
    registry_lookups: list[str] = field(default_factory=list)

    def executable_path(self, executable_name: str) -> str | None:
        return self.executables.get(executable_name)

    def real_path(self, path: str) -> str:
        return self.real_paths.get(path, path)

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
        outcome = self.outcomes.get(
            command,
            CommandOutcome(exit_code=-1, standard_output="", standard_error="no such command"),
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


def _codex_that_answers(*models: CodexModel) -> Callable[[str], Any]:
    async def probe(codex_executable: str) -> CodexModelCatalog:
        del codex_executable
        efforts: list[str] = []
        for model in models:
            for effort in model.reasoning_effort_options:
                if effort not in efforts:
                    efforts.append(effort)
        return CodexModelCatalog(models=models, reasoning_effort_options=tuple(efforts))

    return probe


async def _codex_that_says_nothing(codex_executable: str) -> CodexModelCatalog:
    del codex_executable
    raise CodexModelCatalogUnavailable("codex app-server would not start")


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


def test_a_version_is_found_in_whatever_else_the_line_says() -> None:
    assert parse_version("2.1.219 (Claude Code)\n") == "2.1.219"
    assert parse_version("codex-cli 0.145.0\n") == "0.145.0"
    assert (
        parse_version(
            "Hermes Agent v0.18.2 (2026.7.7.2) · upstream 07e97d2f · local 047ba829\n"
            "Install method: git\n"
        )
        == "0.18.2"
    )
    assert parse_version("something went wrong") is None


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


def test_a_backends_own_installer_wins_over_where_the_link_points() -> None:
    """A CLI that ships its own updater knows better than a path substring."""
    assert (
        classify_install_method(
            (CODEX_PATH, CODEX_REAL_PATH),
            native_path_marker="/.codex/packages/standalone/",
        )
        is BackendInstallMethod.native
    )
    assert (
        classify_install_method(
            (CODEX_PATH, CODEX_REAL_PATH), native_path_marker="/somewhere/else/"
        )
        is BackendInstallMethod.manual_only
    )


# --- what a card says --------------------------------------------------------------------


def test_a_backend_that_is_not_installed_is_said_plainly_and_offers_nothing() -> None:
    async def exercise() -> None:
        machine = _FakeMachine()

        card = await probe_backend(ConversationBackendKey.codex, machine)

        assert card.installed is False
        assert card.version is None
        assert card.identity is None
        assert card.update_advisory is None
        assert card.available_models == ()
        assert card.diagnoses == ("`codex` is not installed or not on PATH.",)
        # Nothing was run: there was nothing to run.
        assert machine.run_commands == []

    _run(exercise)


def test_claude_reports_its_account_its_models_and_its_effort_levels() -> None:
    async def exercise() -> None:
        machine = _installed_claude()

        card = await probe_backend(ConversationBackendKey.claude, machine)

        assert card.installed is True
        assert card.version == "2.1.219"
        assert card.identity is not None
        assert card.identity.status is BackendIdentityStatus.authenticated
        assert card.identity.account_label == "owner@example.com"
        assert card.identity.detail == "max plan via claude.ai"
        assert card.identity.login_command == "claude auth login"
        assert [model.model_id for model in card.available_models] == ["fable", "opus", "sonnet"]
        assert card.reasoning_effort_options == ("low", "medium", "high", "xhigh", "max")
        assert card.diagnoses == ()

    _run(exercise)


def test_a_signed_out_cli_is_told_which_command_to_run() -> None:
    async def exercise() -> None:
        machine = _installed_claude(
            identity=CommandOutcome(
                exit_code=0, standard_output=json.dumps({"loggedIn": False}), standard_error=""
            )
        )

        card = await probe_backend(ConversationBackendKey.claude, machine)

        assert card.identity is not None
        assert card.identity.status is BackendIdentityStatus.unauthenticated
        assert card.identity.account_label is None
        assert card.diagnoses == (
            "`claude` is not signed in. Run `claude auth login` in a terminal.",
        )

    _run(exercise)


def test_an_account_that_cannot_be_read_is_unknown_rather_than_invented() -> None:
    async def exercise() -> None:
        machine = _installed_claude(
            identity=CommandOutcome(
                exit_code=1, standard_output="", standard_error="unreadable credentials"
            )
        )

        card = await probe_backend(ConversationBackendKey.claude, machine)

        assert card.identity is not None
        assert card.identity.status is BackendIdentityStatus.unknown
        assert card.identity.account_label is None
        assert card.diagnoses == (
            "Panels could not read who `claude` is signed in as.",
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
        assert card.update_advisory is not None
        assert card.update_advisory.install_method is BackendInstallMethod.manual_only
        assert card.update_advisory.update_command is None
        assert card.update_advisory.detail == (
            "Hermes is installed from its own checkout, so Panels offers no update here — "
            "update it where it is installed."
        )
        # Nothing was asked of a registry for a backend no registry publishes.
        assert machine.registry_lookups == []

    _run(exercise)


def test_a_version_that_cannot_be_read_is_a_diagnosis_not_a_guess() -> None:
    async def exercise() -> None:
        machine = _installed_claude(version_output="claude: command failed")

        card = await probe_backend(ConversationBackendKey.claude, machine)

        assert card.version is None
        assert card.diagnoses[0] == (
            "`claude --version` did not report a version, so Panels cannot tell which "
            "one is installed."
        )

    _run(exercise)


# --- the update advisory ------------------------------------------------------------------


def test_a_newer_published_version_is_offered_with_the_command_that_installs_it() -> None:
    async def exercise() -> None:
        machine = _installed_claude()
        machine.registry_versions["@anthropic-ai/claude-code"] = "2.1.230"

        card = await probe_backend(ConversationBackendKey.claude, machine)

        assert card.update_advisory is not None
        assert card.update_advisory.install_method is BackendInstallMethod.npm_global
        assert card.update_advisory.update_command == (
            "npm",
            "install",
            "-g",
            "@anthropic-ai/claude-code@latest",
        )
        assert card.update_advisory.latest_version == "2.1.230"
        assert card.update_advisory.update_available is True
        assert card.update_advisory.detail == "Version 2.1.230 is available."

    _run(exercise)


def test_the_newest_version_already_installed_offers_no_update() -> None:
    async def exercise() -> None:
        machine = _installed_claude()
        machine.registry_versions["@anthropic-ai/claude-code"] = "2.1.219"

        card = await probe_backend(ConversationBackendKey.claude, machine)

        assert card.update_advisory is not None
        assert card.update_advisory.update_available is False
        assert card.update_advisory.detail == "This is the newest published version."

    _run(exercise)


def test_an_older_published_version_is_not_an_update() -> None:
    """Versions are compared, not merely differenced: 2.1.9 is not newer than 2.1.219."""

    async def exercise() -> None:
        machine = _installed_claude()
        machine.registry_versions["@anthropic-ai/claude-code"] = "2.1.9"

        card = await probe_backend(ConversationBackendKey.claude, machine)

        assert card.update_advisory is not None
        assert card.update_advisory.update_available is False

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
    return ("npm", "install", "-g", "@anthropic-ai/claude-code@latest")


def test_an_update_that_moves_the_version_succeeded() -> None:
    async def exercise() -> None:
        machine = _installed_claude()
        machine.registry_versions["@anthropic-ai/claude-code"] = "2.1.230"
        machine.outcomes[_claude_update_command()] = CommandOutcome(
            exit_code=0, standard_output="added 1 package\n", standard_error=""
        )

        def the_new_one_is_now_installed() -> None:
            machine.outcomes[(CLAUDE_PATH, "--version")] = CommandOutcome(
                exit_code=0, standard_output="2.1.230 (Claude Code)\n", standard_error=""
            )

        machine.after_run[_claude_update_command()] = the_new_one_is_now_installed

        result = await BackendSnapshotService(machine).update_backend(
            ConversationBackendKey.claude
        )

        assert result.outcome is BackendUpdateOutcome.succeeded
        assert result.detail == "Updated to 2.1.230."
        assert "added 1 package" in result.output_tail

    _run(exercise)


def test_an_update_that_changes_nothing_says_so_rather_than_claiming_success() -> None:
    async def exercise() -> None:
        machine = _installed_claude()
        machine.registry_versions["@anthropic-ai/claude-code"] = "2.1.230"
        machine.outcomes[_claude_update_command()] = CommandOutcome(
            exit_code=0, standard_output="up to date\n", standard_error=""
        )

        result = await BackendSnapshotService(machine).update_backend(
            ConversationBackendKey.claude
        )

        assert result.outcome is BackendUpdateOutcome.unchanged
        assert result.detail == (
            "The update command finished, but the installed version is still 2.1.219."
        )

    _run(exercise)


def test_an_update_that_failed_carries_the_end_of_what_it_printed() -> None:
    async def exercise() -> None:
        machine = _installed_claude()
        machine.registry_versions["@anthropic-ai/claude-code"] = "2.1.230"
        machine.outcomes[_claude_update_command()] = CommandOutcome(
            exit_code=1, standard_output="", standard_error="x" * 20_000 + "EACCES at the end"
        )

        result = await BackendSnapshotService(machine).update_backend(
            ConversationBackendKey.claude
        )

        assert result.outcome is BackendUpdateOutcome.failed
        assert result.detail == "The update command exited with code 1."
        assert result.output_tail.endswith("EACCES at the end")
        assert len(result.output_tail) <= 10_000

    _run(exercise)


def test_an_update_with_no_command_to_run_fails_with_the_reason() -> None:
    async def exercise() -> None:
        machine = _FakeMachine()

        result = await BackendSnapshotService(machine).update_backend(
            ConversationBackendKey.hermes
        )

        assert result.outcome is BackendUpdateOutcome.failed
        assert result.detail == "`hermes` is not installed or not on PATH."
        assert machine.run_commands == []

    _run(exercise)


# --- keeping the answers ---------------------------------------------------------------------


def test_a_probe_is_run_once_and_again_only_when_asked() -> None:
    async def exercise() -> None:
        machine = _installed_claude()
        service = BackendSnapshotService(machine)

        first = await service.snapshot(ConversationBackendKey.claude)
        commands_after_the_first = len(machine.run_commands)
        again = await service.snapshot(ConversationBackendKey.claude)

        assert again == first
        assert len(machine.run_commands) == commands_after_the_first

        await service.snapshot(ConversationBackendKey.claude, refresh=True)
        assert len(machine.run_commands) > commands_after_the_first

    _run(exercise)


def test_every_backend_has_a_card_whether_or_not_it_is_there() -> None:
    async def exercise() -> None:
        cards = await BackendSnapshotService(_FakeMachine()).snapshots()

        assert [card.backend_key for card in cards] == list(ConversationBackendKey)
        assert all(card.installed is False for card in cards)

    _run(exercise)


# --- the real machine, only when asked for by name ---------------------------------------------


@pytest.mark.skipif(
    os.environ.get("PANELS_REAL_BACKEND_PROBES") != "1",
    reason="touches the real CLIs on this machine; set PANELS_REAL_BACKEND_PROBES=1",
)
def test_the_real_backends_on_this_machine_answer() -> None:
    """The same probes against the CLIs that are actually installed here."""

    async def exercise() -> None:
        cards = await BackendSnapshotService().snapshots(refresh=True)
        for card in cards:
            print(f"\n=== {card.backend_key} ===")
            print(f"installed={card.installed} version={card.version}")
            print(f"executable={card.executable_path}")
            print(f"identity={card.identity}")
            print(f"models={[model.model_id for model in card.available_models]}")
            print(f"efforts={card.reasoning_effort_options}")
            print(f"update={card.update_advisory}")
            print(f"diagnoses={card.diagnoses}")
        assert len(cards) == len(ConversationBackendKey)

    _run(exercise)
