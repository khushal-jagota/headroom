"""Bounded, typed frames shared by SSH stdio and owner-only local IPC."""

from __future__ import annotations

import asyncio
import re
import struct
from uuid import UUID

from pydantic import TypeAdapter

from planner.executor.configuration import (
    MAX_ENVELOPE_BYTES,
    PROTOCOL_VERSION,
    SPOOL_FORMAT_VERSION,
)
from planner.executor.contracts import (
    ExecutorIdentity,
    FrameReader,
    WireMessage,
)


class ProtocolRefused(ValueError):
    """The peer did not speak the agreed protocol; replay must not start."""


def validate_identity(identity: ExecutorIdentity) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", identity.application_revision):
        raise ProtocolRefused("application revision must be full source SHA")
    try:
        UUID(identity.executor_incarnation)
    except ValueError as error:
        raise ProtocolRefused("incarnation must be UUID") from error
    if any(
        type(value) is not int
        for value in (
            identity.protocol_version,
            identity.spool_format_version,
            identity.connection_generation,
        )
    ):
        raise ProtocolRefused("identity versions and generation must be integers")
    if (
        identity.protocol_version != PROTOCOL_VERSION
        or identity.spool_format_version != SPOOL_FORMAT_VERSION
    ):
        raise ProtocolRefused("executor protocol or spool format mismatch")
    if identity.connection_generation < 1 or not identity.executor_incarnation:
        raise ProtocolRefused("invalid executor identity")


def encode_frame(value: object) -> bytes:
    body = TypeAdapter(type(value)).dump_json(value)
    if not body or len(body) > MAX_ENVELOPE_BYTES:
        raise ProtocolRefused("frame exceeds envelope limit")
    return struct.pack("!I", len(body)) + body


async def read_typed_frame[T](reader: FrameReader, value_type: type[T]) -> T:
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    if not length or length > MAX_ENVELOPE_BYTES:
        raise ProtocolRefused("invalid frame length")
    return TypeAdapter(value_type).validate_json(await reader.readexactly(length), strict=True)


async def read_frame(reader: FrameReader) -> WireMessage:
    return await read_typed_frame(reader, WireMessage)


async def write_frame(writer: asyncio.StreamWriter, value: object) -> None:
    writer.write(encode_frame(value))
    await writer.drain()


def parse_identity(payload: object) -> ExecutorIdentity:
    if not isinstance(payload, dict) or set(payload) != {
        "protocol_version",
        "application_revision",
        "spool_format_version",
        "executor_incarnation",
        "connection_generation",
    }:
        raise ProtocolRefused("invalid identity fields")
    for key in ("protocol_version", "spool_format_version", "connection_generation"):
        if type(payload[key]) is not int:
            raise ProtocolRefused("identity integer field has wrong type")
    for key in ("application_revision", "executor_incarnation"):
        if type(payload[key]) is not str:
            raise ProtocolRefused("identity text field has wrong type")
    identity = TypeAdapter(ExecutorIdentity).validate_python(payload)
    validate_identity(identity)
    return identity
