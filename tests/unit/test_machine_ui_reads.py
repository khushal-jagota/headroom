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
from fastapi import FastAPI, Request
from tests.support.executor_control import MessageReader, message_link
from tests.support.executor_turns import persist_pending_turn
from tests.unit.test_executor_native_runtime import canonical_snapshot

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.authctx import RequestContext
from planner.core.clock import TestClock as ClockForTest
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.errors import PlannerError
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity
from planner.executor import link as link_module
from planner.executor import vps
from planner.executor.protocol import (
    AskEvidence,
    AskReachability,
    ConnectionState,
    ExecutorHealth,
    JsonObject,
    WireMessage,
    WireMessageKind,
    encode_message,
    encode_value,
)
from planner.executor.remote import RemoteBackendChild
from planner.managers import data as managers
from planner.projects import data as projects
from planner.tickets import data, locale_configuration
from planner.tickets.api import read_ticket_creation_context
from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale


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
    monkeypatch.setattr(link_module, "now_ms", lambda: tick[0])
    handled = asyncio.Event()

    async def send(message: WireMessage) -> None:
        del message

    async def probe() -> None:
        return None

    async def handle(message: WireMessage, identity: ExecutorIdentity, writer: object) -> None:
        assert endpoint.last_contact_at_ms == 200
        await vps.ExecutorEndpoint._handle(endpoint, message, identity, cast(Any, writer))
        handled.set()

    monkeypatch.setattr(endpoint, "_measure_clock", probe)
    monkeypatch.setattr(endpoint, "_handle", handle)

    async def run() -> None:
        reader = MessageReader()
        reader.feed_data(encode_message(_hello()))
        task = asyncio.create_task(endpoint._session(reader, cast(Any, Writer())))
        while endpoint.link is None:
            await asyncio.sleep(0)
        assert endpoint.observed_at_ms == endpoint.last_contact_at_ms == 100
        tick[0] = 200
        # A valid frame establishes contact before its domain handler executes.
        reader.feed_data(encode_message(WireMessage(WireMessageKind.error, "refusal", {})))
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
            monkeypatch.setattr(endpoint, "_measure_clock", probe)
            tick = [100]
            monkeypatch.setattr(vps, "now_ms", lambda tick=tick: tick[0])
            monkeypatch.setattr(link_module, "now_ms", lambda tick=tick: tick[0])
            reader = MessageReader()
            reader.feed_data(encode_message(_hello()))
            writer = Writer()
            task = asyncio.create_task(endpoint._session(reader, cast(Any, writer)))
            while endpoint.link is None:
                await asyncio.sleep(0)
            tick[0] = 200
            if superseded:
                assert endpoint.link is not None
                endpoint.link.close()
                endpoint.link = message_link(Writer(), cast(ExecutorIdentity, endpoint.identity))
                endpoint.identity = replace(
                    cast(ExecutorIdentity, endpoint.identity), connection_generation=2
                )
                reader.feed_data(encode_message(WireMessage(WireMessageKind.error, "old", {})))
            else:
                malformed = b'{"kind":"invented","request_id":"bad","payload":{}}'
                reader.feed_data(struct.pack("!I", len(malformed)) + malformed)
            await task
            assert endpoint.last_contact_at_ms == 100
            assert writer.closed

    asyncio.run(run())


def test_durable_context_matches_exact_child_after_restart_and_allows_offline_answer(
    tmp_db: Connection,
    fake_clock: ClockForTest,
) -> None:
    from planner.conversation.backends.contracts import TurnToken

    endpoint = _endpoint(tmp_db, fake_clock)
    actual = ChildReference("inc", "actual", "conversation")
    ask = AskReference(actual, TurnToken("conversation", 1), "ask", AskKind.permission)
    identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "inc", 1)
    endpoint.store.begin_connection(identity, 100)
    # This fixture represents a new combined-release ask with an explicit public/provider map.
    persist_pending_turn(
        endpoint.store,
        ask,
        runtime_overrides={
            "pending_permission_ask_ids": ["1:permission:ask"],
            "permission_provider_ids": {"1:permission:ask": "ask"},
            "user_input_provider_ids": {},
        },
    )
    old = replace(ask, child=replace(actual, child_id="old"))
    endpoint.store.publish_ask(old, ("allow", "deny"), 100)
    assert endpoint._current_ask_evidence("conversation", "1:permission:ask") is None
    endpoint.store.publish_ask(ask, ("allow", "deny"), 101)
    # A new endpoint has no live children, runtime state, connection or inventory.
    restored = _endpoint(tmp_db, fake_clock)
    assert restored.children == {} and restored.identity is None
    assert restored.owns_conversation("conversation")
    assert restored.conversation_context("conversation") == {
        "child": encode_value(actual),
        "turn": encode_value(ask.turn),
    }
    status = restored.ask_status("conversation", "1:permission:ask")
    assert status["answer_available"] is True
    assert restored.ask_status("conversation", "other")["answer_available"] is False
    answer = restored.store.choose_answer(ask, "allow", 999, 200)
    assert restored.ask_status("conversation", "1:permission:ask")["answer_available"] is False
    assert restored.conversation_asks("conversation")[-1]["answer"] == encode_value(answer)
    restored.store.close_child(actual, 201)
    final = _endpoint(tmp_db, fake_clock)
    assert final.ask_status("conversation", "1:permission:ask")["answer_available"] is False
    evidence = final._current_ask_evidence("conversation", "1:permission:ask")
    assert evidence is not None and evidence.reachability == AskReachability.child_gone


