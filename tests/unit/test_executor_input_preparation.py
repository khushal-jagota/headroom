"""Input preparation is runner-owned; withdrawal proves no provider invocation."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home
from tests.unit.test_executor_native_runtime import native_registry as native_registry

from planner.execution_identity import ChildReference
from planner.executor.contracts import ChildCommand, ChildOperation, CommandDisposition, StopRun
from planner.executor.runtime import NativeChildRegistry


def test_withdraw_preparing_input_settles_before_exact_stop_and_never_invokes(
    native_registry: tuple[NativeChildRegistry, object, object, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry = native_registry[0]
        entered, cleanup = asyncio.Event(), asyncio.Event()

        async def prepare(command: ChildCommand) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await cleanup.wait()

        registry.prepare_inputs = prepare
        child = ChildReference(registry.incarnation, "input-child", "input-conversation")
        command = replace(native_registry[3], command_id="input-command", child=child)
        sending = asyncio.create_task(registry.execute_command(command))
        await entered.wait()
        assert registry.ask((command.command_id,))[0].phase == "preparing"
        assert registry.withdraw(command.command_id)
        stop = asyncio.create_task(
            registry.execute_command(
                replace(
                    command, command_id="stop", operation=ChildOperation.stop, arguments=StopRun()
                )
            )
        )
        await asyncio.sleep(0)
        assert not stop.done()
        cleanup.set()
        assert (await sending).disposition is CommandDisposition.refused
        assert (await stop).disposition is CommandDisposition.succeeded
        assert command.command_id not in registry._invoking_commands
        assert (await registry.execute_command(command)).disposition is CommandDisposition.refused

    asyncio.run(run())
