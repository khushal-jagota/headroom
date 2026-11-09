"""Typed executor messages and their bounded encoding. Identity lives in its leaf module."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import (
    Annotated,
    Any,
    ClassVar,
    Literal,
    Protocol,
    cast,
    overload,
)

from pydantic import ConfigDict, Field, TypeAdapter, model_validator

from planner.conversation.backends.contracts import (
    AgentMessageCompleted,
    AgentMessageDelta,
    BackendEventValue,
    BackendPromptAccepted,
    BackendStreamEnded,
    BackgroundTaskEnded,
    BackgroundTaskStarted,
    CommandReplyReported,
    ComposerCatalogReported,
    ContextCompacted,
    ModelThinkingHappened,
    PermissionAskRaised,
    PlanUpdated,
    TokenUsageReported,
    ToolCallFinished,
    ToolCallProgress,
    ToolCallStarted,
    TurnEnded,
    TurnToken,
    UserInputFailed,
    UserInputRequested,
    VendorSessionCursorRebound,
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
    supports_executor_versions,
)
from planner.executor.configuration import MAX_ENVELOPE_BYTES
from planner.executor.update_contracts import MacUpdateCheck

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


@dataclass(frozen=True, slots=True)
class FileTransfer:
    """Generation reservation; publication authority stays with the original child."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

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
    """Ordered result delivery with stable sender and submission identities.

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
class UnconfirmedCliResult:
    request_id: str
    submission_id: str
    publication: Literal["unknown", "preparing", "queued"]
    acceptance: Literal["unconfirmed"] = "unconfirmed"


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
    command_reply_reported = "command_reply_reported"
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
    background_task_started = "background_task_started"
    background_task_ended = "background_task_ended"
    backend_stream_ended = "backend_stream_ended"


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
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    resolved_start: ResolvedConversationStart
    canonical_skill_snapshot: CanonicalSkillSnapshot
    vendor_session_cursor: str | None = None
    workspace_home_relative: str | None = None

    kind: Literal["start"] = "start"


@dataclass(frozen=True, slots=True, kw_only=True)
class WritePrompt:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
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
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    content: MessageContent
    sender_label: str
    sender_content: MessageContent | None = None

    kind: Literal["steer"] = "steer"


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelTurn:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    kind: Literal["cancel_running_turn"] = "cancel_running_turn"


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerPermission:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    ask_id: str
    option_id: str
    answer: DurableAnswer | None = None
    kind: Literal["answer_permission_ask"] = "answer_permission_ask"


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerUserInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    request_id: str
    answers: tuple[UserInputAnswer, ...]
    answer: DurableAnswer | None = None
    kind: Literal["answer_user_input"] = "answer_user_input"


@dataclass(frozen=True, slots=True, kw_only=True)
class StopRun:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    kind: Literal["stop"] = "stop"


type ChildCommandArguments = Annotated[
    StartRun | WritePrompt | Steer | CancelTurn | AnswerPermission | AnswerUserInput | StopRun,
    Field(discriminator="kind"),
]


def decode_command_arguments(operation: ChildOperation, raw: object) -> ChildCommandArguments:
    """Decode a command at ingress; callers may omit the operation tag."""
    if isinstance(raw, dict):
        raw = {"kind": operation.value, **raw}
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


def encode_command_for_peer(command: ChildCommand, protocol_version: int) -> JsonObject:
    """Protocols 20 and 21 have identical commands, including nested start values.

    Protocol 21 adds only a Mac-to-VPS event. Future command changes must supply an
    explicit older-peer adaptation here, verified against its frozen schema.
    """
    from planner.execution_configuration import SPOOL_FORMAT_VERSION

    if not supports_executor_versions(protocol_version, SPOOL_FORMAT_VERSION):
        raise ProtocolRefused("unsupported command peer protocol")
    return as_object(command)


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


class WireMessageKind(StrEnum):
    hello = "hello"
    hello_accepted = "hello_accepted"
    protocol_refused = "protocol_refused"
    reconnect_ask = "reconnect_ask"
    reconnect_result = "reconnect_result"
    reconciliation_ready = "reconciliation_ready"
    live_backend_event = "live_backend_event"
    envelope = "envelope"
    refuse_envelope = "refuse_envelope"
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
    unconfirmed: UnconfirmedCliResult | None = None


@dataclass(frozen=True, slots=True)
class LiveReadRequest:
    """GET-only allowlisted Panels read; never served from the outbound journal."""

    child: ChildReference
    path: str
    params: JsonObject


@dataclass(frozen=True, slots=True)
class QuiesceRequest:
    """Stop admission and optionally exit once all work has settled."""

    exit_when_ready: bool


@dataclass(frozen=True, slots=True)
class QuiescenceReceipt:
    """Readiness belongs only to this process/generation, never a later restart.

    Resident must retain this identity through stop/exit. A failed candidate needs
    its own fresh quiescence receipt before rollback can replace code.
    """

    identity: ExecutorIdentity
    process_id: int
    quiescence: Quiescence
    exit_committed: bool


# Protocol 22: the mailbox transport over HTTPS. Each route's body is one of these.
# Unknown fields are ignored so the next protocol can add one without a lockstep
# release; identity checks stay strict through parse_identity.
_WIRE_CONFIG: ConfigDict = ConfigDict(extra="ignore")


@dataclass(frozen=True, slots=True)
class WaitingConversation:
    """A conversation with outbox items the VPS has not receipted, named by its oldest."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    conversation_id: str
    oldest_sequence: int


