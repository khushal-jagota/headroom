"""Prove the SDK launch seam contains the persistent Worker child and its failures."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from planner.conversation import worker_scratch
from planner.conversation.backends import claude_model_catalog
from planner.conversation.backends.claude_agent_sdk import _ClaudeCleanupTransport
from planner.conversation.backends.cli_compatibility import BackendCliIncompatible
from planner.worker_supervision import WorkerSupervisor
from planner.worker_supervision.contracts import CleanupResult


@pytest.fixture(autouse=True)
def scripted_cli_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "planner.conversation.backends.claude_agent_sdk.ensure_cli_compatible", AsyncMock()
    )


@pytest.mark.parametrize("entrypoint", ["conversation", "catalog"])
def test_incompatible_cli_refuses_before_native_launch(
    monkeypatch: pytest.MonkeyPatch, entrypoint: str,
) -> None:
    refused = AsyncMock(side_effect=BackendCliIncompatible("Update Claude to at least 2.1.219"))
    native = AsyncMock()
    unit = AsyncMock()
    monkeypatch.setattr(WorkerSupervisor, "create", unit)
    if entrypoint == "conversation":
        monkeypatch.setattr(
            "planner.conversation.backends.claude_agent_sdk.ensure_cli_compatible", refused
        )
        monkeypatch.setattr(SubprocessCLITransport, "connect", native)
        transport = _ClaudeCleanupTransport(
            "", ClaudeAgentOptions(cli_path="/selected/claude", env={"PLAN_TICKET_ID": "ticket"})
        )
        with pytest.raises(BackendCliIncompatible):
            asyncio.run(transport.connect())
    else:
        monkeypatch.setattr(claude_model_catalog, "ensure_cli_compatible", refused)
        monkeypatch.setattr(claude_model_catalog, "ClaudeSDKClient", native)
        with pytest.raises(
            claude_model_catalog.ClaudeModelCatalogUnavailable, match="Update Claude"
        ):
            asyncio.run(claude_model_catalog.probe_claude_model_catalog("/selected/claude"))
    assert refused.call_args.args == ("claude", "/selected/claude")
    native.assert_not_called()
    unit.assert_not_awaited()


def test_worker_launch_preserves_sdk_arguments_and_keeps_unit_across_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")
    monkeypatch.setenv("BASH_ENV", str(tmp_path / "user-bash-env"))

    async def exercise() -> None:
        unit = AsyncMock()
        unit.environment = {}
        unit.stop.return_value = CleanupResult(True, "")
        unit.argv = lambda command: ("contained", *command)
        create = AsyncMock(return_value=unit)
        monkeypatch.setattr(WorkerSupervisor, "create", create)
        options = ClaudeAgentOptions(
            cli_path="/claude", cwd=str(tmp_path), model="model",
            env={"PLAN_TICKET_ID": "ticket",
                 "AUTH_VALUE": "secret"},
        )
        transport = _ClaudeCleanupTransport("", options)
        original_command = SubprocessCLITransport._build_command(transport)

        async def connect(self: SubprocessCLITransport) -> None:
            assert self._build_command() == ["contained", *original_command]

        async def messages(self: SubprocessCLITransport):  # type: ignore[no-untyped-def]
            yield {"type": "result"}
            unit.stop.assert_not_awaited()
            yield {"type": "result"}

        monkeypatch.setattr(SubprocessCLITransport, "connect", connect)
        monkeypatch.setattr(SubprocessCLITransport, "_read_messages_impl", messages)
        monkeypatch.setattr(SubprocessCLITransport, "close", AsyncMock())
        await transport.connect()
        environment, workspace = create.call_args.args
        assert environment["AUTH_VALUE"] == "secret"
        assert environment["PWD"] == str(tmp_path)
        assert environment["CLAUDE_CODE_ENTRYPOINT"] == "sdk-py"
        assert environment["BASH_ENV"] == str(tmp_path / "user-bash-env")
        assert workspace == tmp_path
        scratch = Path(options.env["TMPDIR"])
        assert scratch.parent == tmp_path / "runs"
        assert scratch.is_dir()
        process = AsyncMock()
        process.returncode = 0
        transport._process = process
        assert len([item async for item in transport._read_messages_impl()]) == 2
        assert not scratch.exists()
        unit.stop.assert_awaited_once()
        await transport.close()
        assert unit.stop.await_count == 2
        assert not scratch.exists()

    asyncio.run(exercise())


def test_nonworker_does_not_inherit_parent_supervision(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        monkeypatch.setenv("PLAN_TICKET_ID", "parent-worker")
        create = AsyncMock()
        monkeypatch.setattr(WorkerSupervisor, "create", create)
        monkeypatch.setattr(SubprocessCLITransport, "connect", AsyncMock())
        transport = _ClaudeCleanupTransport("", ClaudeAgentOptions(cli_path="/claude"))
        await transport.connect()
        create.assert_not_awaited()

    asyncio.run(exercise())


def test_failed_worker_start_closes_partial_sdk_process_before_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(WorkerSupervisor, "create", AsyncMock(return_value=None))
        monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")
        process = AsyncMock()
        process.returncode = None
        scratch: Path | None = None

        async def connect(self: SubprocessCLITransport) -> None:
            nonlocal scratch
            scratch = Path(self._options.env["TMPDIR"])
            self._process = process
            raise RuntimeError("startup failed")

        async def close(self: SubprocessCLITransport) -> None:
            assert scratch is not None and scratch.exists()
            assert process.returncode is None
            process.returncode = 0
            self._process = None

        monkeypatch.setattr(SubprocessCLITransport, "connect", connect)
        monkeypatch.setattr(SubprocessCLITransport, "close", close)
        transport = _ClaudeCleanupTransport(
            "", ClaudeAgentOptions(cli_path="/claude", env={
                "PLAN_TICKET_ID": "ticket",
            })
        )
        with pytest.raises(RuntimeError, match="startup failed"):
            await transport.connect()
        assert process.returncode == 0
        assert scratch is not None and not scratch.exists()

    asyncio.run(exercise())


def test_owned_worker_callback_kills_a_child_that_ignores_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK's longer grace sequence cannot consume the shared stop deadline."""
    import anyio
    from anyio.streams.text import TextSendStream

    monkeypatch.setattr(
        "planner.conversation.backends.claude_agent_sdk.NATIVE_PHASE_SECONDS", 0.05
    )
    monkeypatch.setattr(SubprocessCLITransport, "close", AsyncMock())

    async def exercise() -> None:
        process = await anyio.open_process([
            sys.executable, "-c",
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "print('ready',flush=True); time.sleep(60)",
        ])
        try:
            assert process.stdout is not None
            await asyncio.wait_for(process.stdout.receive(), 2)
            transport = _ClaudeCleanupTransport("", ClaudeAgentOptions(
                cli_path=sys.executable, cwd=str(tmp_path),
            ))
            transport._process = process
            assert process.stdin is not None
            transport._stdin_stream = TextSendStream(process.stdin)
            await asyncio.wait_for(transport._stop_native_process(), 1)
            assert process.returncode == -9
            assert transport._cleanup_process is process
        finally:
            if process.returncode is None:
                process.kill()
                await asyncio.wait_for(process.wait(), 2)
            await process.aclose()

    asyncio.run(exercise())