def test_draft_inherits_project_matches_write_and_never_writes(
    tmp_db: Connection, fake_clock: ClockForTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        locale_configuration.LOCALE_SUPPORTED_BACKENDS,
        TicketLocale.macos,
        locale_configuration.LOCALE_SUPPORTED_BACKENDS[TicketLocale.vps],
    )
    project = projects.create_project(tmp_db, name="Mac", requires_macos=True, now=1)
    item = managers.create_manager(tmp_db, title="Parent", project_id=project.id, clock=fake_clock)
    before = tuple(tmp_db.iterdump())
    result = asyncio.run(
        read_ticket_creation_context(
            {"worker_type": "coding", "manager": {"kind": "manager", "id": item.id}},
            tmp_db,
            RequestContext(OWNER_PRINCIPAL),
            _draft_request(),
        )
    )
    legacy_alias = asyncio.run(
        read_ticket_creation_context(
            {"worker_type": "coding", "orchestrator": {"kind": "manager", "id": item.id}},
            tmp_db,
            RequestContext(OWNER_PRINCIPAL),
            _draft_request(),
        )
    )
    assert legacy_alias == result
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
        manager_id=item.id,
    )
    assert created.locale.value == result["resolved_locale"]
    assert created.employee_backend == result["employee_backend"]


def test_claude_mac_draft_matches_canonical_create(tmp_db: Connection) -> None:
    result = asyncio.run(
        read_ticket_creation_context(
            {
                "worker_type": "coding",
                "employee_backend": "claude",
                "employee_launch_model": "claude-model",
                "locale": "macos",
            },
            tmp_db,
            RequestContext(OWNER_PRINCIPAL),
            _draft_request(),
        )
    )
    choice = next(choice for choice in result["locale_choices"] if choice["locale"] == "macos")
    assert result["resolved_locale"] == "macos"
    assert choice["compatible"]
    assert choice["reason"] is None
    created = data.create_ticket(
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
    assert created.locale.value == result["resolved_locale"]
    assert created.employee_backend == result["employee_backend"] == "claude"
    assert created.employee_launch_model == "claude-model"


@pytest.mark.parametrize(
    "fields",
    [
        {"project_id": "missing"},
        {"manager": {"kind": "manager", "id": "missing"}},
        {"manager": {"kind": "owner", "id": "owner"}},
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
                {"worker_type": "coding", **fields},
                tmp_db,
                RequestContext(OWNER_PRINCIPAL),
                _draft_request(),
            )
        )
    assert tuple(tmp_db.iterdump()) == before


def test_control_receipt_advances_contact_without_per_ask_liveness(
    tmp_db: Connection, fake_clock: ClockForTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.backends.contracts import TurnToken

    endpoint = _endpoint(tmp_db, fake_clock)
    tick = [100]
    monkeypatch.setattr(vps, "now_ms", lambda: tick[0])
    monkeypatch.setattr(link_module, "now_ms", lambda: tick[0])
    received = asyncio.Event()
    original_handle = endpoint._handle

    async def probe() -> None:
        return None

    async def handle(message: WireMessage, identity: ExecutorIdentity, writer: Any) -> None:
        assert endpoint.last_contact_at_ms == 200
        assert endpoint.observed_at_ms == 100
        assert endpoint.ask_status("conversation", "1:permission:ask")["answer_available"] is False
        await original_handle(message, identity, writer)
        received.set()

    monkeypatch.setattr(endpoint, "_measure_clock", probe)
    monkeypatch.setattr(endpoint, "_handle", handle)

    async def run() -> None:
        reader = MessageReader()
        reader.feed_data(encode_message(_hello()))
        task = asyncio.create_task(endpoint._session(reader, cast(Any, Writer())))
        while endpoint.link is None:
            await asyncio.sleep(0)
        identity = cast(ExecutorIdentity, endpoint.identity)
        child_reference = ChildReference(identity.executor_incarnation, "child", "conversation")
        turn = TurnToken("turn", 1)
        child = RemoteBackendChild(
            child_reference, cast(Any, None), lambda start: canonical_snapshot()
        )
        state = SimpleNamespace(child=child, running_turn=SimpleNamespace(token=turn))
        endpoint.runtime = SimpleNamespace(
            system=SimpleNamespace(_conversations={"conversation": state})
        )
        evidence = AskEvidence(
            AskReference(child_reference, turn, "ask", AskKind.permission),
            identity.connection_generation,
            AskReachability.uncertain,
            100,
        )
        monkeypatch.setattr(endpoint.store, "evidence_for_conversation", lambda _: (evidence,))
        # A control receipt shows contact, but this ask still lacks liveness.
        endpoint.ready = True
        tick[0] = 200
        request_id = str(uuid4())
        response: asyncio.Future[WireMessage] = asyncio.get_running_loop().create_future()
        assert endpoint.link is not None
        endpoint.link._pending[request_id] = response
        reader.feed_data(
            encode_message(WireMessage(WireMessageKind.transport_opened, request_id, {}))
        )
        await asyncio.wait_for(response, 1)
        assert response.done()
        assert response.result().kind is WireMessageKind.transport_opened
        assert endpoint.ask_status("conversation", "1:permission:ask")["answer_available"] is False
        assert endpoint.health()["last_contact_at_ms"] == 200
        await endpoint.close()
        reader.feed_eof()
        await task
        assert endpoint.health()["last_contact_at_ms"] == 200

    asyncio.run(run())
