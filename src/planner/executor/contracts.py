"""Versioned boundary between the Mac executor and the one VPS store.

No transport disconnect is a domain refusal, child ending, or permission answer.
All wire payloads are validated at ingress; JSON dictionaries are not HTTP routes.
Domain writes and their receipt share a transaction. Native child effects instead
retain command outcomes and report uncertainty without automatic redispatch.
"""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, TypedDict

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import MoveRetirementResult
from planner.conversation.events import UserInputAnswer

# Payloads carry existing domain types as JSON; operation-specific codecs validate
# exact fields before dispatch. Credentials and arbitrary caller headers never travel.
type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ExecutorIdentity:
    protocol_version: int
    application_revision: str
    spool_format_version: int
    executor_incarnation: str
    connection_generation: int


@dataclass(frozen=True, slots=True)
class ExecutorSshConfig:
    host: str
    port: int
    username: str
    identity_file: Path
    known_hosts_file: Path


@dataclass(frozen=True, slots=True)
class FileTransferSlot:
    """Only the opaque name crosses SFTP; a host path never grants authority."""

    token: str
    direction: Literal["upload", "download"]
    manifest: FileManifest


@dataclass(frozen=True, slots=True)
class SftpEndpoint:
    socket_path: str
    server_public_key: str
    username: str


@dataclass(frozen=True, slots=True)
class ClockSample:
    """NTP-style exchange; Unix milliseconds on each host, no zero uncertainty."""

    vps_sent_at_ms: int
    mac_received_at_ms: int
    mac_sent_at_ms: int
    vps_received_at_ms: int


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
    kind: AskKind = AskKind.permission


class AskReachability(StrEnum):
    uncertain = "uncertain"
    reachable = "reachable"
    child_gone = "child_gone"
    callback_gone = "callback_gone"


@dataclass(frozen=True, slots=True)
class ExecutorTurnContext:
    """Durable provenance and closure for the canonical remote turn."""

    child: ChildReference
    turn: TurnToken
    pending_permission_ask_ids: tuple[str, ...]
    pending_user_input_ids: tuple[str, ...]
    ended: bool
    source_retired: bool
    move_retirement_uncertain: bool
    delivery_uncertain: bool
    callbacks_revoked: bool


@dataclass(frozen=True, slots=True)
class AnswerAdmission:
    ask: AskReference
    context: ExecutorTurnContext | None
    assignment_open: bool
    reachability: AskReachability


@dataclass(frozen=True, slots=True)
class AskEvidence:
    """Reachability and durable answer/application are independent facts."""

    ask: AskReference
    connection_generation: int
    reachability: AskReachability
    observed_at_ms: int
    answer_id: str | None = None
    applied_at_mac_ms: int | None = None


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


@dataclass(frozen=True, slots=True)
class AuthorizedAction:
    """Tool-start evidence, separate from callback application; unknown stays absent."""

    answer_id: str
    ask: AskReference
    tool_call_id: str
    observed_at_mac_ms: int


@dataclass(frozen=True, slots=True)
class AnswerActionTiming:
    answer_id: str
    elapsed_lower_ms: int | None
    elapsed_upper_ms: int | None
    clock_basis: str
    unknown_reason: str | None


class FileScope(StrEnum):
    ticket = "ticket"
    conversation = "conversation"


@dataclass(frozen=True, slots=True)
class FileManifest:
    """Path is POSIX relative to its scope root, never an absolute host path."""

    publication_id: str
    scope: FileScope
    owner_id: str
    relative_path: str
    sha256: str
    byte_count: int
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class FilePublicationRequest:
    """The child assignment authorizes bytes before publication, not just the row."""

    manifest: FileManifest
    child: ChildReference


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    manifest: FileManifest
    local_path: Path


class EnvelopeKind(StrEnum):
    backend_event = "backend_event"
    cli_write = "cli_write"
    command_outcome = "command_outcome"
    answer_application = "answer_application"
    authorized_action = "authorized_action"


