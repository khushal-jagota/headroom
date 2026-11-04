"""Prove the SDK launch seam contains the persistent Worker child and its failures."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from planner.conversation.backends.claude_agent_sdk import _ClaudeCleanupTransport
from planner.conversation.worker_unit import WorkerUnit


def test_worker_launch_preserves_sdk_arguments_and_keeps_unit_across_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        unit = AsyncMock()
        unit.environment = {"TMPDIR": str(tmp_path / "disk")}
        unit.argv = lambda command: ("contained", *command)
        create = AsyncMock(return_value=unit)
        monkeypatch.setattr(WorkerUnit, "create", create)
        options = ClaudeAgentOptions(
            cli_path="/claude", cwd=str(tmp_path), model="model",
            env={"PLAN_TICKET_ID": "ticket", "AUTH_VALUE": "secret"},
        )
        transport = _ClaudeCleanupTransport("", options)
        original_command = SubprocessCLITransport._build_command(transport)

        async def connect(self: SubprocessCLITransport) -> None:
            assert self._build_command() == ["contained", *original_command]

        async def messages(self: SubprocessCLITransport):  # type: ignore[no-untyped-def]
            yield {"type": "result"}
            unit.finish.assert_not_awaited()
            yield {"type": "result"}

        monkeypatch.setattr(SubprocessCLITransport, "connect", connect)
        monkeypatch.setattr(SubprocessCLITransport, "_read_messages_impl", messages)
        monkeypatch.setattr(SubprocessCLITransport, "close", AsyncMock())
        unit.finish.return_value = None
        await transport.connect()
        environment, workspace = create.call_args.args
        assert environment["AUTH_VALUE"] == "secret"
        assert environment["PWD"] == str(tmp_path)
        assert environment["CLAUDE_CODE_ENTRYPOINT"] == "sdk-py"
        assert workspace == tmp_path
        assert options.env["TMPDIR"] == str(tmp_path / "disk")
        assert len([item async for item in transport._read_messages_impl()]) == 2
        unit.finish.assert_awaited_once()
        await transport.close()
        unit.stop.assert_awaited_once()

    asyncio.run(exercise())


def test_worker_oom_replaces_generic_sdk_exit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        unit = AsyncMock()
        unit.finish.return_value = "Worker stopped because it exceeded its memory limit."
        transport = _ClaudeCleanupTransport("", ClaudeAgentOptions(cli_path="/claude"))
        transport._worker_unit = unit

        async def messages(self: SubprocessCLITransport):  # type: ignore[no-untyped-def]
            raise RuntimeError("generic process error")
            yield {}

        monkeypatch.setattr(SubprocessCLITransport, "_read_messages_impl", messages)
        with pytest.raises(RuntimeError, match="exceeded its memory limit"):
            _ = [item async for item in transport._read_messages_impl()]
        unit.finish.assert_awaited_once()

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


def test_partial_worker_start_failure_finishes_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        unit = AsyncMock()
        unit.environment = {}
        unit.finish.return_value = None
        monkeypatch.setattr(WorkerUnit, "create", AsyncMock(return_value=unit))
        monkeypatch.setattr(
            SubprocessCLITransport, "connect", AsyncMock(side_effect=RuntimeError("startup failed"))
        )
        transport = _ClaudeCleanupTransport(
            "", ClaudeAgentOptions(cli_path="/claude", env={"PLAN_TICKET_ID": "ticket"})
        )
        with pytest.raises(RuntimeError, match="startup failed"):
            await transport.connect()
        unit.finish.assert_awaited_once()

    asyncio.run(exercise())
