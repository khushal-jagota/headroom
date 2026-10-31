"""WebSocket boundaries replace stream framing; IPC retains its separate framing proof."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.support.executor_control import MessageStream

from planner.execution_identity import ProtocolRefused
from planner.executor.configuration import MAX_ENVELOPE_BYTES
from planner.executor.protocol import WireMessage, WireMessageKind, decode_message, encode_message


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


def test_shared_link_matches_nested_replies_refuses_items_and_fails_waiters_on_loss() -> None:
    """No network host: both roles use the same Link under nested bidirectional work."""
    from tests.unit.test_executor_request_deadlines import IDENTITY

    from planner.executor.link import Link

    async def run() -> None:
        class PairedPeer(MessageStream):
            other: PairedPeer

            async def send_bytes(self, data: bytes) -> None:
                await super().send_bytes(data)
                self.other.incoming.put_nowait(data)

            async def aclose(self) -> None:
                await super().aclose()
                self.other.incoming.put_nowait(b"")

        first_peer, second_peer = PairedPeer(), PairedPeer()
        first_peer.other, second_peer.other = second_peer, first_peer
        first = Link(IDENTITY, first_peer)
        second = Link(IDENTITY, second_peer)
        entered = asyncio.Event()

        async def heartbeat(message: WireMessage) -> WireMessage:
            return WireMessage(message.kind, message.request_id, {"alive": True})

        async def work(message: WireMessage) -> WireMessage:
            if message.payload["block"]:
                entered.set()
                await second.request(WireMessageKind.get_file, {})
            response = await second.request(WireMessageKind.health, {})
            return WireMessage(WireMessageKind.read_response, message.request_id, response.payload)

        first.on(WireMessageKind.health, heartbeat)
        second.on(WireMessageKind.read_request, work)
        reading = asyncio.create_task(second.closed())
        try:
            with pytest.raises(ValueError):
                await first.request(WireMessageKind.read_request, {})
            assert not first.is_closing() and not second.is_closing()
            response = await first.request(WireMessageKind.read_request, {"block": False})
            assert response.kind is WireMessageKind.read_response
            assert response.payload == {"alive": True}
            pending = asyncio.create_task(
                first.request(WireMessageKind.read_request, {"block": True})
            )
            await asyncio.wait_for(entered.wait(), 1)
            second.close()
            with pytest.raises(ConnectionError):
                await pending
            await asyncio.gather(*tuple(second._work))
            assert not first._pending and not second._pending
            sent = len(second_peer.sent)
            assert not await second.tell(WireMessageKind.command, {})
            assert len(second_peer.sent) == sent
        finally:
            first.close()
            second.close()
            await asyncio.gather(reading, return_exceptions=True)
            await first.wait_closed()
            await second.wait_closed()

    asyncio.run(run())


@pytest.mark.usefixtures("executor_transport_home")
@pytest.mark.parametrize("reply", ["wrong_kind", "changed_identity"])
def test_mac_refuses_invalid_hello_reply_before_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    """The service must validate hello before exposing the connection to any owner."""
    from planner.executor import mac_service
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.link import Link
    from planner.executor.mac_service import MacService
    from planner.executor.protocol import as_object

    async def run() -> None:
        service = MacService(
            paths=ExecutorPaths(tmp_path / "executor"),
            revision="a" * 40,
            executor_id="test-executor",
            panels_server_url="https://fixture.invalid",
        )
        disconnected = asyncio.Event()

        class Peer(MessageStream):
            async def send(self, message: WireMessage) -> None:
                await super().send(message)
                identity = as_object(service.identity)
                kind = WireMessageKind.hello_accepted
                if reply == "wrong_kind":
                    kind = WireMessageKind.health
                else:
                    identity["connection_generation"] = 999
                self.feed_data(encode_message(WireMessage(kind, message.request_id, identity)))

        stream = Peer()

        @asynccontextmanager
        async def dial(*_: object) -> AsyncIterator[Link]:
            link = Link(service.identity, stream)
            try:
                yield link
            finally:
                await link.wait_closed()
                disconnected.set()

        monkeypatch.setattr(mac_service, "dial_link", dial)
        task = asyncio.create_task(service.run())
        try:
            await asyncio.wait_for(disconnected.wait(), 1)
            assert service.connection.link is None
            assert stream.is_closing()
            assert [decode_message(body).kind for body in stream.sent] == [WireMessageKind.hello]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
