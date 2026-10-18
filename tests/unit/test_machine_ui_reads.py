"""The machine UI reads expose canonical decisions and measured receipt evidence."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import replace
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from planner.core.clock import TestClock as ClockForTest
from planner.core.contracts import OWNER_PRINCIPAL, Priority
from planner.core.errors import PlannerError
from planner.executor import vps
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AskEvidence,
    AskReachability,
    AskReference,
    ChildReference,
    ConnectionState,
    ExecutorHealth,
    ExecutorIdentity,
    JsonObject,
    WireMessage,
    WireMessageKind,
)
from planner.executor.remote import RemoteBackendChild
from planner.executor.wire import encode_frame
from planner.projects import data as projects
from planner.sprints import data as sprints
from planner.tickets import data, locale_configuration
from planner.tickets.api import read_ticket_creation_context
from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale


class Writer:
    def __init__(self) -> None:
        self.closed = False
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _endpoint(tmp_db: Connection, clock: ClockForTest) -> vps.ExecutorEndpoint:
    return vps.ExecutorEndpoint(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]), clock)


def _hello() -> WireMessage:
    identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, str(uuid4()), 1)
    health = ExecutorHealth(identity, 42, ConnectionState.reconciling, False, 11, None)
    return WireMessage(WireMessageKind.hello, "hello", cast(JsonObject, encode_value(health)))


def test_contact_is_receipt_not_handshake_and_retains_history_after_loss(
    tmp_db: Connection, fake_clock: ClockForTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = _endpoint(tmp_db, fake_clock)
    assert endpoint.health()["last_contact_at_ms"] is None
    assert endpoint.health()["last_contact_source"] == "vps_received_valid_executor_frame"
    tick = [100]
    monkeypatch.setattr(vps, "now_ms", lambda tick=tick: tick[0])
    handled = asyncio.Event()

    async def send(message: WireMessage) -> None:
        del message

    async def probe() -> None:
        return None

    async def handle(message: WireMessage, identity: ExecutorIdentity, writer: object) -> None:
        assert endpoint.last_contact_at_ms == 200
        await vps.ExecutorEndpoint._handle(endpoint, message, identity, cast(Any, writer))
        handled.set()

    monkeypatch.setattr(endpoint, "_send", send)
    monkeypatch.setattr(endpoint, "_measure_clock", probe)
    monkeypatch.setattr(endpoint, "_handle", handle)

    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(encode_frame(_hello()))
        task = asyncio.create_task(endpoint._session(reader, cast(Any, Writer())))
        while endpoint.writer is None:
            await asyncio.sleep(0)
        assert endpoint.observed_at_ms == endpoint.last_contact_at_ms == 100
        tick[0] = 200
        # A valid frame establishes contact before its domain handler executes.
        reader.feed_data(encode_frame(WireMessage(WireMessageKind.error, "refusal", {})))
        await asyncio.wait_for(handled.wait(), 1)
        assert endpoint.observed_at_ms == 100
        tick[0] = 300
        reader.feed_eof()
        await task
        assert endpoint.health()["connection_state"] == "disconnected"
        assert endpoint.health()["last_contact_at_ms"] == 200
        assert endpoint.lost_at_ms == 300
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
            monkeypatch.setattr(endpoint, "_send", send)
            monkeypatch.setattr(endpoint, "_measure_clock", probe)
            tick = [100]
            monkeypatch.setattr(vps, "now_ms", lambda tick=tick: tick[0])
            reader = asyncio.StreamReader()
            reader.feed_data(encode_frame(_hello()))
            writer = Writer()
            task = asyncio.create_task(endpoint._session(reader, cast(Any, writer)))
            while endpoint.writer is None:
                await asyncio.sleep(0)
            tick[0] = 200
            if superseded:
                endpoint.writer = cast(Any, Writer())
                endpoint.identity = replace(
                    cast(ExecutorIdentity, endpoint.identity), connection_generation=2
                )
                reader.feed_data(encode_frame(WireMessage(WireMessageKind.error, "old", {})))
            else:
                malformed = b'{"kind":"invented","request_id":"bad","payload":{}}'
                reader.feed_data(struct.pack("!I", len(malformed)) + malformed)
            await task
            assert endpoint.last_contact_at_ms == 100
            assert writer.closed

    asyncio.run(run())


def test_ask_evidence_matches_actual_child_not_just_reused_ask_and_turn(
    tmp_db: Connection, fake_clock: ClockForTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.backends.contracts import TurnToken

    endpoint = _endpoint(tmp_db, fake_clock)
    actual = ChildReference("inc", "actual", "conversation")
    turn = TurnToken("turn", 1)
    child = RemoteBackendChild(actual, cast(Any, None))
    state = SimpleNamespace(child=child, running_turn=SimpleNamespace(token=turn))
    endpoint.runtime = SimpleNamespace(
        system=SimpleNamespace(_conversations={"conversation": state})
    )
    evidence = AskEvidence(
        AskReference(replace(actual, child_id="old"), turn, "ask"),
        1,
        AskReachability.reachable,
        100,
    )
    monkeypatch.setattr(endpoint.store, "evidence_for_conversation", lambda _: (evidence,))
    assert endpoint._current_ask_evidence("conversation", "ask") is None
    assert endpoint.conversation_context("conversation") == {
        "child": encode_value(actual),
        "turn": encode_value(turn),
    }
    evidence = replace(evidence, ask=replace(evidence.ask, child=actual))
    assert endpoint._current_ask_evidence("conversation", "ask") == evidence
    endpoint.ready = True
    endpoint.identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "inc", 1)
    assert endpoint.ask_status("conversation", "ask")["answer_available"] is True
    assert endpoint.ask_status("conversation", "other")["answer_available"] is False
    endpoint.identity = replace(endpoint.identity, connection_generation=2)
    assert endpoint.ask_status("conversation", "ask")["answer_available"] is False
    endpoint.identity = replace(
        endpoint.identity, executor_incarnation="replacement", connection_generation=1
    )
    assert endpoint.ask_status("conversation", "ask")["answer_available"] is False


def test_draft_inherits_project_matches_write_and_never_writes(
    tmp_db: Connection, fake_clock: ClockForTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        locale_configuration.LOCALE_SUPPORTED_BACKENDS,
        TicketLocale.macos,
        locale_configuration.LOCALE_SUPPORTED_BACKENDS[TicketLocale.vps],
    )
    project = projects.create_project(
        tmp_db, name="Mac", priority=Priority.P2, requires_macos=True, now=1
    )
    item = sprints.create_item(
        tmp_db, title="Parent", project_id=project.id, priority=Priority.P2, clock=fake_clock
    )
    before = tuple(tmp_db.iterdump())
    result = asyncio.run(
        read_ticket_creation_context(
            {"worker_type": "coding", "orchestrator": {"kind": "sprint_item", "id": item.id}},
            tmp_db,
            cast(Any, None),
        )
    )
    assert tuple(tmp_db.iterdump()) == before
    assert result["project_id"] == project.id
    assert result["resolved_locale"] == "macos"
    assert all(choice["compatible"] for choice in result["locale_choices"])
    created = data.create_ticket(
        tmp_db,
        title="Actual",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        sprint_item_id=item.id,
    )
    assert created.locale.value == result["resolved_locale"]
    assert created.employee_backend == result["employee_backend"]


def test_draft_incompatible_choice_explains_canonical_create_refusal(tmp_db: Connection) -> None:
    result = asyncio.run(
        read_ticket_creation_context(
            {
                "worker_type": "coding",
                "employee_backend": "claude",
                "employee_launch_model": "claude-model",
                "locale": "macos",
            },
            tmp_db,
            cast(Any, None),
        )
    )
    choice = next(choice for choice in result["locale_choices"] if choice["locale"] == "macos")
    assert result["resolved_locale"] == "macos"
    assert not choice["compatible"]
    with pytest.raises(PlannerError) as error:
        data.create_ticket(
            tmp_db,
            title="Actual",
            worker_type="coding",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            employee_backend="claude",
            employee_launch_model="claude-model",
            locale="macos",
        )
    assert choice["reason"] == error.value.message


@pytest.mark.parametrize(
    "fields",
    [
        {"project_id": "missing"},
        {"orchestrator": {"kind": "sprint_item", "id": "missing"}},
        {"orchestrator": {"kind": "owner", "id": "owner"}},
        {"worker_type": "missing"},
        {"employee_backend": "missing"},
        {"employee_launch_model": "disabled-model"},
        {"locale": None},
        {"locale": "missing"},
        {"sprint_item_id": "obsolete"},
    ],
)
def test_invalid_draft_never_writes(tmp_db: Connection, fields: dict[str, Any]) -> None:
    from planner.conversation.backend_state import write_model_enablement
    from planner.conversation.contracts import ConversationBackendKey

    write_model_enablement(tmp_db, ConversationBackendKey.codex, "disabled-model", False)
    tmp_db.commit()
    before = tuple(tmp_db.iterdump())
    with pytest.raises(PlannerError):
        asyncio.run(
            read_ticket_creation_context(
                {"worker_type": "coding", **fields}, tmp_db, cast(Any, None)
            )
        )
    assert tuple(tmp_db.iterdump()) == before


def test_preview_receipt_advances_contact_without_per_ask_liveness(
    tmp_db: Connection, fake_clock: ClockForTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.backends.contracts import TurnToken
    from planner.previews.contracts import preview_identity
    from planner.previews.streams import PreviewStreams

    endpoint = _endpoint(tmp_db, fake_clock)
    tick = [100]
    monkeypatch.setattr(vps, "now_ms", lambda: tick[0])
    received = asyncio.Event()
    original_feed = PreviewStreams.feed

    async def probe() -> None:
        return None

    async def unexpected_dispatch(*_: object) -> None:
        pytest.fail("preview receipt must bypass domain dispatch")

    def feed(streams: PreviewStreams, message: WireMessage) -> None:
        assert endpoint.last_contact_at_ms == 200
        assert endpoint.observed_at_ms == 100
        assert endpoint.ask_status("conversation", "ask")["answer_available"] is False
        original_feed(streams, message)
        received.set()

    monkeypatch.setattr(endpoint, "_measure_clock", probe)
    monkeypatch.setattr(endpoint, "_handle", unexpected_dispatch)
    monkeypatch.setattr(PreviewStreams, "feed", feed)

    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(encode_frame(_hello()))
        task = asyncio.create_task(endpoint._session(reader, cast(Any, Writer())))
        while endpoint.writer is None:
            await asyncio.sleep(0)
        identity = cast(ExecutorIdentity, endpoint.identity)
        child_reference = ChildReference(identity.executor_incarnation, "child", "conversation")
        turn = TurnToken("turn", 1)
        child = RemoteBackendChild(child_reference, cast(Any, None))
        state = SimpleNamespace(child=child, running_turn=SimpleNamespace(token=turn))
        endpoint.runtime = SimpleNamespace(
            system=SimpleNamespace(_conversations={"conversation": state})
        )
        evidence = AskEvidence(
            AskReference(child_reference, turn, "ask"),
            identity.connection_generation,
            AskReachability.uncertain,
            100,
        )
        monkeypatch.setattr(endpoint.store, "evidence_for_conversation", lambda _: (evidence,))
        # Reconciled transport permits a preview, but this ask still lacks liveness.
        endpoint.ready = True
        tick[0] = 200
        reader.feed_data(
            encode_frame(
                WireMessage(WireMessageKind.preview_close, str(uuid4()), preview_identity(identity))
            )
        )
        await asyncio.wait_for(received.wait(), 1)
        assert endpoint.ask_status("conversation", "ask")["answer_available"] is False
        assert endpoint.health()["last_contact_at_ms"] == 200
        reader.feed_eof()
        await task
        assert endpoint.health()["last_contact_at_ms"] == 200

    asyncio.run(run())
