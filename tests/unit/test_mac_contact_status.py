"""Version evidence never grants execution and survives a VPS process restart."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from tests.support.executor_control import MessageStream

from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity, ProtocolRefused, TicketLocale
from planner.executor.link import Link
from planner.executor.protocol import (
    WireMessage,
    WireMessageKind,
    as_object,
    decode_message,
    encode_message,
)
from planner.executor.vps_transport import VpsExecutor
from planner.tickets.locale_configuration import execution_unavailable_reason


@pytest.fixture
def endpoint(tmp_path: Path) -> VpsExecutor:
    path = str(tmp_path / "vps.sqlite")
    conn = connect(path)
    create_schema(conn)
    conn.close()
    return VpsExecutor(path, RealClock(), application_revision="server-revision")


def identity() -> ExecutorIdentity:
    return ExecutorIdentity(PROTOCOL_VERSION, "mac-revision", SPOOL_FORMAT_VERSION, str(uuid4()), 1)


def test_old_hello_gets_refusal_before_close_and_durable_evidence(endpoint: VpsExecutor) -> None:
    async def run() -> None:
        stream = MessageStream()
        peer = replace(identity(), protocol_version=PROTOCOL_VERSION - 1)
        # An older hello need not decode as the current ExecutorHealth schema.
        stream.feed_data(
            encode_message(
                WireMessage(WireMessageKind.hello, "hello", {"identity": as_object(peer)})
            )
        )
        await endpoint.session(stream)
        await stream.wait_closed()
        response = decode_message(stream.sent[0])
        assert response.kind is WireMessageKind.protocol_refused
        assert response.payload["reason"] == "Mac needs update"
        assert endpoint.runs.identity is None
        assert endpoint.runs.link is None
        assert not endpoint.runs.ready
        restarted = VpsExecutor(endpoint.db_path, RealClock())
        health = restarted.health()
        assert health["refusal"] == response.payload
        assert health["last_contact_at_ms"] == response.payload["refused_at_ms"]
        assert health["connection_state"] == "disconnected"
        conn = connect(endpoint.db_path)
        try:
            assert "Mac needs update" in execution_unavailable_reason(conn, TicketLocale.macos)
        finally:
            conn.close()
        # Exercise actual admission, not only the store operation.
        from planner.executor.clock import now_ms
        from planner.executor.protocol import ConnectionState, ExecutorHealth

        compatible = identity()
        recovery = MessageStream()
        hello = ExecutorHealth(compatible, 1, ConnectionState.reconciling, False, now_ms(), None)
        recovery.feed_data(
            encode_message(WireMessage(WireMessageKind.hello, "recover", as_object(hello)))
        )
        serving = asyncio.create_task(endpoint.session(recovery))
        for _ in range(100):
            if recovery.sent:
                break
            await asyncio.sleep(0.01)
        assert decode_message(recovery.sent[0]).kind is WireMessageKind.hello_accepted
        assert restarted.health()["refusal"] is None
        assert restarted.health()["connection_state"] == "disconnected"
        recovery.feed_eof()
        await serving
        await recovery.wait_closed()

    asyncio.run(run())


def test_malformed_hello_does_not_create_version_evidence(endpoint: VpsExecutor) -> None:
    async def run() -> None:
        stream = MessageStream()
        bad = as_object(replace(identity(), protocol_version=PROTOCOL_VERSION - 1))
        bad["executor_incarnation"] = "invalid"
        stream.feed_data(
            encode_message(WireMessage(WireMessageKind.hello, "hello", {"identity": bad}))
        )
        await endpoint.session(stream)
        await stream.wait_closed()
        assert not stream.sent
        assert endpoint.health()["refusal"] is None

    asyncio.run(run())


def test_rejected_parallel_hello_does_not_replace_live_health(endpoint: VpsExecutor) -> None:
    async def run() -> None:
        peer = identity()
        current = Link(peer, MessageStream())
        endpoint.runs.identity = peer
        endpoint.runs.link = current
        endpoint.runs.ready = True
        assert (
            endpoint._inspect_identity(replace(peer, protocol_version=PROTOCOL_VERSION - 1))
            is not None
        )
        assert endpoint.health()["connection_state"] == "ready"
        assert endpoint.health()["refusal"] is None
        await current.wait_closed()

    asyncio.run(run())


def test_link_preserves_refusal_reason_for_mac_status() -> None:
    async def run() -> None:
        stream = MessageStream()
        link = Link(identity(), stream)
        task = asyncio.create_task(link.request(WireMessageKind.hello, {}))
        await asyncio.sleep(0)
        request = decode_message(stream.sent[0])
        stream.feed_data(
            encode_message(
                WireMessage(
                    WireMessageKind.protocol_refused,
                    request.request_id,
                    {"reason": "Mac needs update"},
                )
            )
        )
        with pytest.raises(ProtocolRefused, match="Mac needs update"):
            await task
        assert link.refusal == {"reason": "Mac needs update"}
        await link.wait_closed()

    asyncio.run(run())


def test_received_contact_is_durable_before_link_closes(endpoint: VpsExecutor) -> None:
    async def run() -> None:
        peer = identity()
        stream = MessageStream()
        link = Link(peer, stream)
        link.received = endpoint.runs.store.contact
        stream.feed_data(encode_message(WireMessage(WireMessageKind.health, "contact", {})))
        reader = link._start()
        await asyncio.sleep(0)
        restarted = VpsExecutor(endpoint.db_path, RealClock())
        assert restarted.health()["last_contact_at_ms"] == link.last_received_at_ms
        assert not link.is_closing()
        await link.wait_closed()
        await asyncio.gather(reader, return_exceptions=True)

    asyncio.run(run())
