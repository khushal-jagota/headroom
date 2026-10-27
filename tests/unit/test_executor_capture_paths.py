"""Offline proof through the actual VPS request, session, handler and store boundaries."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from tests.unit.test_executor_authoritative_store import (
    effect,
    envelope,
)
from tests.unit.test_executor_authoritative_store import (
    store as store,
)
from tests.unit.test_executor_capture import TARGET, make_capture, records, start

from planner.core.clock import RealClock
from planner.executor import vps
from planner.executor.capture import Capture
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import ExecutorIdentity, WireMessage, WireMessageKind
from planner.executor.store import ExecutorStore
from planner.executor.vps import ExecutorEndpoint
from planner.executor.wire import encode_frame


def owner(generation: int = 1) -> ExecutorIdentity:
    return ExecutorIdentity(
        PROTOCOL_VERSION,
        "a" * 40,
        SPOOL_FORMAT_VERSION,
        TARGET["executor_incarnation"],
        generation,
    )


def writer(reader: asyncio.StreamReader | None = None) -> Any:
    result = Mock()
    result.drain = AsyncMock()
    if reader is not None:
        result.close.side_effect = reader.feed_eof
    return result


def endpoint(tmp_path: Path, capture: Any) -> ExecutorEndpoint:
    result = ExecutorEndpoint(str(tmp_path / "unused.sqlite"), RealClock())
    result._capture = capture
    result.identity = owner()
    result.writer = writer()
    result.store = Mock()
    result._measure_clock = AsyncMock()  # type: ignore[method-assign]
    return result


def associate(capture: Capture, connection: Any, generation: int = 1) -> Any:
    if capture.startup_ns is None:
        start(capture, capture.created_ns)
    observation = capture.hello(owner(generation))
    assert observation is not None
    capture.session_writer(connection, observation)
    return observation


@pytest.mark.parametrize("blocked", ["lock", "drain", "response", "inner_timeout"])
def test_vps_deadline_records_wire_and_saved_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    async def run() -> None:
        monkeypatch.setattr(vps, "REQUEST_TIMEOUT_SECONDS", 0.02)
        capture = make_capture(tmp_path)
        instance = endpoint(tmp_path, capture)
        original: Any = instance.writer
        associate(capture, original)
        error = TimeoutError("credential must stay private")
        if blocked == "lock":
            await instance._send_lock.acquire()
        elif blocked == "drain":
            original.drain.side_effect = asyncio.Event().wait
        elif blocked == "inner_timeout":
            original.drain.side_effect = error
        pending_wire_id: str | None = None
        if blocked == "lock":
            request = asyncio.create_task(
                instance._request(WireMessageKind.health, {"private": "argument secret"})
            )
            while not instance._responses:
                await asyncio.sleep(0)
            pending_wire_id = next(iter(instance._responses))
            with pytest.raises(TimeoutError) as raised:
                await request
        else:
            with pytest.raises(TimeoutError) as raised:
                await instance._request(WireMessageKind.health, {"private": "argument secret"})
        if blocked == "inner_timeout":
            assert raised.value is error
        original.close.assert_called_once_with()
        assert not instance._responses
        assert original.write.call_count == (0 if blocked == "lock" else 1)
        if blocked == "lock":
            instance._send_lock.release()
        event = next(r for r in records(capture) if r["phase"] == "request_timeout")
        assert event["timeout_source"] == (
            "inner_timeout" if blocked == "inner_timeout" else "request_deadline"
        )
        assert event["generation"] == 1
        assert event["wire_kind"] == "health" and event["direction"] == "outbound"
        assert UUID(event["wire_request_id"]) != UUID(event["request_id"])
        if blocked == "lock":
            assert pending_wire_id is not None
            assert event["wire_request_id"] == str(UUID(pending_wire_id))
        else:
            frame = json.loads(original.write.call_args.args[0][4:])
            assert event["wire_request_id"] == str(UUID(frame["request_id"]))
        assert b"secret" not in capture.config.sink.read_bytes()
        capture.finish("test_done")

    asyncio.run(run())


def test_saved_owner_survives_replacement_and_original_session_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        monkeypatch.setattr(vps, "REQUEST_TIMEOUT_SECONDS", 0.03)
        capture = make_capture(tmp_path)
        start(capture, capture.created_ns)
        instance = endpoint(tmp_path, capture)
        instance.identity, instance.writer = None, None
        original_reader, replacement_reader = asyncio.StreamReader(), asyncio.StreamReader()
        original, replacement = writer(original_reader), writer(replacement_reader)
        original_reader.feed_data(hello_frame())
        original_session = asyncio.create_task(instance._session(original_reader, original))
        while instance.writer is not original:
            await asyncio.sleep(0)
        task = asyncio.create_task(instance._request(WireMessageKind.health, {}))
        while not instance._responses:
            await asyncio.sleep(0)
        replacement_reader.feed_data(hello_frame(2))
        replacement_session = asyncio.create_task(
            instance._session(replacement_reader, replacement)
        )
        while instance.writer is not replacement:
            await asyncio.sleep(0)
        await asyncio.wait_for(original_session, 1)
        assert id(original) not in capture.writer_owners
        assert original is not None
        prior_close_count = original.close.call_count
        with pytest.raises(TimeoutError):
            await task
        assert original.close.call_count == prior_close_count + 1
        replacement.close.assert_not_called()
        failures = [r for r in records(capture) if r["phase"] == "request_timeout"]
        assert len(failures) == 1 and failures[0]["generation"] == 1
        closes = [r for r in records(capture) if r.get("close_cause") == "request_timeout"]
        assert closes and all(r["generation"] == 1 for r in closes)
        replacement_reader.feed_eof()
        await asyncio.wait_for(replacement_session, 1)
        await asyncio.gather(*tuple(instance._tasks))
        capture.finish("test_done")

    asyncio.run(run())


@pytest.mark.parametrize("observer_mode", ["disabled", "broken", "enabled"])
def test_request_cancellation_and_observer_failure_preserve_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observer_mode: str
) -> None:
    class Broken:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError("observer secret")

    async def run() -> None:
        monkeypatch.setattr(vps, "REQUEST_TIMEOUT_SECONDS", 0.02)
        capture = make_capture(tmp_path) if observer_mode == "enabled" else None
        instance = endpoint(tmp_path, Broken() if observer_mode == "broken" else capture)
        original: Any = instance.writer
        if capture is not None:
            associate(capture, original)
        task = asyncio.create_task(instance._request(WireMessageKind.health, {}))
        while not original.write.called:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not instance._responses
        original.close.assert_not_called()
        if capture is not None:
            assert not capture.outgoing_requests and not capture.failure_observations
            assert not any(r["phase"] == "request_timeout" for r in records(capture))
        with pytest.raises(TimeoutError):
            await instance._request(WireMessageKind.health, {})
        original.close.assert_called_once_with()
        assert not instance._responses
        if capture is not None:
            assert not capture.outgoing_requests and not capture.failure_observations
            capture.finish("test_done")

    asyncio.run(run())


def hello_frame(generation: int = 1) -> bytes:
    return encode_frame(
        WireMessage(
            WireMessageKind.hello,
            "0" * 32,
            {
                "identity": asdict(owner(generation)),
                "process_id": 1,
                "connection_state": "ready",
                "reconciliation_ready": True,
                "observed_at_ms": 1,
                "lost_at_ms": None,
            },
        )
    )


@pytest.mark.parametrize("cause", ["independent_eof", "request_timeout", "handler_failure"])
def test_actual_session_keeps_initiator_distinct_from_downstream_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cause: str
) -> None:
    async def run() -> None:
        monkeypatch.setattr(vps, "REQUEST_TIMEOUT_SECONDS", 0.03)
        capture = make_capture(tmp_path)
        start(capture, capture.created_ns)
        instance = endpoint(tmp_path, capture)
        instance.identity = None
        instance.writer = None
        reader = asyncio.StreamReader()
        connection = writer(reader)
        reader.feed_data(hello_frame())
        session = asyncio.create_task(instance._session(reader, connection))
        while instance.writer is None:
            await asyncio.sleep(0)
        if cause == "independent_eof":
            reader.feed_eof()
        elif cause == "request_timeout":
            with pytest.raises(TimeoutError):
                await instance._request(WireMessageKind.health, {})
        else:
            instance._read = AsyncMock(side_effect=RuntimeError("handler secret"))
            reader.feed_data(
                encode_frame(
                    WireMessage(
                        WireMessageKind.read_request,
                        "1" * 32,
                        {
                            "child": {
                                "executor_incarnation": owner().executor_incarnation,
                                "child_id": "child",
                                "conversation_id": "conversation",
                            },
                            "path": "/api/tickets",
                            "params": {},
                        },
                    )
                )
            )
        await asyncio.wait_for(session, 1)
        await asyncio.gather(*tuple(instance._tasks))
        output = records(capture)
        terminal = next(r for r in output if r["phase"] == "generation_close")
        assert terminal["exception_class"] == "IncompleteReadError"
        if cause == "independent_eof":
            assert terminal["transport_cause"] == "unknown"
            assert not terminal.get("first_close_token")
            assert not any(r["phase"] == "request_timeout" for r in output)
        else:
            assert terminal["transport_cause"] == "preceding_local_close"
            assert terminal.get("first_close_token")
            initiator = next(
                r
                for r in output
                if r["phase"]
                == ("request_timeout" if cause == "request_timeout" else "handler_enter")
                and r.get("failure_token")
            )
            assert terminal["first_failure_token"] == initiator["failure_token"]
            if cause == "handler_failure":
                assert initiator["wire_request_id"] == str(UUID("1" * 32))
                assert initiator["direction"] == "inbound"
                assert initiator["wire_kind"] == "read_request"
        assert b"handler secret" not in capture.config.sink.read_bytes()
        capture.finish("test_done")

    asyncio.run(run())


def test_actual_handler_response_failure_preserves_durable_receipt(
    tmp_path: Path, store: ExecutorStore
) -> None:
    from tests.unit.test_executor_authoritative_store import IDENTITY

    async def run() -> None:
        capture = make_capture(tmp_path)
        instance = endpoint(tmp_path, capture)
        instance.identity = IDENTITY
        instance.store = store
        accepted = envelope()

        # Keep the real acceptance transaction and observer boundary. The fixture
        # effect replaces unrelated ticket mutation authorization and publication.
        async def accept(envelope: Any, identity: Any, observer: Any = None) -> Any:
            return store.accept(envelope, effect, 1100, identity=identity, observer=observer)

        instance._accept_envelope = accept  # type: ignore[method-assign]
        connection: Any = instance.writer
        connection.drain.side_effect = OSError("response secret")
        message = WireMessage(WireMessageKind.envelope, "2" * 32, asdict(accepted))
        # The configured retained identity does not match this reusable store fixture.
        # A target observer proves receipt/send independence without altering fixture state.
        observation = associate(capture, connection)
        await instance._handle(message, IDENTITY, connection, observation)
        receipt = store.lookup_receipt(accepted.sender_id, accepted.submission_id)
        assert receipt is not None and receipt.disposition == "accepted"
        output = records(capture)
        send = next(r for r in output if r["phase"] == "response_send_failed")
        wrapper = next(r for r in output if r["phase"] == "handler_failed")
        assert send["failure_token"] == wrapper["failure_token"]
        assert wrapper["originating_phase"] == "response_send_failed"
        assert wrapper["first_failure_token"] == send["failure_token"]
        assert b"response secret" not in capture.config.sink.read_bytes()
        assert store.accept(accepted, effect, 1200, identity=IDENTITY) == receipt
        connection.close.assert_called_once_with()
        capture.finish("test_done")

    asyncio.run(run())


@pytest.mark.parametrize(
    "identifier, expected",
    [
        ("ABCDEF0123456789ABCDEF0123456789", "abcdef01-2345-6789-abcd-ef0123456789"),
        ("credential secret", None),
        ("a" * 37, None),
        (123, None),
    ],
)
def test_wire_correlation_normalizes_uuid_without_peer_text(
    tmp_path: Path, identifier: Any, expected: str | None
) -> None:
    capture = make_capture(tmp_path)
    connection = writer()
    associate(capture, connection)
    observation = capture.outgoing(owner(), connection, identifier, "kind secret")
    assert observation is not None
    observation.request_timeout(TimeoutError("exception secret"), True)
    event = next(r for r in records(capture) if r["phase"] == "request_timeout")
    assert event["wire_request_id"] == expected
    assert event["wire_kind"] == "unknown"
    assert UUID(event["request_id"])
    assert b"secret" not in capture.config.sink.read_bytes()
    observation.request_done()
    capture.finish("test_done")


@pytest.mark.parametrize("terminal", ["saturation", "sink_failure", "finish"])
def test_request_metadata_is_bounded_and_late_hooks_allocate_nothing(
    tmp_path: Path, terminal: str
) -> None:
    from planner.executor.capture import MAX_RECORDS, MAX_REQUEST_METADATA

    capture = make_capture(tmp_path)
    connection = writer()
    associate(capture, connection)
    observation = capture.outgoing(owner(), connection, "3" * 32, "health")
    assert observation is not None
    assert MAX_REQUEST_METADATA <= MAX_RECORDS
    if terminal == "saturation":
        for _ in range(MAX_REQUEST_METADATA):
            capture.outgoing(owner(), connection, "3" * 32, "health")
        assert records(capture)[-1]["truncated"]
    elif terminal == "sink_failure":
        capture.sink.close()
        observation.request_timeout(TimeoutError(), True)
    else:
        capture.finish("test_done")
    assert capture.closed
    assert not capture.writer_owners and not capture.outgoing_requests
    assert not capture.generation_evidence and not capture.failure_observations
    original = capture.config.sink.read_bytes()
    observation.request_timeout(TimeoutError("late secret"), True)
    capture.close_attempt(connection, "request_timeout", observation)
    assert capture.outgoing(owner(), connection, "3" * 32, "health") is None
    capture.session_writer(connection, observation)
    assert not capture.writer_owners and not capture.outgoing_requests
    assert capture.config.sink.read_bytes() == original


def test_concurrent_deadlines_keep_first_failure_and_first_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        monkeypatch.setattr(vps, "REQUEST_TIMEOUT_SECONDS", 0.02)
        capture = make_capture(tmp_path)
        instance = endpoint(tmp_path, capture)
        connection: Any = instance.writer
        associate(capture, connection)
        results = await asyncio.gather(
            instance._request(WireMessageKind.health, {}),
            instance._request(WireMessageKind.preview_bind, {}),
            return_exceptions=True,
        )
        assert all(isinstance(result, TimeoutError) for result in results)
        events = records(capture)
        failures = [r for r in events if r["phase"] == "request_timeout"]
        closes = [r for r in events if r["phase"] == "close_attempt"]
        assert len(failures) == 2 and len(closes) == 2
        assert failures[0]["failure_token"] != failures[1]["failure_token"]
        assert failures[1]["first_failure_token"] == failures[0]["failure_token"]
        assert closes[1]["first_close_token"] == closes[0]["close_token"]
        assert not instance._responses and not capture.outgoing_requests
        assert not capture.failure_observations
        capture.finish("test_done")

    asyncio.run(run())
