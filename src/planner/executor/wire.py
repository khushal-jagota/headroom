"""Bounded control messages and length-prefixed owner-only local IPC."""

from __future__ import annotations

import struct

from pydantic import TypeAdapter

from planner.execution_identity import ProtocolRefused
from planner.executor.configuration import MAX_ENVELOPE_BYTES
from planner.executor.contracts import FrameReader, WireMessage


def _encode_payload(value: object) -> bytes:
    body = TypeAdapter(type(value)).dump_json(value)
    if not body or len(body) > MAX_ENVELOPE_BYTES:
        raise ProtocolRefused("frame exceeds envelope limit")
    return body


async def read_typed_frame[T](reader: FrameReader, value_type: type[T]) -> T:
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    if not length or length > MAX_ENVELOPE_BYTES:
        raise ProtocolRefused("invalid frame length")
    return TypeAdapter(value_type).validate_json(await reader.readexactly(length), strict=True)


def encode_message(message: WireMessage) -> bytes:
    return _encode_payload(message)


def decode_message(body: bytes) -> WireMessage:
    if not body or len(body) > MAX_ENVELOPE_BYTES:
        raise ProtocolRefused("message exceeds envelope limit")
    return TypeAdapter(WireMessage).validate_json(body, strict=True)


def encode_frame(value: object) -> bytes:
    """Local IPC has no message boundaries, so prefix its bounded payload."""
    body = _encode_payload(value)
    return struct.pack("!I", len(body)) + body
