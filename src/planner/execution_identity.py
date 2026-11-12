"""Stable executor, child and callback identities shared across module boundaries."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import TypeAdapter

from planner.execution_configuration import (
    MIN_SUPPORTED_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SPOOL_FORMAT_VERSION,
)


class TicketLocale(StrEnum):
    vps = "vps"
    macos = "macos"


@dataclass(frozen=True, slots=True)
class TurnToken:
    """The core's name for one started turn.

    The core reserves its number durably before provider I/O. Numbers never repeat
    after restart, and historical identities never restore a process or callback.
    The core hands it to the adapter with the prompt that starts the turn, and
    the adapter puts it on everything it later reports about that turn. It is what lets
    the core tell this turn's news from the news of a turn that has already been ended and
    replaced. A cancelled turn's late-arriving live and operational events are dropped.
    Finished backend prose is runtime-only, including when it arrives after the ending.
    """

    conversation_id: str
    turn_number: int


@dataclass(frozen=True, slots=True)
class ExecutorIdentity:
    protocol_version: int
    application_revision: str
    spool_format_version: int
    executor_incarnation: str
    # Unread by protocol 22. Kept, and written back unchanged into handoff.json, only so
    # the previous runtime can still parse an identity this one wrote (rollback).
    connection_generation: int = 0


@dataclass(frozen=True, slots=True)
class ChildReference:
    """Child IDs are UUIDs minted at child creation, never vendor session IDs."""

    executor_incarnation: str
    child_id: str
    conversation_id: str


class AskKind(StrEnum):
    permission = "permission"
    user_input = "user_input"


@dataclass(frozen=True, slots=True)
class AskReference:
    child: ChildReference
    turn: TurnToken
    ask_id: str
    kind: AskKind


class ProtocolRefused(ValueError):
    """The peer did not speak the agreed protocol; replay must not start."""


MAC_NEEDS_UPDATE: Final = "Mac needs update"
MAC_VERSION_INCOMPATIBLE: Final = "Mac version is incompatible"


def supports_executor_versions(protocol_version: int, spool_format_version: int) -> bool:
    """Only the current protocol, on the current spool format, is admitted."""
    return (
        type(protocol_version) is int
        and type(spool_format_version) is int
        and MIN_SUPPORTED_PROTOCOL_VERSION <= protocol_version <= PROTOCOL_VERSION
        and spool_format_version == SPOOL_FORMAT_VERSION
    )


def refusal_reason_for_versions(protocol_version: int, spool_format_version: int) -> str:
    """Name a refused Mac: older in either version needs an update; anything else is
    incompatible. An old WebSocket client, which sends no identity, needs an update."""
    if protocol_version < PROTOCOL_VERSION or spool_format_version < SPOOL_FORMAT_VERSION:
        return MAC_NEEDS_UPDATE
    return MAC_VERSION_INCOMPATIBLE


def validate_identity(identity: ExecutorIdentity, *, check_versions: bool = True) -> None:
    if (
        not isinstance(identity.application_revision, str)
        or not identity.application_revision.strip()
    ):
        raise ProtocolRefused("application revision must be a nonempty diagnostic label")
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
    if check_versions and not supports_executor_versions(
        identity.protocol_version, identity.spool_format_version
    ):
        raise ProtocolRefused("executor protocol or spool format mismatch")


def parse_identity(payload: object, *, check_versions: bool = True) -> ExecutorIdentity:
    if not isinstance(payload, dict) or not {
        "protocol_version",
        "application_revision",
        "spool_format_version",
        "executor_incarnation",
    }.issubset(payload):
        raise ProtocolRefused("invalid identity fields")
    for key in ("protocol_version", "spool_format_version", "connection_generation"):
        if key in payload and type(payload[key]) is not int:
            raise ProtocolRefused("identity integer field has wrong type")
    for key in ("application_revision", "executor_incarnation"):
        if type(payload[key]) is not str:
            raise ProtocolRefused("identity text field has wrong type")
    identity = TypeAdapter(ExecutorIdentity).validate_python(payload)
    validate_identity(identity, check_versions=check_versions)
    return identity
