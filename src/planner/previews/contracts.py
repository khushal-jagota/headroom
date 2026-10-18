"""Strict, ephemeral stream payloads. No address other than a port crosses the link."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from uuid import UUID

from planner.executor.contracts import ExecutorIdentity, JsonObject, WireMessage, WireMessageKind
from planner.previews.configuration import PREVIEW_CHUNK_BYTES


@dataclass(frozen=True, slots=True)
class PreviewIdentity:
    executor_incarnation: str
    connection_generation: int


@dataclass(frozen=True, slots=True)
class PreviewOpen(PreviewIdentity):
    port: int


@dataclass(frozen=True, slots=True)
class PreviewOpened(PreviewIdentity):
    pass


@dataclass(frozen=True, slots=True)
class PreviewData(PreviewIdentity):
    content_base64: str


@dataclass(frozen=True, slots=True)
class PreviewCredit(PreviewIdentity):
    byte_count: int


@dataclass(frozen=True, slots=True)
class PreviewClose(PreviewIdentity):
    pass


@dataclass(frozen=True, slots=True)
class PreviewError(PreviewIdentity):
    message: str


type PreviewPayload = (
    PreviewOpen | PreviewOpened | PreviewData | PreviewCredit | PreviewClose | PreviewError
)

PREVIEW_KINDS = frozenset(
    (
        WireMessageKind.preview_open,
        WireMessageKind.preview_opened,
        WireMessageKind.preview_data,
        WireMessageKind.preview_credit,
        WireMessageKind.preview_close,
        WireMessageKind.preview_error,
    )
)


def preview_identity(identity: ExecutorIdentity) -> JsonObject:
    return {
        "executor_incarnation": identity.executor_incarnation,
        "connection_generation": identity.connection_generation,
    }


def decode_preview(message: WireMessage, identity: ExecutorIdentity) -> PreviewPayload:
    """Reject coercion, extra fields, stale generations and oversized data before admission."""
    try:
        UUID(message.request_id)
    except (ValueError, AttributeError) as error:
        raise ValueError("invalid preview stream UUID") from error
    payload = message.payload
    expected = preview_identity(identity)
    for key, value in expected.items():
        if type(payload.get(key)) is not type(value) or payload.get(key) != value:
            raise ValueError("preview identity does not match current executor generation")
    base = (identity.executor_incarnation, identity.connection_generation)
    kind = message.kind
    extra: set[str] = set()
    if kind is WireMessageKind.preview_open:
        extra = {"port"}
    elif kind is WireMessageKind.preview_data:
        extra = {"content_base64"}
    elif kind is WireMessageKind.preview_credit:
        extra = {"byte_count"}
    elif kind is WireMessageKind.preview_error:
        extra = {"message"}
    elif kind not in (WireMessageKind.preview_opened, WireMessageKind.preview_close):
        raise ValueError("unsupported preview frame")
    if set(payload) != set(expected) | extra:
        raise ValueError("unexpected preview fields")
    if kind is WireMessageKind.preview_open:
        port = payload["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid preview port")
        return PreviewOpen(*base, port)
    if kind is WireMessageKind.preview_data:
        content = payload["content_base64"]
        if type(content) is not str:
            raise ValueError("invalid preview data")
        decode_content(content)
        return PreviewData(*base, content)
    if kind is WireMessageKind.preview_credit:
        count = payload["byte_count"]
        if type(count) is not int or not 1 <= count <= PREVIEW_CHUNK_BYTES:
            raise ValueError("invalid preview credit")
        return PreviewCredit(*base, count)
    if kind is WireMessageKind.preview_error:
        detail = payload["message"]
        if type(detail) is not str or len(detail) > 256:
            raise ValueError("invalid preview error")
        return PreviewError(*base, detail)
    if kind is WireMessageKind.preview_opened:
        return PreviewOpened(*base)
    return PreviewClose(*base)


def decode_content(content: str) -> bytes:
    if len(content) > 4 * ((PREVIEW_CHUNK_BYTES + 2) // 3):
        raise ValueError("preview data exceeds chunk limit")
    try:
        decoded = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("invalid preview base64") from error
    if not 1 <= len(decoded) <= PREVIEW_CHUNK_BYTES:
        raise ValueError("invalid preview chunk size")
    return decoded
