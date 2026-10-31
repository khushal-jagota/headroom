"""Typed executor messages and their bounded encoding. Identity lives in its leaf module."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Literal,
    Protocol,
    TypedDict,
    overload,
)

if TYPE_CHECKING:
    from planner.executor.vps_remote_child import MacRemoteChild

from pydantic import ConfigDict, Field, TypeAdapter, model_validator

from planner.conversation.backend_event_contracts import AcceptedEvent, CommitParticipant
from planner.conversation.backends.contracts import (
    AgentMessageCompletedArguments,
    AgentMessageDeltaArguments,
    BackendPromptAccepted,
    ComposerCatalogReportedArguments,
    ContextCompactedArguments,
    DecodedBackendEventArguments,
    ModelThinkingHappenedArguments,
    PermissionAskRaisedArguments,
    PlanUpdatedArguments,
    TokenUsageReportedArguments,
    ToolCallFinishedArguments,
    ToolCallProgressArguments,
    ToolCallStartedArguments,
    TurnEndedArguments,
    TurnToken,
    UserInputFailedArguments,
    UserInputRequestedArguments,
    VendorSessionCursorReboundArguments,
)
from planner.conversation.contracts import PromptDeliveryMode, ResolvedConversationStart
from planner.conversation.events import UserInputAnswer
from planner.conversation.message_content import MessageContent
from planner.execution_identity import (
    AskKind,
    AskReference,
    ChildReference,
    ExecutorIdentity,
    ProtocolRefused,
)
from planner.executor.configuration import MAX_ENVELOPE_BYTES

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


type JsonObject = dict[str, JsonValue]


@overload
def type_adapter[T](value_type: type[T], /) -> TypeAdapter[T]: ...


@overload
def type_adapter(value_type: Any, /) -> TypeAdapter[Any]: ...


def type_adapter(value_type: Any, /) -> TypeAdapter[Any]:
    """Build each executor schema once, then reuse it for parsing and encoding."""
    return _cached_type_adapter(value_type)


@cache
def _cached_type_adapter(value_type: Any, /) -> TypeAdapter[Any]:
    return TypeAdapter(value_type)


@dataclass(frozen=True, slots=True)
class CanonicalSkillSource:
    """One authoritative database row, transported without text transformation."""

    skill_name: str
    source_text: str


@dataclass(frozen=True, slots=True)
class CanonicalSkillSnapshot:
    """The complete canonical skills and the minimum set this Worker needs."""

    sources: tuple[CanonicalSkillSource, ...]
    required_skill_names: tuple[str, ...]


class ConnectionPhase(StrEnum):
    app_connect = "app_connect"
    hello = "hello"
    transport_open = "transport_open"
    reconnect_ask = "reconnect_ask"
    steady_state = "steady_state"


class ConnectionFailureOrigin(StrEnum):
    reader = "reader"
    session = "session"


class ConnectionFailureCategory(StrEnum):
    configuration = "configuration"
    tls = "tls"
    authentication = "authentication"
    timeout = "timeout"
    network = "network"
    connection_or_protocol = "connection_or_protocol"


@dataclass(frozen=True, slots=True)
class FileTransfer:
    """Generation reservation; publication authority stays with the original child."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    transfer_id: str
    direction: Literal["upload", "download"]
    manifest: FileManifest


class ConnectionState(StrEnum):
    disconnected = "disconnected"
    reconciling = "reconciling"
    ready = "ready"
    refused = "refused"


@dataclass(frozen=True, slots=True)
class ExecutorHealth:
    identity: ExecutorIdentity
    process_id: int
    connection_state: ConnectionState
    reconciliation_ready: bool
    observed_at_ms: int
    lost_at_ms: int | None


class ExecutorHealthRead(TypedDict):
    """VPS API projection; receipt time is not activity or answer delivery evidence."""

    identity: JsonObject | None
    process_id: int | None
    connection_state: str
    reconciliation_ready: bool
    observed_at_ms: int | None
    lost_at_ms: int | None
    last_contact_at_ms: int | None
    last_contact_source: Literal["vps_received_valid_executor_frame"]


