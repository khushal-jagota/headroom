"""Input deadlines follow committed bytes and a fixed finalization interval."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from planner.executor import input_transfers
from planner.executor.contracts import (
    FileManifest,
    FileScope,
    FileTransfer,
    InputTransferProgress,
    InputTransferRequest,
)
from planner.executor.input_transfers import InputTransferDeadline


def request(size: int = 10) -> InputTransferRequest:
    return InputTransferRequest(
        "command",
        FileTransfer(
            "transfer",
            "download",
            FileManifest(
                "publication", FileScope.conversation, "conversation", "file", "a" * 64, size
            ),
        ),
    )


def test_policy_bounds_and_protocol_are_explicit() -> None:
    from planner.executor import configuration

    assert configuration.INPUT_PROGRESS_TIMEOUT_SECONDS == 30
    assert configuration.INPUT_FINALIZATION_TIMEOUT_SECONDS == 120
    assert configuration.INPUT_CANCEL_TIMEOUT_SECONDS == 5
    assert configuration.PROTOCOL_VERSION == 14
    assert configuration.SPOOL_FORMAT_VERSION == 2


def test_only_positive_download_bytes_renew_inactivity(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(input_transfers, "INPUT_PROGRESS_TIMEOUT_SECONDS", 0.03)
        state = InputTransferDeadline(request())
        initial = state.deadline
        state.advance(InputTransferProgress("command", "transfer", "download", 0))
        assert state.deadline == initial
        await asyncio.sleep(0.01)
        state.advance(InputTransferProgress("command", "transfer", "download", 1))
        advanced = state.deadline
        assert advanced > initial
        state.advance(InputTransferProgress("command", "transfer", "download", 1))
        assert state.deadline == advanced
        await asyncio.wait_for(state.expired(), 0.1)
        assert asyncio.get_running_loop().time() >= advanced

    asyncio.run(run())


@pytest.mark.parametrize(
    "invalid", ["command", "transfer", "regression", "overflow", "early_finalization"]
)
def test_invalid_progress_cannot_renew_or_transition(invalid: str) -> None:
    async def run() -> None:
        state = InputTransferDeadline(request())
        state.advance(InputTransferProgress("command", "transfer", "download", 2))
        before = (state.phase, state.byte_count, state.deadline)
        progress = InputTransferProgress("command", "transfer", "download", 3)
        if invalid == "command":
            progress = replace(progress, command_id="other")
        elif invalid == "transfer":
            progress = replace(progress, transfer_id="other")
        elif invalid == "regression":
            progress = replace(progress, byte_count=1)
        elif invalid == "overflow":
            progress = replace(progress, byte_count=11)
        else:
            progress = replace(progress, phase="finalization")
        with pytest.raises(ValueError):
            state.advance(progress)
        assert (state.phase, state.byte_count, state.deadline) == before

    asyncio.run(run())


def test_finalization_is_fixed_despite_repeated_complete_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(input_transfers, "INPUT_FINALIZATION_TIMEOUT_SECONDS", 0.03)
        state = InputTransferDeadline(request())
        complete = InputTransferProgress("command", "transfer", "finalization", 10)
        state.advance(complete)
        deadline = state.deadline
        with pytest.raises(ValueError, match="after finalization"):
            state.advance(InputTransferProgress("command", "transfer", "download", 10))
        for _ in range(3):
            await asyncio.sleep(0.015)
            state.advance(complete)
            assert state.deadline == deadline
        await asyncio.wait_for(state.expired(), 0.01)

    asyncio.run(run())


def test_revocation_ignores_late_complete_progress_and_empty_file_finalizes() -> None:
    async def run() -> None:
        state = InputTransferDeadline(request(0))
        state.advance(InputTransferProgress("command", "transfer", "finalization", 0))
        assert state.phase == "finalization" and state.byte_count == 0
        state = InputTransferDeadline(request())
        state.revoked = True
        before = (state.phase, state.byte_count, state.deadline)
        state.advance(InputTransferProgress("command", "transfer", "finalization", 10))
        assert (state.phase, state.byte_count, state.deadline) == before

    asyncio.run(run())


@pytest.mark.parametrize("cancellation", ["idle", "caller", "blocked_cancel_send"])
def test_exchange_revokes_before_bounded_ack_wait_and_ignores_late_acceptance(
    monkeypatch: pytest.MonkeyPatch, cancellation: str
) -> None:
    from typing import Any
    from unittest.mock import Mock

    from planner.executor import vps
    from planner.executor.codec import encode_value
    from planner.executor.configuration import PROTOCOL_VERSION
    from planner.executor.contracts import (
        ExecutorIdentity,
        InputTransferCancelled,
        RemoteInputTransferFailed,
        WireMessage,
        WireMessageKind,
    )
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        monkeypatch.setattr(vps, "INPUT_PROGRESS_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(vps, "INPUT_CANCEL_TIMEOUT_SECONDS", 0.03)
        endpoint = ExecutorEndpoint.__new__(ExecutorEndpoint)
        endpoint.identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, 2, "native", 1)
        writer = Mock()
        endpoint.writer = writer
        endpoint._input_exchanges = {}
        endpoint._revoked_input_commands = set()
        endpoint._possibly_emitted_commands = set()
        endpoint.pending_commands = {}
        cancel_sent = asyncio.Event()
        slot_sent = asyncio.Event()
        state: Any = None
        request_id = ""

        async def send(message: WireMessage, identity: Any, captured_writer: Any) -> bool:
            nonlocal state, request_id
            assert identity == endpoint.identity and captured_writer is writer
            request_id = message.request_id
            state = endpoint._input_exchanges[request_id]
            if message.kind is WireMessageKind.file_slot:
                slot_sent.set()
            elif message.kind is WireMessageKind.input_cancel:
                assert state.deadline.revoked
                cancel_sent.set()
                if cancellation == "blocked_cancel_send":
                    await asyncio.Event().wait()
            return True

        monkeypatch.setattr(endpoint, "_send_reconciliation", send)
        transfer = asyncio.create_task(endpoint._input_exchange(request()))
        await asyncio.wait_for(slot_sent.wait(), 1)
        if cancellation == "caller":
            transfer.cancel()
        await asyncio.wait_for(cancel_sent.wait(), 1)
        accepted = encode_value(request().slot.manifest)
        assert isinstance(accepted, dict)
        endpoint._receive_input_frame(
            WireMessage(WireMessageKind.file_accepted, request_id, accepted),
            endpoint.identity,
            writer,
        )
        assert not state.response.done()
        if cancellation == "caller":
            acknowledged = encode_value(InputTransferCancelled("command", "transfer"))
            assert isinstance(acknowledged, dict)
            endpoint._receive_input_frame(
                WireMessage(WireMessageKind.input_cancelled, request_id, acknowledged),
                endpoint.identity,
                writer,
            )
            assert state.cancelled.is_set()
        with pytest.raises(
            asyncio.CancelledError if cancellation == "caller" else RemoteInputTransferFailed
        ):
            await asyncio.wait_for(transfer, 0.2)
        assert not endpoint._input_exchanges
        writer.close.assert_not_called()
        assert endpoint._receive_input_frame(
            WireMessage(WireMessageKind.input_progress, request_id, {}), endpoint.identity, writer
        )

    asyncio.run(run())


def test_expired_progress_cannot_revive_unscheduled_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(input_transfers, "INPUT_PROGRESS_TIMEOUT_SECONDS", 0.01)
        state = InputTransferDeadline(request())
        initial = state.deadline
        # No expiry task exists yet: wire handling itself must enforce the clock.
        await asyncio.sleep(0.02)
        state.advance(InputTransferProgress("command", "transfer", "download", 1))
        assert state.deadline == initial
        assert not state.active()

    asyncio.run(run())


def test_progress_wire_payload_accepts_typed_json_and_refuses_boolean_bytes() -> None:
    from pydantic import TypeAdapter, ValidationError

    payload = {
        "command_id": "command",
        "transfer_id": "transfer",
        "phase": "download",
        "byte_count": 1,
    }
    adapter = TypeAdapter(InputTransferProgress)
    assert adapter.validate_python(payload) == InputTransferProgress(
        "command", "transfer", "download", 1
    )
    with pytest.raises(ValidationError):
        adapter.validate_python(payload | {"byte_count": True})


@pytest.mark.parametrize("replay", [False, True], ids=["direct", "reconnect"])
@pytest.mark.parametrize(
    "possibly_emitted", [False, True], ids=["never-emitted", "possibly-emitted"]
)
def test_failed_input_removes_only_commands_without_possible_native_admission(
    monkeypatch: pytest.MonkeyPatch, replay: bool, possibly_emitted: bool
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from planner.executor.configuration import PROTOCOL_VERSION
    from planner.executor.contracts import (
        ChildCommand,
        ChildOperation,
        ChildReference,
        CommandDisposition,
        CommandOutcome,
        ExecutorIdentity,
        RemoteInputTransferFailed,
        WireMessage,
        WireMessageKind,
    )
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        endpoint = ExecutorEndpoint.__new__(ExecutorEndpoint)
        endpoint.identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, 2, "native", 1)
        writer = Mock()
        writer.drain = AsyncMock()
        endpoint.writer = writer
        endpoint.ready = not replay
        endpoint.pending_commands = {}
        endpoint._cancelled_initial_children = set()
        endpoint._revoked_input_commands = set()
        endpoint._possibly_emitted_commands = {"command"} if possibly_emitted else set()
        endpoint._input_exchanges = {}
        endpoint._responses = {}
        endpoint._catalog_responses = {}
        endpoint._send_lock = asyncio.Lock()
        endpoint.store = Mock()
        endpoint.store.unapplied_answers.return_value = ()
        endpoint.runtime = SimpleNamespace(
            system=SimpleNamespace(resume_executor_held_prompts=AsyncMock())
        )
        command = ChildCommand(
            "command",
            ChildReference("native", "child", "conversation"),
            ChildOperation.write_prompt,
            None,
            {},
        )
        transferred = asyncio.Event()

        async def fail(command: ChildCommand) -> None:
            transferred.set()
            raise RemoteInputTransferFailed("input corrupt")

        emit = AsyncMock()
        monkeypatch.setattr(endpoint, "_transfer_command_inputs", fail)
        monkeypatch.setattr(endpoint, "_emit_command", emit)
        caller = asyncio.create_task(endpoint.send_command(command))
        await asyncio.sleep(0)
        if replay:
            await endpoint._handle_message(
                WireMessage(WireMessageKind.reconciliation_ready, "ready", {}),
                endpoint.identity,
                writer,
            )
        await asyncio.wait_for(transferred.wait(), 1)
        if possibly_emitted:
            assert not caller.done()
            pending = endpoint.pending_commands[command.command_id][1]
            assert not pending.done()
            outcome = CommandOutcome("command", command.child, CommandDisposition.uncertain, {})
            pending.set_result(outcome)
            assert await asyncio.wait_for(caller, 1) == outcome
        else:
            with pytest.raises(RemoteInputTransferFailed, match="input corrupt"):
                await asyncio.wait_for(caller, 1)
        assert command.command_id not in endpoint.pending_commands
        emit.assert_not_called()
        writer.close.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("possibly_emitted", [False, True])
def test_canceled_input_command_preserves_only_possible_native_outcome(
    monkeypatch: pytest.MonkeyPatch, possibly_emitted: bool
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from planner.executor.configuration import PROTOCOL_VERSION
    from planner.executor.contracts import (
        ChildCommand,
        ChildOperation,
        ChildReference,
        ExecutorIdentity,
        WireMessage,
        WireMessageKind,
    )
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        endpoint = ExecutorEndpoint.__new__(ExecutorEndpoint)
        endpoint.identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, 2, "native", 1)
        writer = Mock()
        writer.drain = AsyncMock()
        endpoint.writer = writer
        endpoint.ready = True
        endpoint.pending_commands = {}
        endpoint._cancelled_initial_children = set()
        endpoint._revoked_input_commands = set()
        endpoint._possibly_emitted_commands = {"command"} if possibly_emitted else set()
        endpoint._input_exchanges = {}
        endpoint._input_tasks = {}
        endpoint._input_transfer_locks = {}
        endpoint._responses = {}
        endpoint._catalog_responses = {}
        endpoint._send_lock = asyncio.Lock()
        endpoint.store = Mock()
        endpoint.store.unapplied_answers.return_value = ()
        endpoint.runtime = SimpleNamespace(
            system=SimpleNamespace(resume_executor_held_prompts=AsyncMock())
        )
        command = ChildCommand(
            "command",
            ChildReference("native", "child", "conversation"),
            ChildOperation.write_prompt,
            None,
            {},
        )
        entered = asyncio.Event()

        async def blocked(command: ChildCommand) -> None:
            entered.set()
            await asyncio.Event().wait()

        emit = AsyncMock()
        monkeypatch.setattr(endpoint, "_transfer_command_inputs_locked", blocked)
        monkeypatch.setattr(endpoint, "_emit_command", emit)
        caller = asyncio.create_task(endpoint.send_command(command))
        await asyncio.wait_for(entered.wait(), 1)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(caller, 1)
        assert not endpoint._input_tasks
        assert command.command_id in endpoint._revoked_input_commands
        if possibly_emitted:
            retained = endpoint.pending_commands[command.command_id][1]
            assert not retained.done()
            retained.cancel()  # Test teardown only; the production future stays pending.
            endpoint.pending_commands.clear()
        else:
            assert not endpoint.pending_commands
        await endpoint._handle_message(
            WireMessage(WireMessageKind.reconciliation_ready, "ready", {}),
            endpoint.identity,
            writer,
        )
        emit.assert_not_called()
        writer.close.assert_not_called()

    asyncio.run(run())


def test_expired_acceptance_cannot_beat_unscheduled_timer() -> None:
    from unittest.mock import Mock

    from planner.executor.codec import encode_value
    from planner.executor.configuration import PROTOCOL_VERSION
    from planner.executor.contracts import ExecutorIdentity, WireMessage, WireMessageKind
    from planner.executor.input_transfers import InputTransferExchange
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        endpoint = ExecutorEndpoint.__new__(ExecutorEndpoint)
        identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, 2, "native", 1)
        writer = Mock()
        state = InputTransferDeadline(request())
        state.phase = "finalization"
        state.deadline = asyncio.get_running_loop().time() - 1
        response = asyncio.get_running_loop().create_future()
        endpoint._input_exchanges = {
            "input": InputTransferExchange(state, identity, writer, response)
        }
        accepted = encode_value(request().slot.manifest)
        assert isinstance(accepted, dict)
        endpoint._receive_input_frame(
            WireMessage(WireMessageKind.file_accepted, "input", accepted), identity, writer
        )
        assert state.revoked and not response.done()

    asyncio.run(run())
