"""Strict preview contracts and bounded admission keep ephemeral work off the journal."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from uuid import uuid4

import pytest

from planner.executor.configuration import PROTOCOL_VERSION
from planner.executor.contracts import ExecutorIdentity, JsonObject, WireMessage, WireMessageKind
from planner.executor.wire import ProtocolRefused, validate_identity
from planner.previews.configuration import MAX_PREVIEW_STREAMS, PREVIEW_CHUNK_BYTES
from planner.previews.contracts import decode_preview, preview_identity
from planner.previews.streams import PreviewStreams

IDENTITY = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, 1, str(uuid4()), 1)


def frame(kind: WireMessageKind, extra: JsonObject | None = None) -> WireMessage:
    payload = preview_identity(IDENTITY)
    payload.update(extra or {})
    return WireMessage(kind, str(uuid4()), payload)


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        (WireMessageKind.preview_open, {"port": True}),
        (WireMessageKind.preview_open, {"port": "8000"}),
        (WireMessageKind.preview_open, {"port": 0}),
        (WireMessageKind.preview_open, {"port": 65536}),
        (WireMessageKind.preview_open, {"port": 8000, "host": "example.com"}),
        (WireMessageKind.preview_credit, {"byte_count": True}),
        (WireMessageKind.preview_credit, {"byte_count": 0}),
        (WireMessageKind.preview_credit, {"byte_count": PREVIEW_CHUNK_BYTES + 1}),
        (WireMessageKind.preview_data, {"content_base64": "@@@"}),
        (WireMessageKind.preview_data, {"content_base64": ""}),
        (
            WireMessageKind.preview_data,
            {"content_base64": base64.b64encode(b"x" * (PREVIEW_CHUNK_BYTES + 1)).decode()},
        ),
        (WireMessageKind.preview_close, {"host": "127.0.0.1"}),
        (WireMessageKind.preview_error, {"message": 17}),
    ],
)
def test_preview_payload_refuses_coercion_hosts_and_over_limit_data(
    kind: WireMessageKind, extra: JsonObject
) -> None:
    with pytest.raises(ValueError):
        decode_preview(frame(kind, extra), IDENTITY)


def test_preview_identity_fences_incarnation_generation_and_stream_id() -> None:
    valid = frame(WireMessageKind.preview_open, {"port": 8000})
    for identity in (
        replace(IDENTITY, connection_generation=2),
        replace(IDENTITY, executor_incarnation=str(uuid4())),
    ):
        with pytest.raises(ValueError, match="generation"):
            decode_preview(valid, identity)
    with pytest.raises(ValueError, match="UUID"):
        decode_preview(replace(valid, request_id="old-stream"), IDENTITY)
    payload = dict(valid.payload)
    payload["connection_generation"] = True
    with pytest.raises(ValueError):
        decode_preview(replace(valid, payload=payload), IDENTITY)


def test_preview_protocol_four_refuses_previous_protocol_three() -> None:
    assert PROTOCOL_VERSION == 4
    with pytest.raises(ProtocolRefused, match="mismatch"):
        validate_identity(replace(IDENTITY, protocol_version=3))


def test_admission_is_bounded_and_credit_must_match_exact_chunk() -> None:
    async def run() -> None:
        async def send(message: WireMessage) -> None:
            del message

        manager = PreviewStreams(IDENTITY, send, lambda: True, accept_open=False)
        for _ in range(MAX_PREVIEW_STREAMS):
            manager._reserve(str(uuid4()))
        with pytest.raises(ConnectionError, match="capacity"):
            manager._reserve(str(uuid4()))
        stream = next(iter(manager.streams.values()))
        stream.opened.set()
        data = replace(
            frame(WireMessageKind.preview_data, {"content_base64": "eA=="}),
            request_id=stream.stream_id,
        )
        manager.feed(data)
        assert stream.incoming.qsize() == 1
        with pytest.raises(ValueError, match="credit"):
            manager.feed(data)
        credit = replace(
            frame(WireMessageKind.preview_credit, {"byte_count": 1}), request_id=stream.stream_id
        )
        with pytest.raises(ValueError, match="outstanding"):
            manager.feed(credit)
        stream.outstanding_bytes = 1
        stream.credit.clear()
        manager.feed(credit)
        assert stream.credit.is_set() and stream.outstanding_bytes == 0
        await manager.close()

    asyncio.run(run())


def test_preview_work_is_refused_before_reconciliation() -> None:
    async def send(message: WireMessage) -> None:
        del message

    manager = PreviewStreams(IDENTITY, send, lambda: False, accept_open=True)
    with pytest.raises(ValueError, match="before reconciliation"):
        manager.feed(frame(WireMessageKind.preview_open, {"port": 8000}))
    assert not manager.streams


def test_activity_in_one_direction_preserves_stream_until_whole_stream_is_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import cast
    from unittest.mock import Mock

    from planner.previews import streams as streams_module

    async def run() -> None:
        timeout = 0.08
        monkeypatch.setattr(streams_module, "PREVIEW_IDLE_TIMEOUT_SECONDS", timeout)
        sent = asyncio.Event()
        manager: PreviewStreams

        async def send(message: WireMessage) -> None:
            if message.kind is WireMessageKind.preview_data:
                manager.feed(
                    WireMessage(
                        WireMessageKind.preview_credit,
                        message.request_id,
                        {**preview_identity(IDENTITY), "byte_count": 1},
                    )
                )
                sent.set()

        manager = PreviewStreams(IDENTITY, send, lambda: True, accept_open=False)
        stream = manager._reserve(str(uuid4()))
        stream.opened.set()
        reader = asyncio.StreamReader()
        # This direction receives no bytes, so the pump never calls this writer.
        writer = cast(asyncio.StreamWriter, Mock(spec=asyncio.StreamWriter))
        task = asyncio.create_task(manager._pump(stream, reader, writer))
        try:
            for _ in range(5):
                sent.clear()
                reader.feed_data(b"x")
                await asyncio.wait_for(sent.wait(), timeout)
                await asyncio.sleep(timeout / 3)
                assert not task.done()
            # Total activity exceeds one idle interval, but opposite-direction
            # silence does not close it. With both directions idle it expires.
            with pytest.raises(TimeoutError, match="idle timeout"):
                await asyncio.wait_for(task, timeout * 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await manager.close()

    asyncio.run(run())