@dataclass(frozen=True, slots=True)
class WorkingTurn:
    """A turn the Mac is still working on. tool_running: a tool call started and has
    not finished, which exempts the turn from the silent-turn stall rule."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    child: ChildReference
    turn: TurnToken
    tool_running: bool


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    """Route 1, every LEASE_INTERVAL_SECONDS. The Mac's own clock is display only."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    identity: ExecutorIdentity
    process_id: int
    mac_wall_clock_ms: int
    # Outbox items without a receipt, and the age of the oldest of them.
    queue_depth: int
    oldest_item_age_ms: int | None
    # Oldest first, at most MAX_LEASE_WAITING_CONVERSATIONS. Truncated means more
    # conversations are waiting than are listed, so no drain may finish on this lease.
    waiting_conversations: tuple[WaitingConversation, ...]
    waiting_conversations_truncated: bool
    working_turns: tuple[WorkingTurn, ...]
    admission_stopped: bool
    going_to_sleep: bool = False


@dataclass(frozen=True, slots=True)
class LeaseReply:
    """mailbox_high_sequence is the highest command_sequence the VPS holds, 0 if none.
    A Mac whose poll cursor is above it lowers the cursor to it (a restored database)."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    accepted_at_vps_ms: int
    away_after_seconds: float
    update: MacUpdateCheck
    mailbox_high_sequence: int


@dataclass(frozen=True, slots=True)
class ExecutorRefusal:
    """Why the VPS refused this Mac. reason is MAC_NEEDS_UPDATE or MAC_VERSION_INCOMPATIBLE
    from execution_identity. peer_identity is None for an old WebSocket client."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    reason: str
    peer_identity: ExecutorIdentity | None
    server_identity: ExecutorIdentity
    refused_at_ms: int


