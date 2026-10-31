"""The machine UI reads expose canonical decisions and measured receipt evidence."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import replace
from sqlite3 import Connection
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from tests.support.executor_control import MessageReader, message_link

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import TestClock as ClockForTest
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor import link as link_module
from planner.executor import vps_transport as vps
from planner.executor.protocol import (
    ConnectionState,
    ExecutorHealth,
    JsonObject,
    WireMessage,
    WireMessageKind,
    encode_message,
    encode_value,
)


def _draft_request() -> Request:
    app = FastAPI()
    app.state.conversation_system = InMemoryConversationSystem()
    return Request({"type": "http", "app": app})


class Writer:
    def __init__(self) -> None:
        self.closed = False
        self.writes: list[WireMessage] = []

    def is_closing(self) -> bool:
        return self.closed

    async def send(self, data: WireMessage) -> None:
        self.writes.append(data)

    async def wait_closed(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _endpoint(tmp_db: Connection, clock: ClockForTest) -> vps.VpsExecutor:
    return vps.VpsExecutor(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]), clock)


def _hello() -> WireMessage:
    identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, str(uuid4()), 1)
    health = ExecutorHealth(identity, 42, ConnectionState.reconciling, False, 11, None)
    return WireMessage(WireMessageKind.hello, "hello", cast(JsonObject, encode_value(health)))


def test_refused_valid_frames_advance_contact_and_history_survives_loss(
    tmp_db: Connection, fake_clock: ClockForTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = _endpoint(tmp_db, fake_clock)
    assert endpoint.health()["last_contact_at_ms"] is None
    tick = [100]
    monkeypatch.setattr(link_module, "now_ms", lambda: tick[0])

    async def run() -> None:
        reader = MessageReader()
        reader.feed_data(encode_message(_hello()))
        writer = Writer()
        task = asyncio.create_task(endpoint.session(reader, cast(Any, writer)))
        while endpoint.runs.link is None:
            await asyncio.sleep(0)
        assert endpoint.health()["last_contact_at_ms"] == 100
        tick[0] = 200
        # A strictly decoded frame counts as contact even when its payload is refused.
        reader.feed_data(
            encode_message(WireMessage(WireMessageKind.refuse_envelope, "refusal", {}))
        )
        async with asyncio.timeout(1):
            while not any(message.kind is WireMessageKind.error for message in writer.writes):
                await asyncio.sleep(0)
        assert endpoint.health()["last_contact_at_ms"] == 200
        tick[0] = 300
        reader.feed_eof()
        await task
        assert endpoint.health()["connection_state"] == "disconnected"
        assert endpoint.health()["last_contact_at_ms"] == 200
        assert _endpoint(tmp_db, fake_clock).health()["last_contact_at_ms"] is None

    asyncio.run(run())


def test_malformed_or_superseded_frame_does_not_advance_contact(
    tmp_db: Connection, fake_clock: ClockForTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def send(message: WireMessage) -> None:
        del message

    async def probe() -> None:
        return None

    async def run() -> None:
        for superseded in (False, True):
            endpoint = _endpoint(tmp_db, fake_clock)

            tick = [100]
            monkeypatch.setattr(link_module, "now_ms", lambda tick=tick: tick[0])
            reader = MessageReader()
            reader.feed_data(encode_message(_hello()))
            writer = Writer()
            task = asyncio.create_task(endpoint.session(reader, cast(Any, writer)))
            while endpoint.runs.link is None:
                await asyncio.sleep(0)
            tick[0] = 200
            if superseded:
                assert endpoint.runs.link is not None
                endpoint.runs.link.close()
                endpoint.runs.link = message_link(
                    Writer(), cast(ExecutorIdentity, endpoint.runs.identity)
                )
                endpoint.runs.identity = replace(
                    cast(ExecutorIdentity, endpoint.runs.identity), connection_generation=2
                )
                reader.feed_data(encode_message(WireMessage(WireMessageKind.error, "old", {})))
                tick[0] = 300
            else:
                malformed = b'{"kind":"invented","request_id":"bad","payload":{}}'
                reader.feed_data(struct.pack("!I", len(malformed)) + malformed)
            await task
            assert endpoint.health()["last_contact_at_ms"] == (200 if superseded else 100)
            assert writer.closed

    asyncio.run(run())
