"""Request failures disconnect their generation without claiming operation refusal."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from planner.executor import mac
from planner.executor.configuration import PROTOCOL_VERSION
from planner.executor.contracts import ExecutorIdentity, WireMessage, WireMessageKind
from planner.executor.mac import MacExecutor
from planner.executor.vps import ExecutorEndpoint


def requester(writer: Any) -> MacExecutor:
    executor = MacExecutor.__new__(MacExecutor)
    executor._writer = writer
    executor._pending = {}
    executor._handshake_accepted = False
    executor._write_lock = asyncio.Lock()
    return executor


def peer() -> Any:
    writer = Mock()
    writer.drain = AsyncMock()
    return writer


@pytest.mark.parametrize("blocked", ["response", "drain", "lock"])
def test_absolute_request_deadline_covers_send_and_response(
    monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    async def run() -> None:
        monkeypatch.setattr(mac, "REQUEST_TIMEOUT_SECONDS", 0.02)
        writer = peer()
        executor = requester(writer)
        if blocked == "drain":
            writer.drain.side_effect = asyncio.Event().wait
        if blocked == "lock":
            await executor._write_lock.acquire()
        with pytest.raises(TimeoutError):
            await executor._request(WireMessageKind.envelope, {})
        assert not executor._pending
        writer.close.assert_called_once_with()
        assert writer.write.call_count == (0 if blocked == "lock" else 1)
        if blocked == "lock":
            executor._write_lock.release()

    asyncio.run(run())


def test_timeout_does_not_close_replacement_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(mac, "REQUEST_TIMEOUT_SECONDS", 0.02)
        original, replacement = peer(), peer()
        executor = requester(original)
        task = asyncio.create_task(executor._request(WireMessageKind.envelope, {}))
        while not original.write.called:
            await asyncio.sleep(0)
        executor._writer = replacement
        with pytest.raises(TimeoutError):
            await task
        original.close.assert_called_once_with()
        replacement.close.assert_not_called()
        replacement.write.assert_not_called()
        assert not executor._pending

    asyncio.run(run())


def test_generation_change_while_waiting_for_send_never_writes_replacement() -> None:
    async def run() -> None:
        original, replacement = peer(), peer()
        executor = requester(original)
        await executor._write_lock.acquire()
        task = asyncio.create_task(executor._request(WireMessageKind.envelope, {}))
        while not executor._pending:
            await asyncio.sleep(0)
        executor._writer = replacement
        executor._write_lock.release()
        with pytest.raises(ConnectionError):
            await task
        original.write.assert_not_called()
        replacement.write.assert_not_called()
        replacement.close.assert_not_called()
        assert not executor._pending

    asyncio.run(run())


def test_external_cancellation_preserves_writer_and_cleans_pending() -> None:
    async def run() -> None:
        writer = peer()
        executor = requester(writer)
        task = asyncio.create_task(executor._request(WireMessageKind.envelope, {}))
        while not writer.write.called:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not executor._pending
        writer.close.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["operation", "validation_reply"])
def test_unexpected_request_task_failure_closes_original_generation(failure: str) -> None:
    async def run() -> None:
        endpoint = ExecutorEndpoint.__new__(ExecutorEndpoint)
        endpoint._input_exchanges = {}
        endpoint._revoked_input_commands = set()
        original, replacement = peer(), peer()
        identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, 2, "incarnation", 1)
        endpoint.identity = identity
        endpoint.writer = original
        endpoint._responses = {}
        if failure == "operation":

            async def fail(*args: object) -> None:
                endpoint.writer = replacement
                raise RuntimeError("private operation details")

            handler = AsyncMock(side_effect=fail)
            endpoint._read = handler  # type: ignore[method-assign]
            payload: Any = {
                "child": {
                    "executor_incarnation": "incarnation",
                    "child_id": "child",
                    "conversation_id": "conversation",
                },
                "path": "/api/tickets",
                "params": {},
            }
        else:

            async def fail_send(*args: object) -> None:
                endpoint.writer = replacement
                raise RuntimeError("private send details")

            handler = AsyncMock(side_effect=fail_send)
            endpoint._send = handler  # type: ignore[method-assign]
            payload = {}
        await asyncio.create_task(
            endpoint._handle(
                WireMessage(WireMessageKind.read_request, "request", payload), identity, original
            )
        )
        handler.assert_awaited_once()
        original.close.assert_called_once_with()
        replacement.close.assert_not_called()

    asyncio.run(run())


def test_request_task_cancellation_propagates() -> None:
    async def run() -> None:
        endpoint = ExecutorEndpoint.__new__(ExecutorEndpoint)
        endpoint._input_exchanges = {}
        endpoint._revoked_input_commands = set()
        original = peer()

        async def cancel(*args: object) -> None:
            raise asyncio.CancelledError

        endpoint._handle_message = AsyncMock(side_effect=cancel)  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            await endpoint._handle(
                WireMessage(WireMessageKind.health, "request", {}),
                ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, 2, "incarnation", 1),
                original,
            )
        original.close.assert_not_called()

    asyncio.run(run())


def test_late_response_after_timeout_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    from planner.executor.wire import encode_frame

    async def run() -> None:
        monkeypatch.setattr(mac, "REQUEST_TIMEOUT_SECONDS", 0.02)
        writer = peer()
        executor = requester(writer)
        task = asyncio.create_task(executor._request(WireMessageKind.envelope, {}))
        while not executor._pending:
            await asyncio.sleep(0)
        request_id = next(iter(executor._pending))
        with pytest.raises(TimeoutError):
            await task
        reader = asyncio.StreamReader()
        reader.feed_data(encode_frame(WireMessage(WireMessageKind.receipt, request_id, {})))
        reader.feed_eof()
        with pytest.raises(asyncio.IncompleteReadError):
            await executor._read_loop(reader)
        assert not executor._pending

    asyncio.run(run())
