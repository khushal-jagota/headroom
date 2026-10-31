"""Stable executor, child and callback identities shared across module boundaries."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import TypeAdapter

from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION


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
    connection_generation: int


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


def validate_identity(identity: ExecutorIdentity) -> None:
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
    if (
        identity.protocol_version != PROTOCOL_VERSION
        or identity.spool_format_version != SPOOL_FORMAT_VERSION
    ):
        raise ProtocolRefused("executor protocol or spool format mismatch")
    if identity.connection_generation < 1 or not identity.executor_incarnation:
        raise ProtocolRefused("invalid executor identity")


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