@dataclass(frozen=True, slots=True)
class LeaseRefused:
    """Route 1's 409 body. error is PlannerError.to_payload()["error"]. refusal is set for
    a version refusal; executor_incarnation_not_current means this process was replaced."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    error: JsonObject
    refusal: ExecutorRefusal | None
    update: MacUpdateCheck | None


@dataclass(frozen=True, slots=True)
class ModelListRequest:
    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    backend_key: str
    refresh: bool


class MailboxOperation(StrEnum):
    """Every command kind the mailbox carries: the child operations, plus a model list."""

    start = "start"
    write_prompt = "write_prompt"
    steer = "steer"
    cancel_running_turn = "cancel_running_turn"
    answer_permission_ask = "answer_permission_ask"
    answer_user_input = "answer_user_input"
    stop = "stop"
    read_model_list = "read_model_list"


@dataclass(frozen=True, slots=True)
class MailboxCommand:
    """One executor_commands row as the Mac receives it. Exactly one payload is set.

    command_sequence is the mailbox cursor; command_id is the identity the Mac's
    journal deduplicates by, so a redelivered or restored command never runs twice.
    """

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    command_sequence: int
    command_id: str
    created_at_vps_ms: int
    child_command: ChildCommand | None = None
    model_list_request: ModelListRequest | None = None

    def __post_init__(self) -> None:
        if (self.child_command is None) == (self.model_list_request is None):
            raise ValueError("a mailbox command carries exactly one payload")
        if self.child_command is not None and self.child_command.command_id != self.command_id:
            raise ValueError("mailbox command id conflicts with its child command")

    @property
    def operation(self) -> MailboxOperation:
        if self.child_command is not None:
            return MailboxOperation(self.child_command.operation.value)
        return MailboxOperation.read_model_list


@dataclass(frozen=True, slots=True)
class MailboxPollReply:
    """Route 2. Ascending command_sequence, at most MAX_MAILBOX_POLL_COMMANDS."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    commands: tuple[MailboxCommand, ...]


@dataclass(frozen=True, slots=True)
class CommandResult:
    """A finished command's result. child_outcome for a child command; model_list (a
    BackendSnapshot as JSON) for read_model_list."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    command_id: str
    child_outcome: CommandOutcome | None = None
    model_list: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class CommandOutcomeReport:
    """Route 3's request, at most MAX_COMMAND_OUTCOMES_PER_REPORT results."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    outcomes: tuple[CommandResult, ...]


@dataclass(frozen=True, slots=True)
class RejectedCommandResult:
    """A result the VPS will never record (unknown id, other incarnation, malformed).
    The Mac marks it reported and logs it, so it never blocks the reporter."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    command_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class CommandOutcomeReply:
    """Route 3's 200 answer. recorded includes results the VPS already had (first wins)."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    recorded: tuple[str, ...]
    rejected: tuple[RejectedCommandResult, ...] = ()


@dataclass(frozen=True, slots=True)
class ItemSubmission:
    """One outbox item. sender_refusal carries a permanent preparation failure (such as
    an upload refused for good), which the VPS records as the item's refused receipt."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    envelope: QueuedEnvelope
    sender_refusal: str | None = None


@dataclass(frozen=True, slots=True)
class ItemBatch:
    """Route 4: one conversation's items in ascending sequence, at most
    MAX_ITEM_BATCH_ITEMS and MAX_ITEM_BATCH_BYTES. A batch ends right after the first
    item that is not a plain backend event (another kind, or any file dependencies)."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    sender_id: str
    items: tuple[ItemSubmission, ...]


@dataclass(frozen=True, slots=True)
class ItemBatchStop:
    """Where the VPS stopped: no receipt for this sequence or any later one."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    sequence: int
    reason: Literal["retry_later", "missing_files"]
    message: str
    missing_files: tuple[FileManifest, ...] = ()


@dataclass(frozen=True, slots=True)
class ItemBatchReply:
    """Route 4's 200 answer: receipts for a prefix of the batch, in order, and where it
    stopped if it did. Retry-later always comes back here, never as a 503."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    receipts: tuple[AcceptanceReceipt, ...]
    stop: ItemBatchStop | None = None


