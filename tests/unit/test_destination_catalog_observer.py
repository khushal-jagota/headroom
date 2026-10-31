"""Destination launch inputs and physical probe settlement are separate boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio
import pytest

from planner.conversation.backends.claude_model_catalog import ClaudeModel, ClaudeModelCatalog
from planner.conversation.backends.codex_app_server.model_catalog import (
    CodexModel,
    CodexModelCatalog,
)
from planner.conversation.contracts import ConversationBackendKey, WorkspacePathPolicy
from planner.conversation.model_resolution import observer
from planner.conversation.model_resolution.contracts import (
    CatalogObservation,
    CatalogWorkspaceBinding,
    PrerequisiteStatus,
    SelectionKind,
)
from planner.conversation.production_backends import (
    ProductionBackendLaunches,
    production_backend_launches,
)
from planner.conversation.snapshot import (
    CommandOutcome,
    SubprocessBackendProbeEnvironment,
)
from planner.tickets.contracts import TicketLocale


class ScriptedLaunchEnvironment(SubprocessBackendProbeEnvironment):
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, str], Path | None]] = []

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        environment_overrides: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
    ) -> CommandOutcome:
        del timeout_seconds
        self.calls.append((tuple(argv), dict(environment_overrides or {}), working_directory))
        output = "2.1.267" if argv[-1] == "--version" else "Logged in"
        if tuple(argv[1:]) == ("auth", "status", "--json"):
            output = '{"loggedIn":true}'
        return CommandOutcome(0, output, "")


def launch_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProductionBackendLaunches:
    executable = tmp_path / "selected-cli"
    executable.write_text("fixture, never executed")
    executable.chmod(0o700)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "provider-home"))
    launches = production_backend_launches(panels_server_url="http://fixture.invalid")
    return replace(
        launches,
        codex=replace(
            launches.codex,
            argv=(str(executable), "app-server"),
            environment_overrides=(("PROVIDER_FIXTURE", "selected"),),
        ),
        claude=replace(
            launches.claude,
            claude_executable=executable,
            environment_overrides=(("PROVIDER_FIXTURE", "selected"),),
        ),
    )


def binding(path: Path) -> CatalogWorkspaceBinding:
    return CatalogWorkspaceBinding(path, WorkspacePathPolicy.native_absolute, None)


@pytest.mark.parametrize("ending", ["cancel", "timeout"])
def test_nested_provider_probe_stays_owned_until_physical_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
) -> None:
    from planner.conversation.backends import claude_model_catalog

    launches = launch_fixture(tmp_path, monkeypatch)

    async def run() -> None:
        entered, cleanup_started, cleanup_release = (asyncio.Event() for _ in range(3))
        cleaned = False

        async def process(command: Sequence[str], **kwargs: object) -> Any:
            nonlocal cleaned
            assert command == [str(launches.claude.claude_executable), "--version"]
            assert kwargs["cwd"] == tmp_path
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                with anyio.CancelScope(shield=True):
                    cleanup_started.set()
                    await cleanup_release.wait()
                    cleaned = True

        monkeypatch.setattr(anyio, "run_process", process)
        if ending == "timeout":
            monkeypatch.setattr(claude_model_catalog, "MODEL_CATALOG_TIMEOUT_SECONDS", 0.05)
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps,
            panels_server_url="http://fixture.invalid",
            launches=lambda: launches,
            environment=ScriptedLaunchEnvironment(),
        )
        task = asyncio.create_task(
            reader.observe(
                ConversationBackendKey.claude,
                workspace=binding(tmp_path),
                attempt_id="cleanup",
                deadline=asyncio.get_running_loop().time() + 10,
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        if ending == "cancel":
            task.cancel()
        await asyncio.wait_for(cleanup_started.wait(), 2)
        assert not task.done()
        if ending == "cancel":
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        cleanup_release.set()
        if ending == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        else:
            result = await asyncio.wait_for(task, 2)
            assert isinstance(result, CatalogObservation)
            assert "claude-opus-5-5" not in {row.submitted_token for row in result.rows}
            assert result.rows  # Unbounded recipes remain useful without a version.
        assert cleaned

    asyncio.run(run())


@pytest.mark.parametrize("backend", [ConversationBackendKey.codex, ConversationBackendKey.claude])
def test_observation_uses_selected_launch_and_preserves_row_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: ConversationBackendKey
) -> None:
    launches = launch_fixture(tmp_path, monkeypatch)
    environment = ScriptedLaunchEnvironment()
    probe_calls: list[tuple[str, Mapping[str, str] | None, Path | None]] = []
    clock = [100.0]

    async def codex(
        executable: str,
        *,
        environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
    ) -> CodexModelCatalog:
        probe_calls.append((executable, environment, working_directory))
        clock[0] = 120.0
        return CodexModelCatalog(
            (CodexModel("gpt-6.1-terra", "Terra", ("medium",), "medium", True),),
            ("medium",),
        )

    async def claude(
        executable: str,
        *,
        environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
    ) -> ClaudeModelCatalog:
        probe_calls.append((executable, environment, working_directory))
        clock[0] = 120.0
        return ClaudeModelCatalog(
            (
                ClaudeModel("fable", "Fable", "claude-fable-5", None),
                ClaudeModel("claude-fable-4", "Fable 4", "claude-fable-4", ()),
            ),
            (),
            provider_scope="owned-probe",
        )

    monkeypatch.setattr(observer, "probe_codex_model_catalog", codex)
    monkeypatch.setattr(observer, "probe_claude_model_catalog", claude)

    async def run() -> None:
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps,
            panels_server_url="http://fixture.invalid",
            launches=lambda: launches,
            environment=environment,
            now=lambda: clock[0],
        )
        result = await reader.observe(
            backend,
            workspace=binding(tmp_path),
            attempt_id="attempt-one",
            deadline=asyncio.get_running_loop().time() + 10,
        )
        assert isinstance(result, CatalogObservation)
        assert result.evidence.context.localized_workspace_folder == tmp_path
        assert result.evidence.attempt_id == "attempt-one"
        assert result.evidence.observed_at == 100
        assert not result.absence_authoritative
        assert all(call[0][0] == str(tmp_path / "selected-cli") for call in environment.calls)
        assert all(call[1]["PROVIDER_FIXTURE"] == "selected" for call in environment.calls)
        assert all(call[2] == tmp_path for call in environment.calls)
        assert probe_calls[0][0] == str(tmp_path / "selected-cli")
        assert probe_calls[0][1] is not None
        assert probe_calls[0][1]["PROVIDER_FIXTURE"] == "selected"
        assert probe_calls[0][2] == tmp_path
        if backend is ConversationBackendKey.claude:
            assert result.rows[0].selection_kind is SelectionKind.provider_alias
            assert result.rows[0].supported_reasoning_efforts is None
            assert result.rows[0].identity_preserving_launch_token is None
            assert result.rows[1].supported_reasoning_efforts == ()
            assert result.rows[1].resolved_model_id == "claude-fable-4"
        else:
            assert result.rows[0].submitted_token == "gpt-6.1-terra"
        # Secrets are not captured as proof or exposed in advisory observations.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "private-fixture-secret")
        changed = await reader.capture_context(backend, workspace=binding(tmp_path))
        assert changed.context_ref != result.evidence.context.context_ref
        assert changed.provider_scope is None
        assert "private-fixture-secret" not in repr(changed)

    asyncio.run(run())


def test_observation_preserves_affirmative_credential_denial_and_unknown_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launches = launch_fixture(tmp_path, monkeypatch)

    class StatusEnvironment(ScriptedLaunchEnvironment):
        reply = '{"loggedIn":false}'

        async def run(
            self,
            argv: Sequence[str],
            *,
            timeout_seconds: float,
            environment_overrides: Mapping[str, str] | None = None,
            working_directory: Path | None = None,
        ) -> CommandOutcome:
            if tuple(argv[1:]) == ("auth", "status", "--json"):
                return CommandOutcome(0, self.reply, "private stderr must not cross")
            return await super().run(
                argv,
                timeout_seconds=timeout_seconds,
                environment_overrides=environment_overrides,
                working_directory=working_directory,
            )

    async def catalog(
        executable: str,
        *,
        environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
    ) -> ClaudeModelCatalog:
        del executable, environment, working_directory
        return ClaudeModelCatalog(
            (ClaudeModel("opus", "Opus", "claude-opus-5", ()),),
            (),
            provider_scope="owned-probe",
        )

    monkeypatch.setattr(observer, "probe_claude_model_catalog", catalog)

    async def run() -> None:
        environment = StatusEnvironment()
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps,
            panels_server_url="http://fixture.invalid",
            launches=lambda: launches,
            environment=environment,
        )
        for reply, expected in (
            ('{"loggedIn":false}', PrerequisiteStatus.unavailable),
            ("malformed status", PrerequisiteStatus.unknown),
        ):
            environment.reply = reply
            result = await reader.observe(
                ConversationBackendKey.claude,
                workspace=binding(tmp_path),
                attempt_id=reply,
                deadline=asyncio.get_running_loop().time() + 10,
            )
            assert isinstance(result, CatalogObservation)
            assert result.credentials is expected
            assert "private stderr" not in repr(result)

    asyncio.run(run())


@pytest.mark.parametrize("token", ["bin/fixture-cli", "fixture-cli"])
def test_prepared_executable_uses_authorized_workspace_and_launch_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    launches = launch_fixture(tmp_path, monkeypatch)
    panels_cwd, workspace = tmp_path / "panels", tmp_path / "workspace"
    for directory in (panels_cwd, workspace):
        (directory / "bin").mkdir(parents=True)
        binary = directory / "bin" / "fixture-cli"
        binary.write_text("same version, distinct executable")
        binary.chmod(0o700)
    monkeypatch.chdir(panels_cwd)
    launches = replace(
        launches,
        codex=replace(
            launches.codex, argv=(token, "app-server"), environment_overrides=(("PATH", "bin"),)
        ),
    )
    environment = ScriptedLaunchEnvironment()

    async def run() -> None:
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps,
            panels_server_url="http://fixture.invalid",
            launches=lambda: launches,
            environment=environment,
        )
        first = await reader.capture_context(
            ConversationBackendKey.codex, workspace=binding(workspace)
        )
        from planner.conversation.backends.contracts import BackendEmissionInputs

        prepared = BackendEmissionInputs(
            "exact-token",
            None,
            workspace,
            token,
            {"PATH": "bin"},
        )
        assert reader.verify_prepared_launch(first, prepared)
        assert not reader.verify_prepared_launch(
            first,
            replace(prepared, workspace=panels_cwd, executable="other-cli"),
        )
        assert environment.calls == []

    asyncio.run(run())