@dataclass(frozen=True, slots=True)
class QueuedEnvelope:
    """Digest binds routing identity, kind, payload and ordered file dependencies.

    sender_id survives process restart in the spool metadata. Sequence starts at 1
    per (sender_id, conversation_id); submission_id never changes during retries.
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


class ReceiptDisposition(StrEnum):
    accepted = "accepted"
    refused = "refused"


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


@dataclass(frozen=True, slots=True)
class BackendEvent:
    child: ChildReference
    method: BackendEventMethod
    turn: TurnToken | None
    arguments: JsonObject


@dataclass(frozen=True, slots=True)
class ChildCommand:
    command_id: str
    child: ChildReference
    operation: ChildOperation
    turn: TurnToken | None
    arguments: JsonObject
    file_dependencies: tuple[FileManifest, ...] = ()


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
    pending_user_input_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceRetirementRequest:
    expected_identity: ExecutorIdentity
    ticket_id: str
    conversation_id: str | None
    assigned_children: tuple[ChildReference, ...]


@dataclass(frozen=True, slots=True)
class SourceRetirementReceipt:
    identity: ExecutorIdentity
    ticket_id: str
    conversation_id: str | None
    assigned_children: tuple[ChildReference, ...]
    result: MoveRetirementResult


@dataclass(frozen=True, slots=True)
class ReconciliationInventory:
    identity: ExecutorIdentity
    children: tuple[ChildInventory, ...]
    queued_envelopes: tuple[QueuedEnvelope, ...]
    retained_command_outcomes: tuple[CommandOutcome, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    identity: ExecutorIdentity
    receipts: tuple[AcceptanceReceipt, ...]
    answers: tuple[DurableAnswer, ...]
    ask_evidence: tuple[AskEvidence, ...]
    acknowledged_command_ids: tuple[str, ...]


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


class DurableSpool(Protocol):
    """Methods are synchronous; async callers use a worker thread for disk I/O."""

    def enqueue(self, envelope: QueuedEnvelope, snapshots: tuple[FileSnapshot, ...] = ()) -> None:
        """Return after snapshots, metadata, publication and parent directory fsync."""
        ...

    def pending(self) -> tuple[QueuedEnvelope, ...]: ...

    def pending_heads(self) -> tuple[QueuedEnvelope, ...]:
        """First pending submission per conversation, retaining conversation order."""
        ...

    def pending_count(self) -> int: ...

    def has_pending(self, conversation_id: str | None = None) -> bool: ...

    def snapshots(self, submission_id: str) -> tuple[FileSnapshot, ...]: ...

    def pin_snapshots(self, submission_id: str) -> AbstractContextManager[tuple[FileSnapshot, ...]]:
        """Retain immutable paths until exit without a network-spanning transaction."""
        ...

    def acknowledge(self, receipt: AcceptanceReceipt) -> None:
        """Delete only matching sender/ID/digest after a durable VPS first result."""
        ...


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
    preview_bind = "preview_bind"
    preview_bound = "preview_bound"
    preview_release = "preview_release"
    file_slot = "file_slot"
    protocol_refused = "protocol_refused"
    reconcile_inventory = "reconcile_inventory"
    reconcile_result = "reconcile_result"
    reconciliation_ready = "reconciliation_ready"
    source_retire = "source_retire"
    source_retired = "source_retired"
    envelope = "envelope"
    receipt = "receipt"
    file_manifest = "file_manifest"
    file_complete = "file_complete"
    file_accepted = "file_accepted"
    command = "command"
    command_outcome = "command_outcome"
    command_outcome_ack = "command_outcome_ack"
    read_request = "read_request"
    read_response = "read_response"
    health = "health"
    quiesce = "quiesce"
    quiescence = "quiescence"
    error = "error"


@dataclass(frozen=True, slots=True)
class WireMessage:
    """Frame body: {kind, request_id, payload}; kind selects the validated codec.

    request_id correlates one exchange only. Domain retry identity always remains
    submission_id, command_id or publication_id in the payload. Never redispatch a
    native command merely because a request_id response was lost.
    """

    kind: WireMessageKind
    request_id: str
    payload: JsonObject


class IpcOperation(StrEnum):
    read = "read"
    write = "write"
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


@dataclass(frozen=True, slots=True)
class IpcResponse:
    request_id: str
    result: JsonObject | None
    error: JsonObject | None
    queued: QueuedCliResult | None = None


@dataclass(frozen=True, slots=True)
class LiveReadRequest:
    """GET-only allowlisted Panels read; never served from the outbound journal."""

    child: ChildReference
    path: str
    params: JsonObject


class AllocatingDurableSpool(DurableSpool, Protocol):
    @property
    def sender_id(self) -> str:
        """Persisted sender identity, independent of process incarnation."""
        ...

    def mint_envelope(
        self,
        *,
        conversation_id: str,
        kind: EnvelopeKind,
        payload: JsonObject,
        snapshots: tuple[FileSnapshot, ...] = (),
        submission_id: str | None = None,
    ) -> QueuedEnvelope:
        """Allocate sequence and persist envelope atomically before returning.

        A failed allocation must not strand a sequence gap. Counters survive empty
        journals, acknowledgments and process restart. A supplied existing ID returns
        its identical immutable envelope; conflicting content is refused.
        """
        ...


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


class ByteReader(Protocol):
    async def read(self, n: int = -1) -> bytes: ...


class FrameReader(Protocol):
    async def readexactly(self, n: int) -> bytes: ...


class ByteWriter(Protocol):
    def write(self, data: bytes) -> None: ...
    async def drain(self) -> None: ...
    def close(self) -> None: ...