@dataclass(frozen=True, slots=True)
class DurableAnswer:
    answer_id: str
    ask: AskReference
    option_id: str | None
    accepted_at_vps_ms: int
    user_input_answers: tuple[UserInputAnswer, ...] | None = None


@dataclass(frozen=True, slots=True)
class AnswerApplication:
    """Created only after the original native callback returns successfully."""

    answer_id: str
    ask: AskReference
    applied_at_mac_ms: int


class FileScope(StrEnum):
    ticket = "ticket"
    conversation = "conversation"


@dataclass(frozen=True, slots=True)
class FileManifest:
    """Path is POSIX relative to its scope root, never an absolute host path."""

    scope: FileScope
    owner_id: str
    relative_path: str
    sha256: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class FileUploadRequest:
    """The child assignment authorizes staging and installation of these bytes."""

    manifest: FileManifest
    child: ChildReference


@dataclass(frozen=True, slots=True)
class WithdrawCommands:
    child: ChildReference
    command_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WithdrawResult:
    withdrawn: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileDownloadRequest:
    command_id: str
    stored_file_id: str


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    manifest: FileManifest
    local_path: Path


class EnvelopeKind(StrEnum):
    backend_event = "backend_event"
    cli_write = "cli_write"
    child_stopped = "child_stopped"
    answer_application = "answer_application"


@dataclass(frozen=True, slots=True)
class QueuedEnvelope:
    """Digest binds routing identity, kind, payload and ordered file dependencies.

    sender_id survives process restart in the sender row.
    Sequence increases globally for the sender;
    submission_id never changes during retries.
    Authority is bound by VPS child assignment, never taken from payload claims.
    """

    sender_id: str
    conversation_id: str
    sequence: int
    submission_id: str
    kind: EnvelopeKind
    payload: JsonObject
    payload_sha256: str
    file_dependencies: tuple[FileManifest, ...] = ()


@dataclass(frozen=True, slots=True)
class EnvelopeRefusal:
    """A sender's permanent preparation failure, recorded as the item's receipt."""

    envelope: QueuedEnvelope
    error_message: str


class ReceiptDisposition(StrEnum):
    accepted = "accepted"
    refused = "refused"
    uncertain = "uncertain"


@dataclass(frozen=True, slots=True)
class AcceptanceReceipt:
    sender_id: str
    submission_id: str
    payload_sha256: str
    disposition: ReceiptDisposition
    result: JsonObject
    accepted_at_vps_ms: int


@dataclass(frozen=True, slots=True)
class CliWrite:
    """Registry-owned mutation name; never arbitrary HTTP execution."""

    operation: str
    arguments: JsonObject
    child: ChildReference
    turn: TurnToken | None


@dataclass(frozen=True, slots=True)
class QueuedCliResult:
    submission_id: str
    disposition: str = "queued"
    acceptance: str = "unconfirmed"


@dataclass(frozen=True, slots=True)
class UnconfirmedCliResult:
    request_id: str
    submission_id: str
    publication: Literal["unknown", "preparing", "queued", "acknowledged"]
    acceptance: Literal["unconfirmed"] = "unconfirmed"


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    envelope: QueuedEnvelope
    receipt: AcceptanceReceipt | None = None

    @property
    def pending(self) -> bool:
        return self.receipt is None


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    conversation_id: str
    kind: EnvelopeKind
    payload: JsonObject
    submission_id: str | None = None


def envelope_child(payload: JsonObject, *, ask_only: bool = False) -> JsonValue:
    """Read a raw child; refused answers take it only from their ask."""
    child = payload.get("child")
    if not ask_only and isinstance(child, dict):
        return child
    ask = payload.get("ask")
    return ask.get("child") if isinstance(ask, dict) else None


class ChildOperation(StrEnum):
    start = "start"
    write_prompt = "write_prompt"
    steer = "steer"
    cancel_running_turn = "cancel_running_turn"
    answer_permission_ask = "answer_permission_ask"
    answer_user_input = "answer_user_input"
    stop = "stop"


