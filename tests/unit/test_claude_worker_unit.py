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
from planner.conversation.tool_shell_kill_priority import TOOL_SHELL_STARTUP
from planner.conversation.worker_unit import WorkerUnit


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
    monkeypatch.setattr(WorkerUnit, "create", unit)
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
    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")

    async def exercise() -> None:
        unit = AsyncMock()
        unit.environment = {}
        unit.argv = lambda command: ("contained", *command)
        create = AsyncMock(return_value=unit)
        monkeypatch.setattr(WorkerUnit, "create", create)
        options = ClaudeAgentOptions(
            cli_path=str(executable), cwd=str(tmp_path), model="model",
            env={"PLAN_TICKET_ID": "ticket", "PLAN_TICKET_FILES_DIR": str(tmp_path),
                 "AUTH_VALUE": "secret"},
        )
        transport = _ClaudeCleanupTransport("", options)
        original_command = SubprocessCLITransport._build_command(transport)

        async def connect(self: SubprocessCLITransport) -> None:
            assert self._build_command()[0] == "contained"
            assert self._build_command()[-len(original_command):] == original_command

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
        assert environment["BASH_ENV"] == str(TOOL_SHELL_STARTUP)
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


def test_nonworker_does_not_inherit_parent_containment(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        monkeypatch.setenv("PLAN_TICKET_ID", "parent-worker")
        create = AsyncMock()
        monkeypatch.setattr(WorkerUnit, "create", create)
        monkeypatch.setattr(SubprocessCLITransport, "connect", AsyncMock())
        transport = _ClaudeCleanupTransport("", ClaudeAgentOptions(cli_path="/claude"))
        await transport.connect()
        create.assert_not_awaited()

    asyncio.run(exercise())


def test_failed_worker_start_closes_partial_sdk_process_before_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(WorkerUnit, "create", AsyncMock(return_value=None))
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
                "PLAN_TICKET_ID": "ticket", "PLAN_TICKET_FILES_DIR": str(tmp_path),
            })
        )
        with pytest.raises(RuntimeError, match="startup failed"):
            await transport.connect()
        assert process.returncode == 0
        assert scratch is not None and not scratch.exists()

    asyncio.run(exercise())
