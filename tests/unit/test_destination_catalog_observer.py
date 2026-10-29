"""Destination launch inputs and physical probe settlement are separate boundaries."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from planner.conversation.backends.claude_model_catalog import ClaudeModel, ClaudeModelCatalog
from planner.conversation.backends.claude_settings import (
    ClaudeSettingsScopeRegistry,
    ClaudeSettingsSnapshot,
)
from planner.conversation.backends.codex_app_server.model_catalog import (
    CodexModel,
    CodexModelCatalog,
)
from planner.conversation.backends.probe_lifetime import owned_probe
from planner.conversation.contracts import ConversationBackendKey, WorkspacePathPolicy
from planner.conversation.model_resolution import observer
from planner.conversation.model_resolution.contracts import (
    CatalogObservation,
    CatalogUnavailable,
    CatalogWorkspaceBinding,
    PrerequisiteStatus,
    SelectionKind,
    ValidationReason,
)
from planner.conversation.production_backends import (
    ProductionBackendLaunches,
    production_backend_launches,
)
from planner.conversation.snapshot import (
    CommandOutcome,
    SubprocessBackendProbeEnvironment,
)
from planner.executor.workspace import localize_workspace
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
    monkeypatch.setattr(observer, "_claude_managed_directory", lambda: tmp_path / "managed")
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
            launches.codex, argv=(str(executable), "app-server"),
            environment_overrides=(("PROVIDER_FIXTURE", "selected"),),
        ),
        claude=replace(
            launches.claude, claude_executable=executable,
            environment_overrides=(("PROVIDER_FIXTURE", "selected"),),
        ),
    )


def synthetic_scope(registry: ClaudeSettingsScopeRegistry | None) -> str:
    assert registry is not None
    return registry.retain(ClaudeSettingsSnapshot({}, (), "claude-opus-5", None))


def binding(path: Path) -> CatalogWorkspaceBinding:
    return CatalogWorkspaceBinding(path, WorkspacePathPolicy.native_absolute, None)


@pytest.mark.parametrize("change", ["stable", "configuration", "provider"])
def test_real_nested_claude_probe_preserves_provider_and_ordinary_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    """Keep both real probe owners and the actual settings comparison callback."""
    import json

    from tests.unit.test_claude_settings_snapshot import ScriptedTransport

    from planner.conversation.backends import claude_model_catalog
    from planner.conversation.model_resolution import diagnostics

    for key in tuple(os.environ):
        if key.startswith(("PLAN_", "PANELS_", "HERMES_", "CLAUDE_", "CODEX_", "ANTHROPIC_")):
            monkeypatch.delenv(key)
    launches = launch_fixture(tmp_path, monkeypatch)
    config = tmp_path / "claude-home" / "settings.json"
    config.parent.mkdir()
    (tmp_path / "managed" / "managed-settings.d").mkdir(parents=True)
    filesystem: list[Any] = []
    descriptors: dict[int, str] = {}
    original_open, original_fdopen = os.open, os.fdopen

    def opened(path: Any, *args: Any, **kwargs: Any) -> int:
        filesystem.append(("open", str(path)))
        descriptor = original_open(path, *args, **kwargs)
        descriptors[descriptor] = str(path)
        return descriptor

    class Stream:
        def __init__(self, stream: Any, path: str) -> None:
            self.stream, self.path = stream, path

        def __getattr__(self, name: str) -> Any:
            return getattr(self.stream, name)

        def __enter__(self) -> Stream:
            self.stream.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.stream.__exit__(*args)

        def read(self, *args: Any) -> Any:
            filesystem.append(("read", self.path))
            return self.stream.read(*args)

    def fdopened(descriptor: int, *args: Any, **kwargs: Any) -> Stream:
        return Stream(original_fdopen(descriptor, *args, **kwargs), descriptors[descriptor])

    def trace_operation(name: str) -> Any:
        original = getattr(os, name)

        def operation(path: Any, *args: Any, **kwargs: Any) -> Any:
            filesystem.append((name, descriptors.get(path, path) if isinstance(path, int)
                               else str(path)))
            return original(path, *args, **kwargs)

        return operation

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "fdopen", fdopened)
    for name in ("stat", "lstat", "readlink", "scandir", "access", "fstat"):
        monkeypatch.setattr(os, name, trace_operation(name))
    transports: list[ScriptedTransport] = []
    task_evidence: list[tuple[bool, bool, bool]] = []
    failures: list[dict[str, Any]] = []
    original_fail = diagnostics.DiagnosticSink._fail

    def observe_failure(sink: Any, status: Any) -> None:
        caller = sys._getframe(1)
        lease = diagnostics._observation.get()
        failures.append({
            "status": status,
            "caller": caller.f_code.co_name,
            "source": "diagnostics.py:" + str(caller.f_lineno),
            "task_differs_from_lease": (
                lease is not None and lease.task is not asyncio.current_task()
            ),
            "thread_matches_lease": lease is not None and lease.thread == threading.get_ident(),
        })
        original_fail(sink, status)

    monkeypatch.setattr(diagnostics.DiagnosticSink, "_fail", observe_failure)

    class ExternalTransport(ScriptedTransport):
        async def send(self, data: str) -> None:
            frame = json.loads(data)
            if frame.get("request", {}).get("subtype") == "list_models":
                lease = diagnostics._observation.get()
                sink = diagnostics._sink.get()
                if lease is not None and sink is not None:
                    task_evidence.append((
                        lease.task is not asyncio.current_task(),
                        lease.thread == threading.get_ident(),
                        sink._failure is None,
                    ))
                if change == "configuration":
                    config.write_text('{"model":"sonnet"}')
                elif change == "provider":
                    self.settings["sources"][0]["settings"]["model"] = "sonnet"
            await super().send(data)

    def transport_factory(*, prompt: Any, options: Any) -> ExternalTransport:
        del prompt
        transport = ExternalTransport(options)
        transports.append(transport)
        return transport

    monkeypatch.setattr(claude_model_catalog, "SubprocessCLITransport", transport_factory)

    async def run() -> None:
        results: list[Any] = []
        calls: list[Any] = []
        traces: list[Any] = []
        original_compare = diagnostics._compare

        def diagnostic_failure(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("synthetic diagnostic-only failure")

        def ordinary_facts(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: ("ephemeral-reference" if key in {
                    "context_ref", "observation_id", "observation_revision", "provider_scope",
                } and isinstance(item, str) else ordinary_facts(item))
                        for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [ordinary_facts(item) for item in value]
            return value

        for mode in ("off", "on", "failure"):
            enabled = mode != "off"
            config.write_text('{"model":"opus"}')
            filesystem.clear()
            descriptors.clear()
            environment = ScriptedLaunchEnvironment()
            reader = observer.ProductionCatalogObserver(
                destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
                launches=lambda: launches, environment=environment, now=lambda: 100.0,
            )
            sink = diagnostics.DiagnosticSink(clock=lambda: 0.0)
            monkeypatch.setattr(diagnostics, "_compare",
                                diagnostic_failure if mode == "failure" else original_compare)
            scope = diagnostics.diagnostic_scope(sink) if enabled else contextlib.nullcontext()
            with scope:
                result = await reader.observe(
                    ConversationBackendKey.claude, workspace=binding(tmp_path), attempt_id="nested",
                    deadline=asyncio.get_running_loop().time() + 10,
                )
            results.append(result)
            calls.append(environment.calls)
            traces.append(list(filesystem))
            if enabled:
                record = sink.finish()
                assert failures == []
                assert task_evidence[-1] == (True, True, True)
                if mode == "failure" and change != "provider":
                    assert record == diagnostics.unavailable_record("internal_error")
                else:
                    assert record["diagnostic_status"] == (
                        "capture_missing" if change == "provider" else "compared"
                    )
                    assert record["provider_source_snapshot"] == (
                        "changed" if change == "provider" else "equal"
                    )
                    if change != "provider":
                        assert record["roles"]["settings"]["semantic_content"] == (
                            "changed" if change == "configuration" else "equal"
                        )
                print("finite nested reproduction: " + json.dumps({
                    "change": change,
                    "mode": mode,
                    "callback_task_differs_from_lease": task_evidence[0][0],
                    "callback_thread_matches_lease": task_evidence[0][1],
                    "failure_absent_before_callback": task_evidence[0][2],
                    "diagnostic_status": record["diagnostic_status"],
                    "receipt": record,
                    "failure_calls": failures,
                    "ordinary_reason": getattr(result, "reason", "observation"),
                }, default=str, sort_keys=True))
        assert calls[0] == calls[1] == calls[2]
        assert traces[0] == traces[1] == traces[2]
        assert ordinary_facts(asdict(results[0])) == ordinary_facts(asdict(results[1]))
        assert ordinary_facts(asdict(results[0])) == ordinary_facts(asdict(results[2]))
        if change == "stable":
            assert all(isinstance(result, CatalogObservation) for result in results)
            assert results[0].rows == results[1].rows
        else:
            expected = (ValidationReason.context_changed if change == "configuration"
                        else ValidationReason.catalog_unavailable)
            assert all(isinstance(result, CatalogUnavailable) and result.reason is expected
                       for result in results)
        subtypes = [[frame["request"]["subtype"] for frame in transport.frames
                     if frame.get("type") == "control_request"] for transport in transports]
        assert subtypes == [["initialize", "get_settings", "list_models", "get_settings"]] * 3
        assert all(not transport.is_ready() for transport in transports)
        assert all(all(frame.get("type") != "user" for frame in transport.frames)
                   for transport in transports)

    asyncio.run(run())


@pytest.mark.parametrize("ending", ["cancel", "timeout"])
def test_real_nested_provider_permit_survives_until_physical_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str,
) -> None:
    from tests.unit.test_claude_settings_snapshot import ScriptedTransport

    from planner.conversation.backends import claude_model_catalog
    from planner.conversation.model_resolution import diagnostics

    launches = launch_fixture(tmp_path, monkeypatch)
    (tmp_path / "managed" / "managed-settings.d").mkdir(parents=True)

    async def run() -> None:
        cleanup_started, cleanup_release = asyncio.Event(), asyncio.Event()
        transports: list[ScriptedTransport] = []
        permits: list[Any] = []

        class ExternalTransport(ScriptedTransport):
            async def send(self, data: str) -> None:
                import json

                if json.loads(data).get("request", {}).get("subtype") == "list_models":
                    self.pause = True
                    permits.append(diagnostics._provider_operation.get())
                await super().send(data)

            async def close(self) -> None:
                cleanup_started.set()
                await cleanup_release.wait()
                await super().close()

        def factory(*, prompt: Any, options: Any) -> ExternalTransport:
            transport = ExternalTransport(options)
            transports.append(transport)
            return transport

        monkeypatch.setattr(claude_model_catalog, "SubprocessCLITransport", factory)
        if ending == "timeout":
            monkeypatch.setattr(claude_model_catalog, "MODEL_CATALOG_TIMEOUT_SECONDS", 0.05)
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
            launches=lambda: launches, environment=ScriptedLaunchEnvironment(),
        )
        sink = diagnostics.DiagnosticSink(clock=lambda: 0.0)
        with diagnostics.diagnostic_scope(sink):
            task = asyncio.create_task(reader.observe(
                ConversationBackendKey.claude, workspace=binding(tmp_path), attempt_id="cleanup",
                deadline=asyncio.get_running_loop().time() + 10,
            ))
            while not transports:
                await asyncio.sleep(0)
            await asyncio.wait_for(transports[0].requests.wait(), 2)
            if ending == "cancel":
                task.cancel()
            await asyncio.wait_for(cleanup_started.wait(), 2)
            assert not task.done()
            assert permits[0] is not None and permits[0].active
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
                assert isinstance(result, CatalogUnavailable)
                assert result.reason is ValidationReason.catalog_unavailable
        assert not transports[0].is_ready()
        permit = permits[0]
        assert not permit.active
        assert (permit.sink, permit.lease, permit.observer, permit.parent_task, permit.task) == (
            None, None, None, None, None,
        )
        assert sink.finish()["diagnostic_status"] == "capture_missing"

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
        executable: str, *, environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
    ) -> CodexModelCatalog:
        probe_calls.append((executable, environment, working_directory))
        clock[0] = 120.0
        return CodexModelCatalog(
            (CodexModel("gpt-6.1-terra", "Terra", ("medium",), "medium", True),),
            ("medium",),
        )

    async def claude(
        executable: str, *, environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
        claude_scope_registry: ClaudeSettingsScopeRegistry | None = None,
    ) -> ClaudeModelCatalog:
        probe_calls.append((executable, environment, working_directory))
        clock[0] = 120.0
        return ClaudeModelCatalog(
            (ClaudeModel("fable", "Fable", "claude-fable-5", None),
             ClaudeModel("claude-fable-4", "Fable 4", "claude-fable-4", ())), (),
            provider_scope=synthetic_scope(claude_scope_registry),
        )

    monkeypatch.setattr(observer, "probe_codex_model_catalog", codex)
    monkeypatch.setattr(observer, "probe_claude_model_catalog", claude)

    async def run() -> None:
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
            launches=lambda: launches, environment=environment, now=lambda: clock[0],
        )
        result = await reader.observe(
            backend, workspace=binding(tmp_path), attempt_id="attempt-one",
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
        # A secret influences private equality, but no secret or digest crosses it.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "private-fixture-secret")
        changed = await reader.capture_context(backend, workspace=binding(tmp_path))
        assert changed.provider_scope != result.evidence.context.provider_scope
        assert "private-fixture-secret" not in repr(changed)

    asyncio.run(run())


def test_configuration_change_during_observation_is_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launches = launch_fixture(tmp_path, monkeypatch)
    config = tmp_path / ".claude" / "settings.json"
    config.parent.mkdir()
    config.write_text("{}")

    async def claude(
        executable: str, *, environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
        claude_scope_registry: ClaudeSettingsScopeRegistry | None = None,
    ) -> ClaudeModelCatalog:
        del executable, environment, working_directory
        config.write_text('{"changed":true}')
        return ClaudeModelCatalog(
            (ClaudeModel("opus", "Opus", "claude-opus-5", ()),), (),
            provider_scope=synthetic_scope(claude_scope_registry),
        )

    monkeypatch.setattr(observer, "probe_claude_model_catalog", claude)

    async def run() -> None:
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
            launches=lambda: launches, environment=ScriptedLaunchEnvironment(),
        )
        result = await reader.observe(
            ConversationBackendKey.claude, workspace=binding(tmp_path), attempt_id="a",
            deadline=asyncio.get_running_loop().time() + 10,
        )
        assert isinstance(result, CatalogUnavailable)
        assert result.reason is ValidationReason.context_changed

    asyncio.run(run())


def test_workspace_localization_reuses_execution_directory_checks(tmp_path: Path) -> None:
    native = tmp_path / "native"
    native.mkdir()
    assert localize_workspace(
        Path("/foreign/native"), workspace_home=tmp_path, workspace_home_relative="native"
    ) == native
    shortcut = tmp_path / "shortcut"
    shortcut.symlink_to(native, target_is_directory=True)
    assert localize_workspace(
        Path("/foreign/shortcut"), workspace_home=tmp_path, workspace_home_relative="shortcut"
    ) == native
    for relative in ("../escape", "native/../native", "/native", "native\\child"):
        with pytest.raises(ValueError):
            localize_workspace(Path("/foreign/native"), workspace_home=tmp_path,
                               workspace_home_relative=relative)
    with pytest.raises(FileNotFoundError):
        localize_workspace(tmp_path / "missing", workspace_home=tmp_path)


@pytest.mark.parametrize("timeout", [False, True])
def test_probe_capacity_stays_owned_until_cleanup_despite_repeated_cancel(timeout: bool) -> None:
    async def run() -> None:
        entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        capacity = asyncio.Semaphore(1)

        async def sdk_operation() -> None:
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                # Mirrors the installed SDK transport's AnyIO cleanup shield.
                with anyio.CancelScope(shield=True):
                    cleanup.set()
                    await release.wait()

        async def request() -> None:
            async with capacity:
                await owned_probe(sdk_operation, timeout_seconds=0.01 if timeout else 10)

        task = asyncio.create_task(request())
        await entered.wait()
        if not timeout:
            task.cancel()
        await cleanup.wait()
        task.cancel()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert capacity.locked()
        release.set()
        with pytest.raises((asyncio.CancelledError, TimeoutError)):
            await task
        assert not capacity.locked()

    asyncio.run(run())


def test_subprocess_timeout_reaps_its_group_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        cleanup, release = asyncio.Event(), asyncio.Event()
        signaled: list[int] = []

        class Process:
            pid = 12345
            returncode: int | None = None

            async def communicate(self) -> tuple[bytes, bytes]:
                await asyncio.Event().wait()
                return b"", b""

            async def wait(self) -> int:
                cleanup.set()
                await release.wait()
                self.returncode = -9
                return -9

        process = Process()

        async def create(*args: Any, **kwargs: Any) -> Process:
            del args
            assert kwargs["start_new_session"]
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        monkeypatch.setattr(
            "planner.conversation.snapshot.os.killpg", lambda pid, sig: signaled.append(pid)
        )
        task = asyncio.create_task(SubprocessBackendProbeEnvironment().run(
            ("fixture-cli", "--version"), timeout_seconds=0.01
        ))
        await cleanup.wait()
        assert not task.done()
        assert signaled == [process.pid]
        release.set()
        outcome = await task
        assert outcome.exit_code == -1
        assert process.returncode == -9

    asyncio.run(run())


def test_generation_does_not_claim_continuity_for_alternate_provider_sources(
    tmp_path: Path,
) -> None:
    from planner.conversation.backends.codex_app_server.login import (
        CodexLoginGeneration,
    )

    generation = CodexLoginGeneration("fixture-account", "fixture-refresh")
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    # A real, nonsecret stale file must not stand in for keyring/auto credentials.
    (home / "auth.json").write_text(
        '{"tokens":{"account_id":"fixture-account"},"last_refresh":"fixture-refresh"}'
    )
    environment = {"CODEX_HOME": str(home)}
    assert observer._account_generation(
        ConversationBackendKey.codex, environment, workspace
    ) == generation
    config = home / "config.toml"
    for document in (
        'model_provider="alternate"', '[model_providers.openai]\nbase_url="https://fixture.invalid"',
        'profile="alternate"',
        'cli_auth_credentials_store="keyring"',
        'cli_auth_credentials_store="auto"',
        'cli_auth_credentials_store="ephemeral"',
    ):
        config.write_text(document)
        assert observer._account_generation(
            ConversationBackendKey.codex, environment, workspace
        ) is None
        assert not observer._uses_default_codex_account(environment, workspace)
    config.write_text('cli_auth_credentials_store="file"')
    assert observer._account_generation(
        ConversationBackendKey.codex, environment, workspace
    ) == generation
    config.unlink()
    project = workspace / ".codex" / "config.toml"
    project.parent.mkdir()
    project.write_text('model_provider="alternate"')
    assert observer._account_generation(
        ConversationBackendKey.codex, environment, workspace
    ) is None
    project.unlink()
    environment["OPENAI_API_KEY"] = "fixture-secret"
    assert observer._account_generation(
        ConversationBackendKey.codex, environment, workspace
    ) is None
    environment.pop("OPENAI_API_KEY")
    environment["CODEX_CONFIG_PATH"] = str(tmp_path / "unknown-source")
    assert observer._configuration_revisions(
        ConversationBackendKey.codex, workspace, environment
    ) is None
    environment.pop("CODEX_CONFIG_PATH")
    config.write_text('include="untracked.toml"')
    assert observer._configuration_revisions(
        ConversationBackendKey.codex, workspace, environment
    ) is None


def test_snapshot_executable_uses_the_production_claude_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "selected-claude"
    selected.write_text("fixture, never executed")
    monkeypatch.setenv("PLAN_CLAUDE_EXECUTABLE", str(selected))
    monkeypatch.setattr("planner.conversation.snapshot.shutil.which", lambda name: "/other/claude")
    assert SubprocessBackendProbeEnvironment().executable_path("claude") == str(selected)


def test_observation_preserves_affirmative_credential_denial_and_unknown_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launches = launch_fixture(tmp_path, monkeypatch)

    class StatusEnvironment(ScriptedLaunchEnvironment):
        reply = '{"loggedIn":false}'

        async def run(
            self, argv: Sequence[str], *, timeout_seconds: float,
            environment_overrides: Mapping[str, str] | None = None,
            working_directory: Path | None = None,
        ) -> CommandOutcome:
            if tuple(argv[1:]) == ("auth", "status", "--json"):
                return CommandOutcome(0, self.reply, "private stderr must not cross")
            return await super().run(
                argv, timeout_seconds=timeout_seconds,
                environment_overrides=environment_overrides, working_directory=working_directory,
            )

    async def catalog(
        executable: str, *, environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
        claude_scope_registry: ClaudeSettingsScopeRegistry | None = None,
    ) -> ClaudeModelCatalog:
        del executable, environment, working_directory
        return ClaudeModelCatalog(
            (ClaudeModel("opus", "Opus", "claude-opus-5", ()),), (),
            provider_scope=synthetic_scope(claude_scope_registry),
        )

    monkeypatch.setattr(observer, "probe_claude_model_catalog", catalog)

    async def run() -> None:
        environment = StatusEnvironment()
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
            launches=lambda: launches, environment=environment,
        )
        for reply, expected in (
            ('{"loggedIn":false}', PrerequisiteStatus.unavailable),
            ('malformed status', PrerequisiteStatus.unknown),
        ):
            environment.reply = reply
            result = await reader.observe(
                ConversationBackendKey.claude, workspace=binding(tmp_path), attempt_id=reply,
                deadline=asyncio.get_running_loop().time() + 10,
            )
            assert isinstance(result, CatalogObservation)
            assert result.credentials is expected
            assert "private stderr" not in repr(result)

    asyncio.run(run())


@pytest.mark.parametrize("token", ["bin/fixture-cli", "fixture-cli"])
def test_executable_identity_uses_launch_cwd_and_relative_effective_path(
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
        codex=replace(launches.codex, argv=(token, "app-server"),
                      environment_overrides=(("PATH", "bin"),)),
    )
    environment = ScriptedLaunchEnvironment()

    async def run() -> None:
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
            launches=lambda: launches, environment=environment,
        )
        first = await reader.capture_context(
            ConversationBackendKey.codex, workspace=binding(workspace)
        )
        assert first.executable_identity is not None
        assert first.executable_identity.startswith(str(workspace / "bin" / "fixture-cli") + ":")
        assert str(panels_cwd) not in first.executable_identity
        assert environment.calls[0][0] == (token, "--version")
        replacement = workspace / "bin" / "replacement"
        replacement.write_text("same version, distinct executable")
        replacement.chmod(0o700)
        replacement.replace(workspace / "bin" / "fixture-cli")
        second = await reader.capture_context(
            ConversationBackendKey.codex, workspace=binding(workspace)
        )
        assert first.executable_version == second.executable_version == "2.1.267"
        assert second.executable_identity != first.executable_identity
        assert second.context_ref != first.context_ref

    asyncio.run(run())


def test_private_diagnostic_preserves_ordinary_reads_refusal_and_admission_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.conversation.model_resolution import diagnostics

    launches = launch_fixture(tmp_path, monkeypatch)
    config = tmp_path / ".claude" / "settings.json"
    target = tmp_path / "linked-settings"
    target.mkdir()
    config.parent.symlink_to(target, target_is_directory=True)
    (tmp_path / "managed" / "managed-settings.d").mkdir(parents=True)
    trace: list[tuple[str, str]] = []
    descriptors: dict[int, str] = {}
    real_open, real_fdopen, real_fstat = os.open, os.fdopen, os.fstat

    def counted_open(path: Any, *args: Any, **kwargs: Any) -> int:
        trace.append(("open", str(path)))
        descriptor = real_open(path, *args, **kwargs)
        descriptors[descriptor] = str(path)
        return descriptor

    def counted_fstat(descriptor: int) -> os.stat_result:
        trace.append(("fstat", descriptors.get(descriptor, "untracked")))
        return real_fstat(descriptor)

    class TracedStream:
        def __init__(self, stream: Any, path: str) -> None:
            self.stream, self.path = stream, path

        def __getattr__(self, name: str) -> Any:
            return getattr(self.stream, name)

        def __enter__(self) -> TracedStream:
            self.stream.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.stream.__exit__(*args)

        def read(self, *args: Any) -> Any:
            trace.append(("read", self.path))
            return self.stream.read(*args)

    def counted_fdopen(descriptor: int, *args: Any, **kwargs: Any) -> TracedStream:
        return TracedStream(real_fdopen(descriptor, *args, **kwargs), descriptors[descriptor])

    def trace_path_operation(name: str) -> Any:
        original = getattr(os, name)

        def operation(path: Any, *args: Any, **kwargs: Any) -> Any:
            trace.append((name, str(path)))
            return original(path, *args, **kwargs)

        return operation

    async def claude(
        executable: str, *, environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
        claude_scope_registry: ClaudeSettingsScopeRegistry | None = None,
        **kwargs: Any,
    ) -> ClaudeModelCatalog:
        del executable, environment, working_directory
        config.write_text('{"changed":true}')
        return ClaudeModelCatalog(
            (ClaudeModel("opus", "Opus", "claude-opus-5", ()),), (),
            provider_scope=synthetic_scope(claude_scope_registry),
        )

    def diagnostic_failure(*args: Any) -> Any:
        raise RuntimeError("PRIVATE_SENTINEL")

    monkeypatch.setattr(os, "open", counted_open)
    monkeypatch.setattr(os, "fdopen", counted_fdopen)
    monkeypatch.setattr(os, "fstat", counted_fstat)
    for operation in ("stat", "lstat", "readlink", "scandir", "access"):
        monkeypatch.setattr(os, operation, trace_path_operation(operation))
    monkeypatch.setattr(observer, "probe_claude_model_catalog", claude)
    traces: list[tuple[list[tuple[str, str]], list[
        tuple[tuple[str, ...], dict[str, str], Path | None]
    ]]] = []

    async def run() -> None:
        for mode in ("off", "on", "failure"):
            config.write_text("{}")
            trace.clear()
            descriptors.clear()
            environment = ScriptedLaunchEnvironment()
            reader = observer.ProductionCatalogObserver(
                destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
                launches=lambda: launches, environment=environment,
            )
            sink = diagnostics.DiagnosticSink(clock=lambda: 0.0)
            if mode == "failure":
                monkeypatch.setattr(diagnostics, "_compare", diagnostic_failure)
            scope = (
                diagnostics.diagnostic_scope(sink) if mode != "off" else contextlib.nullcontext()
            )
            with scope:
                await reader.capture_context(
                    ConversationBackendKey.claude, workspace=binding(tmp_path),
                )
                result = await reader.observe(
                    ConversationBackendKey.claude, workspace=binding(tmp_path), attempt_id="a",
                    deadline=asyncio.get_running_loop().time() + 10,
                )
                await reader.capture_context(
                    ConversationBackendKey.claude, workspace=binding(tmp_path),
                )
            assert isinstance(result, CatalogUnavailable)
            assert result.reason is ValidationReason.context_changed
            record = sink.finish()
            if mode == "on":
                assert record["roles"] == {
                    role: {"source_validity": "equal", "semantic_content": (
                        "changed" if role == "settings" else "equal"
                    )}
                    for role in ("selected_global_state", "settings", "managed_settings",
                                 "remote_policy_cache")
                }
            else:
                assert record == (
                    diagnostics.unavailable_record("capture_missing") if mode == "off"
                    else diagnostics.unavailable_record("internal_error")
                )
            traces.append((list(trace), environment.calls))
        assert traces[0] == traces[1] == traces[2]
        operations = {operation for operation, _ in traces[0][0]}
        assert {
            "open", "read", "fstat", "stat", "lstat", "readlink", "scandir", "access",
        } <= operations

    asyncio.run(run())


@pytest.mark.parametrize("change", [
    "stable", "environment", "selected_content", "malformed_initial", "malformed_late",
])
def test_canonical_shared_limit_preserves_service_and_prepared_launch_admission(
    tmp_path: Path, tmp_db: Any, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from planner.conversation.admission import service as admission
    from planner.conversation.backend_state import BackendStateStore
    from planner.conversation.backends.contracts import (
        AdmissionRefused,
        BackendEmissionInputs,
        current_emission_check,
    )
    from planner.conversation.model_resolution import diagnostics
    from planner.conversation.model_resolution.contracts import ModelSelection, ValidationState
    from planner.conversation.model_resolution.data import ModelResolutionStore
    from planner.conversation.model_resolution.service import ModelResolutionService

    launch_fixture(tmp_path, monkeypatch)
    executable = str(tmp_path / "selected-cli")
    managed = tmp_path / "managed" / "managed-settings.d"
    managed.mkdir(parents=True)
    for index in range(20):
        (managed / f"{index:02}.json").write_text("{}")
    selected = tmp_path / ".claude" / "settings.json"
    selected.parent.mkdir()
    database_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(time, "time", lambda: 1000)
    results: list[Any] = []

    async def run() -> None:
        for enabled in (False, True):
            selected.write_text("{broken" if change == "malformed_initial" else '{"private":1}')
            launch_environment = {"HOME": str(tmp_path), "PRIVATE": "before"}

            async def provider(
                *args: Any, claude_scope_registry: ClaudeSettingsScopeRegistry | None = None,
                launch_environment: dict[str, str] = launch_environment,
                **kwargs: Any,
            ) -> ClaudeModelCatalog:
                permit = kwargs.pop("_diagnostic_provider_operation", None)
                if permit is not None:
                    async def child() -> ClaudeModelCatalog:
                        with permit.claim():
                            return await provider(
                                *args, claude_scope_registry=claude_scope_registry,
                                launch_environment=launch_environment, **kwargs,
                            )
                    return await owned_probe(child, timeout_seconds=10)
                if change == "environment":
                    launch_environment["PRIVATE"] = "after"
                elif change == "selected_content":
                    selected.write_text('{"private":2}')
                elif change == "malformed_late":
                    selected.write_text("{broken")
                diagnostics.offer_provider_comparison(True)
                return ClaudeModelCatalog(
                    (ClaudeModel("opus", "Opus", "claude-opus-5", (), True),), (),
                    provider_scope=synthetic_scope(claude_scope_registry),
                )

            monkeypatch.setattr(observer, "probe_claude_model_catalog", provider)
            reader = observer.ProductionCatalogObserver(
                destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
                environment=ScriptedLaunchEnvironment(), now=lambda: 1000,
            )
            monkeypatch.setattr(
                reader, "_launch", lambda _, env=launch_environment: (executable, dict(env)),
            )
            service = ModelResolutionService(
                snapshots=cast(Any, None), store=ModelResolutionStore(database_path),
                backend_state=BackendStateStore(database_path), now=lambda: 1000, observer=reader,
            )
            selection = ModelSelection(
                TicketLocale.vps, ConversationBackendKey.claude, "opus",
                SelectionKind.provider_alias, None, "fixture-selection",
            )
            sink = diagnostics.DiagnosticSink(clock=lambda: 0.0)
            scope = diagnostics.diagnostic_scope(sink) if enabled else contextlib.nullcontext()
            with scope:
                result = await service.validate_selection(
                    selection, workspace=binding(tmp_path), attempt_id="fixture-attempt",
                )
            results.append((
                result.state, result.reason, result.launch_token, result.resolved_model_id,
            ))
            record = sink.finish()
            if change == "stable":
                assert result.state is ValidationState.compatible
                expectation = admission.expectation_from_validation(
                    result, attempt_id="fixture-attempt", conversation_id="fixture-conversation",
                    disabled_models_revision="fixture-policy",
                )
                prepared = BackendEmissionInputs(
                    expectation.launch_token, expectation.reasoning_effort, tmp_path,
                    executable, dict(launch_environment),
                )
                with admission.admission_scope(expectation, observer=reader):
                    check = current_emission_check()
                    assert check is not None
                    check(prepared)
                    for altered in (
                        replace(prepared, model="foreign-model"),
                        replace(prepared, environment={"PRIVATE": "foreign"}),
                    ):
                        with pytest.raises(AdmissionRefused):
                            check(altered)
            else:
                assert result.state is ValidationState.unresolved
                assert result.reason is (
                    ValidationReason.configuration_scope_unknown if change == "malformed_initial"
                    else ValidationReason.context_changed
                )
                with pytest.raises(AdmissionRefused):
                    admission.expectation_from_validation(
                        result, attempt_id="fixture-attempt",
                        conversation_id="fixture-conversation",
                        disabled_models_revision="fixture-policy",
                    )
            if enabled:
                assert record["launch_environment"] == (
                    "changed" if change == "environment" else "unavailable"
                    if change.startswith("malformed") else "equal"
                )
                assert record["diagnostic_status"] == (
                    "capture_missing" if change.startswith("malformed") else "limit_documents"
                )
        assert results[0] == results[1]

    asyncio.run(run())