class BackendEventMethod(StrEnum):
    agent_message_delta = "agent_message_delta"
    model_thinking_happened = "model_thinking_happened"
    agent_message_completed = "agent_message_completed"
    tool_call_started = "tool_call_started"
    tool_call_progress = "tool_call_progress"
    tool_call_finished = "tool_call_finished"
    plan_updated = "plan_updated"
    token_usage_reported = "token_usage_reported"
    context_compacted = "context_compacted"
    permission_ask_raised = "permission_ask_raised"
    user_input_requested = "user_input_requested"
    user_input_failed = "user_input_failed"
    turn_ended = "turn_ended"
    vendor_session_cursor_rebound = "vendor_session_cursor_rebound"
    composer_catalog_reported = "composer_catalog_reported"


LIVE_BACKEND_EVENT_METHODS = frozenset(
    (
        BackendEventMethod.agent_message_delta,
        BackendEventMethod.model_thinking_happened,
        BackendEventMethod.tool_call_progress,
    )
)


@dataclass(frozen=True, slots=True)
class BackendEvent:
    child: ChildReference
    method: BackendEventMethod
    turn: TurnToken | None
    arguments: JsonObject


@dataclass(frozen=True, slots=True, kw_only=True)
class StartRun:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    resolved_start: ResolvedConversationStart
    canonical_skill_snapshot: CanonicalSkillSnapshot
    vendor_session_cursor: str | None = None
    workspace_home_relative: str | None = None

    kind: Literal["start"] = "start"


@dataclass(frozen=True, slots=True, kw_only=True)
class WritePrompt:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    content: MessageContent
    sender_content: MessageContent
    sender_label: str
    mode: PromptDeliveryMode
    sender_message_count: int = 1
    model_change: str | None = None
    reasoning_effort_change: str | None = None
    reasoning_effort_is_set: bool = False
    automatic_compaction: bool = False

    kind: Literal["write_prompt"] = "write_prompt"


@dataclass(frozen=True, slots=True, kw_only=True)
class Steer:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    content: MessageContent
    sender_label: str
    sender_content: MessageContent | None = None

    kind: Literal["steer"] = "steer"


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelTurn:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    kind: Literal["cancel_running_turn"] = "cancel_running_turn"


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerPermission:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    ask_id: str
    option_id: str
    answer: DurableAnswer | None = None
    kind: Literal["answer_permission_ask"] = "answer_permission_ask"


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerUserInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    request_id: str
    answers: tuple[UserInputAnswer, ...]
    answer: DurableAnswer | None = None
    kind: Literal["answer_user_input"] = "answer_user_input"


@dataclass(frozen=True, slots=True, kw_only=True)
class StopRun:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    kind: Literal["stop"] = "stop"


type ChildCommandArguments = Annotated[
    StartRun | WritePrompt | Steer | CancelTurn | AnswerPermission | AnswerUserInput | StopRun,
    Field(discriminator="kind"),
]


def decode_command_arguments(operation: ChildOperation, raw: object) -> ChildCommandArguments:
    """Decode the JSON command at ingress, including the previous untagged wire form."""
    if isinstance(raw, dict):
        raw = {"kind": operation.value, **raw}
        # Saved answers have one stable identity and their complete chosen value.
        if "answer" in raw and raw["answer"] is not None:
            answer = type_adapter(DurableAnswer).validate_python(raw["answer"])
            raw = {**raw, "answer": answer}
            if operation is ChildOperation.answer_permission_ask:
                raw.setdefault("ask_id", answer.ask.ask_id)
                raw.setdefault("option_id", answer.option_id)
            elif operation is ChildOperation.answer_user_input:
                raw.setdefault("request_id", answer.ask.ask_id)
                raw.setdefault("answers", answer.user_input_answers)
    result: ChildCommandArguments = type_adapter(ChildCommandArguments).validate_python(raw)
    if result.kind != operation.value:
        raise ValueError("command operation conflicts with arguments")
    return result


