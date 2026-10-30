"""WebSocket boundaries replace stream framing; IPC retains its separate framing proof."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError
from tests.support.executor_control import BinaryPeer

from planner.execution_identity import ProtocolRefused
from planner.executor.app_transport import ControlWebSocket
from planner.executor.configuration import MAX_ENVELOPE_BYTES
from planner.executor.contracts import WireMessage, WireMessageKind
from planner.executor.wire import decode_message, encode_message


def test_plain_control_messages_validate_and_bound_the_whole_payload() -> None:
    message = WireMessage(WireMessageKind.health, "request", {})
    body = encode_message(message)
    assert body.startswith(b"{")
    assert decode_message(body) == message
    assert decode_message(body + b" " * (MAX_ENVELOPE_BYTES - len(body))) == message
    for oversized in (body + b" " * (MAX_ENVELOPE_BYTES + 1 - len(body)), b""):
        with pytest.raises(ProtocolRefused):
            decode_message(oversized)
    with pytest.raises(ProtocolRefused):
        encode_message(WireMessage(WireMessageKind.health, "x" * MAX_ENVELOPE_BYTES, {}))
    for invalid in (
        body + body,
        b"\0\0\0\x01" + body,
        b'{"kind":"unknown"}',
        b'{"kind":"health","request_id":1,"payload":{}}',
    ):
        with pytest.raises(ValidationError):
            decode_message(invalid)


def test_control_websocket_keeps_message_boundaries_and_awaits_send() -> None:
    async def run() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        class StalledPeer(BinaryPeer):
            async def send_bytes(self, data: bytes) -> None:
                entered.set()
                await release.wait()
                await super().send_bytes(data)

        peer = StalledPeer()
        transport = ControlWebSocket(peer)
        first = WireMessage(WireMessageKind.health, "first", {})
        second = WireMessage(WireMessageKind.health, "second", {})
        peer.incoming.put_nowait(encode_message(first))
        peer.incoming.put_nowait(encode_message(second))
        assert await transport.receive() == first
        assert await transport.receive() == second
        task = asyncio.create_task(transport.send(first))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert not task.done() and not peer.sent
            release.set()
            await task
            assert peer.sent == [encode_message(first)]
            await transport.wait_closed()
            assert transport.is_closing() and peer.closed
            with pytest.raises(ConnectionError):
                await transport.receive()
            with pytest.raises(ConnectionError):
                await transport.send(second)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
