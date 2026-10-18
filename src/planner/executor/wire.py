"""Bounded, typed frames shared by SSH stdio and owner-only local IPC."""

from __future__ import annotations

import asyncio
import base64
import re
import struct
from uuid import UUID

from pydantic import TypeAdapter

from planner.executor.configuration import (
    FILE_CHUNK_BYTES,
    MAX_ENVELOPE_BYTES,
    PROTOCOL_VERSION,
    SPOOL_FORMAT_VERSION,
)
from planner.executor.contracts import (
    ExecutorIdentity,
    FileChunk,
    FileChunkAcknowledgment,
    WireMessage,
    WireMessageKind,
)


class FileChunkAcknowledgmentRefused(ValueError):
    """The chunk reply is unusable; retain its caller through link reconciliation."""


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


async def read_typed_frame[T](reader: asyncio.StreamReader, value_type: type[T]) -> T:
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    if not length or length > MAX_ENVELOPE_BYTES:
        raise ProtocolRefused("invalid frame length")
    return TypeAdapter(value_type).validate_json(await reader.readexactly(length), strict=True)


async def read_frame(reader: asyncio.StreamReader) -> WireMessage:
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


def validate_file_chunk_ack(response: WireMessage, chunk: FileChunk) -> FileChunkAcknowledgment:
    """Require an exact typed acknowledgment before advancing the file sender.

    This confirms one successful chunk write only. It grants neither completed
    file publication nor acceptance of a dependent application envelope.
    """
    if response.kind is not WireMessageKind.file_chunk_ack:
        raise FileChunkAcknowledgmentRefused("File chunk requires a file_chunk_ack response")
    payload = response.payload
    if not isinstance(payload, dict) or set(payload) != {"publication_id", "offset", "byte_count"}:
        raise FileChunkAcknowledgmentRefused("Invalid file chunk acknowledgment fields")
    publication_id = payload["publication_id"]
    offset = payload["offset"]
    byte_count = payload["byte_count"]
    if type(publication_id) is not str or type(offset) is not int or type(byte_count) is not int:
        raise FileChunkAcknowledgmentRefused("Invalid file chunk acknowledgment types")
    if (
        type(chunk.offset) is not int
        or chunk.offset < 0
        or type(chunk.publication_id) is not str
        or not chunk.publication_id
        or type(chunk.content_base64) is not str
    ):
        raise FileChunkAcknowledgmentRefused("Invalid expected file chunk")
    try:
        expected_count = len(base64.b64decode(chunk.content_base64, validate=True))
    except (ValueError, UnicodeError) as error:
        raise FileChunkAcknowledgmentRefused("Invalid expected file chunk encoding") from error
    if expected_count > FILE_CHUNK_BYTES:
        raise FileChunkAcknowledgmentRefused("Expected file chunk exceeds the chunk limit")
    if (
        publication_id != chunk.publication_id
        or offset != chunk.offset
        or byte_count != expected_count
    ):
        raise FileChunkAcknowledgmentRefused(
            "File chunk acknowledgment does not match the sent chunk"
        )
    return FileChunkAcknowledgment(publication_id, offset, byte_count)