@dataclass(frozen=True, slots=True)
class ChildCommand:
    command_id: str
    child: ChildReference
    operation: ChildOperation
    turn: TurnToken | None
    arguments: ChildCommandArguments
    file_dependencies: tuple[FileManifest, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def decode_arguments(cls, value: object) -> object:
        if isinstance(value, dict):
            return {
                **value,
                "arguments": decode_command_arguments(
                    ChildOperation(value["operation"]), value["arguments"]
                ),
            }
        return value

    def __post_init__(self) -> None:
        # Python callers supply typed arguments; the wire decoder admits old JSON.
        if self.arguments.kind != self.operation.value:
            raise ValueError("command operation conflicts with arguments")
        if isinstance(self.arguments, (WritePrompt, Steer)) and self.turn is None:
            raise ValueError("prompt command needs its exact turn")
        if isinstance(self.arguments, (AnswerPermission, AnswerUserInput)):
            answer = self.arguments.answer
            if answer is not None and (
                answer.answer_id != self.command_id
                or answer.ask.child != self.child
                or answer.ask.turn != self.turn
                or (
                    isinstance(self.arguments, AnswerPermission)
                    and (
                        answer.ask.kind is not AskKind.permission
                        or answer.ask.ask_id != self.arguments.ask_id
                        or answer.option_id != self.arguments.option_id
                        or answer.user_input_answers is not None
                    )
                )
                or (
                    isinstance(self.arguments, AnswerUserInput)
                    and (
                        answer.ask.kind is not AskKind.user_input
                        or answer.ask.ask_id != self.arguments.request_id
                        or answer.user_input_answers != self.arguments.answers
                        or answer.option_id is not None
                    )
                )
            ):
                raise ValueError("durable answer conflicts with command")


class CommandDisposition(StrEnum):
    succeeded = "succeeded"
    refused = "refused"
    uncertain = "uncertain"


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    command_id: str
    child: ChildReference
    disposition: CommandDisposition
    result: JsonObject


@dataclass(frozen=True, slots=True)
class ChildInventory:
    child: ChildReference
    active_turn: TurnToken | None
    pending_asks: tuple[AskReference, ...]


@dataclass(frozen=True, slots=True)
class CommandState:
    command_id: str
    phase: Literal["unknown", "preparing", "invoking", "finished"]
    outcome: CommandOutcome | None = None


@dataclass(frozen=True, slots=True)
class ReconnectAsk:
    command_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReconnectResult:
    identity: ExecutorIdentity
    commands: tuple[CommandState, ...]
    children: tuple[ChildInventory, ...]


@dataclass(frozen=True, slots=True)
class Quiescence:
    admission_stopped: bool
    active_turn_count: int
    pending_callback_count: int
    uncertain_operation_count: int
    unsent_entry_count: int
    idle_children_stopped: bool

    @property
    def ready(self) -> bool:
        return (
            self.admission_stopped
            and self.idle_children_stopped
            and not (
                self.active_turn_count
                or self.pending_callback_count
                or self.uncertain_operation_count
                or self.unsent_entry_count
            )
        )


class TransactionalWriter(Protocol):
    def __call__(self, connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        """Validate original authority and apply effect; never commit or do native I/O.

        Acceptor checks a matching receipt first, verifies order/files, then invokes
        this writer and inserts the first receipt in this same transaction. A
        permanent PlannerError is recorded under a savepoint; transient errors roll
        back without receipt. Async/native effects use their separate command fate.
        """
        ...


class WireMessageKind(StrEnum):
    hello = "hello"
    hello_accepted = "hello_accepted"
    transport_open = "transport_open"
    transport_opened = "transport_opened"
    protocol_refused = "protocol_refused"
    reconnect_ask = "reconnect_ask"
    reconnect_result = "reconnect_result"
    reconciliation_ready = "reconciliation_ready"
    live_backend_event = "live_backend_event"
    envelope = "envelope"
    receipt = "receipt"
    missing_files = "missing_files"
    put_file = "put_file"
    withdraw = "withdraw"
    withdraw_result = "withdraw_result"
    get_file = "get_file"
    file_transfer = "file_transfer"
    command = "command"
    command_outcome = "command_outcome"
    read_request = "read_request"
    read_response = "read_response"
    catalog_request = "catalog_request"
    catalog_response = "catalog_response"
    health = "health"
    error = "error"


@dataclass(frozen=True, slots=True)
class WireMessage:
    """Frame body: {kind, request_id, payload}; kind selects the validated codec.

    request_id correlates one exchange only. Domain retry identity always remains
    submission_id or command_id in the payload. Never redispatch a
    native command merely because a request_id response was lost.
    """

    kind: WireMessageKind
    request_id: str
    payload: JsonObject


class IpcOperation(StrEnum):
    read = "read"
    write = "write"
    retry = "retry"
    health = "health"
    quiesce = "quiesce"


@dataclass(frozen=True, slots=True)
class IpcRequest:
    """Capability stays on local IPC; VPS resolves authority from child assignment.

    For reads payload has path and params. For writes it has operation and
    arguments from the canonical mutation registry. Resident health/quiesce access
    uses the owner-only Unix socket, not a fabricated child principal.
    """

    request_id: str
    operation: IpcOperation
    child_id: str | None
    capability: str | None
    payload: JsonObject
    response_deadline: float | None = None


@dataclass(frozen=True, slots=True)
class IpcWritePayload:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    operation: str
    arguments: JsonObject


@dataclass(frozen=True, slots=True)
class IpcRetryPayload:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    submission_id: str


@dataclass(frozen=True, slots=True)
class IpcResponse:
    request_id: str
    result: JsonObject | None
    error: JsonObject | None
    queued: QueuedCliResult | None = None
    unconfirmed: UnconfirmedCliResult | None = None


@dataclass(frozen=True, slots=True)
class LiveReadRequest:
    """GET-only allowlisted Panels read; never served from the outbound journal."""

    child: ChildReference
    path: str
    params: JsonObject


class MacOutboxWriter(Protocol):
    """The runner produces durable output and counts items awaiting a receipt."""

    def add(self, entry: OutboxEntry, *, ticket_id: str | None = None) -> QueuedEnvelope: ...

    def pending_count(self) -> int: ...


@dataclass(frozen=True, slots=True)
class QuiesceRequest:
    """Expected live identity from fresh health, checked atomically with admission stop."""

    expected_identity: ExecutorIdentity
    expected_process_id: int
    exit_when_ready: bool


@dataclass(frozen=True, slots=True)
class QuiescenceReceipt:
    """Readiness belongs only to this process/generation, never a later restart.

    Resident must retain this identity through stop/exit. A failed candidate needs
    its own fresh health and quiescence request before rollback can replace code.
    """

    identity: ExecutorIdentity
    process_id: int
    quiescence: Quiescence
    exit_committed: bool


class FrameReader(Protocol):
    async def readexactly(self, n: int) -> bytes: ...


class WebSocketPeer(Protocol):
    """One bounded executor control WebSocket, without HTTP credentials."""

    @property
    def closed(self) -> bool: ...
    async def receive_bytes(self, max_bytes: int) -> bytes: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def aclose(self) -> None: ...


class ControlMessageReader(Protocol):
    async def receive(self) -> WireMessage: ...


class ControlMessageWriter(Protocol):
    async def send(self, message: WireMessage) -> None: ...
    def close(self) -> None: ...
    async def wait_closed(self) -> None: ...
    def is_closing(self) -> bool: ...


def payload_child(payload: JsonObject) -> JsonObject | None:
    child = envelope_child(payload)
    return child if isinstance(child, dict) else None


def encode_value(value: object) -> JsonValue:
    return type_adapter(object).dump_python(value, mode="json")  # type: ignore[no-any-return]


def encode_backend_event(
    method: BackendEventMethod,
    turn: object,
    arguments: dict[str, object],
) -> JsonObject:
    del method, turn
    return {key: encode_value(value) for key, value in arguments.items()}


def decode_event_arguments(event: BackendEvent) -> DecodedBackendEventArguments:
    """Decode one closed callback shape without changing its wire representation."""
    match event.method:
        case BackendEventMethod.agent_message_delta:
            return type_adapter(AgentMessageDeltaArguments).validate_python(event.arguments)
        case BackendEventMethod.model_thinking_happened:
            return type_adapter(ModelThinkingHappenedArguments).validate_python(event.arguments)
        case BackendEventMethod.agent_message_completed:
            return type_adapter(AgentMessageCompletedArguments).validate_python(event.arguments)
        case BackendEventMethod.tool_call_started:
            return type_adapter(ToolCallStartedArguments).validate_python(event.arguments)
        case BackendEventMethod.tool_call_progress:
            return type_adapter(ToolCallProgressArguments).validate_python(event.arguments)
        case BackendEventMethod.tool_call_finished:
            return type_adapter(ToolCallFinishedArguments).validate_python(event.arguments)
        case BackendEventMethod.plan_updated:
            return type_adapter(PlanUpdatedArguments).validate_python(event.arguments)
        case BackendEventMethod.token_usage_reported:
            return type_adapter(TokenUsageReportedArguments).validate_python(event.arguments)
        case BackendEventMethod.context_compacted:
            return type_adapter(ContextCompactedArguments).validate_python(event.arguments)
        case BackendEventMethod.permission_ask_raised:
            return type_adapter(PermissionAskRaisedArguments).validate_python(event.arguments)
        case BackendEventMethod.user_input_requested:
            return type_adapter(UserInputRequestedArguments).validate_python(event.arguments)
        case BackendEventMethod.user_input_failed:
            return type_adapter(UserInputFailedArguments).validate_python(event.arguments)
        case BackendEventMethod.turn_ended:
            return type_adapter(TurnEndedArguments).validate_python(event.arguments)
        case BackendEventMethod.vendor_session_cursor_rebound:
            return type_adapter(VendorSessionCursorReboundArguments).validate_python(
                event.arguments
            )
        case BackendEventMethod.composer_catalog_reported:
            return type_adapter(ComposerCatalogReportedArguments).validate_python(event.arguments)


def decode_prompt_result(result: JsonObject) -> BackendPromptAccepted:
    return BackendPromptAccepted(
        composed_content_delivered=bool(result["composed_content_delivered"]),
    )


def envelope_payload_digest(
    payload: JsonObject,
    manifests: tuple[FileManifest, ...],
    *,
    sender_id: str,
    conversation_id: str,
    sequence: int,
    submission_id: str,
    kind: EnvelopeKind,
) -> str:
    value = {
        "payload": payload,
        "sender_id": sender_id,
        "conversation_id": conversation_id,
        "sequence": sequence,
        "submission_id": submission_id,
        "kind": kind.value,
        "file_dependencies": type_adapter(tuple[FileManifest, ...]).dump_python(
            manifests, mode="json"
        ),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _encode_payload(value: object) -> bytes:
    body = type_adapter(type(value)).dump_json(value)
    if not body or len(body) > MAX_ENVELOPE_BYTES:
        raise ProtocolRefused("frame exceeds envelope limit")
    return body


async def read_typed_frame[T](reader: FrameReader, value_type: type[T]) -> T:
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    if not length or length > MAX_ENVELOPE_BYTES:
        raise ProtocolRefused("invalid frame length")
    return type_adapter(value_type).validate_json(await reader.readexactly(length), strict=True)


def encode_message(message: WireMessage) -> bytes:
    return _encode_payload(message)


def decode_message(body: bytes) -> WireMessage:
    if not body or len(body) > MAX_ENVELOPE_BYTES:
        raise ProtocolRefused("message exceeds envelope limit")
    return type_adapter(WireMessage).validate_json(body, strict=True)


def encode_frame(value: object) -> bytes:
    """Local IPC has no message boundaries, so prefix its bounded payload."""
    body = _encode_payload(value)
    return struct.pack("!I", len(body)) + body


def as_object(value: object) -> JsonObject:
    encoded = encode_value(value)
    if not isinstance(encoded, dict):
        raise ValueError("expected object")
    return encoded


class MacRunConversations(Protocol):
    async def emit_answer(
        self, turn: TurnToken, emit: Callable[[], Coroutine[Any, Any, None]]
    ) -> None: ...

    async def interrupt_replaced_executor_runs(self, current_incarnation: str) -> None: ...

    async def stop_interrupted_remote_child(
        self, reference: ChildReference, child: MacRemoteChild
    ) -> None: ...


class MacInboxConversations(Protocol):
    async def take_backend_event(
        self,
        turn: TurnToken | None,
        arguments: DecodedBackendEventArguments,
        *,
        in_commit: CommitParticipant,
        conversation_id: str | None = None,
    ) -> AcceptedEvent: ...

    async def take_answer_application(
        self,
        turn: TurnToken,
        answer_id: str,
        provider_id: str,
        kind: str,
        in_commit: CommitParticipant,
    ) -> AcceptedEvent: ...