@dataclass(frozen=True, slots=True)
class LiveEventBatch:
    """Route 8: at most MAX_LIVE_EVENTS_PER_BATCH, methods in LIVE_BACKEND_EVENT_METHODS."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    events: tuple[BackendEvent, ...]


@dataclass(frozen=True, slots=True)
class MacLocalStatus:
    """The executor's own health over local IPC. Times are the Mac's clock."""

    __pydantic_config__: ClassVar[ConfigDict] = _WIRE_CONFIG
    identity: ExecutorIdentity
    process_id: int
    lease_accepted_at_mac_ms: int | None
    last_lease_failure: str | None
    refusal: ExecutorRefusal | None
    admission_stopped: bool
    going_to_sleep: bool


def encode_bounded(value: object, *, limit: int = MAX_ENVELOPE_BYTES) -> bytes:
    """Encode one protocol value as JSON, refusing an empty or oversized body.

    The size check every sender makes before it stores or sends a value. An item batch
    passes MAX_ITEM_BATCH_BYTES; everything else keeps the envelope limit.
    """
    body = type_adapter(type(value)).dump_json(value)
    if not body or len(body) > limit:
        raise ProtocolRefused("value exceeds its encoded size limit")
    return body


def decode_bounded[T](
    body: bytes, value_type: type[T], *, limit: int = MAX_ENVELOPE_BYTES
) -> T:
    """Decode one protocol value from a JSON body of at most limit bytes.

    Lax, not strict: a ChildCommand decodes its arguments in a before-validator, which
    strict mode would refuse. An identity inside the value is checked only by its shape
    here; the receiver runs validate_identity on it before it trusts it.
    """
    if not body or len(body) > limit:
        raise ProtocolRefused("body exceeds its size limit")
    return type_adapter(value_type).validate_json(body)


class FrameReader(Protocol):
    async def readexactly(self, n: int) -> bytes: ...


def payload_child(payload: JsonObject) -> JsonObject | None:
    child = envelope_child(payload)
    return child if isinstance(child, dict) else None


def encode_value(value: object) -> JsonValue:
    return type_adapter(object).dump_python(value, mode="json")  # type: ignore[no-any-return]


# One closed registry preserves the native wire method names and event shapes.
_BACKEND_EVENT_TYPES: dict[BackendEventMethod, type[BackendEventValue]] = {
    BackendEventMethod.agent_message_delta: AgentMessageDelta,
    BackendEventMethod.model_thinking_happened: ModelThinkingHappened,
    BackendEventMethod.agent_message_completed: AgentMessageCompleted,
    BackendEventMethod.command_reply_reported: CommandReplyReported,
    BackendEventMethod.tool_call_started: ToolCallStarted,
    BackendEventMethod.tool_call_progress: ToolCallProgress,
    BackendEventMethod.tool_call_finished: ToolCallFinished,
    BackendEventMethod.plan_updated: PlanUpdated,
    BackendEventMethod.token_usage_reported: TokenUsageReported,
    BackendEventMethod.context_compacted: ContextCompacted,
    BackendEventMethod.permission_ask_raised: PermissionAskRaised,
    BackendEventMethod.user_input_requested: UserInputRequested,
    BackendEventMethod.user_input_failed: UserInputFailed,
    BackendEventMethod.turn_ended: TurnEnded,
    BackendEventMethod.vendor_session_cursor_rebound: VendorSessionCursorRebound,
    BackendEventMethod.composer_catalog_reported: ComposerCatalogReported,
    BackendEventMethod.background_task_started: BackgroundTaskStarted,
    BackendEventMethod.background_task_ended: BackgroundTaskEnded,
    BackendEventMethod.backend_stream_ended: BackendStreamEnded,
}
_BACKEND_EVENT_METHODS = {event_type: method for method, event_type in _BACKEND_EVENT_TYPES.items()}


def encode_backend_event(event: BackendEventValue) -> tuple[BackendEventMethod, JsonObject]:
    return _BACKEND_EVENT_METHODS[type(event)], cast(JsonObject, encode_value(event))


def decode_event_arguments(event: BackendEvent) -> BackendEventValue:
    """Decode one closed event value without changing its wire representation."""
    return type_adapter(_BACKEND_EVENT_TYPES[event.method]).validate_python(event.arguments)


def decode_prompt_result(result: JsonObject) -> BackendPromptAccepted:
    return BackendPromptAccepted(
        composed_content_delivered=bool(result["composed_content_delivered"]),
    )


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
