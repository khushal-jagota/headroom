from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp.schema import (
    AgentMessageChunk,
    LoadSessionRequest,
    PromptResponse,
    SessionNotification,
    TextContentBlock,
)
from acp.transports import default_environment

import planner.environments.hermes_smoke as hermes_smoke
from planner.conversation import (
    HERMES_INHERITED_ENVIRONMENT_NAMES,
    build_confined_child_environment,
)
from planner.conversation.hermes_backend_configuration import hermes_src_root
from planner.environments.contracts import EnvironmentManifest, EnvironmentValidationError
from planner.environments.hermes_smoke import (
    HermesSmokeInvocation,
    smoke_prepared_nonproduction_instances,
)


def test_official_acp_smoke_child_keeps_only_validated_credentials_and_isolated_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    ambient = {
        **default_environment(),
        "ANTHROPIC_API_KEY": "anthropic-file-secret",
        "OPENAI_API_KEY": "openai-file-secret",
        "GOOGLE_API_KEY": "google-not-in-file",
        "HERMES_HOME": "/ambient/hermes-home",
        "HERMES_SESSION_KEY": "ambient-session",
        "PLAN_DB_PATH": "/ambient/planner.db",
        "PYTHONPATH": "/ambient/pythonpath",
    }

    async def async_noop(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def new_session(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return SimpleNamespace(session_id="stored-session")

    class CapturingFactory:
        def __init__(self, definition: Any) -> None:
            self.definition = definition
            captured["definition"] = definition

        async def create(
            self,
            employee: Any,
            generation: int,
            update_ingress: Any,
            permission_callback: Any,
            death_callback: Any,
        ) -> Any:
            del generation, permission_callback, death_callback
            captured["environment"] = build_confined_child_environment(
                self.definition, employee, ambient_environment=ambient
            )

            async def prompt(request: Any) -> PromptResponse:
                await update_ingress(
                    SessionNotification(
                        session_id=request.session_id,
                        update=AgentMessageChunk(
                            session_update="agent_message_chunk",
                            content=TextContentBlock(type="text", text="ok"),
                        ),
                    )
                )
                return PromptResponse(stop_reason="end_turn")

            return SimpleNamespace(
                initialize=async_noop,
                new_session=new_session,
                prompt=prompt,
                load_session=async_noop,
                close=async_noop,
            )

    monkeypatch.setattr(hermes_smoke, "SdkAcpEmployeeChildFactory", CapturingFactory)
    home = (tmp_path / "isolated-hermes-home").resolve()
    repository_root = (tmp_path / "repository").resolve()
    ambient["HOME"] = str(home)

    session_id = asyncio.run(
        hermes_smoke._create_official_acp_session(
            home=home,
            hermes_python=tmp_path / "hermes-python",
            repository_root=repository_root,
            prompt="Reply with exactly: ok",
            credential_environment_names=("ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
        )
    )

    definition = captured["definition"]
    assert definition.inherited_environment_names == (
        *HERMES_INHERITED_ENVIRONMENT_NAMES,
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    )
    environment = captured["environment"]
    assert environment["ANTHROPIC_API_KEY"] == "anthropic-file-secret"
    assert environment["OPENAI_API_KEY"] == "openai-file-secret"
    assert "GOOGLE_API_KEY" not in environment
    assert environment["HOME"] == str(home)
    assert environment["HERMES_HOME"] == str(home)
    assert environment["HERMES_PYTHON_SRC_ROOT"] == str(
        hermes_src_root(tmp_path / "hermes-python")
    )
    assert "HERMES_SESSION_KEY" not in environment
    assert "PLAN_DB_PATH" not in environment
    assert "PYTHONPATH" not in environment
    assert session_id == "stored-session"


def test_official_acp_smoke_rejects_failed_prompt_response_and_agent_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class FailedChild:
        async def initialize(self, request: Any) -> None:
            del request

        async def new_session(self, request: Any) -> Any:
            del request
            return SimpleNamespace(session_id="stored-session")

        async def prompt(self, request: Any) -> PromptResponse:
            await self.ingress(
                SessionNotification(
                    session_id=request.session_id,
                    update=AgentMessageChunk(
                        session_update="agent_message_chunk",
                        content=TextContentBlock(type="text", text="no"),
                    ),
                )
            )
            return PromptResponse(stop_reason="cancelled")

        async def close(self) -> None:
            events.append("close")

        def __init__(self, ingress: Any) -> None:
            self.ingress = ingress

    class FailedFactory:
        def __init__(self, definition: Any) -> None:
            del definition

        async def create(
            self,
            employee: Any,
            generation: int,
            update_ingress: Any,
            permission_callback: Any,
            death_callback: Any,
        ) -> FailedChild:
            del employee, generation, permission_callback, death_callback
            return FailedChild(update_ingress)

    monkeypatch.setattr(hermes_smoke, "SdkAcpEmployeeChildFactory", FailedFactory)

    with pytest.raises(
        EnvironmentValidationError,
        match=r"stop_reason='cancelled'.*agent_text='no'",
    ):
        asyncio.run(
            hermes_smoke._create_official_acp_session(
                home=tmp_path / "isolated-hermes-home",
                hermes_python=tmp_path / "hermes-python",
                repository_root=_repository_root(tmp_path),
                prompt="Reply with exactly: ok",
            )
        )

    assert events == ["close"]


def test_official_acp_smoke_loads_session_in_a_fresh_child_before_reporting_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    children: list[Any] = []
    events: list[str] = []

    class PersistentChild:
        def __init__(self, child_number: int, ingress: Any) -> None:
            self.child_number = child_number
            self.ingress = ingress
            self.load_requests: list[LoadSessionRequest] = []

        async def initialize(self, request: Any) -> None:
            del request

        async def new_session(self, request: Any) -> Any:
            del request
            events.append(f"new:{self.child_number}")
            return SimpleNamespace(session_id="stored-session")

        async def prompt(self, request: Any) -> PromptResponse:
            events.append(f"prompt:{self.child_number}")
            await self.ingress(
                SessionNotification(
                    session_id=request.session_id,
                    update=AgentMessageChunk(
                        session_update="agent_message_chunk",
                        content=TextContentBlock(type="text", text="ok"),
                    ),
                )
            )
            return PromptResponse(stop_reason="end_turn")

        async def load_session(self, request: LoadSessionRequest) -> Any:
            events.append(f"load:{self.child_number}")
            self.load_requests.append(request)
            return SimpleNamespace()

        async def close(self) -> None:
            events.append(f"close:{self.child_number}")

    class PersistentFactory:
        def __init__(self, definition: Any) -> None:
            del definition

        async def create(
            self,
            employee: Any,
            generation: int,
            update_ingress: Any,
            permission_callback: Any,
            death_callback: Any,
        ) -> PersistentChild:
            del employee, generation, permission_callback, death_callback
            child = PersistentChild(len(children) + 1, update_ingress)
            children.append(child)
            return child

    monkeypatch.setattr(hermes_smoke, "SdkAcpEmployeeChildFactory", PersistentFactory)
    home = tmp_path / "isolated-hermes-home"

    session_id = asyncio.run(
        hermes_smoke._create_official_acp_session(
            home=home,
            hermes_python=tmp_path / "hermes-python",
            repository_root=_repository_root(tmp_path),
            prompt="Reply with exactly: ok",
        )
    )

    assert session_id == "stored-session"
    assert len(children) == 2
    assert children[1].load_requests[0].session_id == session_id
    assert events == ["new:1", "prompt:1", "close:1", "load:2", "close:2"]


def test_hermes_smoke_runs_staging_and_preview_concurrently_with_scrubbed_envs(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    staging_env = tmp_path / "staging.env"
    preview_env = tmp_path / "preview.env"
    staging_env.write_text("ANTHROPIC_API_KEY=staging-secret\n", encoding="utf-8")
    preview_env.write_text("OPENAI_API_KEY=preview-secret\n", encoding="utf-8")
    staging = _manifest(
        kind="staging",
        instance_id="staging",
        environment_root=tmp_path / "envs",
        port=8768,
        credentials_env_file=staging_env,
        repository_roots=(repository_root,),
    )
    preview = _manifest(
        kind="preview",
        instance_id="feature-123",
        environment_root=tmp_path / "envs",
        port=9012,
        credentials_env_file=preview_env,
        repository_roots=(repository_root,),
    )
    invocations: list[HermesSmokeInvocation] = []

    def runner(invocation: HermesSmokeInvocation) -> str:
        invocations.append(invocation)
        stored = "stored-staging" if invocation.label == "staging" else "stored-preview"
        return f"[smoke] live sid=live-{invocation.label} stored={stored}\n"

    report = smoke_prepared_nonproduction_instances(
        staging,
        preview,
        hermes_python=tmp_path / "hermes-python",
        ambient_env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": "/tmp/poison",
            "HERMES_HOME": "/tmp/copied-home",
            "HERMES_SESSION_KEY": "copied-session",
            "ANTHROPIC_API_KEY": "ambient-secret",
            "PLAN_DB_PATH": "/tmp/live.db",
        },
        runner=runner,
    )

    assert report.staging_stored_session_id == "stored-staging"
    assert report.preview_stored_session_id == "stored-preview"
    assert report.staging_home == staging.hermes_home
    assert report.preview_home == preview.hermes_home
    assert {invocation.label for invocation in invocations} == {"staging", "preview"}
    for invocation in invocations:
        expected_argv = [
            sys.executable,
            "-m",
            "planner.environments.hermes_smoke",
            "--home",
            str(invocation.home),
            "--hermes-python",
            str(tmp_path / "hermes-python"),
            "--repository-root",
            str(repository_root),
            "--prompt",
            "Reply with exactly: ok",
        ]
        expected_argv.extend(
            [
                "--credential-key",
                "ANTHROPIC_API_KEY"
                if invocation.label == "staging"
                else "OPENAI_API_KEY",
            ]
        )
        assert invocation.argv == expected_argv
        assert invocation.env["PATH"] == "/usr/bin:/bin"
        assert invocation.env["HOME"] == str(invocation.home)
        assert invocation.env["HERMES_HOME"] == str(invocation.home)
        assert invocation.env["HERMES_PYTHON_SRC_ROOT"] == str(
            hermes_src_root(tmp_path / "hermes-python")
        )
        assert "PYTHONPATH" not in invocation.env
        assert "HERMES_SESSION_KEY" not in invocation.env
        assert "PLAN_DB_PATH" not in invocation.env
    staging_invocation = next(
        invocation for invocation in invocations if invocation.label == "staging"
    )
    preview_invocation = next(
        invocation for invocation in invocations if invocation.label == "preview"
    )
    assert staging_invocation.env["ANTHROPIC_API_KEY"] == "staging-secret"
    assert "OPENAI_API_KEY" not in staging_invocation.env
    assert preview_invocation.env["OPENAI_API_KEY"] == "preview-secret"
    assert "ANTHROPIC_API_KEY" not in preview_invocation.env


def test_hermes_smoke_rejects_live_instances_and_equal_session_ids(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)
    live = _manifest(
        kind="live",
        instance_id="live",
        environment_root=tmp_path / "envs",
        port=8767,
        credentials_env_file=None,
        repository_roots=(repository_root,),
    )
    preview = _manifest(
        kind="preview",
        instance_id="feature-123",
        environment_root=tmp_path / "envs",
        port=9012,
        credentials_env_file=None,
        repository_roots=(repository_root,),
    )

    with pytest.raises(EnvironmentValidationError, match="non-production"):
        smoke_prepared_nonproduction_instances(
            live,
            preview,
            hermes_python=tmp_path / "hermes-python",
            ambient_env={},
            runner=lambda _: "[smoke] live sid=live stored=stored\n",
        )

    staging = _manifest(
        kind="staging",
        instance_id="staging",
        environment_root=tmp_path / "envs",
        port=8768,
        credentials_env_file=None,
        repository_roots=(repository_root,),
    )
    with pytest.raises(EnvironmentValidationError, match="distinct stored session ids"):
        smoke_prepared_nonproduction_instances(
            staging,
            preview,
            hermes_python=tmp_path / "hermes-python",
            ambient_env={},
            runner=lambda _: "[smoke] live sid=live stored=same-stored\n",
        )


def test_hermes_smoke_fails_clearly_when_output_has_no_stored_session_id(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    staging = _manifest(
        kind="staging",
        instance_id="staging",
        environment_root=tmp_path / "envs",
        port=8768,
        credentials_env_file=None,
        repository_roots=(repository_root,),
    )
    preview = _manifest(
        kind="preview",
        instance_id="feature-123",
        environment_root=tmp_path / "envs",
        port=9012,
        credentials_env_file=None,
        repository_roots=(repository_root,),
    )

    with pytest.raises(EnvironmentValidationError, match="printed no stored session id"):
        smoke_prepared_nonproduction_instances(
            staging,
            preview,
            hermes_python=tmp_path / "hermes-python",
            ambient_env={},
            runner=lambda _: "[smoke] ready\n",
        )


def _manifest(
    *,
    kind: str,
    instance_id: str,
    environment_root: Path,
    port: int,
    credentials_env_file: Path | None,
    repository_roots: tuple[Path, ...],
) -> EnvironmentManifest:
    environment_root = environment_root.resolve()
    instance_root = (
        environment_root / "previews" / instance_id
        if kind == "preview"
        else environment_root / instance_id
    )
    return EnvironmentManifest(
        kind=kind,  # type: ignore[arg-type]
        instance_id=instance_id,
        environment_root=environment_root,
        instance_root=instance_root,
        db_path=instance_root / "data" / "planner.db",
        managed_files_root=instance_root / "data" / "files",
        hermes_home=instance_root / "hermes-home",
        logs_dir=instance_root / "logs",
        dispatcher_lock_path=instance_root / "run" / "dispatcher.lock",
        server_control_socket_path=instance_root / "run" / "server-control.sock",
        port=port,
        credentials_env_file=credentials_env_file.resolve()
        if credentials_env_file is not None
        else None,
        expected_linux_account="panels-live" if kind == "live" else "panels-worker",
        fixture_version=None if kind == "live" else "fake-fixture-v1",
        prepared_at=1_800_000_000,
        repository_roots=tuple(root.resolve() for root in repository_roots),
    )


def _repository_root(tmp_path: Path) -> Path:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    (repository_root / ".git").mkdir()
    return repository_root
