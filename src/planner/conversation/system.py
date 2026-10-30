"""The conversation system: SQLite for the record, one child process per conversation.

This is the real implementation of ``ConversationSystem``. Everything the contract says
lives here, and nothing about any vendor's protocol does — that is behind the backend
adapter seam in ``backends/contracts.py``.

Three ideas hold the concurrency together.

**One lock per conversation, and no wire I/O under it.** A send takes the lock only long
enough to decide what it is doing and to reserve the turn it is about to start. It lets go
while it talks to the child — spawning can take seconds — and takes it back to finish:
write the rows, mark the turn running. Everything that decides anything about a
conversation goes through that lock, so two callers never both think the agent is free.

**A turn token, minted by the core.** A turn is named when it is reserved, and the adapter
puts that name on everything it later reports. A turn the core has already ended is over:
its late news names a turn that is gone and is dropped. Endings are once per turn and the
first one wins, whoever caused it.

**One queue per conversation for backend news, worked through in order.** A turn's news
cannot land before the send that started it has finished writing its rows, so the queue
waits for a turn it has not seen finish reserving. That is the whole reason a turn can be
"reserved" as well as "running": between the two, the text is on the wire and the record
does not know about it yet, and an agent that answers that fast must still queue behind
its own prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from collections.abc import Callable, Coroutine, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import partial
from typing import IO, TYPE_CHECKING, Any
from uuid import uuid4

from planner.conversation.backends.contracts import EmissionCheck
from planner.conversation.contracts import NativeMessageAdmission, NativeMessageOperation

if TYPE_CHECKING:
    from planner.conversation.admission.contracts import AdmissionExpectation
    from planner.conversation.snapshot import BackendSnapshot, BackendSnapshotService
    from planner.executor.vps import ExecutorEndpoint


from planner.conversation.backend_event_contracts import TurnEndingContext
from planner.conversation.backends.contracts import (
    AdmissionRefused,
    AgentMessageCompletedArguments,
    AgentMessageDeltaArguments,
    BackendChild,
    BackendChildFactory,
    BackendPermissionAsk,
    BackendPromptRejected,
    BackendSpawnFailed,
    BackendSteerAccepted,
    BackendSteerOutcome,
    BackendSteerRefused,
    BackendSteerUncertain,
    BackendUserInputRequest,
    ComposerCatalogReportedArguments,
    ContextCompactedArguments,
    ModelThinkingHappenedArguments,
    NeedsRebind,
    PermissionAnswerWriteFailed,
    PermissionAskRaisedArguments,
    PlanUpdatedArguments,
    PromptWriteFailed,
    PromptWriteRefused,
    ProviderSelectionReportedArguments,
    SessionLoadFailed,
    TokenUsageReportedArguments,
    ToolCallFinishedArguments,
    ToolCallProgressArguments,
    ToolCallStartedArguments,
    TurnEndedArguments,
    TurnToken,
    UserInputAnswerWriteFailed,
    UserInputFailedArguments,
    UserInputRequestedArguments,
    VendorSessionCursorReboundArguments,
)
from planner.conversation.contracts import (
    AddressedPromptDeliveryReceipt,
    ComposerCatalogEntry,
    ConversationBackendKey,
    ConversationStartRequest,
    ConversationTurnReference,
    DeploymentRecoveryReference,
    HeldPrompt,
    HeldPromptPromotionFate,
    HeldPromptPromotionMode,
    PromptDeliveryFate,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliverySuppressed,
    PromptDeliveryUncertain,
    PromptQueueReason,
    backend_supports_steer,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    AutomaticCompactionResult,
    ContextCompactedEventPayload,
    ConversationEventPayload,
    ConversationLiveTailFrame,
    ConversationTurnEnding,
    HeldPromptsChangedFrame,
    MessageToOwnerEventPayload,
    ModelChangedEventPayload,
    ModelThinkingFrame,
    PermissionAnsweredEventPayload,
    PlanEntry,
    PlanUpdatedEventPayload,
    PromptDeliveryRefusedEventPayload,
    PromptDeliveryUncertainEventPayload,
    PromptDiscardedEventPayload,
    PromptEventPayload,
    ProviderSelectionReportedEventPayload,
    ProviderSubmissionFact,
    TokenUsageEventPayload,
    ToolCallFinishedEventPayload,
    ToolCallProgressFrame,
    ToolCallStartedEventPayload,
    ToolCallStatus,
    UserInputAnswer,
    UserInputAnsweredEventPayload,
    UserInputFailedEventPayload,
    UserInputQuestion,
)
from planner.conversation.launch_intent import validate_launch_intent
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.logic.addressed_reply import (
    with_authenticated_reply_directive,
)
from planner.conversation.logic.backend_events import (
    interpret_permission_ask,
    interpret_turn_ending,
    interpret_user_input_request,
)
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.logic.held_line import (
    leading_run_that_can_share_a_turn,
    one_prompt_from,
)
from planner.conversation.message_content import (
    MessageContent,
    MessageText,
    prefix_message_content_text,
    require_message_content,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.model_resolution.contracts import (
    AliasResolutionFact,
    CanonicalModelValidation,
    CatalogResult,
    CatalogWorkspaceBinding,
    CorrectiveAction,
    LaunchContext,
    ModelSelection,
    ProviderCatalogObserver,
    SelectionKind,
    ValidationReason,
    ValidationState,
)
from planner.conversation.publication.mutations import PlainFieldMutation, SignalRuntimeMutation
from planner.conversation.storage import (
    ConversationRecord,
    ConversationRecordNamesNoModel,
    ConversationStore,
    StoredConversationEvent,
)
from planner.core import change_signal
from planner.core.commit_capture import register_capture_task
from planner.core.contracts import JsonDict, PlannerError, Principal, PrincipalKind
from planner.executor.contracts import (
    AcceptanceReceipt,
    AnswerApplication,
    BackendEvent,
    ChildCommand,
    ChildOperation,
    ChildReference,
    ExecutorEffectAcceptor,
    JsonObject,
    QueuedEnvelope,
)

LOGGER = logging.getLogger("planner.conversation")

# How long a child may sit doing nothing before it is stopped, and how often that is
# checked. A stopped child costs a conversation nothing: its session cursor is stored, so
# the next message resumes it without saying anything about the gap.
IDLE_CHILD_STOP_AFTER_SECONDS = 30 * 60
IDLE_CHILD_SWEEP_INTERVAL_SECONDS = 5 * 60

# A sweep runs every five minutes. This lead time keeps the automatic compaction before
# the backend cache boundary even when the conversation becomes eligible just after one.
AUTOMATIC_COMPACTION_AFTER_SECONDS = 50 * 60
AUTOMATIC_COMPACTION_BEFORE_SECONDS = 60 * 60
AUTOMATIC_COMPACTION_PROMPT = "/compact"
AUTOMATIC_COMPACTION_SENDER_LABEL = "Panels"

# How much of a failed turn's standard error goes in the error-log line. Enough to see
# what happened, not enough to bury the line it is part of.
STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS = 2048

# How often a conversation may say that its model is thinking. Reasoning arrives in a
# burst of many small pieces and they all mean one thing — the agent is alive and working
# — so telling a browser hundreds of times would be hundreds of frames saying the same
# thing. Dropping the rest costs nothing: a pulse carries no information to lose.
MODEL_THINKING_PULSE_INTERVAL_SECONDS = 0.25

# What separates a conversation's role text from the first message it is composed onto.
ROLE_TEXT_PROMPT_SEPARATOR = "\n\n"


def _queue_reason_for_refused_steer(
    refusal_reason: PromptDeliveryRefusalReason,
) -> PromptQueueReason:
    """Why a message that could not steer is now waiting.

    A refused steer always goes back to the line. The reason it went back is what the
    composer reads out, and a command that Codex takes only as its own turn is not the
    same event as a turn that would not accept steering at all.
    """
    if refusal_reason is PromptDeliveryRefusalReason.command_cannot_join_running_turn:
        return PromptQueueReason.command_needs_its_own_turn
    return PromptQueueReason.steer_refused


def completed_backend_text_event(content: MessageContent) -> None:
    """Classify backend prose as runtime output, never an addressed durable message."""
    del content
    return None


def _turn_fact(token: TurnToken, status: LifecycleStatus) -> LifecycleFact:
    return LifecycleFact(
        LifecycleKind.turn,
        str(token.turn_number),
        token.turn_number,
        LifecycleOperation.turn,
        status,
    )


def _prompt_fact(token: TurnToken, status: LifecycleStatus) -> LifecycleFact:
    return LifecycleFact(
        LifecycleKind.attempt,
        f"prompt:{token.turn_number}",
        token.turn_number,
        LifecycleOperation.prompt,
        status,
    )


class _ConversationPhase(StrEnum):
    """What a conversation is in the middle of. One phase, one owner.

    ``idle`` and ``running`` are the resting phases: nobody is part-way through anything,
    and a caller that needs the conversation to hold still can act. The other three each
    say that somebody already owns the conversation — a send is on the wire, an ending is
    being recorded, or the held queue is being worked through — and that a caller who
    wants to kill or replace the running turn has to wait for them to finish.
    """

    idle = "idle"
    starting = "starting"
    running = "running"
    ending = "ending"
    draining = "draining"


@dataclass(slots=True)
class _ReservedTurn:
    """A turn that has been named and is being written to the wire.

    ``resolved`` is set once its fate is known, whether it became the running turn or died
    on the way. The backend event queue waits on it rather than guessing.
    """

    token: TurnToken
    resolved: asyncio.Event
    automatic_compaction: bool = False
    automatic_notice: bool = False
    shutdown_owned: bool = False
    shutdown_fate: PromptDeliveryFate | None = None
    messages: tuple[_HeldPrompt, ...] = ()
    mode: PromptDeliveryMode = PromptDeliveryMode.queue


@dataclass(slots=True)
class _RunningTurn:
    token: TurnToken
    launch_validation: CanonicalModelValidation | None = None
    automatic_compaction: bool = False
    compaction_confirmed: bool = False
    pending_permission_ask_ids: set[str] = field(default_factory=set)
    permission_provider_ids: dict[str, str] = field(default_factory=dict)
    user_input_provider_ids: dict[str, str] = field(default_factory=dict)
    pending_user_input_questions: dict[str, tuple[UserInputQuestion, ...]] = field(
        default_factory=dict
    )
    prompt_senders: dict[Principal, None] = field(default_factory=dict)
    explicit_reply_recipients: set[Principal] = field(default_factory=set)
    automatic_notice: bool = False
    ended: bool = False
    callbacks_revoked: bool = False
    # Set when the core is part-way through ending this turn itself — an interrupt, or a
    # send-now killing the incumbent. The cancel goes out with the lock let go, and the
    # backend reports the ending it was just asked for while that is happening. Only one
    # of the two may be recorded, and it is the core's: the core is the side that knows
    # the turn was interrupted rather than merely over.
    ending_is_the_cores: bool = False


def _ending_context(running: _RunningTurn) -> TurnEndingContext:
    return TurnEndingContext(
        running.token,
        running.automatic_compaction,
        running.compaction_confirmed,
        running.automatic_notice,
        tuple(running.prompt_senders),
        tuple(running.explicit_reply_recipients),
    )


@dataclass(slots=True)
class _AdmittedSenderMessage:
    """A send that is in flight, named by the id its sender minted.

    A second call naming the same id is the same message, so it waits on ``settled`` and
    then answers from what the first call actually did. Without the event it could only
    guess, and what it used to guess was a place in a queue the message was not in.
    """

    content: MessageContent
    sender_label: str
    sender: Principal | None = None
    recipient: Principal | None = None
    reply_requested: bool = True
    recovery_references: tuple[DeploymentRecoveryReference, ...] = ()
    settled: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(frozen=True, slots=True)
class _HeldPrompt:
    """A message waiting for the agent to free up, with the change it carries.

    The sender's own id and send instant wait here with it: a held message is delivered,
    refused or discarded long after the caller has gone, and whichever row it becomes has
    to carry the same id the sender minted.

    The owner read position is also captured here. Delivery can happen after unseen agent
    rows arrive, and the queued prompt cannot truthfully credit those later rows.
    """

    held_prompt_id: str
    content: MessageContent
    sender_label: str
    model_change: str | None
    reasoning_effort_change: str | None
    sender_message_id: str | None
    sent_at_unix_milliseconds: int | None
    snapshot_sent_at_unix_milliseconds: int
    sender: Principal | None
    recipient: Principal | None
    reply_requested: bool
    queue_reason: PromptQueueReason
    owner_read_through_sequence: int | None
    acknowledge_owner_on_failure: bool = True
    recovery_references: tuple[DeploymentRecoveryReference, ...] = ()
    delivery_uncertain: bool = False

    reasoning_effort_is_set: bool = False
    selection_kind: SelectionKind | None = None
    original_selection: ModelSelection | None = None


@dataclass(frozen=True, slots=True)
class _PromptDeliveryAttempt:
    """The wire result and whether a refusal must remain after its caller leaves.

    A normal direct refusal is returned to the caller. A refusal after replacement of an
    already-failed child is also recorded because recovery is the durable outcome.
    """

    refusal_reason: PromptDeliveryRefusalReason | None = None
    record_refusal: bool = False
    composed_content_delivered: bool = True
    canonical_validation: CanonicalModelValidation | None = None
    original_selection: ModelSelection | None = None
    refusal_detail: str | None = None
    suppression_reason: str | None = None
    messages: tuple[_HeldPrompt, ...] = ()
    automatic_notice: bool = False
    provider_submission: ProviderSubmissionFact | None = None


type _BackendEventHandler = Callable[[], Coroutine[Any, Any, None]]


@dataclass(slots=True)
class _ConversationState:
    """Everything this process knows about one conversation.

    ``record`` mirrors the conversation's stored row and is replaced whenever the row
    moves, so there is one answer in memory rather than a row and a drifting copy of it.
    """

    record: ConversationRecord
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    phase: _ConversationPhase = _ConversationPhase.idle
    phase_settled: asyncio.Event = field(default_factory=asyncio.Event)
    held_prompts: deque[_HeldPrompt] = field(default_factory=deque)
    admitted_sender_messages: dict[str, _AdmittedSenderMessage] = field(default_factory=dict)
    sender_messages_being_delivered: dict[str, _AdmittedSenderMessage] = field(default_factory=dict)
    backend_event_queue: asyncio.Queue[_BackendEventHandler] = field(default_factory=asyncio.Queue)
    backend_event_pump: asyncio.Task[None] | None = None
    backend_event_in_progress: bool = False
    child_event_sink: object | None = None
    child_reported_model: str | None = None
    settlement_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    steer_settlements: set[asyncio.Event] = field(default_factory=set)
    reserved_turn: _ReservedTurn | None = None
    running_turn: _RunningTurn | None = None
    last_ended_turn_token: TurnToken | None = None
    last_ended_turn_was_automatic_compaction: bool = False
    child: BackendChild | None = None
    child_resolved_model_id: str | None = None
    child_launch_context: LaunchContext | None = None
    local_child_retirement_proven: bool = False
    child_is_quarantined: bool = False
    executor_child_reference: Any = None
    executor_initial_commands: tuple[ChildCommand, ...] = ()
    executor_delivery_uncertain: bool = False
    initial_shutdown_task: asyncio.Task[bool] | None = None
    initial_shutdown_child: BackendChild | None = None
    next_turn_number: int = 1
    has_delivered_prompt: bool = False
    last_touched_monotonic: float = 0.0
    # When this conversation last said its model was thinking. Kept per conversation
    # because the rate it may be said at is about one browser watching one conversation.
    model_thinking_shown_at_monotonic: float = 0.0

    def __post_init__(self) -> None:
        self.phase_settled.set()


class SqliteProcessConversationSystem:
    """The conversation system Panels runs on.

    It is handed the store its record lives in and one child factory per backend key. It
    spawns nothing until a conversation has something to send. Capabilities do not
    survive a restart. Retained native identities fence fresh work until their old
    children have been stopped; inventory never restores a live turn.
    """

    def __init__(
        self,
        *,
        store: ConversationStore,
        backend_child_factories: Mapping[ConversationBackendKey, BackendChildFactory],
        message_files: ConversationMessageFiles,
        live_tail: ConversationLiveTail | None = None,
        monotonic_now: Callable[[], float] = time.monotonic,
        idle_child_stop_after_seconds: float = IDLE_CHILD_STOP_AFTER_SECONDS,
        idle_child_sweep_interval_seconds: float = IDLE_CHILD_SWEEP_INTERVAL_SECONDS,
        unix_time_now: Callable[[], float] = time.time,
        backend_snapshots: BackendSnapshotService | None = None,
        runtime_ownership: IO[bytes] | None = None,
    ) -> None:
        missing = sorted(set(ConversationBackendKey) - set(backend_child_factories))
        if missing:
            raise ValueError(f"no backend child factory for {missing}")
        self._store = store
        from planner.conversation.snapshot import BackendSnapshotService

        self._backend_snapshots = backend_snapshots or BackendSnapshotService()
        self._catalog_observer: ProviderCatalogObserver | None = None
        self._catalog_endpoint: ExecutorEndpoint | None = None
        self._message_files = message_files
        # Nobody watching is the ordinary case for a system built without one: the record
        # is written exactly the same way, and there is simply nowhere to show it.
        self._live_tail = live_tail
        self._backend_child_factories = dict(backend_child_factories)
        self._monotonic_now = monotonic_now
        self._idle_child_stop_after_seconds = idle_child_stop_after_seconds
        self._idle_child_sweep_interval_seconds = idle_child_sweep_interval_seconds
        self._unix_time_now = unix_time_now
        self._conversations: dict[str, _ConversationState] = {}
        self._conversations_lock = asyncio.Lock()
        self._idle_child_janitor: asyncio.Task[None] | None = None
        self._signal = store.signal_coordinator
        self._runtime_ownership = store.claim_runtime(existing_ownership=runtime_ownership)
        try:
            for record, metadata in store.read_executor_runtime_snapshots():
                state = _ConversationState(record=record)
                if metadata is not None:
                    self._restore_executor_turn_metadata(state, metadata)
                self._conversations[record.conversation_id] = state
        except BaseException:
            self._runtime_ownership.close()
            raise
        if self._signal is not None:
            self._signal.bind_runtime(self._runtime_turn)
        self._shutting_down = False
        self._shutdown_task: asyncio.Task[None] | None = None

    # --- the contract -------------------------------------------------------------------

    async def run_if_idle(self, conversation_id: str, operation: Callable[[], None]) -> bool:
        state = await self._conversation_state(conversation_id)
        if state is None:
            operation()
            return True
        if state.lock.locked():
            return False
        async with state.lock:
            if (
                state.phase is not _ConversationPhase.idle
                or state.reserved_turn is not None
                or state.running_turn is not None
                or state.held_prompts
                or state.admitted_sender_messages
                or state.sender_messages_being_delivered
                or state.steer_settlements
                or any(not task.done() for task in state.settlement_tasks)
            ):
                return False
            operation()
            return True


    async def start_conversation(self, request: ConversationStartRequest) -> None:
        self._require_runtime_ownership()
        """Create the conversation. Writing its row is the first thing that happens.

        The row exists the moment it is committed, so a message can arrive for this
        conversation before this call has finished — and that message will have picked the
        conversation up from its row, and may already have a child running. Which is why
        what follows only fills a gap: whatever is there is what this process is using, and
        replacing it would strand a live child that nothing can reach any more.
        """
        resolved = resolve_conversation_start_request(request)
        record = await self._store.create_conversation(resolved)
        async with self._conversations_lock:
            if record.conversation_id not in self._conversations:
                with nullcontext() if self._signal is None else self._signal.runtime_registry():
                    self._conversations[record.conversation_id] = _ConversationState(record=record)

    async def send(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        owner_read_through_sequence: int | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
        recovery_references: tuple[DeploymentRecoveryReference, ...] = (),
        native_message_operation: NativeMessageOperation | None = None,
    ) -> PromptDeliveryFate:
        receipt = await self.send_with_receipt(
            conversation_id,
            content,
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            reasoning_effort_is_set=reasoning_effort_is_set,
            selection_kind=selection_kind,
            owner_read_through_sequence=owner_read_through_sequence,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
            recovery_references=recovery_references,
            native_message_operation=native_message_operation,
        )
        return receipt.fate

    async def send_with_receipt(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        owner_read_through_sequence: int | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
        recovery_references: tuple[DeploymentRecoveryReference, ...] = (),
        record_refusal: bool = False,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> AddressedPromptDeliveryReceipt:
        """Send a message in. See the contract; the two sender-minted fields are extra.

        ``sender_message_id`` and ``sent_at_unix_milliseconds`` are the sender's own facts
        about this message and are stored on its row exactly as they were given. A sender
        that mints neither — every caller inside Panels today — leaves both absent and
        nothing about its rows changes.
        """
        validate_launch_intent(model_change, selection_kind)
        if record_refusal and sender_message_id is None:
            raise ValueError("record_refusal requires a sender_message_id")
        state = await self._conversation_state(conversation_id)
        if state is None:
            return AddressedPromptDeliveryReceipt(
                PromptDeliveryRefused(PromptDeliveryRefusalReason.no_such_conversation), False
            )
        return await self._own_settlement(
            state,
            self._send_with_receipt(
                conversation_id,
                content,
                sender_label=sender_label,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
                owner_read_through_sequence=owner_read_through_sequence,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
                reply_requested=reply_requested,
                recovery_references=recovery_references,
                record_refusal=record_refusal,
                reasoning_effort_is_set=reasoning_effort_is_set,
                selection_kind=selection_kind,
                native_message_operation=native_message_operation,
            ),
        )

    async def _send_with_receipt(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        owner_read_through_sequence: int | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
        recovery_references: tuple[DeploymentRecoveryReference, ...] = (),
        record_refusal: bool = False,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> AddressedPromptDeliveryReceipt:
        """Send a message in. See the contract; the two sender-minted fields are extra.

        ``sender_message_id`` and ``sent_at_unix_milliseconds`` are the sender's own facts
        about this message and are stored on its row exactly as they were given. A sender
        that mints neither — every caller inside Panels today — leaves both absent and
        nothing about its rows changes.
        """
        require_message_content(content)
        state = await self._conversation_state(conversation_id)
        if state is None:
            return AddressedPromptDeliveryReceipt(
                fate=PromptDeliveryRefused(
                    refusal_reason=PromptDeliveryRefusalReason.no_such_conversation
                ),
                newly_accepted=False,
            )
        state.last_touched_monotonic = self._monotonic_now()

        # A prompt from the owner acknowledges only the record that existed when the
        # send entered the conversation's ordering lock. Every later delivery outcome
        # carries this one admission point instead of sampling the record again.
        acknowledge_owner_on_failure = owner_read_through_sequence is None
        async with state.lock:
            if owner_read_through_sequence is not None:
                if sender is None or sender.kind is not PrincipalKind.owner:
                    raise ValueError("only the owner can acknowledge messages with a reply")
                if not 0 <= owner_read_through_sequence <= state.record.latest_sequence:
                    raise ValueError("owner read boundary is outside this conversation")
            else:
                owner_read_through_sequence = (
                    state.record.latest_sequence
                    if sender is not None and sender.kind is PrincipalKind.owner
                    else None
                )

        if sender_message_id is not None:
            while True:
                settled: asyncio.Event | None = None
                async with state.lock:
                    observed_sequence = state.record.latest_sequence
                    admitted = state.sender_messages_being_delivered.get(sender_message_id)
                    if admitted is None:
                        admitted = state.admitted_sender_messages.get(sender_message_id)
                    if admitted is not None:
                        if (
                            admitted.content,
                            admitted.sender_label,
                            admitted.sender,
                            admitted.recipient,
                            admitted.recovery_references,
                        ) != (content, sender_label, sender, recipient, recovery_references):
                            raise ValueError("sender_message_id already names a different message")
                        settled = admitted.settled
                    else:
                        for position, held in enumerate(state.held_prompts, start=1):
                            if held.sender_message_id != sender_message_id:
                                continue
                            if (
                                held.content != content
                                or held.sender_label != sender_label
                                or held.sender != sender
                                or held.recipient != recipient
                                or held.recovery_references != recovery_references
                                or held.model_change != model_change
                                or held.reasoning_effort_change != reasoning_effort_change
                                or held.reasoning_effort_is_set != reasoning_effort_is_set
                                or held.selection_kind != selection_kind
                            ):
                                raise ValueError(
                                    "sender_message_id already names a different message"
                                )
                            return AddressedPromptDeliveryReceipt(
                                fate=PromptDeliveryQueued(queue_position=position),
                                newly_accepted=False,
                            )
                if settled is not None:
                    # The first send of this same message has not finished. Its fate is
                    # this call's answer too, so this waits for it and then reads what it
                    # actually did, rather than naming a place in a queue the message is
                    # not in and may never join.
                    await settled.wait()
                    continue
                outcome = await self._store.sender_message_outcome(
                    conversation_id, sender_message_id
                )
                if outcome is not None:
                    payload = outcome.payload
                    if (
                        getattr(payload, "content", None) != content
                        or getattr(payload, "sender_label", None) != sender_label
                        or getattr(payload, "sender", None) != sender
                        or getattr(payload, "recipient", None) != recipient
                        or getattr(payload, "recovery_references", ()) != recovery_references
                    ):
                        raise ValueError("sender_message_id already names a different message")
                    if isinstance(payload, PromptEventPayload):
                        if payload.mode is PromptDeliveryMode.steer:
                            fate: PromptDeliveryFate = PromptDeliveryInjected()
                        else:
                            fate = PromptDeliveryStarted()
                        return AddressedPromptDeliveryReceipt(fate, newly_accepted=False)
                    if isinstance(payload, PromptDeliveryUncertainEventPayload):
                        return AddressedPromptDeliveryReceipt(
                            PromptDeliveryUncertain(), newly_accepted=False
                        )
                    if (
                        isinstance(payload, PromptDiscardedEventPayload)
                        and payload.suppression_reason
                    ):
                        return AddressedPromptDeliveryReceipt(
                            PromptDeliverySuppressed(payload.suppression_reason),
                            newly_accepted=False,
                        )
                    reason = getattr(
                        payload,
                        "refusal_reason",
                        PromptDeliveryRefusalReason.write_to_backend_failed,
                    )
                    return AddressedPromptDeliveryReceipt(
                        PromptDeliveryRefused(
                            refusal_reason=reason,
                            model_validation=getattr(payload, "model_validation", None),
                        ),
                        newly_accepted=False,
                    )
                async with state.lock:
                    if (
                        state.record.latest_sequence != observed_sequence
                        or sender_message_id in state.admitted_sender_messages
                        or sender_message_id in state.sender_messages_being_delivered
                        or any(
                            message.sender_message_id == sender_message_id
                            for message in state.held_prompts
                        )
                    ):
                        # The outcome read raced another admission or its completed
                        # receipt. Recheck it before this identity crosses the wire.
                        continue
                    state.admitted_sender_messages[sender_message_id] = _AdmittedSenderMessage(
                        content=content,
                        sender_label=sender_label,
                        sender=sender,
                        recipient=recipient,
                        reply_requested=reply_requested,
                        recovery_references=recovery_references,
                    )
                break

        try:
            if mode is PromptDeliveryMode.steer:
                fate = await self._steer(
                    state,
                    content,
                    sender_label,
                    model_change,
                    reasoning_effort_change,
                    sender_message_id,
                    sent_at_unix_milliseconds,
                    sender,
                    recipient,
                    reply_requested,
                    recovery_references,
                    owner_read_through_sequence,
                    acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                    selection_kind=selection_kind,
                    native_message_operation=native_message_operation,
                )
            elif mode is PromptDeliveryMode.send_now:
                fate = await self._send_now(
                    state,
                    content,
                    sender_label,
                    model_change,
                    reasoning_effort_change,
                    sender_message_id,
                    sent_at_unix_milliseconds,
                    sender,
                    recipient,
                    reply_requested,
                    recovery_references,
                    owner_read_through_sequence,
                    acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                    selection_kind=selection_kind,
                    native_message_operation=native_message_operation,
                )
            else:
                fate = await self._queue(
                    state,
                    content,
                    sender_label,
                    model_change,
                    reasoning_effort_change,
                    sender_message_id,
                    sent_at_unix_milliseconds,
                    sender,
                    recipient,
                    reply_requested,
                    recovery_references,
                    owner_read_through_sequence,
                    acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                    selection_kind=selection_kind,
                    native_message_operation=native_message_operation,
                )
            if (
                record_refusal
                and isinstance(fate, PromptDeliveryRefused)
                and sender_message_id is not None
                and await self._store.sender_message_outcome(conversation_id, sender_message_id)
                is None
            ):
                # A server-originated first send has no browser sender cache. Preserve
                # its refusal here, unless recovery already recorded the same message.
                async with state.lock:
                    await self._append_event(
                        state,
                        PromptDeliveryRefusedEventPayload(
                            recovery_references=recovery_references,
                            content=content,
                            sender_label=sender_label,
                            mode=mode,
                            refusal_reason=fate.refusal_reason,
                            sender_message_id=sender_message_id,
                            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                            sender=sender,
                            recipient=recipient,
                        ),
                        owner_read_through_sequence=(
                            owner_read_through_sequence if acknowledge_owner_on_failure else None
                        ),
                        native_message_operation=native_message_operation,
                    )
            if (
                not acknowledge_owner_on_failure
                and owner_read_through_sequence is not None
                and isinstance(fate, PromptDeliveryQueued)
            ):
                # Queue admission accepts the reply now. Its eventual delivery keeps
                # the same boundary, so later agent messages remain unread.
                async with state.lock:
                    record = await self._persist(
                        state,
                        partial(
                            self._store.advance_owner_read_through_sequence,
                            conversation_id,
                            owner_read_through_sequence,
                        ),
                    )
                    if record is not None:
                        state.record = replace(
                            state.record,
                            owner_read_through_sequence=max(
                                state.record.owner_read_through_sequence,
                                record.owner_read_through_sequence,
                            ),
                        )
            return AddressedPromptDeliveryReceipt(
                fate=fate,
                newly_accepted=not isinstance(fate, PromptDeliveryRefused),
            )
        finally:
            if sender_message_id is not None:
                async with state.lock:
                    admitted = state.admitted_sender_messages.pop(sender_message_id, None)
                if admitted is not None:
                    # Whatever this send turned into, it is over. Anything holding the same
                    # id can stop waiting and read the answer.
                    admitted.settled.set()

    async def interrupt(self, conversation_id: str) -> None:
        state = await self._conversation_state(conversation_id)
        if state is None:
            return
        await self._own_settlement(state, self._interrupt(conversation_id))

    async def _interrupt(self, conversation_id: str) -> None:
        state = await self._conversation_state(conversation_id)
        if state is None:
            return
        state.last_touched_monotonic = self._monotonic_now()
        await self._acquire_settled(state)
        try:
            running = state.running_turn
            if running is None:
                return
            try:
                await self._cancel_and_end_running_turn(state, running)
            finally:
                self._settle_phase(state)
        finally:
            state.lock.release()
        await self._drain_held_prompts(state)

    async def record_message_to_owner(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        sender: Principal,
        recipient: Principal,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        credit_explicit_reply: bool = True,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> None:
        """Append an owner-bound message under the conversation's ordering lock."""
        state = await self._conversation_state(conversation_id)
        if state is None:
            raise ValueError("no such conversation")
        await self._own_settlement(
            state,
            self._record_message_to_owner(
                conversation_id,
                content,
                sender_label=sender_label,
                sender=sender,
                recipient=recipient,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                credit_explicit_reply=credit_explicit_reply,
                native_message_operation=native_message_operation,
            ),
        )

    async def _record_message_to_owner(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        sender: Principal,
        recipient: Principal,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        credit_explicit_reply: bool = True,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> None:
        """Append an owner-bound message under the conversation's ordering lock."""
        require_message_content(content)
        state = await self._conversation_state(conversation_id)
        if state is None:
            raise ValueError("no such conversation")
        async with state.lock:
            if sender_message_id is not None:
                outcome = await self._store.sender_message_outcome(
                    conversation_id, sender_message_id
                )
                if outcome is not None:
                    payload = outcome.payload
                    if not isinstance(payload, MessageToOwnerEventPayload) or (
                        payload.content,
                        payload.sender_label,
                        payload.sender,
                        payload.recipient,
                    ) != (content, sender_label, sender, recipient):
                        raise ValueError("sender_message_id already names a different message")
                    return
            await self._append_event(
                state,
                MessageToOwnerEventPayload(
                    content=content,
                    sender=sender,
                    recipient=recipient,
                    sender_label=sender_label,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                ),
                native_message_operation=native_message_operation,
            )
            if credit_explicit_reply:
                self._credit_explicit_reply(state, recipient)

    async def active_turn_reference(self, conversation_id: str) -> ConversationTurnReference | None:
        """Capture the active turn under the same lock that ends it."""
        state = await self._conversation_state(conversation_id)
        if state is None:
            return None
        async with state.lock:
            running = state.running_turn
            if running is None:
                return None
            return ConversationTurnReference(conversation_id, running.token.turn_number)

    async def turn_expects_reply(
        self, turn: ConversationTurnReference, recipient: Principal
    ) -> bool:
        """Return whether ``recipient`` is an unanswered sender on the named turn."""
        state = await self._conversation_state(turn.conversation_id)
        if state is None:
            return False
        async with state.lock:
            running = state.running_turn
            return bool(
                running is not None
                and running.token.turn_number == turn.turn_number
                and recipient in running.prompt_senders
                and recipient not in running.explicit_reply_recipients
            )

    async def record_explicit_reply(
        self, turn: ConversationTurnReference, recipient: Principal
    ) -> None:
        """Credit an accepted explicit send to the source turn, without another row."""
        state = await self._conversation_state(turn.conversation_id)
        if state is None:
            return
        async with state.lock:
            running = state.running_turn
            if running is not None and running.token.turn_number == turn.turn_number:
                running.explicit_reply_recipients.add(recipient)

    @staticmethod
    def _credit_explicit_reply(state: _ConversationState, recipient: Principal) -> None:
        running = state.running_turn
        if running is not None:
            running.explicit_reply_recipients.add(recipient)

    async def retirement_is_confirmed(self, conversation_id: str) -> bool:
        """Require an actual cleanup receipt or proof that no source ever started."""
        from planner.core.db import connect

        conn = connect(self._store._db_path)
        try:
            retained = conn.execute(
                "SELECT runtime_json FROM executor_core_turns WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            remote_receipt = (
                retained is not None and json.loads(retained[0]).get("source_retired") is True
            )
        finally:
            conn.close()
        state = await self._conversation_state(conversation_id)
        if state is None:
            return remote_receipt
        async with state.lock:
            return (
                state.child is None
                and state.executor_child_reference is None
                and not state.child_is_quarantined
                and (state.initial_shutdown_task is None or state.initial_shutdown_task.done())
                and (
                    remote_receipt
                    or state.local_child_retirement_proven
                    or not state.has_delivered_prompt
                )
            )

    async def kill(self, conversation_id: str) -> None:
        state = await self._conversation_state(conversation_id)
        if state is None:
            return
        await self._own_settlement(state, self._kill(conversation_id))

    async def _kill(self, conversation_id: str) -> None:
        """Stop the running turn and throw away everything that was waiting behind it.

        Unlike an interrupt, this is the end of the conversation's traffic: the queue is
        emptied rather than let run, so nothing happens afterwards until someone sends
        again. Each discarded message is written down before the turn's ending, because
        text a caller handed over must never disappear without a trace.

        The agent goes too. A killed conversation is one nobody is coming back to, and a
        child left running is an agent working in somebody's folder that nothing will speak
        to again — it would sit there until the idle sweep noticed. Whether a turn was
        running makes no difference to that.

        Queue disposition and local ending hold the conversation lock. The ending
        phase owns remote cancellation while the lock is free, so final native events
        can commit before cancellation settles. Local cancellation retains the lock.
        Remote retirement has its own task and quarantine fence, and its caller joins
        after it releases the lock.
        """
        state = await self._conversation_state(conversation_id)
        if state is None:
            return
        state.last_touched_monotonic = self._monotonic_now()
        if await self._kill_initial_delivery(state):
            return
        await self._acquire_settled(state)
        try:
            await self._discard_held_prompts(state)
            running = state.running_turn
            if running is not None:
                await self._revoke_executor_turn_asks(state, running)
            owns_ending = True
            try:
                cancel_fact = None
                child = state.child
                if child is None and state.executor_child_reference is not None:
                    from planner.executor.remote import RemoteBackendChildFactory
                    from planner.executor.routing import LocaleBackendChildFactory
                    from planner.executor.store import ExecutorStore

                    reference = state.executor_child_reference
                    factory = self._backend_child_factories[state.record.backend_key]
                    if ExecutorStore(self._store._db_path).child_assignment(reference) is not None:
                        if isinstance(factory, LocaleBackendChildFactory):
                            child = factory.recover_remote_child(reference)
                        elif isinstance(factory, RemoteBackendChildFactory):
                            child = factory.recover(reference)
                        if child is not None:
                            state.child = child
                if child is not None:
                    from planner.executor.remote import RemoteBackendChild

                    if running is not None:
                        if isinstance(child, RemoteBackendChild):
                            reference = child.reference
                            reservation = state.reserved_turn
                            self._set_phase(state, _ConversationPhase.ending)
                            running.ending_is_the_cores = True
                            state.lock.release()
                            try:
                                cancel_fact = await self._cancel_child_turn(state, child)
                            finally:
                                await state.lock.acquire()
                            current_child = state.child
                            current_turn = state.running_turn
                            last_ended = state.last_ended_turn_token
                            owns_ending = (
                                not self._shutting_down
                                and state.reserved_turn is reservation
                                and (
                                    (
                                        current_turn is not None
                                        and current_turn.token == running.token
                                    )
                                    or (current_turn is None and last_ended == running.token)
                                )
                                and (
                                    last_ended is None
                                    or (
                                        last_ended.conversation_id == running.token.conversation_id
                                        and last_ended.turn_number <= running.token.turn_number
                                    )
                                )
                            )
                            if (
                                not owns_ending
                                or not isinstance(current_child, RemoteBackendChild)
                                or current_child.reference != reference
                                or state.executor_child_reference != reference
                            ):
                                owns_ending = False
                                return
                            # Inventory can replace the proxy for this same native child.
                            child = current_child
                            running = current_turn
                        else:
                            cancel_fact = await self._cancel_child_turn(state, child)
                    if state.child is child:
                        assert child is not None
                        await self._discard_child(state, child)
                if running is not None:
                    await self._end_turn(
                        state,
                        running,
                        ConversationTurnEnding.interrupted,
                        None,
                        cancel_fact=cancel_fact,
                    )
            finally:
                if owns_ending and not self._shutting_down:
                    self._settle_phase(state)
        finally:
            state.lock.release()
        cleanup = state.initial_shutdown_task
        if cleanup is not None and not cleanup.done():
            await asyncio.shield(cleanup)

    async def _kill_initial_delivery(self, state: _ConversationState) -> bool:
        """Own local disposition without waiting for an unacknowledged native operation."""
        from planner.executor.remote import RemoteBackendChild, RemoteBackendChildFactory
        from planner.executor.routing import LocaleBackendChildFactory
        from planner.executor.store import ExecutorStore

        async with state.lock:
            reservation = state.reserved_turn
            if reservation is None and state.running_turn is not None:
                return False
            if reservation is None:
                if not state.child_is_quarantined or state.executor_child_reference is None:
                    return False
                reference = state.executor_child_reference
                child = state.child
                if child is None:
                    factory = self._backend_child_factories[state.record.backend_key]
                    if isinstance(factory, LocaleBackendChildFactory):
                        child = factory.recover_remote_child(reference)
                    elif isinstance(factory, RemoteBackendChildFactory):
                        child = factory.recover(reference)
                    state.child = child
                await self._discard_held_prompts(state)
                if isinstance(child, RemoteBackendChild):
                    self._schedule_remote_retirement(state, child)
                return True
            if reservation.shutdown_owned and reservation.shutdown_fate is not None:
                await self._discard_held_prompts(state)
                return True
            child = state.child
            if not isinstance(child, RemoteBackendChild):
                factory = self._backend_child_factories[state.record.backend_key]
                if not isinstance(factory, (RemoteBackendChildFactory, LocaleBackendChildFactory)):
                    return False
                if state.executor_child_reference is not None:
                    reference = state.executor_child_reference
                    if ExecutorStore(self._store._db_path).child_assignment(reference) is None:
                        return True
                    child = (
                        factory.recover_remote_child(reference)
                        if isinstance(factory, LocaleBackendChildFactory)
                        else factory.recover(reference)
                    )
                    state.child = child
            # Prompt acceptance can finish through the ordinary factual delivery path.
            if (
                not reservation.shutdown_owned
                and isinstance(child, RemoteBackendChild)
                and child.prompt_accepted
            ):
                return False
            reservation.shutdown_owned = True
            suppressed = (
                await child.cancel_initial_delivery()
                if isinstance(child, RemoteBackendChild)
                else True
            )
            old_facts = await self._store.lifecycle_facts(state.record.conversation_id)
            previous_prompt = next(
                (
                    fact
                    for fact in old_facts
                    if fact.kind is LifecycleKind.attempt
                    and fact.identity == f"prompt:{reservation.token.turn_number}"
                ),
                None,
            )
            if previous_prompt is not None and previous_prompt.status is LifecycleStatus.suppressed:
                suppressed = True
            state.child_is_quarantined = child is not None
            state.executor_delivery_uncertain = not suppressed
            self._set_phase(state, _ConversationPhase.ending)
            await self._discard_held_prompts(state)
            lifecycle = (
                _turn_fact(reservation.token, LifecycleStatus.unavailable),
                _prompt_fact(
                    reservation.token,
                    LifecycleStatus.suppressed if suppressed else LifecycleStatus.unknown,
                ),
            )
            metadata = self._initial_shutdown_metadata(state, reservation)
            payloads: list[ConversationEventPayload] = []
            if reservation.messages:
                for message in reservation.messages:
                    payload: ConversationEventPayload
                    if suppressed:
                        payload = PromptDiscardedEventPayload(
                            content=message.content,
                            sender_label=message.sender_label,
                            sender_message_id=message.sender_message_id,
                            sent_at_unix_milliseconds=message.sent_at_unix_milliseconds,
                            sender=message.sender,
                            recipient=message.recipient,
                            recovery_references=message.recovery_references,
                            suppression_reason="conversation_killed",
                        )
                    else:
                        payload = PromptDeliveryUncertainEventPayload(
                            content=message.content,
                            sender_label=message.sender_label,
                            mode=reservation.mode,
                            sender_message_id=message.sender_message_id,
                            sent_at_unix_milliseconds=message.sent_at_unix_milliseconds,
                            sender=message.sender,
                            recipient=message.recipient,
                            recovery_references=message.recovery_references,
                        )
                    existing = (
                        await self._store.sender_message_outcome(
                            state.record.conversation_id, message.sender_message_id
                        )
                        if message.sender_message_id is not None
                        else None
                    )
                    if existing is None:
                        payloads.append(payload)
            # One commit contains every sender envelope, including envelopes without IDs.
            written = await self._persist(
                state,
                partial(
                    self._store.append_initial_shutdown_disposition,
                    state.record.conversation_id,
                    tuple(payloads),
                    lifecycle,
                    metadata,
                ),
            )
            self._take_in_written_rows(state, written)
            reservation.shutdown_fate = (
                PromptDeliverySuppressed("conversation_killed")
                if suppressed
                else PromptDeliveryUncertain()
            )
            state.last_ended_turn_token = reservation.token
            state.running_turn = None
            state.reserved_turn = None
            self._set_phase(state, _ConversationPhase.idle)
            reservation.resolved.set()
            if child is not None:
                self._configure_initial_command_recording(state, child, reservation)
                self._schedule_remote_retirement(state, child, reservation=reservation)
            return True

    def _initial_shutdown_metadata(
        self,
        state: _ConversationState,
        reservation: _ReservedTurn,
        *,
        source_retired: bool = False,
    ) -> tuple[str, str, str] | None:
        metadata = self._executor_turn_metadata(
            state, _RunningTurn(token=reservation.token, ended=True, callbacks_revoked=True)
        )
        if metadata is None:
            return None
        runtime = json.loads(metadata[2])
        runtime["source_retired"] = source_retired
        return metadata[0], metadata[1], json.dumps(runtime)

    def _configure_initial_command_recording(
        self, state: _ConversationState, child: Any, reservation: _ReservedTurn
    ) -> None:
        from planner.executor.remote import RemoteInitialDeliveryCancelled

        async def record(command: ChildCommand) -> None:
            async with state.lock:
                if reservation.shutdown_owned and command.operation is not ChildOperation.stop:
                    raise RemoteInitialDeliveryCancelled()
                if state.executor_child_reference != child.reference:
                    raise RemoteInitialDeliveryCancelled()
                if command.operation is ChildOperation.write_prompt:
                    if (
                        state.reserved_turn is not reservation
                        or command.turn != reservation.token
                        or (
                            state.last_ended_turn_token is not None
                            and state.last_ended_turn_token.turn_number
                            >= reservation.token.turn_number
                        )
                    ):
                        raise RemoteInitialDeliveryCancelled()
                    state.executor_initial_commands = tuple(
                        earlier
                        for earlier in state.executor_initial_commands
                        if earlier.operation is ChildOperation.start
                    )
                state.executor_initial_commands += (command,)
                metadata = (
                    self._initial_shutdown_metadata(state, reservation)
                    if reservation.shutdown_owned
                    else self._executor_turn_metadata(state, _RunningTurn(token=reservation.token))
                )
                if not reservation.shutdown_owned and metadata is not None:
                    runtime = json.loads(metadata[2])
                    # Intent is not native acceptance. A crash at any preparation or
                    # frame boundary restores uncertainty, never a fabricated run.
                    runtime["delivery_uncertain"] = True
                    metadata = metadata[0], metadata[1], json.dumps(runtime)
                await self._persist(
                    state,
                    partial(
                        self._store.transition_lifecycle,
                        state.record.conversation_id,
                        (),
                        executor_turn=metadata,
                        launch_command=command,
                        assigned_child=(
                            (child.reference, child.assignment_principal)
                            if child.assignment_principal is not None
                            else None
                        ),
                    ),
                )

        child.record_command = record

    def _schedule_remote_retirement(
        self,
        state: _ConversationState,
        child: Any,
        *,
        reservation: _ReservedTurn | None = None,
        nonadmitted_turn: TurnToken | None = None,
    ) -> asyncio.Task[bool]:
        """Fence the captured identity before its cleanup owner yields the lock."""
        reference: ChildReference = child.reference
        if nonadmitted_turn is not None:
            # Only the owner of this exact refused write may remove its circular
            # reservation dependency. The reservation itself remains unresolved.
            if (
                state.child is not child
                or state.executor_child_reference != reference
                or state.reserved_turn is not reservation
                or (
                    reservation is not None
                    and (reservation.token != nonadmitted_turn or reservation.shutdown_owned)
                )
                or (
                    state.last_ended_turn_token is not None
                    and state.last_ended_turn_token.turn_number >= nonadmitted_turn.turn_number
                )
            ):
                raise ValueError("Nonadmission does not own this child and turn")
        existing = state.initial_shutdown_task
        if existing is not None and not existing.done():
            if state.initial_shutdown_child is not child:
                raise ValueError("Pending retirement belongs to another child")
            return existing
        state.child_is_quarantined = True
        token = next(
            (
                command.turn
                for command in reversed(
                    state.executor_initial_commands + tuple(child.initial_commands)
                )
                if command.child == child.reference and command.turn is not None
            ),
            None,
        )
        if token is None:
            token = (
                reservation.token
                if reservation is not None
                else state.reserved_turn.token
                if state.reserved_turn is not None and child.initial_commands
                else state.running_turn.token
                if state.running_turn is not None
                else state.last_ended_turn_token
            )
        pending = state.reserved_turn
        dependency = pending if pending is not None and pending.token == token else None
        if dependency is not None and (
            dependency.token == nonadmitted_turn
            or (
                state.phase is _ConversationPhase.idle
                and state.child_is_quarantined
                and state.executor_delivery_uncertain
            )
        ):
            # A settled unknown acknowledgment has no outstanding local write
            # to serialize. Its retained reservation cannot make exact source
            # retirement wait for the admission fact that retirement must settle.
            dependency = None
        if token is None:
            # Without original metadata there is no identity-safe retirement commit.
            async def unconfirmed() -> bool:
                return False

            task = asyncio.create_task(unconfirmed())
        else:
            snapshot = self._executor_turn_metadata(
                state, _RunningTurn(token=token, ended=True, callbacks_revoked=True)
            )
            if snapshot is not None:
                cleanup_runtime = json.loads(snapshot[2])
                cleanup_runtime["source_retired"] = False
                snapshot = (snapshot[0], snapshot[1], json.dumps(cleanup_runtime))

            async def record_stop(command: ChildCommand) -> None:
                async with state.lock:
                    if snapshot is None or command.child != child.reference:
                        raise ValueError("Retirement lacks exact child metadata")
                    from planner.executor.codec import encode_value

                    runtime = json.loads(snapshot[2])
                    commands = tuple(
                        earlier
                        for earlier in state.executor_initial_commands
                        if earlier.child == child.reference
                    ) + (command,)
                    runtime["initial_commands"] = encode_value(commands)
                    if state.executor_child_reference == child.reference:
                        state.executor_initial_commands = commands
                    await self._persist(
                        state,
                        partial(
                            self._store.transition_lifecycle,
                            state.record.conversation_id,
                            (),
                            executor_turn=(snapshot[0], snapshot[1], json.dumps(runtime)),
                        ),
                    )

            child.record_command = record_stop
            task = asyncio.create_task(self._stop_initial_child(state, child, dependency, snapshot))
        state.initial_shutdown_task = task
        state.initial_shutdown_child = child
        state.settlement_tasks.add(task)
        task.add_done_callback(state.settlement_tasks.discard)
        return task

    async def _stop_initial_child(
        self,
        state: _ConversationState,
        child: Any,
        dependency: _ReservedTurn | None,
        metadata: tuple[str, str, str] | None,
    ) -> bool:
        from planner.executor.store import ExecutorStore

        reference = child.reference
        if dependency is not None:
            await dependency.resolved.wait()
        try:
            await child.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Remote child termination remains unconfirmed")
            return False
        async with state.lock:
            if metadata is None or ExecutorStore(self._store._db_path).cleanup_evidence_uncertain(
                reference
            ):
                return False
            runtime = json.loads(metadata[2])
            if state.executor_child_reference == reference:
                from planner.executor.codec import encode_value

                runtime["initial_commands"] = encode_value(state.executor_initial_commands)
            runtime["source_retired"] = True
            retired = await self._persist(
                state,
                partial(
                    self._store.transition_lifecycle,
                    state.record.conversation_id,
                    (),
                    executor_turn=(metadata[0], metadata[1], json.dumps(runtime)),
                    retired_child=reference,
                ),
            )
            if retired is False:
                return False
            child.retire_local_reference()
            if state.child is child:
                state.child = None
            if state.executor_child_reference == reference:
                state.executor_child_reference = None
                state.child_is_quarantined = False
                pending = state.reserved_turn
                if (
                    pending is not None
                    and state.executor_delivery_uncertain
                    and pending.token.turn_number == json.loads(metadata[1])["turn_number"]
                ):
                    state.last_ended_turn_token = pending.token
                    state.reserved_turn = None
                    pending.resolved.set()
        if state.held_prompts and not self._shutting_down:
            drain = asyncio.create_task(self._drain_held_prompts(state))
            state.settlement_tasks.add(drain)
            drain.add_done_callback(state.settlement_tasks.discard)
        return True

    async def is_running(self, conversation_id: str) -> bool:
        """Whether a turn is running right now.

        Inspect only live state, without adopting a record or waiting for the adoption lock.
        A conversation absent from this runtime has no running turn.
        """
        state = self._live_conversation_state(conversation_id)
        with self._signal_read():
            return state is not None and state.running_turn is not None

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        state = self._live_conversation_state(conversation_id)
        with self._signal_read():
            if state is None or state.running_turn is None:
                return False
            return bool(state.running_turn.pending_permission_ask_ids)

    async def pending_permission_ask_ids(self, conversation_id: str) -> frozenset[str]:
        """Which asks are waiting for an answer right now.

        The same fact ``has_pending_permission_ask`` answers, named rather than counted,
        so a surface that has to show the ask itself can find it in the record instead of
        working out for itself which one is still live.
        """
        state = await self._conversation_state(conversation_id)
        with self._signal_read():
            if state is None or state.running_turn is None:
                return frozenset()
            return frozenset(state.running_turn.pending_permission_ask_ids)

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        state = self._live_conversation_state(conversation_id)
        with self._signal_read():
            if state is None or state.running_turn is None:
                return False
            return bool(state.running_turn.pending_user_input_questions)

    async def pending_user_input_request_ids(self, conversation_id: str) -> frozenset[str]:
        state = await self._conversation_state(conversation_id)
        with self._signal_read():
            if state is None or state.running_turn is None:
                return frozenset()
            return frozenset(state.running_turn.pending_user_input_questions)

    async def held_prompts(self, conversation_id: str) -> tuple[HeldPrompt, ...]:
        """Return a stable FIFO snapshot of the messages waiting in this process."""
        state = await self._conversation_state(conversation_id)
        if state is None:
            return ()
        async with state.lock:
            return tuple(
                HeldPrompt(
                    held_prompt_id=held.held_prompt_id,
                    content=held.content,
                    sender_label=held.sender_label,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.snapshot_sent_at_unix_milliseconds,
                    sender=held.sender,
                    recipient=held.recipient,
                    queue_reason=held.queue_reason,
                    delivery_uncertain=held.delivery_uncertain,
                )
                for held in state.held_prompts
            )

    async def promote_held_prompt(
        self,
        conversation_id: str,
        held_prompt_id: str,
        mode: HeldPromptPromotionMode,
    ) -> HeldPromptPromotionFate | None:
        """Atomically claim one held message and deliver it in the selected mode."""
        state = await self._conversation_state(conversation_id)
        if state is None:
            return None
        return await self._own_settlement(
            state, self._promote_held_prompt(conversation_id, held_prompt_id, mode)
        )

    async def _promote_held_prompt(
        self,
        conversation_id: str,
        held_prompt_id: str,
        mode: HeldPromptPromotionMode,
    ) -> HeldPromptPromotionFate | None:
        """Atomically claim one held message and deliver it in the selected mode."""
        from planner.executor.remote import RemoteCommandUncertain

        state = await self._conversation_state(conversation_id)
        if state is None:
            return None
        state.last_touched_monotonic = self._monotonic_now()

        await self._acquire_settled(state)
        held: _HeldPrompt | None = None
        reservation: _ReservedTurn | None = None
        steer_child: BackendChild | None = None
        steer_turn_token: TurnToken | None = None
        immediate_refusal: PromptDeliveryRefusalReason | None = None
        immediate_fate: HeldPromptPromotionFate | None = None
        try:
            position = next(
                (
                    index
                    for index, candidate in enumerate(state.held_prompts)
                    if candidate.held_prompt_id == held_prompt_id
                ),
                None,
            )
            if position is None:
                return None
            held = state.held_prompts[position]
            if held.delivery_uncertain:
                return PromptDeliveryUncertain()
            if state.child_is_quarantined or any(
                candidate.delivery_uncertain for candidate in state.held_prompts
            ):
                return PromptDeliveryQueued(queue_position=position + 1)

            if mode is HeldPromptPromotionMode.send_now:
                running = state.running_turn
                if running is not None and running.automatic_compaction:
                    return PromptDeliveryQueued(queue_position=position + 1)
                if running is not None:
                    try:
                        await self._cancel_and_end_running_turn(state, running)
                    except BaseException:
                        self._settle_phase(state)
                        raise
                self._begin_held_deliveries(state, (held,))
                del state.held_prompts[position]
                self._publish_held_prompts_changed(state)
                reservation = await self._reserve_turn(state)
            else:
                # A deliberate model identity choice needs a new admitted turn.
                # Steering bypasses launch admission, so keep its exact queue entry.
                if (
                    held.model_change is not None
                    or held.reasoning_effort_change is not None
                    or held.reasoning_effort_is_set
                    or held.selection_kind is not None
                ):
                    return PromptDeliveryQueued(queue_position=position + 1)
                self._begin_held_deliveries(state, (held,))
                del state.held_prompts[position]
                self._publish_held_prompts_changed(state)
                if not backend_supports_steer(state.record.backend_key):
                    immediate_refusal = PromptDeliveryRefusalReason.backend_cannot_steer
                elif state.running_turn is None or state.child is None:
                    immediate_refusal = PromptDeliveryRefusalReason.no_running_turn_to_steer_into
                else:
                    steer_child = state.child
                    steer_turn_token = state.running_turn.token
                    automatic_notice = await self._persist(
                        state,
                        partial(
                            self._store.all_sender_messages_are_manager_notices,
                            state.record.conversation_id,
                            (held.sender_message_id,),
                        ),
                    )
                    barrier = asyncio.Event()
                    state.steer_settlements.add(barrier)

                if immediate_refusal is not None:
                    immediate_fate = await self._record_steer_outcome(
                        state,
                        BackendSteerRefused(immediate_refusal),
                        turn_token=steer_turn_token,
                        content=held.content,
                        sender_label=held.sender_label,
                        sender_message_id=held.sender_message_id,
                        sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                        sender=held.sender,
                        recipient=held.recipient,
                        reply_requested=held.reply_requested,
                        recovery_references=held.recovery_references,
                        owner_read_through_sequence=held.owner_read_through_sequence,
                        acknowledge_owner_on_failure=held.acknowledge_owner_on_failure,
                    )
                    self._settle_held_deliveries(state, (held,))
        finally:
            state.lock.release()

        assert held is not None
        if mode is HeldPromptPromotionMode.send_now:
            assert reservation is not None
            try:
                reply_senders = (
                    (held.sender,)
                    if held.reply_requested
                    and held.sender is not None
                    and held.recipient is not None
                    else ()
                )
                reservation.automatic_notice = await self._persist(
                    state,
                    partial(
                        self._store.all_sender_messages_are_manager_notices,
                        state.record.conversation_id,
                        (held.sender_message_id,),
                    ),
                )
                delivery = await self._deliver_prompt(
                    state,
                    reservation.token,
                    content=held.content,
                    sender_label=held.sender_label,
                    sender_message_count=1,
                    mode=PromptDeliveryMode.send_now,
                    model_change=held.model_change,
                    reasoning_effort_change=held.reasoning_effort_change,
                    automatic_compaction=False,
                    messages=(held,),
                    reply_senders=reply_senders,
                )
            except (PromptWriteFailed, RemoteCommandUncertain):
                await self._record_delivery_uncertainty(state, reservation, (held,))
                return PromptDeliveryUncertain()
            except BaseException:
                async with state.lock:
                    self._quarantine_uncertain_held_batch(state, (held,))
                raise
            if delivery.suppression_reason is not None:
                self._abandon_reserved_turn(state, reservation, _ConversationPhase.idle)
                async with state.lock:
                    self._settle_held_deliveries(state, (held,))
                await self._drain_held_prompts(state)
                return PromptDeliverySuppressed(delivery.suppression_reason)
            reservation.automatic_notice = delivery.automatic_notice
            try:
                started = await self._finalize_delivery(
                    state,
                    reservation,
                    delivery.refusal_reason,
                    content=held.content,
                    sender_label=held.sender_label,
                    mode=PromptDeliveryMode.send_now,
                    model_change=held.model_change,
                    reasoning_effort_change=held.reasoning_effort_change,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                    sender=held.sender,
                    recipient=held.recipient,
                    reply_requested=held.reply_requested,
                    recovery_references=held.recovery_references,
                    canonical_validation=delivery.canonical_validation,
                    original_selection=delivery.original_selection,
                    refusal_detail=delivery.refusal_detail,
                    composed_content_delivered=delivery.composed_content_delivered,
                    provider_submission=delivery.provider_submission,
                    record_refusal=True,
                    phase_when_not_started=_ConversationPhase.idle,
                    owner_read_through_sequence=(
                        held.owner_read_through_sequence
                        if held.sender is not None and held.sender.kind is PrincipalKind.owner
                        else None
                    ),
                    acknowledge_owner_on_failure=held.acknowledge_owner_on_failure,
                )
                async with state.lock:
                    self._settle_held_deliveries(state, (held,))
            except BaseException:
                # Accepted work retains its reservation until recording settles.
                raise
            if started:
                return PromptDeliveryStarted()
            refusal = delivery.refusal_reason
            assert refusal is not None
            await self._drain_held_prompts(state)
            return PromptDeliveryRefused(
                refusal_reason=refusal,
                model_validation=(
                    self._validation_json(delivery.canonical_validation)
                    if refusal is PromptDeliveryRefusalReason.model_resolution_failed
                    else None
                ),
            )

        if immediate_refusal is not None:
            await self._drain_held_prompts(state)
            assert immediate_fate is not None
            return immediate_fate

        assert steer_child is not None
        assert steer_turn_token is not None
        attempt = await self._begin_attempt(state, steer_turn_token, LifecycleOperation.steer)
        messages, suppression_reason, automatic_notice = await self._admit_dispatch_messages(
            state,
            (held,),
            lifecycle_if_all_suppressed=(replace(attempt, status=LifecycleStatus.suppressed),),
        )
        if not messages:
            async with state.lock:
                self._settle_held_deliveries(state, (held,))
                state.steer_settlements.discard(barrier)
                barrier.set()
            assert suppression_reason is not None
            return PromptDeliverySuppressed(suppression_reason)
        adapter_error: Exception | None = None
        try:
            wire_content = with_authenticated_reply_directive(
                self._with_native_worker_context(state, steer_child, held.content),
                (
                    (held.sender,)
                    if held.reply_requested
                    and held.sender is not None
                    and held.recipient is not None
                    else ()
                ),
            )
            steer_outcome = await self._steer_with_admission(
                state,
                steer_child,
                steer_turn_token,
                wire_content,
                sender_content=held.content,
                sender_label=held.sender_label,
                attempt_id=attempt.identity,
            )
        except AdmissionRefused:
            steer_outcome = BackendSteerRefused(PromptDeliveryRefusalReason.model_resolution_failed)
        except PromptWriteRefused:
            steer_outcome = BackendSteerRefused(
                PromptDeliveryRefusalReason.write_to_backend_failed
            )
        except PromptWriteFailed:
            steer_outcome = BackendSteerUncertain()
        except asyncio.CancelledError:
            assert barrier is not None
            state.steer_settlements.discard(barrier)
            barrier.set()
            async with state.lock:
                self._quarantine_uncertain_held_batch(state, (held,))
            raise
        except Exception as error:
            # An unexpected adapter fault proves neither acceptance nor refusal.
            # Settle an unknown fate before propagating it, so this identity cannot
            # cross the backend boundary again or disappear from a held batch.
            adapter_error = error
            steer_outcome = BackendSteerUncertain()

        if isinstance(steer_outcome, BackendSteerRefused) and (
            steer_outcome.refusal_reason
            is PromptDeliveryRefusalReason.command_cannot_join_running_turn
        ):
            await self._persist(
                state,
                partial(
                    self._store.transition_lifecycle,
                    state.record.conversation_id,
                    (replace(attempt, status=LifecycleStatus.refused),),
                ),
            )
            await self._persist(
                state,
                partial(
                    self._store.record_sender_message_returned_to_queue,
                    state.record.conversation_id,
                    PromptDiscardedEventPayload(
                        content=held.content,
                        sender_label=held.sender_label,
                        sender_message_id=held.sender_message_id,
                        sender=held.sender,
                        recipient=held.recipient,
                    ),
                ),
            )
            # A command the backend takes only as its own turn. Promoting it was the
            # right gesture at the wrong moment, so the message goes back to the line
            # rather than being written down as undeliverable and thrown away.
            state.steer_settlements.discard(barrier)
            barrier.set()
            async with state.lock:
                self._settle_held_deliveries(state, (held,))
                return self._hold_prompt(
                    state,
                    content=held.content,
                    sender_label=held.sender_label,
                    model_change=held.model_change,
                    reasoning_effort_change=held.reasoning_effort_change,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                    sender=held.sender,
                    recipient=held.recipient,
                    reply_requested=held.reply_requested,
                    recovery_references=held.recovery_references,
                    owner_read_through_sequence=held.owner_read_through_sequence,
                    queue_reason=PromptQueueReason.command_needs_its_own_turn,
                    acknowledge_owner_on_failure=held.acknowledge_owner_on_failure,
                )

        async with state.lock:
            fate = await self._record_steer_outcome(
                state,
                steer_outcome,
                attempt=attempt,
                turn_token=steer_turn_token,
                content=held.content,
                sender_label=held.sender_label,
                sender_message_id=held.sender_message_id,
                sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                sender=held.sender,
                recipient=held.recipient,
                reply_requested=held.reply_requested,
                recovery_references=held.recovery_references,
                owner_read_through_sequence=held.owner_read_through_sequence,
                automatic_notice=automatic_notice,
                acknowledge_owner_on_failure=held.acknowledge_owner_on_failure,
            )
            self._settle_held_deliveries(state, (held,))
            state.steer_settlements.discard(barrier)
            barrier.set()
        if adapter_error is not None:
            raise adapter_error
        if isinstance(fate, PromptDeliveryRefused):
            await self._drain_held_prompts(state)
        return fate

    async def discard_held_prompt(self, conversation_id: str, held_prompt_id: str) -> bool:
        """Throw away one message that is waiting, and say whether there was one to throw.

        A held message is this system's own and nothing else's: it has never been written
        to a backend, so taking it out of the queue reaches nothing and undoes nothing.
        It is still written down as discarded — the same row a kill writes — because text
        somebody handed over must never disappear without a trace.

        It is addressed by the id the server gave the held queue entry.

        Not finding it is an ordinary answer rather than an error. A held message runs the
        moment the agent frees up, so the one somebody is looking at may already have
        gone — and being told so is the truth, not a failure.
        """
        state = await self._conversation_state(conversation_id)
        if state is None:
            return False
        state.last_touched_monotonic = self._monotonic_now()

        async with state.lock:
            for position, held in enumerate(state.held_prompts):
                if held.held_prompt_id != held_prompt_id:
                    continue
                if held.delivery_uncertain:
                    return False
                # Out of the queue before its row is written, so a write that falls over
                # leaves the message discarded rather than delivered.
                del state.held_prompts[position]
                self._publish_held_prompts_changed(state)
                await self._append_event(
                    state,
                    PromptDiscardedEventPayload(
                        recovery_references=held.recovery_references,
                        content=held.content,
                        sender_label=held.sender_label,
                        sender_message_id=held.sender_message_id,
                        sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                        sender=held.sender,
                        recipient=held.recipient,
                    ),
                )
                return True
        return False

    # --- answering a permission ask -----------------------------------------------------

    async def answer_permission_ask(
        self, conversation_id: str, ask_id: str, option_id: str
    ) -> bool:
        state = await self._conversation_state(conversation_id)
        if state is None:
            return False
        return await self._own_settlement(
            state, self._answer_permission_ask(conversation_id, ask_id, option_id)
        )

    async def _answer_permission_ask(
        self, conversation_id: str, ask_id: str, option_id: str
    ) -> bool:
        """Give the backend the option a person chose, and say whether it landed.

        It lands only if the ask is still pending on the turn that is live now *and* the
        backend took the answer. Answering an unknown ask, an already-answered ask, or an
        ask whose turn has ended changes nothing and records nothing. An answer the backend
        would not take leaves the ask waiting, so it can be given again.
        """
        state = await self._conversation_state(conversation_id)
        if state is None:
            return False
        state.last_touched_monotonic = self._monotonic_now()

        async with state.lock:
            running = state.running_turn
            child = state.child
            if running is None or child is None:
                return False
            if ask_id not in running.pending_permission_ask_ids:
                return False
            # Taken out of the pending set before the wire write, so a second answer
            # arriving at the same time finds nothing to answer.
            provider_id = running.permission_provider_ids[ask_id]
            request_fact = LifecycleFact(
                LifecycleKind.request,
                ask_id,
                running.token.turn_number,
                LifecycleOperation.permission,
                LifecycleStatus.answering,
                provider_id,
            )
            attempt = await self._begin_attempt(
                state,
                running.token,
                LifecycleOperation.permission_answer,
                provider_id,
                (request_fact,),
                runtime_mutation=self._runtime_mutation(
                    state,
                    PlainFieldMutation(
                        running,
                        "pending_permission_ask_ids",
                        running.pending_permission_ask_ids - {ask_id},
                    ),
                ),
            )
            if self._signal is None:
                running.pending_permission_ask_ids.discard(ask_id)
            answered_turn_token = running.token

        reached_the_backend = True
        try:
            await child.answer_permission_ask(provider_id, option_id)
        except PermissionAnswerWriteFailed:
            reached_the_backend = False

        async with state.lock:
            running = state.running_turn
            if running is None or running.token != answered_turn_token:
                # The turn ended while the answer was on its way, so the ask died with it.
                await self._persist(
                    state,
                    partial(
                        self._store.transition_lifecycle,
                        conversation_id,
                        (
                            replace(
                                attempt,
                                status=(
                                    LifecycleStatus.accepted
                                    if reached_the_backend
                                    else LifecycleStatus.refused
                                ),
                            ),
                        ),
                    ),
                )
                return False
            if not reached_the_backend:
                # The backend never got it, so the ask is still waiting to be answered.
                await self._persist(
                    state,
                    partial(
                        self._store.transition_lifecycle,
                        conversation_id,
                        (
                            replace(attempt, status=LifecycleStatus.refused),
                            replace(request_fact, status=LifecycleStatus.pending),
                        ),
                    ),
                    runtime_mutation=self._runtime_mutation(
                        state,
                        PlainFieldMutation(
                            running,
                            "pending_permission_ask_ids",
                            running.pending_permission_ask_ids | {ask_id},
                        ),
                    ),
                )
                if self._signal is None:
                    running.pending_permission_ask_ids.add(ask_id)
                return False
            await self._append_event(
                state,
                PermissionAnsweredEventPayload(ask_id=ask_id, option_id=option_id),
                lifecycle=(
                    replace(attempt, status=LifecycleStatus.accepted),
                    replace(request_fact, status=LifecycleStatus.answered),
                ),
            )
            return True

    async def answer_user_input(
        self, conversation_id: str, request_id: str, answers: tuple[UserInputAnswer, ...]
    ) -> bool:
        state = await self._conversation_state(conversation_id)
        if state is None:
            return False
        return await self._own_settlement(
            state, self._answer_user_input(conversation_id, request_id, answers)
        )

    async def _answer_user_input(
        self,
        conversation_id: str,
        request_id: str,
        answers: tuple[UserInputAnswer, ...],
    ) -> bool:
        """Deliver one complete answer map and record it only after the backend takes it."""
        state = await self._conversation_state(conversation_id)
        if state is None:
            return False
        state.last_touched_monotonic = self._monotonic_now()

        async with state.lock:
            running = state.running_turn
            child = state.child
            if running is None or child is None:
                return False
            questions = running.pending_user_input_questions.get(request_id)
            if questions is None:
                return False
            from planner.conversation.logic.user_input import ordered_user_input_answers

            ordered_answers = ordered_user_input_answers(questions, answers)
            if ordered_answers is None:
                return False
            provider_id = running.user_input_provider_ids[request_id]
            request_fact = LifecycleFact(
                LifecycleKind.request,
                request_id,
                running.token.turn_number,
                LifecycleOperation.user_input,
                LifecycleStatus.answering,
                provider_id,
            )
            attempt = await self._begin_attempt(
                state,
                running.token,
                LifecycleOperation.user_input_answer,
                provider_id,
                (request_fact,),
                runtime_mutation=self._runtime_mutation(
                    state,
                    PlainFieldMutation(
                        running,
                        "pending_user_input_questions",
                        {
                            key: value
                            for key, value in running.pending_user_input_questions.items()
                            if key != request_id
                        },
                    ),
                ),
            )
            if self._signal is None:
                del running.pending_user_input_questions[request_id]
            answered_turn_token = running.token

        reached_the_backend = True
        try:
            await child.answer_user_input(provider_id, ordered_answers)
        except UserInputAnswerWriteFailed:
            reached_the_backend = False

        async with state.lock:
            running = state.running_turn
            if running is None or running.token != answered_turn_token:
                await self._persist(
                    state,
                    partial(
                        self._store.transition_lifecycle,
                        conversation_id,
                        (
                            replace(
                                attempt,
                                status=(
                                    LifecycleStatus.accepted
                                    if reached_the_backend
                                    else LifecycleStatus.refused
                                ),
                            ),
                        ),
                    ),
                )
                return False
            if not reached_the_backend:
                await self._persist(
                    state,
                    partial(
                        self._store.transition_lifecycle,
                        conversation_id,
                        (
                            replace(attempt, status=LifecycleStatus.refused),
                            replace(request_fact, status=LifecycleStatus.pending),
                        ),
                    ),
                    runtime_mutation=self._runtime_mutation(
                        state,
                        PlainFieldMutation(
                            running,
                            "pending_user_input_questions",
                            {**running.pending_user_input_questions, request_id: questions},
                        ),
                    ),
                )
                if self._signal is None:
                    running.pending_user_input_questions[request_id] = questions
                return False
            await self._append_event(
                state,
                UserInputAnsweredEventPayload(request_id=request_id, answers=ordered_answers),
                lifecycle=(
                    replace(attempt, status=LifecycleStatus.accepted),
                    replace(request_fact, status=LifecycleStatus.answered),
                ),
            )
            return True

    # --- running the system -------------------------------------------------------------

    async def start_idle_child_janitor(self) -> None:
        """Begin stopping children that have sat idle long enough to have been forgotten."""
        self._require_runtime_ownership()
        if self._idle_child_janitor is None:
            self._idle_child_janitor = asyncio.create_task(self._sweep_idle_children_forever())

    async def shutdown(self) -> None:
        """Own complete cleanup, retaining the database lease if the caller leaves."""
        if self._signal is not None:
            self._signal.invalidate("shutdown")
        self._shutting_down = True
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown_owned())
        await asyncio.shield(self._shutdown_task)

    async def _shutdown_owned(self) -> None:
        """Stop every local writer before releasing exclusive reconciliation ownership."""
        try:
            janitor = self._idle_child_janitor
            self._idle_child_janitor = None
            if janitor is not None:
                janitor.cancel()
                await asyncio.gather(janitor, return_exceptions=True)
            async with self._conversations_lock:
                states = list(self._conversations.values())
            # Stop all event producers and settlements before a slow child stop can
            # delay teardown. These tasks can no longer write ahead of a successor.
            tasks: list[asyncio.Task[Any]] = []
            for state in states:
                tasks.extend(state.settlement_tasks)
                pump = state.backend_event_pump
                state.backend_event_pump = None
                if pump is not None:
                    tasks.append(pump)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for state in states:
                child = state.child
                state.child = None
                from planner.executor.remote import RemoteBackendChild

                if child is not None and not isinstance(child, RemoteBackendChild):
                    try:
                        if not await self._stop_child(state, child):
                            state.child = child
                            state.child_is_quarantined = True
                    except asyncio.CancelledError:
                        state.child = child
                        state.child_is_quarantined = True
                        # An adapter can abort its own stop. No local callback or writer
                        # survives this cleanup, but provider termination is unproven.
                        LOGGER.error(
                            "conversation %s backend stop was cancelled; "
                            "provider termination remains unknown",
                            state.record.conversation_id,
                        )
        finally:
            try:
                await self._store.finish_writes()
            finally:
                self._runtime_ownership.close()

    async def wait_until_quiescent(self) -> None:
        """Return once everything the system was going to do on its own has happened.

        Backend news is worked through on a queue per conversation, and a turn ending on
        that queue can set off a whole drain of held messages. This waits for all of it,
        which is what lets a test assert consequences instead of sleeping for them.
        """
        while True:
            async with self._conversations_lock:
                states = list(self._conversations.values())
            for state in states:
                await state.backend_event_queue.join()
                for task in tuple(state.settlement_tasks):
                    if task is not asyncio.current_task():
                        await asyncio.shield(task)
            await asyncio.sleep(0)
            if all(state.backend_event_queue.empty() for state in states):
                return

    # --- the three modes ----------------------------------------------------------------

    async def _queue(
        self,
        state: _ConversationState,
        content: MessageContent,
        sender_label: str,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
        sender: Principal | None,
        recipient: Principal | None,
        reply_requested: bool,
        recovery_references: tuple[DeploymentRecoveryReference, ...],
        owner_read_through_sequence: int | None,
        acknowledge_owner_on_failure: bool = True,
        *,
        queue_reason: PromptQueueReason = PromptQueueReason.requested,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> PromptDeliveryFate:
        automatic_reservation: _ReservedTurn | None = None
        async with state.lock:
            if sender_message_id is not None:
                for position, held in enumerate(state.held_prompts, start=1):
                    if held.sender_message_id != sender_message_id:
                        continue
                    if (
                        held.content != content
                        or held.sender_label != sender_label
                        or held.model_change != model_change
                        or held.reasoning_effort_change != reasoning_effort_change
                        or held.reasoning_effort_is_set != reasoning_effort_is_set
                        or held.selection_kind != selection_kind
                        or held.sender != sender
                        or held.recipient != recipient
                        or held.recovery_references != recovery_references
                    ):
                        raise ValueError("sender_message_id already names a different message")
                    return PromptDeliveryQueued(queue_position=position)
            # Held if anything at all is going on, and held if anything is already
            # waiting: a message that arrived later never runs earlier.
            if (
                state.child_is_quarantined
                or state.phase is not _ConversationPhase.idle
                or state.held_prompts
            ):
                return self._hold_prompt(
                    state,
                    content=content,
                    sender_label=sender_label,
                    model_change=model_change,
                    reasoning_effort_change=reasoning_effort_change,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    sender=sender,
                    recipient=recipient,
                    reply_requested=reply_requested,
                    recovery_references=recovery_references,
                    queue_reason=queue_reason,
                    owner_read_through_sequence=owner_read_through_sequence,
                    acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                    selection_kind=selection_kind,
                )
            if self._automatic_compaction_is_due(state):
                held_fate = self._hold_prompt(
                    state,
                    content=content,
                    sender_label=sender_label,
                    model_change=model_change,
                    reasoning_effort_change=reasoning_effort_change,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    sender=sender,
                    recipient=recipient,
                    reply_requested=reply_requested,
                    recovery_references=recovery_references,
                    queue_reason=queue_reason,
                    owner_read_through_sequence=owner_read_through_sequence,
                    acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                    selection_kind=selection_kind,
                )
                automatic_reservation = await self._reserve_turn(state, automatic_compaction=True)
            else:
                reservation = await self._reserve_turn(state)

        if automatic_reservation is not None:
            await self._deliver_automatic_compaction(state, automatic_reservation)
            return held_fate

        return await self._deliver_and_finalize(
            state,
            reservation,
            content=content,
            sender_label=sender_label,
            mode=PromptDeliveryMode.queue,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
            recovery_references=recovery_references,
            owner_read_through_sequence=owner_read_through_sequence,
            acknowledge_owner_on_failure=acknowledge_owner_on_failure,
            reasoning_effort_is_set=reasoning_effort_is_set,
            selection_kind=selection_kind,
            native_message_operation=native_message_operation,
        )

    def _hold_prompt(
        self,
        state: _ConversationState,
        *,
        content: MessageContent,
        sender_label: str,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
        sender: Principal | None,
        recipient: Principal | None,
        reply_requested: bool,
        recovery_references: tuple[DeploymentRecoveryReference, ...],
        owner_read_through_sequence: int | None,
        acknowledge_owner_on_failure: bool = True,
        queue_reason: PromptQueueReason = PromptQueueReason.requested,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
    ) -> PromptDeliveryQueued:
        """Put one admitted message at the tail. The conversation lock must be held."""
        from planner.core.db import connect
        from planner.core.errors import ErrorCode
        from planner.tickets.validation.data import read_launch_selection

        original_selection = None
        conn = connect(self._store._db_path)
        try:
            row = conn.execute(
                "SELECT id,locale FROM tickets WHERE conversation_id=?",
                (state.record.conversation_id,),
            ).fetchone()
            captured = None
            if row is not None:
                try:
                    captured = read_launch_selection(conn, row[0])
                except PlannerError as error:
                    if error.code not in (ErrorCode.validation, ErrorCode.not_found):
                        raise
            if captured is None and state.held_prompts:
                # Generic conversations have no separately saved next-turn choice.
                # Their last explicit queued choice is the deliberate carried intent.
                captured = state.held_prompts[-1].original_selection
            if captured is None and state.record.model:
                from planner.tickets.contracts import TicketLocale

                captured = ModelSelection(
                    TicketLocale(row[1])
                    if row is not None
                    else TicketLocale.macos
                    if state.executor_child_reference is not None
                    else TicketLocale.vps,
                    state.record.backend_key,
                    state.record.model,
                    SelectionKind.legacy_unclassified,
                    state.record.reasoning_effort,
                    f"conversation:{state.record.conversation_id}",
                )
            if captured is not None:
                original_selection = replace(
                    captured,
                    submitted_token=model_change or captured.submitted_token,
                    selection_kind=selection_kind
                    or (
                        captured.selection_kind
                        if model_change is None or model_change == captured.submitted_token
                        else SelectionKind.legacy_unclassified
                    ),
                    reasoning_effort=(
                        reasoning_effort_change
                        if reasoning_effort_is_set or reasoning_effort_change is not None
                        else captured.reasoning_effort
                    ),
                )
        finally:
            conn.close()
        held = _HeldPrompt(
            original_selection=original_selection,
            reasoning_effort_is_set=reasoning_effort_is_set,
            selection_kind=selection_kind,
            held_prompt_id=f"held_{uuid4().hex}",
            content=content,
            sender_label=sender_label,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            snapshot_sent_at_unix_milliseconds=(
                sent_at_unix_milliseconds
                if sent_at_unix_milliseconds is not None
                else int(self._unix_time_now() * 1000)
            ),
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
            recovery_references=recovery_references,
            queue_reason=queue_reason,
            owner_read_through_sequence=owner_read_through_sequence,
            acknowledge_owner_on_failure=acknowledge_owner_on_failure,
        )
        state.held_prompts.append(held)
        self._publish_held_prompts_changed(state)
        return PromptDeliveryQueued(queue_position=len(state.held_prompts))

    def _begin_held_deliveries(
        self, state: _ConversationState, held_prompts: tuple[_HeldPrompt, ...]
    ) -> None:
        """Keep sender identities visible after dequeue and before their durable outcome."""
        for held in held_prompts:
            sender_message_id = held.sender_message_id
            if sender_message_id is None:
                continue
            existing = state.sender_messages_being_delivered.get(sender_message_id)
            if existing is not None:
                raise RuntimeError("a held sender message is already being delivered")
            state.sender_messages_being_delivered[sender_message_id] = _AdmittedSenderMessage(
                content=held.content,
                sender_label=held.sender_label,
                sender=held.sender,
                recipient=held.recipient,
            )

    @staticmethod
    def _settle_held_deliveries(
        state: _ConversationState, held_prompts: tuple[_HeldPrompt, ...]
    ) -> None:
        """Release duplicate callers after each dequeued message has a durable outcome."""
        for held in held_prompts:
            sender_message_id = held.sender_message_id
            if sender_message_id is None:
                continue
            admitted = state.sender_messages_being_delivered.pop(sender_message_id, None)
            if admitted is not None:
                admitted.settled.set()

    def _automatic_compaction_is_due(self, state: _ConversationState) -> bool:
        activity_at = state.record.latest_agent_activity_at
        activity_age = None if activity_at is None else int(self._unix_time_now()) - activity_at
        return (
            not state.child_is_quarantined
            and activity_age is not None
            and AUTOMATIC_COMPACTION_AFTER_SECONDS
            <= activity_age
            < AUTOMATIC_COMPACTION_BEFORE_SECONDS
            and state.record.latest_agent_activity_sequence
            > state.record.automatic_compaction_attempted_through_sequence
        )

    async def _send_now(
        self,
        state: _ConversationState,
        content: MessageContent,
        sender_label: str,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
        sender: Principal | None,
        recipient: Principal | None,
        reply_requested: bool,
        recovery_references: tuple[DeploymentRecoveryReference, ...],
        owner_read_through_sequence: int | None,
        acknowledge_owner_on_failure: bool = True,
        *,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> PromptDeliveryFate:
        automatic_reservation: _ReservedTurn | None = None
        held_fate: PromptDeliveryQueued | None = None
        await self._acquire_settled(state)
        try:
            try:
                running = state.running_turn
                if state.child_is_quarantined or any(
                    held.delivery_uncertain for held in state.held_prompts
                ):
                    return self._hold_prompt(
                        state,
                        content=content,
                        sender_label=sender_label,
                        model_change=model_change,
                        reasoning_effort_change=reasoning_effort_change,
                        sender_message_id=sender_message_id,
                        sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                        sender=sender,
                        recipient=recipient,
                        reply_requested=reply_requested,
                        recovery_references=recovery_references,
                        owner_read_through_sequence=owner_read_through_sequence,
                        acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                        reasoning_effort_is_set=reasoning_effort_is_set,
                        selection_kind=selection_kind,
                    )
                if running is not None and running.automatic_compaction:
                    return self._hold_prompt(
                        state,
                        content=content,
                        sender_label=sender_label,
                        model_change=model_change,
                        reasoning_effort_change=reasoning_effort_change,
                        sender_message_id=sender_message_id,
                        sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                        sender=sender,
                        recipient=recipient,
                        reply_requested=reply_requested,
                        recovery_references=recovery_references,
                        owner_read_through_sequence=owner_read_through_sequence,
                        acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                        reasoning_effort_is_set=reasoning_effort_is_set,
                        selection_kind=selection_kind,
                    )
                if running is not None:
                    try:
                        await self._cancel_and_end_running_turn(state, running)
                    except BaseException:
                        # The incumbent's ending fell over, so this send never happens.
                        # The conversation goes back to whichever phase is now true rather
                        # than staying in an ending nobody is finishing.
                        self._settle_phase(state)
                        raise
                if self._automatic_compaction_is_due(state):
                    held_fate = self._hold_prompt(
                        state,
                        content=content,
                        sender_label=sender_label,
                        model_change=model_change,
                        reasoning_effort_change=reasoning_effort_change,
                        sender_message_id=sender_message_id,
                        sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                        sender=sender,
                        recipient=recipient,
                        reply_requested=reply_requested,
                        recovery_references=recovery_references,
                        owner_read_through_sequence=owner_read_through_sequence,
                        acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                        reasoning_effort_is_set=reasoning_effort_is_set,
                        selection_kind=selection_kind,
                    )
                    automatic_reservation = await self._reserve_turn(
                        state, automatic_compaction=True
                    )
                else:
                    reservation = await self._reserve_turn(state)
            finally:
                state.lock.release()
        except asyncio.CancelledError:
            raise
        except BaseException:
            # The lock is let go above, so the line can be emptied here. The conversation
            # is settled and free, and whatever was waiting is still owed its run — before
            # this it was left behind with nothing coming back for it.
            await self._drain_held_prompts(state)
            raise

        if automatic_reservation is not None:
            await self._deliver_automatic_compaction(state, automatic_reservation)
            assert held_fate is not None
            return held_fate

        return await self._deliver_and_finalize(
            state,
            reservation,
            content=content,
            sender_label=sender_label,
            mode=PromptDeliveryMode.send_now,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
            recovery_references=recovery_references,
            owner_read_through_sequence=owner_read_through_sequence,
            acknowledge_owner_on_failure=acknowledge_owner_on_failure,
            reasoning_effort_is_set=reasoning_effort_is_set,
            selection_kind=selection_kind,
            native_message_operation=native_message_operation,
        )

    async def _steer(
        self,
        state: _ConversationState,
        content: MessageContent,
        sender_label: str,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
        sender: Principal | None,
        recipient: Principal | None,
        reply_requested: bool,
        recovery_references: tuple[DeploymentRecoveryReference, ...],
        owner_read_through_sequence: int | None,
        acknowledge_owner_on_failure: bool = True,
        *,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> PromptDeliveryFate:
        """Steer active work, or apply the shared idle and fallback rules."""
        reservation: _ReservedTurn | None = None
        queued: PromptDeliveryQueued | None = None
        child: BackendChild | None = None
        running: _RunningTurn | None = None
        await self._acquire_settled(state)
        try:
            running = state.running_turn
            child = state.child
            if (
                not state.child_is_quarantined
                and state.phase is _ConversationPhase.idle
                and not state.held_prompts
            ):
                reservation = await self._reserve_turn(state)
            elif any(not isinstance(piece, MessageText) for piece in content):
                queued = self._hold_prompt(
                    state,
                    content=content,
                    sender_label=sender_label,
                    model_change=model_change,
                    reasoning_effort_change=reasoning_effort_change,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    sender=sender,
                    recipient=recipient,
                    reply_requested=reply_requested,
                    recovery_references=recovery_references,
                    queue_reason=PromptQueueReason.attachment,
                    owner_read_through_sequence=owner_read_through_sequence,
                    acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                    selection_kind=selection_kind,
                )
            elif (
                model_change is not None
                or reasoning_effort_change is not None
                or reasoning_effort_is_set
            ):
                queued = self._hold_prompt(
                    state,
                    content=content,
                    sender_label=sender_label,
                    model_change=model_change,
                    reasoning_effort_change=reasoning_effort_change,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    sender=sender,
                    recipient=recipient,
                    reply_requested=reply_requested,
                    recovery_references=recovery_references,
                    queue_reason=PromptQueueReason.run_change,
                    owner_read_through_sequence=owner_read_through_sequence,
                    acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                    selection_kind=selection_kind,
                )
            elif (
                state.child_is_quarantined
                or any(held.delivery_uncertain for held in state.held_prompts)
                or running is None
                or child is None
                or not backend_supports_steer(state.record.backend_key)
            ):
                queued = self._hold_prompt(
                    state,
                    content=content,
                    sender_label=sender_label,
                    model_change=model_change,
                    reasoning_effort_change=reasoning_effort_change,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    sender=sender,
                    recipient=recipient,
                    reply_requested=reply_requested,
                    recovery_references=recovery_references,
                    queue_reason=PromptQueueReason.steer_refused,
                    owner_read_through_sequence=owner_read_through_sequence,
                    acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                    selection_kind=selection_kind,
                )
            barrier: asyncio.Event | None = None
            automatic_notice = False
            if reservation is None and queued is None:
                automatic_notice = await self._persist(
                    state,
                    partial(
                        self._store.all_sender_messages_are_manager_notices,
                        state.record.conversation_id,
                        (sender_message_id,),
                    ),
                )
                barrier = asyncio.Event()
                state.steer_settlements.add(barrier)
        finally:
            state.lock.release()

        if reservation is not None:
            # Steer names how to address active work. With no turn to join, this message
            # opens an ordinary turn, so its durable mode and backend metadata are queue.
            return await self._deliver_and_finalize(
                state,
                reservation,
                content=content,
                sender_label=sender_label,
                mode=PromptDeliveryMode.queue,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
                reply_requested=reply_requested,
                recovery_references=recovery_references,
                owner_read_through_sequence=owner_read_through_sequence,
                acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                reasoning_effort_is_set=reasoning_effort_is_set,
                selection_kind=selection_kind,
                native_message_operation=native_message_operation,
            )
        if queued is not None:
            return queued
        assert child is not None
        assert running is not None
        attempt = await self._begin_attempt(state, running.token, LifecycleOperation.steer)
        messages, suppression_reason, automatic_notice = await self._admit_dispatch_messages(
            state,
            (
                _HeldPrompt(
                    reasoning_effort_is_set=reasoning_effort_is_set,
                    selection_kind=selection_kind,
                    held_prompt_id="",
                    content=content,
                    sender_label=sender_label,
                    model_change=model_change,
                    reasoning_effort_change=reasoning_effort_change,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    snapshot_sent_at_unix_milliseconds=0,
                    sender=sender,
                    recipient=recipient,
                    reply_requested=reply_requested,
                    recovery_references=recovery_references,
                    queue_reason=PromptQueueReason.steer_refused,
                    owner_read_through_sequence=owner_read_through_sequence,
                    acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                ),
            ),
            lifecycle_if_all_suppressed=(replace(attempt, status=LifecycleStatus.suppressed),),
        )
        if not messages:
            assert barrier is not None
            state.steer_settlements.discard(barrier)
            barrier.set()
            assert suppression_reason is not None
            return PromptDeliverySuppressed(suppression_reason)
        adapter_error: Exception | None = None
        try:
            wire_content = with_authenticated_reply_directive(
                self._with_native_worker_context(state, child, content),
                (
                    (sender,)
                    if reply_requested and sender is not None and recipient is not None
                    else ()
                ),
            )
            steer_outcome = await self._steer_with_admission(
                state,
                child,
                running.token,
                wire_content,
                sender_content=content,
                sender_label=sender_label,
                attempt_id=attempt.identity,
            )
        except AdmissionRefused:
            steer_outcome = BackendSteerRefused(PromptDeliveryRefusalReason.model_resolution_failed)
        except PromptWriteRefused:
            steer_outcome = BackendSteerRefused(
                PromptDeliveryRefusalReason.write_to_backend_failed
            )
        except PromptWriteFailed:
            steer_outcome = BackendSteerUncertain()
        except asyncio.CancelledError:
            assert barrier is not None
            state.steer_settlements.discard(barrier)
            barrier.set()
            raise
        except Exception as error:
            # An unexpected adapter fault proves neither acceptance nor refusal.
            # Settle an unknown fate before propagating it, so this identity cannot
            # cross the backend boundary again or disappear from a held batch.
            adapter_error = error
            steer_outcome = BackendSteerUncertain()

        if isinstance(steer_outcome, BackendSteerRefused):
            await self._persist(
                state,
                partial(
                    self._store.transition_lifecycle,
                    state.record.conversation_id,
                    (replace(attempt, status=LifecycleStatus.refused),),
                ),
            )
            assert barrier is not None
            state.steer_settlements.discard(barrier)
            barrier.set()
            # The target may have ended while the backend decided. Re-enter the shared
            # queue path so an idle conversation starts this message and a replacement
            # turn holds it. Merely appending here can strand it after the last drain.
            return await self._queue(
                state,
                content,
                sender_label,
                model_change,
                reasoning_effort_change,
                sender_message_id,
                sent_at_unix_milliseconds,
                sender,
                recipient,
                reply_requested,
                recovery_references,
                owner_read_through_sequence,
                queue_reason=_queue_reason_for_refused_steer(steer_outcome.refusal_reason),
                acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                reasoning_effort_is_set=reasoning_effort_is_set,
                selection_kind=selection_kind,
                native_message_operation=native_message_operation,
            )

        async with state.lock:
            fate = await self._record_steer_outcome(
                state,
                steer_outcome,
                attempt=attempt,
                turn_token=running.token,
                content=content,
                sender_label=sender_label,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
                reply_requested=reply_requested,
                recovery_references=recovery_references,
                owner_read_through_sequence=owner_read_through_sequence,
                automatic_notice=automatic_notice,
                acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                native_message_operation=native_message_operation,
            )

            assert barrier is not None
            state.steer_settlements.discard(barrier)
            barrier.set()
            if adapter_error is not None:
                raise adapter_error
            return fate

    # --- delivering ---------------------------------------------------------------------

    async def _deliver_automatic_compaction(
        self, state: _ConversationState, reservation: _ReservedTurn
    ) -> None:
        """Start the maintenance turn and always release a message held behind failure."""
        try:
            await self._deliver_and_finalize(
                state,
                reservation,
                content=text_message_content(AUTOMATIC_COMPACTION_PROMPT),
                sender_label=AUTOMATIC_COMPACTION_SENDER_LABEL,
                mode=PromptDeliveryMode.queue,
                model_change=None,
                reasoning_effort_change=None,
                sender_message_id=None,
                sent_at_unix_milliseconds=None,
                drain_after_delivery_exception=False,
                acknowledge_owner_on_failure=True,
            )
        except asyncio.CancelledError:
            raise
        except BaseException:
            LOGGER.exception(
                "conversation %s could not start automatic compaction",
                state.record.conversation_id,
            )
            termination_confirmed = True
            async with state.lock:
                child = state.child
                if child is not None:
                    from planner.executor.remote import RemoteBackendChild

                    if isinstance(child, RemoteBackendChild):
                        self._schedule_remote_retirement(state, child)
                        termination_confirmed = False
                    else:
                        state.child = None
                        termination_confirmed = await self._stop_child(state, child)
                    if not termination_confirmed:
                        # Keep the uncertain process attached so no replacement can run
                        # beside it. The held line stays intact until an operator or a
                        # later successful stop establishes that release is safe.
                        state.child = child
                        state.child_is_quarantined = True
                    else:
                        state.child_is_quarantined = False
            if termination_confirmed:
                await self._drain_held_prompts(state)
            else:
                LOGGER.error(
                    "conversation %s kept held traffic quarantined after automatic "
                    "compaction child termination failed",
                    state.record.conversation_id,
                )
        else:
            # A refused delivery already drains inside the normal delivery path. This
            # second call is a no-op then, and closes any exceptional path that settled
            # the reservation without starting a turn.
            await self._drain_held_prompts(state)

    async def _record_delivery_uncertainty(
        self,
        state: _ConversationState,
        reservation: _ReservedTurn,
        messages: tuple[_HeldPrompt, ...],
    ) -> None:
        """Keep the reservation and child owned while recording possible emission."""
        async with state.lock:
            state.child_is_quarantined = True
            state.executor_delivery_uncertain = True
            pending = _RunningTurn(
                token=reservation.token,
                automatic_compaction=reservation.automatic_compaction,
                automatic_notice=reservation.automatic_notice,
            )
            metadata = self._executor_turn_metadata(state, pending)
            for message in messages:
                await self._append_event(
                    state,
                    PromptDeliveryUncertainEventPayload(
                        content=message.content,
                        sender_label=message.sender_label,
                        mode=reservation.mode,
                        sender_message_id=message.sender_message_id,
                        sent_at_unix_milliseconds=message.sent_at_unix_milliseconds,
                        sender=message.sender,
                        recipient=message.recipient,
                        recovery_references=message.recovery_references,
                    ),
                    lifecycle=(_prompt_fact(reservation.token, LifecycleStatus.unknown),),
                    executor_turn=metadata,
                )
            self._settle_held_deliveries(state, messages)
            present = {held.held_prompt_id for held in state.held_prompts}
            for message in reversed(messages):
                if message.held_prompt_id and message.held_prompt_id not in present:
                    state.held_prompts.appendleft(replace(message, delivery_uncertain=True))
            self._set_phase(state, _ConversationPhase.idle)
            self._publish_held_prompts_changed(state)

    async def _deliver_and_finalize(
        self,
        state: _ConversationState,
        reservation: _ReservedTurn,
        *,
        content: MessageContent,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
        recovery_references: tuple[DeploymentRecoveryReference, ...] = (),
        drain_after_delivery_exception: bool = True,
        owner_read_through_sequence: int | None = None,
        acknowledge_owner_on_failure: bool = True,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> PromptDeliveryFate:
        reservation.messages = (
            _HeldPrompt(
                reasoning_effort_is_set=reasoning_effort_is_set,
                selection_kind=selection_kind,
                held_prompt_id="",
                content=content,
                sender_label=sender_label,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                snapshot_sent_at_unix_milliseconds=0,
                sender=sender,
                recipient=recipient,
                reply_requested=reply_requested,
                recovery_references=recovery_references,
                queue_reason=PromptQueueReason.steer_refused,
                owner_read_through_sequence=owner_read_through_sequence,
                acknowledge_owner_on_failure=acknowledge_owner_on_failure,
            ),
        )
        try:
            reservation.automatic_notice = await self._persist(
                state,
                partial(
                    self._store.all_sender_messages_are_manager_notices,
                    state.record.conversation_id,
                    (sender_message_id,),
                ),
            )
            delivery = await self._deliver_prompt(
                state,
                reservation.token,
                content=content,
                sender_label=sender_label,
                sender_message_count=1,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
                automatic_compaction=reservation.automatic_compaction,
                messages=(
                    _HeldPrompt(
                        reasoning_effort_is_set=reasoning_effort_is_set,
                        selection_kind=selection_kind,
                        held_prompt_id="",
                        content=content,
                        sender_label=sender_label,
                        model_change=model_change,
                        reasoning_effort_change=reasoning_effort_change,
                        sender_message_id=sender_message_id,
                        sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                        snapshot_sent_at_unix_milliseconds=0,
                        sender=sender,
                        recipient=recipient,
                        reply_requested=reply_requested,
                        recovery_references=recovery_references,
                        queue_reason=PromptQueueReason.steer_refused,
                        owner_read_through_sequence=owner_read_through_sequence,
                        acknowledge_owner_on_failure=acknowledge_owner_on_failure,
                    ),
                ),
                reply_senders=(
                    (sender,)
                    if reply_requested and sender is not None and recipient is not None
                    else ()
                ),
                native_message_operation=native_message_operation,
            )
        except asyncio.CancelledError:
            if reservation.shutdown_owned:
                raise
            self._abandon_reserved_turn(state, reservation, _ConversationPhase.idle)
            raise
        except Exception as delivery_error:
            from planner.executor.remote import RemoteCommandUncertain

            if reservation.shutdown_owned:
                await reservation.resolved.wait()
                assert reservation.shutdown_fate is not None
                return reservation.shutdown_fate
            if isinstance(delivery_error, (RemoteCommandUncertain, PromptWriteFailed)):
                async with state.lock:
                    state.child_is_quarantined = True
                    state.executor_delivery_uncertain = True
                    pending = _RunningTurn(
                        token=reservation.token,
                        automatic_compaction=reservation.automatic_compaction,
                        automatic_notice=reservation.automatic_notice,
                    )
                    metadata = self._executor_turn_metadata(state, pending)
                    if metadata is not None:
                        runtime = json.loads(metadata[2])
                        runtime["delivery_uncertain"] = True
                        metadata = (metadata[0], metadata[1], json.dumps(runtime))
                    await self._append_event(
                        state,
                        PromptDeliveryUncertainEventPayload(
                            content=content,
                            sender_label=sender_label,
                            mode=mode,
                            sender_message_id=sender_message_id,
                            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                            sender=sender,
                            recipient=recipient,
                        ),
                        lifecycle=(_prompt_fact(reservation.token, LifecycleStatus.unknown),),
                        executor_turn=metadata,
                        native_message_operation=native_message_operation,
                    )
                self._set_phase(state, _ConversationPhase.idle)
                # Preserve the unresolved reservation and quarantine; record the
                # unknown delivery without inventing refusal or native execution.
                return PromptDeliveryUncertain()
            self._abandon_reserved_turn(state, reservation, _ConversationPhase.idle)
            if drain_after_delivery_exception:
                await self._drain_held_prompts(state)
            raise
        except BaseException:
            # The text reached no backend and this send is over, so the conversation is
            # free and whatever was waiting is owed its run. Without this the line was
            # left behind with the conversation idle and no drain coming back for it.
            self._abandon_reserved_turn(state, reservation, _ConversationPhase.idle)
            if drain_after_delivery_exception:
                await self._drain_held_prompts(state)
            raise

        if reservation.shutdown_owned:
            await reservation.resolved.wait()
            assert reservation.shutdown_fate is not None
            return reservation.shutdown_fate
        if delivery.suppression_reason is not None:
            self._abandon_reserved_turn(state, reservation, _ConversationPhase.idle)
            await self._drain_held_prompts(state)
            return PromptDeliverySuppressed(delivery.suppression_reason)
        reservation.automatic_notice = delivery.automatic_notice
        if reservation.shutdown_owned:
            await reservation.resolved.wait()
            assert reservation.shutdown_fate is not None
            return reservation.shutdown_fate
        started = await self._finalize_delivery(
            state,
            reservation,
            delivery.refusal_reason,
            content=content,
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
            recovery_references=recovery_references,
            canonical_validation=delivery.canonical_validation,
            original_selection=delivery.original_selection,
            refusal_detail=delivery.refusal_detail,
            composed_content_delivered=delivery.composed_content_delivered,
            provider_submission=delivery.provider_submission,
            record_refusal=delivery.record_refusal,
            phase_when_not_started=_ConversationPhase.idle,
            owner_read_through_sequence=owner_read_through_sequence,
            acknowledge_owner_on_failure=acknowledge_owner_on_failure,
            reasoning_effort_is_set=reasoning_effort_is_set,
            selection_kind=selection_kind,
            native_message_operation=native_message_operation,
        )
        if started:
            return PromptDeliveryStarted()
        if reservation.shutdown_owned:
            await reservation.resolved.wait()
            assert reservation.shutdown_fate is not None
            return reservation.shutdown_fate
        refusal = delivery.refusal_reason
        assert refusal is not None
        # The agent is free and this message is not going anywhere, so whatever was waiting
        # for it is owed its run. A send-now has already killed the incumbent to get here.
        await self._drain_held_prompts(state)
        return PromptDeliveryRefused(
            refusal_reason=refusal,
            model_validation=(
                self._validation_json(delivery.canonical_validation)
                if refusal is PromptDeliveryRefusalReason.model_resolution_failed
                else None
            ),
        )

    @staticmethod
    def _reply_senders(messages: tuple[_HeldPrompt, ...]) -> tuple[Principal, ...]:
        return tuple(
            dict.fromkeys(
                message.sender
                for message in messages
                if message.reply_requested
                and message.sender is not None
                and message.recipient is not None
            )
        )

    async def _admit_dispatch_messages(
        self,
        state: _ConversationState,
        messages: tuple[_HeldPrompt, ...],
        *,
        lifecycle_if_all_suppressed: tuple[LifecycleFact, ...] = (),
    ) -> tuple[tuple[_HeldPrompt, ...], str | None, bool]:
        """Keep sender envelopes intact until the final transactional dispatch decision."""
        async with state.lock:
            decisions = await self._persist(
                state,
                partial(
                    self._store.admit_sender_messages,
                    state.record.conversation_id,
                    tuple(
                        PromptDiscardedEventPayload(
                            recovery_references=m.recovery_references,
                            content=m.content,
                            sender_label=m.sender_label,
                            sender_message_id=m.sender_message_id,
                            sent_at_unix_milliseconds=m.sent_at_unix_milliseconds,
                            sender=m.sender,
                            recipient=m.recipient,
                        )
                        for m in messages
                    ),
                    lifecycle_if_all_suppressed=lifecycle_if_all_suppressed,
                ),
            )
            written = await self._store.read_events_after(
                state.record.conversation_id, state.record.latest_sequence
            )
            self._take_in_written_rows(state, written)
        survivors = tuple(
            m for m, d in zip(messages, decisions, strict=True) if d.suppression_reason is None
        )
        reason = next((d.suppression_reason for d in decisions if d.suppression_reason), None)
        automatic_notice = bool(survivors) and all(
            d.is_manager_notice for d in decisions if d.suppression_reason is None
        )
        return survivors, reason, automatic_notice

    async def _deliver_prompt(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        native_message_operation: NativeMessageOperation | None = None,
        **kwargs: Any,
    ) -> _PromptDeliveryAttempt:
        reservation = state.reserved_turn
        if reservation is not None and reservation.token == turn_token:
            reservation.messages = kwargs.get("messages", ())
            reservation.mode = kwargs["mode"]
            if reservation.shutdown_owned:
                from planner.executor.remote import RemoteInitialDeliveryCancelled

                raise RemoteInitialDeliveryCancelled()
        try:
            if reservation is None:
                return await self._deliver_prompt_unsettled(state, turn_token, **kwargs)

            async def deliver_reserved_prompt() -> _PromptDeliveryAttempt:
                from planner.executor.remote import RemoteInitialDeliveryCancelled

                try:
                    return await self._deliver_prompt_unsettled(state, turn_token, **kwargs)
                except RemoteInitialDeliveryCancelled:
                    if not reservation.shutdown_owned:
                        raise
                    # The exact reservation already owns its durable disposition.
                    # Its old preparation can finish without failing cleanup.
                    return _PromptDeliveryAttempt(suppression_reason="conversation_killed")

            delivery = asyncio.create_task(deliver_reserved_prompt())
            state.settlement_tasks.add(delivery)

            def settled(task: asyncio.Task[_PromptDeliveryAttempt]) -> None:
                state.settlement_tasks.discard(task)
                if not task.cancelled():
                    task.exception()

            delivery.add_done_callback(settled)
            shutdown = asyncio.create_task(reservation.resolved.wait())
            try:
                await asyncio.wait((delivery, shutdown), return_when=asyncio.FIRST_COMPLETED)
                if reservation.shutdown_owned:
                    from planner.executor.remote import RemoteInitialDeliveryCancelled

                    raise RemoteInitialDeliveryCancelled()
                return await delivery
            finally:
                shutdown.cancel()
                await asyncio.gather(shutdown, return_exceptions=True)
        except BaseException:
            if reservation is not None and reservation.shutdown_owned:
                raise

            async def settle_recovery_exception() -> None:
                messages = tuple(
                    message for message in kwargs.get("messages", ()) if message.recovery_references
                )
                if not messages:
                    await self._persist(
                        state,
                        partial(
                            self._store.fail_recovery_claims,
                            state.record.conversation_id,
                            turn_token.turn_number,
                        ),
                    )
                    return
                async with state.lock:
                    for index, message in enumerate(messages):
                        await self._append_event(
                            state,
                            PromptDeliveryUncertainEventPayload(
                                content=message.content,
                                sender_label=message.sender_label,
                                mode=kwargs["mode"],
                                sender_message_id=message.sender_message_id,
                                sent_at_unix_milliseconds=message.sent_at_unix_milliseconds,
                                sender=message.sender,
                                recipient=message.recipient,
                                recovery_references=message.recovery_references,
                            ),
                            lifecycle=(
                                _turn_fact(turn_token, LifecycleStatus.unavailable),
                                _prompt_fact(turn_token, LifecycleStatus.unknown),
                            )
                            if index == 0
                            else (),
                            native_message_operation=native_message_operation,
                        )

            await asyncio.shield(settle_recovery_exception())
            raise

    @staticmethod
    def _validation_json(validation: CanonicalModelValidation | None) -> JsonDict | None:
        from pydantic import TypeAdapter

        if validation is None:
            return None
        value = TypeAdapter(CanonicalModelValidation).dump_python(validation, mode="json")
        assert isinstance(value, dict)
        return value

    def configure_destination_validation(
        self, observer: ProviderCatalogObserver, *, endpoint: ExecutorEndpoint | None = None
    ) -> None:
        """Wire actual local launch inputs and the authenticated native endpoint."""
        if self._catalog_observer is not observer or self._catalog_endpoint is not endpoint:
            self._backend_snapshots.clear_destination_snapshots()
        self._catalog_observer = observer
        self._catalog_endpoint = endpoint

    def launch_policy_revision(self, backend_key: ConversationBackendKey) -> str:
        from planner.conversation.backend_state import BackendStateStore

        return repr(
            sorted(BackendStateStore(self._store._db_path).read_disabled_models(backend_key))
        )

    async def destination_backend_snapshot(
        self,
        backend_key: ConversationBackendKey,
        workspace: CatalogWorkspaceBinding,
        destination: str,
        *,
        refresh: bool = False,
    ) -> BackendSnapshot:
        """Read advisory configuration through the same retained snapshot service."""
        from planner.conversation.model_resolution.contracts import (
            CatalogExpectedContext,
            CatalogRequest,
        )
        from planner.conversation.snapshot import BackendSnapshotScope
        from planner.core.errors import ErrorCode

        endpoint = self._catalog_endpoint
        observer = self._catalog_observer
        account = None
        identity = None
        if destination == "macos":
            if endpoint is None or endpoint.identity is None or not endpoint.ready:
                raise PlannerError(ErrorCode.validation, "The selected machine is offline")
            identity = endpoint.identity
        elif observer is not None:
            context = await observer.capture_context(backend_key, workspace=workspace)
            account = context.account_generation
        else:
            raise PlannerError(ErrorCode.validation, "Provider configuration is unavailable")
        scope = BackendSnapshotScope(backend_key, destination, workspace, identity, account)

        async def is_current() -> bool:
            if destination == "macos":
                current = self._catalog_endpoint
                return (current is not None and current is endpoint
                        and current.ready and current.identity == identity)
            if self._catalog_observer is not observer or observer is None:
                return False
            current_context = await observer.capture_context(backend_key, workspace=workspace)
            return current_context.account_generation == account

        async def observe() -> CatalogResult:
            attempt_id = uuid4().hex
            if destination == "macos":
                assert identity is not None and endpoint is not None
                return await endpoint.observe_catalog(
                    CatalogRequest(backend_key, CatalogExpectedContext(identity, workspace)),
                    attempt_id=attempt_id,
                )
            assert observer is not None
            return await observer.observe(
                backend_key, workspace=workspace, attempt_id=attempt_id,
                deadline=asyncio.get_running_loop().time() + 30,
            )

        return await self._backend_snapshots.destination_snapshot(
            scope, observe=observe, is_current=is_current, refresh=refresh,
        )

    async def validate_launch_selection(
        self,
        selection: ModelSelection,
        workspace: CatalogWorkspaceBinding,
        *,
        attempt_id: str,
        classify_new: bool = False,
        previous_alias_resolution: AliasResolutionFact | None = None,
    ) -> CanonicalModelValidation:
        from planner.conversation.backend_state import BackendStateStore
        from planner.conversation.launch_intent import bind_model_intent
        from planner.conversation.snapshot import BackendModel, BackendSnapshotScope

        # Exact tokens never depend on advisory context or provider discovery.
        models: tuple[BackendModel, ...] = ()
        if selection.selection_kind is SelectionKind.panels_alias and self._backend_snapshots:
            account = None
            identity = None
            if selection.destination.value == "macos":
                endpoint = self._catalog_endpoint
                identity = endpoint.identity if endpoint is not None and endpoint.ready else None
                if identity is None:
                    return bind_model_intent(
                        selection,
                        disabled_models=BackendStateStore(
                            self._store._db_path
                        ).read_disabled_models(selection.backend_key),
                    )
            elif self._catalog_observer is not None:
                context = await self._catalog_observer.capture_context(
                    selection.backend_key, workspace=workspace
                )
                account = context.account_generation
            scope = BackendSnapshotScope(
                selection.backend_key, selection.destination.value, workspace, identity, account
            )
            retained = self._backend_snapshots.retained_destination(scope)
            if retained is not None:
                models = retained.available_models
        return bind_model_intent(
            selection, models=models,
            disabled_models=BackendStateStore(self._store._db_path).read_disabled_models(
                selection.backend_key
            ),
        )

    async def _steer_with_admission(
        self,
        state: _ConversationState,
        child: BackendChild,
        token: TurnToken,
        content: MessageContent,
        *,
        sender_content: MessageContent,
        sender_label: str,
        attempt_id: str,
    ) -> BackendSteerOutcome:
        """Validate the running turn's accepted intent without changing next-turn intent."""
        from typing import cast

        from planner.conversation.admission.policy import (
            check_policy_dependencies,
            checked_validation_policy,
        )
        from planner.conversation.admission.service import (
            AdmissionContextObserver,
            admission_check,
            expectation_from_validation,
        )
        from planner.conversation.backends.contracts import BackendEmissionInputs
        from planner.core.db import connect

        conn = connect(self._store._db_path)
        try:
            ticket_row = conn.execute(
                "SELECT id FROM tickets WHERE conversation_id=?",
                (state.record.conversation_id,),
            ).fetchone()
            if ticket_row is None or state.record.backend_key is ConversationBackendKey.hermes:
                return await child.steer(
                    token, content, sender_content=sender_content, sender_label=sender_label
                )
            running = state.running_turn
            basis = None if running is None else running.launch_validation
            if (
                running is None
                or running.token != token
                or state.child is not child
                or basis is None
                or basis.launch_token is None
            ):
                raise AdmissionRefused("running turn submission binding is unavailable")
            validation = basis
            if running.ended or running.ending_is_the_cores:
                raise AdmissionRefused("running turn ownership changed")
            dependencies = checked_validation_policy(conn, validation)
            expectation = expectation_from_validation(
                validation,
                attempt_id=attempt_id,
                conversation_id=state.record.conversation_id,
                disabled_models_revision=self.launch_policy_revision(basis.selection.backend_key),
                turn=token,
                workspace=(
                    basis.evidence.context.workspace
                    if basis.evidence is not None
                    else basis.unavailable.attempted_context.workspace
                    if basis.unavailable and basis.unavailable.attempted_context
                    else CatalogWorkspaceBinding(
                        state.record.workspace_folder, state.record.workspace_path_policy, None
                    )
                ),
                executor_identity=(
                    self._catalog_endpoint.identity
                    if basis.selection.destination.value == "macos"
                    and self._catalog_endpoint is not None
                    else None
                ),
            )
            observer = cast(AdmissionContextObserver, self._catalog_observer)
            accepted_running = running

            class RunningContext:
                def verify_context(inner, expected: LaunchContext | None) -> bool:
                    check_policy_dependencies(conn, dependencies)
                    return (
                        state.running_turn is accepted_running
                        and not accepted_running.ended
                        and not accepted_running.ending_is_the_cores
                        and state.child is child
                        and (
                            expected is None
                            or (observer is not None and observer.verify_context(expected))
                        )
                    )

                def verify_prepared_launch(
                    inner, expected: LaunchContext, prepared: BackendEmissionInputs
                ) -> bool:
                    return inner.verify_context(expected) and observer.verify_prepared_launch(
                        expected, prepared
                    )

            return await child.steer(
                token,
                content,
                sender_content=sender_content,
                sender_label=sender_label,
                admission_expectation=expectation,
                emission_check=admission_check(expectation, RunningContext()),
            )
        finally:
            conn.close()

    async def _deliver_prompt_unsettled(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        admission_expectation: AdmissionExpectation | None = None,
        emission_check: EmissionCheck | None = None,
        **kwargs: Any,
    ) -> _PromptDeliveryAttempt:
        """Verify the real lifecycle intent immediately around child preparation and send."""
        from planner.conversation.admission.service import (
            admission_check,
            expectation_from_validation,
        )
        from planner.conversation.backends.contracts import AdmissionRefused
        from planner.core.db import connect
        from planner.core.errors import ErrorCode
        from planner.runtime.conversation_start import (
            worker_resolve,
            workspace_binding_from_start,
        )
        from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides
        from planner.tickets import data as ticket_data
        from planner.tickets.validation.data import (
            read_launch_selection,
            record_launch_validation,
        )

        conn = connect(self._store._db_path)
        validation: CanonicalModelValidation | None = None
        workspace: CatalogWorkspaceBinding | None = None
        ticket_id: str | None = None
        messages = kwargs.get("messages", ())
        effort_is_set = messages[0].reasoning_effort_is_set if messages else reasoning_effort_is_set
        model = kwargs.get("model_change")
        effort = kwargs.get("reasoning_effort_change")
        explicit_kind = messages[0].selection_kind if messages else selection_kind
        captured_held_selection = messages[0].original_selection if messages else None
        try:
            row = conn.execute(
                "SELECT id FROM tickets WHERE conversation_id=?",
                (state.record.conversation_id,),
            ).fetchone()
            if row is None:
                from planner.conversation.admission.policy import checked_validation_policy
                from planner.conversation.backend_state import BackendStateStore
                from planner.conversation.launch_intent import bind_model_intent
                from planner.tickets.contracts import TicketLocale

                endpoint = self._catalog_endpoint
                native = endpoint is not None and endpoint.owns_conversation(
                    state.record.conversation_id
                )
                requested_model = state.record.model if model is None else model
                if requested_model is None and captured_held_selection is None:
                    return await self._deliver_prompt_unsettled_verified(
                        state,
                        turn_token,
                        **kwargs,
                        admission_expectation=admission_expectation,
                        emission_check=emission_check,
                        reasoning_effort_is_set=reasoning_effort_is_set,
                    )
                selection = captured_held_selection or ModelSelection(
                    TicketLocale.macos if native else TicketLocale.vps,
                    state.record.backend_key,
                    requested_model or "",
                    explicit_kind or SelectionKind.legacy_unclassified,
                    (state.record.reasoning_effort
                     if effort is None and not effort_is_set else effort),
                    "conversation",
                )
                validation = bind_model_intent(
                    selection,
                    disabled_models=BackendStateStore(self._store._db_path).read_disabled_models(
                        selection.backend_key
                    ),
                )
                checked_validation_policy(conn, validation)
                workspace = CatalogWorkspaceBinding(
                    state.record.workspace_folder, state.record.workspace_path_policy, None
                )
                identity = endpoint.identity if native and endpoint is not None else None
                expectation = expectation_from_validation(
                    validation, attempt_id=uuid4().hex,
                    conversation_id=state.record.conversation_id,
                    disabled_models_revision=self.launch_policy_revision(selection.backend_key),
                    workspace=workspace, executor_identity=identity, turn=turn_token,
                )
                system = self

                carried_validation = validation

                class ConversationContext:
                    def verify_context(self, expected: LaunchContext | None) -> bool:
                        checked_validation_policy(conn, carried_validation)
                        current = system._catalog_endpoint
                        return not native or (
                            current is not None and current.ready and current.identity == identity
                        )

                    def verify_prepared_launch(
                        self, expected: LaunchContext, prepared: Any
                    ) -> bool:
                        return self.verify_context(expected)

                kwargs["model_change"] = (
                    validation.launch_token
                    if kwargs.get("model_change") is not None
                    or validation.launch_token != state.record.model
                    else None
                )
                kwargs["reasoning_effort_change"] = (
                    selection.reasoning_effort
                    if effort is not None or effort_is_set
                    or selection.reasoning_effort != state.record.reasoning_effort
                    else None
                )
                delivered = await self._deliver_prompt_unsettled_verified(
                    state,
                    turn_token,
                    canonical_validation=validation,
                    admission_expectation=expectation,
                    emission_check=admission_check(expectation, ConversationContext()),
                    **kwargs,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                )
                return replace(delivered, canonical_validation=validation)
            ticket_id = row["id"]
            ticket = ticket_data.read_ticket(conn, ticket_id)
            if state.record.backend_key is ConversationBackendKey.hermes:
                return await self._deliver_prompt_unsettled_verified(
                    state,
                    turn_token,
                    **kwargs,
                    admission_expectation=admission_expectation,
                    emission_check=emission_check,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                )
            if (
                messages
                and messages[0].held_prompt_id
                and captured_held_selection is None
                and model is None
            ):
                raise AdmissionRefused(
                    "held prompt has no original provider token; explicit correction required"
                )
            try:
                selection = read_launch_selection(conn, ticket_id)
                original_selection = selection
            except PlannerError as error:
                if captured_held_selection is None or error.code not in (
                    ErrorCode.validation,
                    ErrorCode.not_found,
                ):
                    raise
                # The newly captured conversation request remains exact even when
                # historical Ticket settings never recorded a model. It cannot
                # fabricate a source configuration for a departure CAS write.
                selection = captured_held_selection
                original_selection = None
            if captured_held_selection is not None:
                selection = captured_held_selection
                model = selection.submitted_token
                effort = selection.reasoning_effort
                effort_is_set = True
            else:
                model = kwargs.get("model_change")
            if captured_held_selection is None:
                effort = kwargs.get("reasoning_effort_change")
            if model is not None or effort is not None or effort_is_set:
                selection = replace(
                    selection,
                    submitted_token=selection.submitted_token if model is None else model,
                    backend_key=(
                        state.record.backend_key
                        if model is not None and not state.has_delivered_prompt
                        else selection.backend_key
                    ),
                    selection_kind=(
                        explicit_kind
                        if explicit_kind is not None
                        else (
                            selection.selection_kind
                            if model is None or model == selection.submitted_token
                            else SelectionKind.legacy_unclassified
                        )
                    ),
                    reasoning_effort=(
                        selection.reasoning_effort
                        if effort is None and not effort_is_set
                        else effort
                    ),
                )
            values = worker_resolve(
                conn,
                ticket,
                ConversationStartOverrides(
                    backend_key=selection.backend_key,
                    model=model,
                    reasoning_effort=effort,
                    reasoning_effort_is_set=effort_is_set,
                ),
            )
            workspace = workspace_binding_from_start(values, selection.destination)
            attempt_id = uuid4().hex
            validation = await self.validate_launch_selection(
                selection, workspace, attempt_id=attempt_id, classify_new=model is not None,
            )
            if model is None and effort is None and not effort_is_set:
                if not record_launch_validation(
                    conn,
                    ticket_id,
                    validation,
                    expected_workspace=workspace,
                    now=int(self._unix_time_now()),
                    notify=True,
                ):
                    return _PromptDeliveryAttempt(
                        refusal_reason=PromptDeliveryRefusalReason.model_resolution_failed,
                        record_refusal=True,
                        refusal_detail="model verification context_changed",
                    )
            if validation.launch_token is None:
                return _PromptDeliveryAttempt(
                    refusal_reason=PromptDeliveryRefusalReason.model_resolution_failed,
                    record_refusal=True,
                    canonical_validation=validation,
                    refusal_detail=(
                        f"{validation.reason.value}: {validation.corrective_action.value}"
                    ),
                )
            from planner.conversation.admission.policy import (
                check_policy_dependencies,
                checked_validation_policy,
            )

            policy_dependencies = checked_validation_policy(conn, validation)
            selection = validation.selection
            assert validation.launch_token is not None
            expectation = expectation_from_validation(
                validation,
                attempt_id=attempt_id,
                conversation_id=state.record.conversation_id,
                disabled_models_revision=self.launch_policy_revision(selection.backend_key),
                turn=turn_token,
                workspace=workspace,
                executor_identity=(
                    self._catalog_endpoint.identity
                    if selection.destination.value == "macos" and self._catalog_endpoint is not None
                    else None
                ),
            )
            kwargs["model_change"] = (
                validation.launch_token
                if kwargs.get("model_change") is not None
                or validation.launch_token != state.record.model
                else None
            )
            kwargs["reasoning_effort_change"] = (
                selection.reasoning_effort
                if effort is not None or effort_is_set
                or selection.reasoning_effort != state.record.reasoning_effort
                else None
            )
            from typing import cast

            from planner.conversation.admission.service import AdmissionContextObserver
            from planner.conversation.backends.contracts import BackendEmissionInputs

            system = self
            captured_observer = cast(AdmissionContextObserver, self._catalog_observer)

            class CurrentContext:
                def verify_context(self, expected: LaunchContext | None) -> bool:
                    policy_conn = connect(system._store._db_path)
                    try:
                        check_policy_dependencies(policy_conn, policy_dependencies)
                    finally:
                        policy_conn.close()
                    if selection.destination.value == "macos":
                        endpoint = system._catalog_endpoint
                        return (
                            endpoint is not None
                            and endpoint.ready
                            and endpoint.identity == expectation.executor_identity
                        )
                    return expected is None or (
                        captured_observer is not None and captured_observer.verify_context(expected)
                    )

                def verify_prepared_launch(
                    self,
                    expected: LaunchContext,
                    prepared: BackendEmissionInputs,
                ) -> bool:
                    return self.verify_context(
                        expected
                    ) and captured_observer.verify_prepared_launch(expected, prepared)

            delivered = await self._deliver_prompt_unsettled_verified(
                state,
                turn_token,
                canonical_validation=validation,
                admission_expectation=expectation,
                emission_check=admission_check(expectation, CurrentContext()),
                **kwargs,
                reasoning_effort_is_set=reasoning_effort_is_set,
            )
            return replace(
                delivered,
                canonical_validation=validation,
                original_selection=original_selection,
            )
        except (PlannerError, AdmissionRefused) as error:
            if isinstance(error, AdmissionRefused) and validation is not None:
                reason = ValidationReason(error.reason)
                action = CorrectiveAction(error.corrective_action)
                validation = replace(
                    validation,
                    state=(
                        ValidationState.incompatible
                        if reason
                        in (
                            ValidationReason.model_incompatible,
                            ValidationReason.effort_incompatible,
                        )
                        else ValidationState.unresolved
                    ),
                    reason=reason,
                    corrective_action=action,
                    launch_token=None,
                )
                if (
                    ticket_id is not None
                    and workspace is not None
                    and model is None
                    and effort is None
                    and not effort_is_set
                ):
                    record_launch_validation(
                        conn,
                        ticket_id,
                        validation,
                        expected_workspace=workspace,
                        now=int(self._unix_time_now()),
                        notify=True,
                    )
            return _PromptDeliveryAttempt(
                refusal_reason=PromptDeliveryRefusalReason.model_resolution_failed,
                record_refusal=True,
                canonical_validation=validation,
                refusal_detail=str(error),
            )
        finally:
            conn.close()

    async def _deliver_prompt_unsettled_verified(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        *,
        content: MessageContent,
        sender_label: str,
        sender_message_count: int,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        automatic_compaction: bool,
        reply_senders: tuple[Principal, ...] = (),
        messages: tuple[_HeldPrompt, ...] = (),
        canonical_validation: CanonicalModelValidation | None = None,
        admission_expectation: AdmissionExpectation | None = None,
        emission_check: EmissionCheck | None = None,
        reasoning_effort_is_set: bool = False,
    ) -> _PromptDeliveryAttempt:
        """Get the message onto a live child's wire, or name why that was impossible.

        No lock is held here: spawning a process and loading a session take as long as they
        take, and the conversation stays readable while they do.
        """
        effort_is_set = (
            messages[0].reasoning_effort_is_set if messages else reasoning_effort_is_set
        ) or (
            canonical_validation is not None
            and canonical_validation.selection.reasoning_effort != state.record.reasoning_effort
        )
        interruption_notice = await self._persist(
            state,
            partial(
                self._store.bind_interruption_delivery,
                state.record.conversation_id,
                turn_token.turn_number,
                tuple(ref for m in messages for ref in m.recovery_references) or None,
                {
                    ref.interruption_id: m.sender_message_id
                    for m in messages
                    if m.sender_message_id is not None
                    for ref in m.recovery_references
                }
                or None,
            ),
        )
        requires_exact_rebind = (
            effort_is_set
            and state.child is not None
            and reasoning_effort_change is None
            and state.record.reasoning_effort is not None
        )
        try:
            if (
                state.last_ended_turn_token is not None
                and state.last_ended_turn_token.turn_number >= turn_token.turn_number
            ):
                from planner.executor.remote import RemoteInitialDeliveryCancelled

                raise RemoteInitialDeliveryCancelled()
            child = await self._ensure_child(
                state,
                model=model_change,
                reasoning_effort=reasoning_effort_change,
                reasoning_effort_is_set=effort_is_set,
                admission_expectation=admission_expectation,
                emission_check=emission_check,
            )
        except BackendSpawnFailed:
            return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.backend_did_not_start)
        except SessionLoadFailed:
            return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.session_did_not_load)

        from planner.executor.remote import RemoteBackendChild

        if isinstance(child, RemoteBackendChild) and state.reserved_turn is not None:
            self._configure_initial_command_recording(state, child, state.reserved_turn)
        automatic_notice = False
        if messages:
            messages, suppression_reason, automatic_notice = await self._admit_dispatch_messages(
                state,
                messages,
                lifecycle_if_all_suppressed=(
                    _turn_fact(turn_token, LifecycleStatus.unavailable),
                    _prompt_fact(turn_token, LifecycleStatus.suppressed),
                ),
            )
            if not messages:
                # Preparation can spawn on uncommitted model values. Never retain that child.
                await self._discard_child(state, child)
                return _PromptDeliveryAttempt(suppression_reason=suppression_reason)
            content = one_prompt_from(messages)
            sender_label = messages[0].sender_label
            sender_message_count = len(messages)
            reply_senders = self._reply_senders(messages)
        composed_content = await self._compose_prompt_content(state, content)
        if interruption_notice is not None:
            composed_content = prefix_message_content_text(
                composed_content, interruption_notice, "\n\n"
            )
        composed = with_authenticated_reply_directive(
            self._with_native_worker_context(state, child, composed_content), reply_senders
        )
        if (
            state.last_ended_turn_token is not None
            and state.last_ended_turn_token.turn_number >= turn_token.turn_number
        ):
            from planner.executor.remote import RemoteInitialDeliveryCancelled

            raise RemoteInitialDeliveryCancelled()
        attempted_child = child
        attempted_reservation = state.reserved_turn
        try:
            if requires_exact_rebind:
                raise NeedsRebind("exact desired default or alias target requires a fresh child")
            accepted = await attempted_child.write_prompt(
                turn_token,
                composed,
                sender_content=content,
                sender_label=sender_label,
                sender_message_count=sender_message_count,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
                automatic_compaction=automatic_compaction,
                emission_check=emission_check,
                admission_expectation=admission_expectation,
                reasoning_effort_is_set=effort_is_set,
            )
        except PromptWriteRefused as refused:
            return _PromptDeliveryAttempt(
                PromptDeliveryRefusalReason.write_to_backend_failed,
                refusal_detail=str(refused),
            )
        except BackendPromptRejected as rejected:
            return _PromptDeliveryAttempt(
                PromptDeliveryRefusalReason.backend_rejected_prompt,
                record_refusal=True,
                refusal_detail=str(rejected),
                provider_submission=rejected.provider_submission,
            )
        except NeedsRebind as rebind:
            rebound = await self._rebind_and_write_prompt(
                state,
                turn_token,
                refused_child=attempted_child,
                reservation=attempted_reservation,
                content=composed,
                sender_content=content,
                sender_label=sender_label,
                sender_message_count=sender_message_count,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
                automatic_compaction=automatic_compaction,
                messages=messages,
                interruption_notice=interruption_notice,
                reasoning_effort_is_set=effort_is_set,
                admission_expectation=admission_expectation,
                emission_check=emission_check,
            )
            return replace(
                rebound,
                record_refusal=(
                    rebound.refusal_reason is not None and rebind.failed_child_recovery
                ),
            )
        return _PromptDeliveryAttempt(
            messages=messages,
            automatic_notice=automatic_notice,
            composed_content_delivered=accepted.composed_content_delivered,
            provider_submission=accepted.provider_submission,
        )

    async def _rebind_and_write_prompt(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        *,
        refused_child: BackendChild,
        reservation: _ReservedTurn | None,
        content: MessageContent,
        sender_content: MessageContent,
        sender_label: str,
        sender_message_count: int,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        automatic_compaction: bool,
        messages: tuple[_HeldPrompt, ...] = (),
        interruption_notice: str | None = None,
        reasoning_effort_is_set: bool = False,
        admission_expectation: AdmissionExpectation | None = None,
        emission_check: EmissionCheck | None = None,
    ) -> _PromptDeliveryAttempt:
        """Start the child again on the new values, under the same conversation, and write.

        This is the path for a backend that cannot be moved onto another model without
        starting over. The conversation is the same one — it resumes from the session
        cursor it already has — and if any of it fails, the change has not happened and
        neither has the delivery.

        A rebound child that is then not written to is thrown away rather than kept. It
        was started on the values the failed delivery was carrying, and those values did
        not stand, so keeping it would leave the conversation's record and its live agent
        saying two different things about what it is running on. Dropping it costs one
        lazy respawn and keeps the record the only answer.
        """
        from planner.executor.remote import RemoteBackendChild, RemoteInitialDeliveryCancelled

        def check_delivery_owner() -> None:
            if (
                self._shutting_down
                or state.reserved_turn is not reservation
                or (
                    reservation is not None
                    and (reservation.token != turn_token or reservation.shutdown_owned)
                )
                or (
                    state.last_ended_turn_token is not None
                    and state.last_ended_turn_token.turn_number >= turn_token.turn_number
                )
            ):
                raise RemoteInitialDeliveryCancelled()

        old_child = refused_child
        async with state.lock:
            check_delivery_owner()
            if state.child is not old_child:
                raise RemoteInitialDeliveryCancelled()
            if isinstance(old_child, RemoteBackendChild):
                if state.executor_child_reference != old_child.reference:
                    raise RemoteInitialDeliveryCancelled()
                cleanup = self._schedule_remote_retirement(
                    state,
                    old_child,
                    reservation=reservation,
                    nonadmitted_turn=turn_token,
                )
            else:
                state.child_is_quarantined = True
                cleanup = None
        if cleanup is not None:
            if not await asyncio.shield(cleanup):
                return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.write_to_backend_failed)
        else:
            if not await self._stop_child(state, old_child):
                return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.write_to_backend_failed)
            async with state.lock:
                if state.child is old_child:
                    state.child = None
                    state.child_is_quarantined = False
        async with state.lock:
            check_delivery_owner()
            if state.child is not None or state.child_is_quarantined:
                return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.write_to_backend_failed)
        try:
            child = await self._ensure_child(
                state,
                model=model_change,
                reasoning_effort=reasoning_effort_change,
                reasoning_effort_is_set=reasoning_effort_is_set,
                admission_expectation=admission_expectation,
                emission_check=emission_check,
            )
        except BackendSpawnFailed:
            return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.backend_did_not_start)
        except SessionLoadFailed:
            return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.session_did_not_load)
        async with state.lock:
            check_delivery_owner()
            if isinstance(child, RemoteBackendChild) and reservation is not None:
                self._configure_initial_command_recording(state, child, reservation)
        automatic_notice = False
        if messages:
            messages, suppression_reason, automatic_notice = await self._admit_dispatch_messages(
                state,
                messages,
                lifecycle_if_all_suppressed=(
                    _turn_fact(turn_token, LifecycleStatus.unavailable),
                    _prompt_fact(turn_token, LifecycleStatus.suppressed),
                ),
            )
            if not messages:
                await self._discard_child(state, child)
                return _PromptDeliveryAttempt(suppression_reason=suppression_reason)
            sender_content = one_prompt_from(messages)
            sender_label = messages[0].sender_label
            sender_message_count = len(messages)
            composed_content = await self._compose_prompt_content(state, sender_content)
            if interruption_notice is not None:
                composed_content = prefix_message_content_text(
                    composed_content, interruption_notice, "\n\n"
                )
            content = with_authenticated_reply_directive(
                self._with_native_worker_context(state, child, composed_content),
                self._reply_senders(messages),
            )
        async with state.lock:
            check_delivery_owner()
            if state.child is not child:
                raise RemoteInitialDeliveryCancelled()
        try:
            accepted = await child.write_prompt(
                turn_token,
                content,
                sender_content=sender_content,
                sender_label=sender_label,
                sender_message_count=sender_message_count,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
                automatic_compaction=automatic_compaction,
                emission_check=emission_check,
                admission_expectation=admission_expectation,
                reasoning_effort_is_set=reasoning_effort_is_set,
            )
        except PromptWriteRefused as refused:
            return _PromptDeliveryAttempt(
                PromptDeliveryRefusalReason.write_to_backend_failed,
                refusal_detail=str(refused),
            )
        except BackendPromptRejected as rejected:
            return _PromptDeliveryAttempt(
                PromptDeliveryRefusalReason.backend_rejected_prompt,
                record_refusal=True,
                refusal_detail=str(rejected),
                provider_submission=rejected.provider_submission,
            )
        except PromptWriteFailed:
            raise
        except NeedsRebind:
            await self._discard_child(state, child)
            return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.write_to_backend_failed)
        except BaseException:
            await self._discard_child(state, child)
            raise
        return _PromptDeliveryAttempt(
            composed_content_delivered=accepted.composed_content_delivered,
            provider_submission=accepted.provider_submission,
            messages=messages,
            automatic_notice=automatic_notice,
        )

    async def _finalize_delivery(
        self,
        state: _ConversationState,
        reservation: _ReservedTurn,
        refusal: PromptDeliveryRefusalReason | None,
        *,
        content: MessageContent,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
        sender: Principal | None,
        recipient: Principal | None,
        reply_requested: bool,
        recovery_references: tuple[DeploymentRecoveryReference, ...],
        composed_content_delivered: bool,
        provider_submission: ProviderSubmissionFact | None = None,
        canonical_validation: CanonicalModelValidation | None = None,
        original_selection: ModelSelection | None = None,
        refusal_detail: str | None = None,
        record_refusal: bool,
        phase_when_not_started: _ConversationPhase,
        also_delivered: tuple[_HeldPrompt, ...] = (),
        owner_read_through_sequence: int | None = None,
        acknowledge_owner_on_failure: bool = True,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> bool:
        """Turn a delivery that has happened, or failed to, into the record and the state.

        The rows are written before the turn is admitted, so nothing the backend says about
        this turn can be recorded ahead of the prompt that started it.
        """
        reasoning_effort_is_set = reasoning_effort_is_set or any(
            message.reasoning_effort_is_set for message in reservation.messages
        )
        delivery_carries_change = (
            model_change is not None
            or reasoning_effort_change is not None
            or reasoning_effort_is_set
            or selection_kind is not None
            or any(message.selection_kind is not None for message in reservation.messages)
            or any(message.reasoning_effort_is_set for message in reservation.messages)
            or (
                canonical_validation is not None
                and (
                    canonical_validation.launch_token != state.record.model
                    or canonical_validation.selection.reasoning_effort
                    != state.record.reasoning_effort
                )
            )
        )
        if canonical_validation is not None and delivery_carries_change:
            model_change = canonical_validation.launch_token
            reasoning_effort_change = canonical_validation.selection.reasoning_effort
        async with state.lock:
            try:
                if reservation.shutdown_owned:
                    return False
                if refusal is not None:
                    refused_facts = (
                        _turn_fact(reservation.token, LifecycleStatus.unavailable),
                        _prompt_fact(reservation.token, LifecycleStatus.refused),
                    )
                    if not record_refusal:
                        await self._persist(
                            state,
                            partial(
                                self._store.transition_lifecycle,
                                state.record.conversation_id,
                                refused_facts,
                            ),
                        )
                    if record_refusal:
                        await self._append_event(
                            state,
                            PromptDeliveryRefusedEventPayload(
                                recovery_references=recovery_references,
                                content=content,
                                sender_label=sender_label,
                                mode=mode,
                                refusal_reason=refusal,
                                provider_submission=provider_submission,
                                refusal_detail=refusal_detail,
                                model_validation=self._validation_json(canonical_validation),
                                sender_message_id=sender_message_id,
                                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                                sender=sender,
                                recipient=recipient,
                            ),
                            lifecycle=refused_facts,
                            owner_read_through_sequence=(
                                owner_read_through_sequence
                                if acknowledge_owner_on_failure
                                else None
                            ),
                            native_message_operation=native_message_operation,
                        )
                        for message in also_delivered:
                            # The others went to the same refused delivery, so each is
                            # refused in its own right and its sender is told so.
                            await self._append_event(
                                state,
                                PromptDeliveryRefusedEventPayload(
                                    recovery_references=message.recovery_references,
                                    content=message.content,
                                    sender_label=message.sender_label,
                                    mode=mode,
                                    refusal_reason=refusal,
                                    refusal_detail=refusal_detail,
                                    model_validation=self._validation_json(canonical_validation),
                                    sender_message_id=message.sender_message_id,
                                    sent_at_unix_milliseconds=message.sent_at_unix_milliseconds,
                                    sender=message.sender,
                                    recipient=message.recipient,
                                ),
                                native_message_operation=native_message_operation,
                            )
                    self._set_phase(state, phase_when_not_started)
                    return False

                carried_change: ModelChangedEventPayload | None = None
                if delivery_carries_change:
                    running_on = state.record.model if model_change is None else model_change
                    if running_on is None:
                        # A change that does not name a model moves the one the record
                        # holds, and this row holds none. The same rows cannot start a
                        # child either, so this says what that says: nothing to run on.
                        raise ConversationRecordNamesNoModel(state.record.conversation_id)
                    carried_change = ModelChangedEventPayload(
                        model=running_on,
                        reasoning_effort=(
                            canonical_validation.selection.reasoning_effort
                            if canonical_validation is not None
                            else (
                                state.record.reasoning_effort
                                if reasoning_effort_change is None and not reasoning_effort_is_set
                                else reasoning_effort_change
                            )
                        ),
                    )

                accepted_turn = _RunningTurn(
                    token=reservation.token,
                    launch_validation=canonical_validation,
                    automatic_compaction=reservation.automatic_compaction,
                    prompt_senders={
                        principal: None
                        for principal in (
                            (sender if reply_requested and composed_content_delivered else None),
                            *(
                                message.sender
                                if message.reply_requested and composed_content_delivered
                                else None
                                for message in also_delivered
                            ),
                        )
                        if principal is not None
                    },
                    automatic_notice=reservation.automatic_notice,
                )
                # One transaction: the change, the conversation's new values and the
                # prompt are one fact about one delivery, and a half-written one would
                # leave the record saying something that never happened.
                accepted_metadata = self._executor_turn_metadata(state, accepted_turn)
                if accepted_metadata is not None:
                    accepted_runtime = json.loads(accepted_metadata[2])
                    accepted_runtime["delivery_uncertain"] = False
                    accepted_metadata = (
                        accepted_metadata[0],
                        accepted_metadata[1],
                        json.dumps(accepted_runtime),
                    )
                written = await self._persist(
                    state,
                    partial(
                        self._store.append_delivered_prompt,
                        state.record.conversation_id,
                        native_message_operation=native_message_operation,
                        prompt=PromptEventPayload(
                            provider_submission=provider_submission,
                            recovery_references=recovery_references,
                            content=content,
                            sender_label=sender_label,
                            mode=mode,
                            sender_message_id=sender_message_id,
                            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                            sender=sender,
                            recipient=recipient,
                        ),
                        lifecycle=(
                            _turn_fact(reservation.token, LifecycleStatus.running),
                            _prompt_fact(reservation.token, LifecycleStatus.started),
                        ),
                        executor_turn=accepted_metadata,
                        original_ticket_selection=(
                            original_selection if delivery_carries_change else None
                        ),
                        requested_ticket_validation=(
                            canonical_validation if delivery_carries_change else None
                        ),
                        composed_content_delivered=composed_content_delivered,
                        model_change=carried_change,
                        extra_prompts=tuple(
                            PromptEventPayload(
                                recovery_references=message.recovery_references,
                                content=message.content,
                                sender_label=message.sender_label,
                                mode=mode,
                                sender_message_id=message.sender_message_id,
                                sent_at_unix_milliseconds=message.sent_at_unix_milliseconds,
                                sender=message.sender,
                                recipient=message.recipient,
                            )
                            for message in also_delivered
                        ),
                        owner_read_through_sequence=owner_read_through_sequence,
                    ),
                    runtime_mutation=self._runtime_mutation(
                        state,
                        PlainFieldMutation(state, "running_turn", accepted_turn),
                    ),
                )
                self._take_in_written_rows(state, written)
                owner_prompt_sequences = [
                    event.sequence
                    for event in written
                    if isinstance(event.payload, PromptEventPayload)
                    and event.payload.sender is not None
                    and event.payload.sender.kind is PrincipalKind.owner
                ]
                if owner_prompt_sequences:
                    read_through = (
                        max(owner_prompt_sequences)
                        if owner_read_through_sequence is None
                        else owner_read_through_sequence
                    )
                    state.record = replace(
                        state.record,
                        owner_read_through_sequence=max(
                            state.record.owner_read_through_sequence, read_through
                        ),
                    )
                if carried_change is not None:
                    state.record = replace(
                        state.record,
                        model=carried_change.model,
                        reasoning_effort=carried_change.reasoning_effort,
                    )
                state.has_delivered_prompt = True
                state.executor_delivery_uncertain = False
                if self._signal is None:
                    state.running_turn = accepted_turn
                self._set_phase(state, _ConversationPhase.running)
                # The prompt commit can announce before this process-owned state is
                # visible. Publish again after the runtime transition, so a reader of
                # that earlier announcement cannot keep an idle answer indefinitely.
                change_signal.emit()
                return True
            finally:
                if state.phase is not _ConversationPhase.starting:
                    state.reserved_turn = None
                    reservation.resolved.set()

    async def _begin_attempt(
        self,
        state: _ConversationState,
        token: TurnToken,
        operation: LifecycleOperation,
        provider_identity: str | None = None,
        related: tuple[LifecycleFact, ...] = (),
        *,
        runtime_mutation: SignalRuntimeMutation | None = None,
    ) -> LifecycleFact:
        attempt = LifecycleFact(
            LifecycleKind.attempt,
            uuid4().hex,
            token.turn_number,
            operation,
            LifecycleStatus.intent,
            provider_identity,
        )
        await self._persist(
            state,
            partial(
                self._store.transition_lifecycle, state.record.conversation_id, (attempt, *related)
            ),
            runtime_mutation=runtime_mutation,
        )
        return attempt

    async def _own_settlement[T](
        self, state: _ConversationState, operation: Coroutine[Any, Any, T]
    ) -> T:
        """Keep the exchange and its recording alive after the caller leaves."""
        if asyncio.current_task() in state.settlement_tasks:
            return await operation
        task = asyncio.create_task(operation)
        register_capture_task(task)
        state.settlement_tasks.add(task)
        task.add_done_callback(state.settlement_tasks.discard)
        return await asyncio.shield(task)

    def _runtime_turn(self, conversation_id: str) -> object | None:
        state = self._conversations.get(conversation_id)
        return None if state is None else state.running_turn

    def _signal_read(self) -> AbstractContextManager[None]:
        return nullcontext() if self._signal is None else self._signal.current_read()

    def _runtime_mutation(
        self,
        state: _ConversationState,
        *operations: PlainFieldMutation,
    ) -> SignalRuntimeMutation | None:
        if self._signal is None:
            return None
        return SignalRuntimeMutation(
            conversation_id=state.record.conversation_id,
            runtime_epoch=self._signal.runtime_epoch,
            expected_turn=state.running_turn,
            operations=operations,
        )

    async def _persist[T](
        self,
        state: _ConversationState,
        operation: Callable[[], Coroutine[Any, Any, T]],
        *,
        runtime_mutation: SignalRuntimeMutation | None = None,
    ) -> T:
        with self._store.signal_mutation(runtime_mutation):
            return await self._store.settle_recording(state.record.conversation_id, operation)

    async def _drain_held_prompts(self, state: _ConversationState) -> None:
        await self._own_settlement(state, self._drain_held_prompts_owned(state))

    async def _drain_held_prompts_owned(self, state: _ConversationState) -> None:
        """Run held messages until one starts a turn or there are none left.

        One owner: the conversation is in its draining phase for the whole of it, so a
        second caller finding it idle a moment later does not start draining beside this
        one. A held message that cannot be delivered has its own refusal recorded — the
        caller that sent it is long gone — and the next one is tried.
        """
        from planner.executor.remote import RemoteCommandUncertain

        async with state.lock:
            if (
                state.child_is_quarantined
                or state.phase is not _ConversationPhase.idle
                or not state.held_prompts
                or any(held.delivery_uncertain for held in state.held_prompts)
            ):
                return
            self._set_phase(state, _ConversationPhase.draining)

        while True:
            async with state.lock:
                if not state.held_prompts:
                    self._set_phase(state, _ConversationPhase.idle)
                    return
                batch = leading_run_that_can_share_a_turn(state.held_prompts)
                await self._persist(
                    state,
                    partial(
                        self._store.mark_held_sender_messages_leaving_queue,
                        state.record.conversation_id,
                        tuple(
                            message.sender_message_id
                            for message in batch
                            if message.sender_message_id is not None
                        ),
                    ),
                )
                self._begin_held_deliveries(state, batch)
                for _ in batch:
                    state.held_prompts.popleft()
                held = batch[0]
                rest = batch[1:]
                self._publish_held_prompts_changed(state)
                reservation = await self._reserve_turn(state)
                reservation.messages = batch

            try:
                reservation.automatic_notice = await self._persist(
                    state,
                    partial(
                        self._store.all_sender_messages_are_manager_notices,
                        state.record.conversation_id,
                        tuple(message.sender_message_id for message in batch),
                    ),
                )
                delivery = await self._deliver_prompt(
                    state,
                    reservation.token,
                    content=held.content,
                    sender_label=held.sender_label,
                    sender_message_count=len(batch),
                    mode=PromptDeliveryMode.queue,
                    model_change=held.model_change,
                    reasoning_effort_change=held.reasoning_effort_change,
                    automatic_compaction=False,
                    messages=batch,
                    reply_senders=tuple(
                        dict.fromkeys(
                            message.sender
                            for message in batch
                            if message.reply_requested
                            and message.sender is not None
                            and message.recipient is not None
                        )
                    ),
                )
            except (PromptWriteFailed, RemoteCommandUncertain):
                await self._record_delivery_uncertainty(state, reservation, batch)
                return
            except asyncio.CancelledError:
                self._quarantine_uncertain_held_batch(state, batch)
                raise
            except Exception:
                if reservation.shutdown_owned:
                    await reservation.resolved.wait()
                    self._settle_held_deliveries(state, batch)
                    return
                async with state.lock:
                    self._quarantine_uncertain_held_batch(state, batch)
                return

            if delivery.suppression_reason is not None:
                self._abandon_reserved_turn(state, reservation, _ConversationPhase.draining)
                async with state.lock:
                    self._settle_held_deliveries(state, batch)
                continue
            reservation.automatic_notice = delivery.automatic_notice
            if delivery.messages:
                held, *surviving_rest = delivery.messages
                rest = tuple(surviving_rest)
            try:
                started = await self._finalize_delivery(
                    state,
                    reservation,
                    delivery.refusal_reason,
                    content=held.content,
                    sender_label=held.sender_label,
                    mode=PromptDeliveryMode.queue,
                    model_change=held.model_change,
                    reasoning_effort_change=held.reasoning_effort_change,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                    sender=held.sender,
                    recipient=held.recipient,
                    reply_requested=held.reply_requested,
                    recovery_references=held.recovery_references,
                    canonical_validation=delivery.canonical_validation,
                    original_selection=delivery.original_selection,
                    refusal_detail=delivery.refusal_detail,
                    composed_content_delivered=delivery.composed_content_delivered,
                    provider_submission=delivery.provider_submission,
                    record_refusal=True,
                    phase_when_not_started=_ConversationPhase.draining,
                    also_delivered=rest,
                    owner_read_through_sequence=max(
                        (
                            message.owner_read_through_sequence
                            for message in delivery.messages
                            if message.sender is not None
                            and message.sender.kind is PrincipalKind.owner
                            and message.owner_read_through_sequence is not None
                        ),
                        default=None,
                    ),
                    acknowledge_owner_on_failure=True,
                )
                async with state.lock:
                    self._settle_held_deliveries(state, batch)
            except BaseException:
                # The text is already on a live agent's wire and only the record fell
                # over. The line stops here rather than sending a second message into a
                # turn that is now running.
                raise
            if started:
                return

    def _quarantine_uncertain_held_batch(
        self, state: _ConversationState, batch: tuple[_HeldPrompt, ...]
    ) -> None:
        # The canonical dispatching marker predates the native attempt. Preserve
        # its bytes and identity; transport loss supplies no terminal outcome.
        state.child_is_quarantined = True
        reservation = state.reserved_turn
        if reservation is not None:
            self._abandon_reserved_turn(state, reservation, _ConversationPhase.idle)
        self._settle_held_deliveries(state, batch)
        for held in reversed(batch):
            state.held_prompts.appendleft(replace(held, delivery_uncertain=True))
        self._publish_held_prompts_changed(state)

    async def _discard_held_prompts(self, state: _ConversationState) -> None:
        """Throw away everything waiting, writing each one down. The lock must be held.

        Each message leaves the queue before its row is written, so a write that falls
        over part-way leaves the rest of the queue discarded rather than delivered.
        """
        for discarded in tuple(state.held_prompts):
            if discarded.delivery_uncertain:
                continue
            state.held_prompts.remove(discarded)
            self._publish_held_prompts_changed(state)
            await self._append_event(
                state,
                PromptDiscardedEventPayload(
                    content=discarded.content,
                    sender_label=discarded.sender_label,
                    sender_message_id=discarded.sender_message_id,
                    sent_at_unix_milliseconds=discarded.sent_at_unix_milliseconds,
                    sender=discarded.sender,
                    recipient=discarded.recipient,
                ),
            )

    async def _record_steer_outcome(
        self,
        state: _ConversationState,
        outcome: BackendSteerOutcome,
        *,
        turn_token: TurnToken | None,
        attempt: LifecycleFact | None = None,
        content: MessageContent,
        sender_label: str,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
        sender: Principal | None,
        recipient: Principal | None,
        reply_requested: bool,
        recovery_references: tuple[DeploymentRecoveryReference, ...],
        owner_read_through_sequence: int | None,
        acknowledge_owner_on_failure: bool = True,
        automatic_notice: bool = False,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> PromptDeliveryInjected | PromptDeliveryRefused | PromptDeliveryUncertain:
        """Record and map one steering outcome while the conversation lock is held."""
        fate: PromptDeliveryFate
        if isinstance(outcome, BackendSteerAccepted):
            fate = PromptDeliveryInjected()
        elif isinstance(outcome, BackendSteerRefused):
            fate = PromptDeliveryRefused(outcome.refusal_reason)
        else:
            fate = PromptDeliveryUncertain()
        executor_turn = None
        current = state.running_turn
        if current is not None and current.token == turn_token:
            predicted = replace(current, prompt_senders=dict(current.prompt_senders))
            if (
                isinstance(outcome, (BackendSteerAccepted, BackendSteerUncertain))
                and outcome.composed_content_delivered
            ):
                predicted.automatic_notice = predicted.automatic_notice and automatic_notice
                if reply_requested and sender is not None:
                    predicted.prompt_senders.setdefault(sender, None)
            executor_turn = self._executor_turn_metadata(state, predicted)
        if isinstance(outcome, BackendSteerAccepted):
            await self._append_event(
                state,
                PromptEventPayload(
                    provider_submission=outcome.provider_submission,
                    recovery_references=recovery_references,
                    content=content,
                    sender_label=sender_label,
                    mode=PromptDeliveryMode.steer,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    sender=sender,
                    recipient=recipient,
                ),
                executor_turn=executor_turn,
                native_message_admission=NativeMessageAdmission(
                    fate, getattr(outcome, "composed_content_delivered", False) is True
                ),
                lifecycle=(
                    () if attempt is None else (replace(attempt, status=LifecycleStatus.injected),)
                ),
                owner_read_through_sequence=(
                    owner_read_through_sequence
                    if sender is not None and sender.kind is PrincipalKind.owner
                    else None
                ),
                native_message_operation=native_message_operation,
            )
            if (
                outcome.composed_content_delivered
                and state.running_turn is not None
                and state.running_turn.token == turn_token
            ):
                state.running_turn.automatic_notice = (
                    state.running_turn.automatic_notice and automatic_notice
                )
                if reply_requested and sender is not None:
                    state.running_turn.prompt_senders.setdefault(sender, None)
            return PromptDeliveryInjected()
        if isinstance(outcome, BackendSteerRefused):
            await self._append_event(
                state,
                PromptDeliveryRefusedEventPayload(
                    recovery_references=recovery_references,
                    content=content,
                    sender_label=sender_label,
                    mode=PromptDeliveryMode.steer,
                    refusal_reason=outcome.refusal_reason,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    sender=sender,
                    recipient=recipient,
                ),
                lifecycle=(
                    () if attempt is None else (replace(attempt, status=LifecycleStatus.refused),)
                ),
                executor_turn=executor_turn,
                native_message_admission=NativeMessageAdmission(
                    fate, getattr(outcome, "composed_content_delivered", False) is True
                ),
                owner_read_through_sequence=(
                    owner_read_through_sequence
                    if acknowledge_owner_on_failure
                    and sender is not None
                    and sender.kind is PrincipalKind.owner
                    else None
                ),
                native_message_operation=native_message_operation,
            )
            return PromptDeliveryRefused(refusal_reason=outcome.refusal_reason)
        assert isinstance(outcome, BackendSteerUncertain)
        await self._append_event(
            state,
            PromptDeliveryUncertainEventPayload(
                recovery_references=recovery_references,
                content=content,
                sender_label=sender_label,
                mode=PromptDeliveryMode.steer,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
            ),
            executor_turn=executor_turn,
            native_message_admission=NativeMessageAdmission(
                fate, outcome.composed_content_delivered is True
            ),
            lifecycle=(
                () if attempt is None else (replace(attempt, status=LifecycleStatus.unknown),)
            ),
            owner_read_through_sequence=(
                owner_read_through_sequence
                if acknowledge_owner_on_failure
                and sender is not None
                and sender.kind is PrincipalKind.owner
                else None
            ),
            native_message_operation=native_message_operation,
        )
        if (
            outcome.composed_content_delivered
            and state.running_turn is not None
            and state.running_turn.token == turn_token
        ):
            # The backend may have accepted the text. Treating it as part of this turn
            # avoids a false silence marker and matches the delivery's non-retry fate.
            state.running_turn.automatic_notice = (
                state.running_turn.automatic_notice and automatic_notice
            )
            if reply_requested and sender is not None:
                state.running_turn.prompt_senders.setdefault(sender, None)
        return PromptDeliveryUncertain()

    # --- turns --------------------------------------------------------------------------

    async def _reserve_turn(
        self, state: _ConversationState, *, automatic_compaction: bool = False
    ) -> _ReservedTurn:
        """Name the turn that is about to be written. The lock must be held."""
        turn_number = await self._store.reserve_turn(state.record.conversation_id)
        reservation = _ReservedTurn(
            token=TurnToken(
                conversation_id=state.record.conversation_id,
                turn_number=turn_number,
            ),
            resolved=asyncio.Event(),
            automatic_compaction=automatic_compaction,
        )
        # A new turn may say it is thinking straight away. Holding its first pulse back
        # because the turn before it had just said so would silence the very moment this
        # exists for: a turn that has started and has nothing to show yet.
        state.model_thinking_shown_at_monotonic = 0.0
        state.reserved_turn = reservation
        from planner.executor.remote import RemoteBackendChild

        if isinstance(state.child, RemoteBackendChild):
            state.child.initial_commands = []
            state.child.prompt_accepted = False
        self._set_phase(state, _ConversationPhase.starting)
        return reservation

    def _abandon_reserved_turn(
        self,
        state: _ConversationState,
        reservation: _ReservedTurn,
        phase: _ConversationPhase,
    ) -> None:
        """Give up a turn that was named and will now never start.

        No lock: whoever reserved the turn owns it, and owns the conversation's phase,
        until it resolves — so there is nothing here to serialise against. It matters most
        when something has gone wrong in an adapter, because a reservation that is never
        resolved is a conversation nothing can use again.
        """
        if reservation.shutdown_owned:
            return
        if state.reserved_turn is reservation:
            state.reserved_turn = None
        self._set_phase(state, phase)
        reservation.resolved.set()

    async def emit_executor_answer(
        self, conversation_id: str, emit: Callable[[], Coroutine[Any, Any, None]]
    ) -> None:
        """Order the bounded answer frame with durable local callback revocation.

        This lock covers emission only, never a native result or application wait.
        The emitter rechecks durable identity after its asynchronous preparation.
        """
        state = await self._conversation_state(conversation_id)
        if state is None:
            return
        async with state.lock:
            await emit()

    async def _revoke_executor_turn_asks(
        self, state: _ConversationState, running: _RunningTurn
    ) -> None:
        if state.executor_child_reference is not None:
            await self._store.revoke_executor_turn_asks(
                state.executor_child_reference, running.token
            )
            running.callbacks_revoked = True

    async def _cancel_and_end_running_turn(
        self, state: _ConversationState, running: _RunningTurn
    ) -> None:
        """Stop the agent and record the interruption. The lock is held on both sides.

        It is let go for the cancel itself, which is a wire call; the conversation is in
        its ending phase throughout, so nobody else takes it in the meantime. The backend
        will report this turn's ending as soon as it takes the cancel, and that report
        arrives while the lock is let go — the turn is marked as being ended here first, so
        the report is dropped and this interruption is the one that gets recorded.
        """
        self._set_phase(state, _ConversationPhase.ending)
        running.ending_is_the_cores = True
        await self._revoke_executor_turn_asks(state, running)
        child = state.child
        cancel_fact = None
        state.lock.release()
        try:
            if child is not None:
                cancel_fact = await self._cancel_child_turn(state, child)
        finally:
            await state.lock.acquire()
        await self._end_turn(
            state, running, ConversationTurnEnding.interrupted, None, cancel_fact=cancel_fact
        )

    async def _end_turn(
        self,
        state: _ConversationState,
        running: _RunningTurn,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        *,
        cancel_fact: LifecycleFact | None = None,
    ) -> StoredConversationEvent | None:
        """Record a turn's ending, once. The lock must be held.

        Once per turn, whoever gets there first: a turn that has already ended writes
        nothing and returns nothing. A turn has one ending, and two rows saying it stopped
        would be two different stories about the same moment.

        The turn's permission asks die here: nothing more can be answered on them, and no
        answer is recorded for them. Settling them with the backend belongs to the adapter,
        which is the only side that knows what a withdrawn ask means to its vendor.
        """
        if running.ended:
            return None
        await self._revoke_executor_turn_asks(state, running)
        interpreted = interpret_turn_ending(
            _ending_context(running),
            ending,
            error_summary,
            await self._store.lifecycle_facts(state.record.conversation_id),
        )
        automatic_compaction_result = interpreted.compaction_result
        payloads = interpreted.payloads
        written = await self._persist(
            state,
            partial(
                self._store.append_turn_ending,
                state.record.conversation_id,
                payloads,
                lifecycle=(
                    interpreted.lifecycle[0],
                    *((cancel_fact,) if cancel_fact is not None else ()),
                    *interpreted.lifecycle[1:],
                ),
                agent_activity=not running.automatic_compaction,
                automatic_compaction_confirmed=running.compaction_confirmed,
                automatic_compaction_result=automatic_compaction_result,
                create_failure_notice=interpreted.failure_notice,
                executor_turn=self._executor_turn_metadata(
                    state,
                    replace(
                        running,
                        ended=True,
                        pending_permission_ask_ids=set(),
                        pending_user_input_questions={},
                    ),
                ),
            ),
            runtime_mutation=self._runtime_mutation(
                state,
                PlainFieldMutation(running, "ended", True),
                PlainFieldMutation(running, "pending_permission_ask_ids", set()),
                PlainFieldMutation(running, "pending_user_input_questions", {}),
                PlainFieldMutation(running, "permission_provider_ids", {}),
                PlainFieldMutation(running, "user_input_provider_ids", {}),
                PlainFieldMutation(state, "last_ended_turn_token", running.token),
                PlainFieldMutation(
                    state, "last_ended_turn_was_automatic_compaction", running.automatic_compaction
                ),
                PlainFieldMutation(state, "running_turn", None),
            ),
        )
        if self._signal is None:
            running.ended = True
            running.pending_permission_ask_ids.clear()
            running.pending_user_input_questions.clear()
            running.permission_provider_ids.clear()
            running.user_input_provider_ids.clear()
            state.last_ended_turn_token = running.token
            state.last_ended_turn_was_automatic_compaction = running.automatic_compaction
            state.running_turn = None
        self._take_in_written_rows(state, written)
        ended = written[-1]
        if not running.automatic_compaction:
            state.record = replace(
                state.record,
                latest_agent_activity_at=ended.created_at,
                latest_agent_activity_sequence=ended.sequence,
            )
        if running.compaction_confirmed:
            state.record = replace(
                state.record,
                automatically_compacted_through_sequence=(
                    state.record.latest_agent_activity_sequence
                ),
                automatic_compaction_attempted_through_sequence=(
                    state.record.latest_agent_activity_sequence
                ),
            )
        elif automatic_compaction_result is AutomaticCompactionResult.not_compacted:
            state.record = replace(
                state.record,
                automatic_compaction_attempted_through_sequence=(
                    state.record.latest_agent_activity_sequence
                ),
            )
        # The ending commit precedes clearing the live turn. Announce the runtime
        # transition only after its state and committed record agree.
        change_signal.emit()
        return ended

    # --- the child ----------------------------------------------------------------------

    async def _ensure_child(
        self,
        state: _ConversationState,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
        reasoning_effort_is_set: bool = False,
        admission_expectation: AdmissionExpectation | None = None,
        emission_check: EmissionCheck | None = None,
    ) -> BackendChild:
        """The conversation's live child, spawned and bound on first need.

        ``model`` and ``reasoning_effort`` name values a delivery is carrying, so a child
        that has to be spawned for that delivery is spawned on them rather than on the
        values it is replacing.
        """
        if state.child_is_quarantined:
            raise BackendSpawnFailed(state.record.conversation_id)
        child = state.child
        if child is not None:
            from planner.executor.remote import RemoteBackendChild

            if isinstance(child, RemoteBackendChild) and state.reserved_turn is not None:
                child.initial_commands = []
                child.prompt_accepted = False
                self._configure_initial_command_recording(state, child, state.reserved_turn)
            return child

        resolved_start = state.record.resolved_start()
        expectation = admission_expectation
        if expectation is not None:
            resolved_start = replace(
                resolved_start,
                model=expectation.launch_token,
                reasoning_effort=expectation.reasoning_effort,
            )
        elif model is not None or reasoning_effort is not None or reasoning_effort_is_set:
            resolved_start = replace(
                resolved_start,
                model=resolved_start.model if model is None else model,
                reasoning_effort=(
                    resolved_start.reasoning_effort
                    if reasoning_effort is None and not reasoning_effort_is_set
                    else reasoning_effort
                ),
            )
        if resolved_start.role_materials is not None:
            identity = dict(resolved_start.role_materials.identity_environment_variables)
            ticket_id = identity.get("PLAN_TICKET_ID")
            if ticket_id:
                from planner.files.logic.paths import ticket_files_root

                artifact_root = (ticket_files_root(self._store._db_path) / ticket_id).resolve()
                resolved_start = replace(
                    resolved_start,
                    role_materials=replace(
                        resolved_start.role_materials,
                        identity_environment_variables=tuple(
                            (key, value)
                            for key, value in (
                                resolved_start.role_materials.identity_environment_variables
                            )
                            if key != "PLAN_TICKET_FILES_DIR"
                        )
                        + (("PLAN_TICKET_FILES_DIR", str(artifact_root)),),
                    ),
                )
        factory = self._backend_child_factories[state.record.backend_key]
        sink = _CoreBackendEventSink(self, state)
        state.child_event_sink = sink
        child = factory(
            resolved_start=resolved_start,
            event_sink=sink,
            message_files=self._message_files,
        )
        from planner.executor.remote import RemoteBackendChild

        if isinstance(child, RemoteBackendChild):
            state.executor_child_reference = child.reference
            state.executor_initial_commands = ()
            state.executor_delivery_uncertain = False
            if state.initial_shutdown_task is not None and state.initial_shutdown_task.done():
                state.initial_shutdown_task = None
                state.initial_shutdown_child = None
            # Retain the original identity even while native start is unacknowledged.
            state.child = child
            if state.reserved_turn is not None:
                self._configure_initial_command_recording(state, child, state.reserved_turn)
        # The pump is running before the child is, so news the child makes while it starts
        # up — the session cursor it mints — has somewhere to go.
        self._ensure_backend_event_pump(state)
        start_reservation = state.reserved_turn
        state.local_child_retirement_proven = False
        await child.start(
            resolved_start,
            vendor_session_cursor=state.record.vendor_session_cursor,
            emission_check=emission_check,
            admission_expectation=admission_expectation,
        )
        state.child_resolved_model_id = (
            None if expectation is None else expectation.resolved_model_id
        )
        state.child_launch_context = None if expectation is None else expectation.context
        if (
            isinstance(child, RemoteBackendChild)
            and (
                self._shutting_down
                or (
                    start_reservation is not None
                    and (
                        state.reserved_turn is not start_reservation
                        or start_reservation.shutdown_owned
                    )
                )
            )
        ) or (state.reserved_turn is not None and state.reserved_turn.shutdown_owned):
            from planner.executor.remote import RemoteInitialDeliveryCancelled

            raise RemoteInitialDeliveryCancelled()
        state.child = child
        return child

    async def _discard_child(self, state: _ConversationState, child: BackendChild) -> None:
        """Stop a child and forget it, so the next message starts a fresh one.

        The new one resumes from the stored session cursor on the values the record
        holds, which is how a conversation gets back to one answer about itself.
        """
        from planner.executor.remote import RemoteBackendChild

        if isinstance(child, RemoteBackendChild):
            self._schedule_remote_retirement(state, child)
            return
        if state.child is child:
            state.child = None
        stopped = await self._stop_child(state, child)
        if stopped:
            state.child_is_quarantined = False
        else:
            state.child = child
            state.child_is_quarantined = True

    async def _stop_child(self, state: _ConversationState, child: BackendChild) -> bool:
        try:
            await child.stop()
        except Exception:
            LOGGER.exception(
                "conversation %s could not stop its backend child cleanly",
                state.record.conversation_id,
            )
            return False
        state.local_child_retirement_proven = True
        return True

    async def _cancel_child_turn(
        self, state: _ConversationState, child: BackendChild
    ) -> LifecycleFact | None:
        """Tell the child to stop its turn, and deal honestly with a cancel that failed.

        The ending is the core's and it stands either way: an agent that would not take
        the cancel does not get to keep the turn open in the record. But a cancel that did
        not reach the child means its wire did not carry it, and a child that cannot be
        told to stop cannot be trusted to be told anything — it may still be working on a
        turn this conversation considers over. So it is stopped and thrown away, and the
        next message spawns a fresh one from the session cursor. The failure gets a line of
        its own, because "the agent was interrupted" and "the agent was told to stop" have
        just stopped being the same statement.
        """
        running = state.running_turn
        attempt = (
            None
            if running is None
            else await self._begin_attempt(state, running.token, LifecycleOperation.cancel)
        )
        try:
            await child.cancel_running_turn()
            return None if attempt is None else replace(attempt, status=LifecycleStatus.accepted)
        except Exception as cancel_failure:
            LOGGER.error(
                "conversation turn cancel failed conversation_id=%s backend=%s error=%r",
                state.record.conversation_id,
                str(state.record.backend_key),
                cancel_failure,
            )
        await self._discard_child(state, child)
        return None if attempt is None else replace(attempt, status=LifecycleStatus.unknown)

    @staticmethod
    def _with_native_worker_context(
        state: _ConversationState, child: BackendChild, content: MessageContent
    ) -> MessageContent:
        """Tell native Workers where their localized skills and Ticket files live."""
        from planner.executor.configuration import NATIVE_WORKER_CONTEXT
        from planner.executor.remote import RemoteBackendChild

        identity = dict(state.record.identity_environment_variables)
        if (
            isinstance(child, RemoteBackendChild)
            and identity.get("PLAN_ACTOR") == "worker"
            and identity.get("PLAN_TICKET_ID")
        ):
            return prefix_message_content_text(content, NATIVE_WORKER_CONTEXT, "\n\n")
        return content

    async def _compose_prompt_content(
        self, state: _ConversationState, content: MessageContent
    ) -> MessageContent:
        """The message as the backend gets it: the role text rides the very first prompt.

        The role is composed here, by the core, so every backend is told what it is in the
        same way. It goes on once, on the first prompt this conversation ever delivers —
        after that the agent has it. The record keeps the sender's own message: the role is
        the conversation's, and it is stored on the conversation.

        A message that opens with words takes the role onto those words, which is what
        joining two strings always did and leaves an ordinary prompt exactly the text it
        used to be. A message that opens with a picture takes the role as a piece in front
        of it, because there is no run of words at the front to join it to.
        """
        role_text = state.record.role_text
        if role_text is None or state.has_delivered_prompt:
            return content
        if await self._persist(
            state, partial(self._store.has_delivered_prompt, state.record.conversation_id)
        ):
            state.has_delivered_prompt = True
            return content
        return prefix_message_content_text(content, role_text, ROLE_TEXT_PROMPT_SEPARATOR)

    # --- backend news -------------------------------------------------------------------

    def _ensure_backend_event_pump(self, state: _ConversationState) -> None:
        pump = state.backend_event_pump
        if pump is None or pump.done():
            state.backend_event_pump = asyncio.create_task(self._pump_backend_events(state))

    async def _pump_backend_events(self, state: _ConversationState) -> None:
        """Work through one conversation's backend news, one item at a time, in order."""
        queue = state.backend_event_queue
        while True:
            handle = await queue.get()
            state.backend_event_in_progress = True
            try:
                await handle()
            except asyncio.CancelledError:
                raise
            except BaseException:
                # Anything but this pump's own cancellation is survived. A pump that ends
                # here stops the conversation ever hearing that a turn finished, and a
                # conversation that never hears that never empties its held line again —
                # nothing re-arms the pump while its child is alive.
                LOGGER.exception(
                    "conversation %s could not take in a backend event",
                    state.record.conversation_id,
                )
            finally:
                state.backend_event_in_progress = False
                queue.task_done()

    async def _hold_for_the_live_turn(
        self, state: _ConversationState, turn_token: TurnToken
    ) -> _RunningTurn | None:
        """Wait until this token's fate is settled, then take the lock if it is the live turn.

        Returns with the conversation's lock held when the news belongs to the turn running
        now, and ``None`` — lock released — when it belongs to a turn that is over, or one
        that never started.
        """
        while True:
            reserved = state.reserved_turn
            if reserved is None or reserved.token != turn_token:
                break
            await reserved.resolved.wait()

        while True:
            for barrier in tuple(state.steer_settlements):
                await barrier.wait()
            await state.lock.acquire()
            if not state.steer_settlements:
                break
            state.lock.release()
        running = state.running_turn
        if running is None or running.token != turn_token:
            state.lock.release()
            return None
        state.last_touched_monotonic = self._monotonic_now()
        return running

    def _bind_provider_selection_report(
        self,
        state: _ConversationState,
        running: _RunningTurn,
        payload: ProviderSelectionReportedEventPayload,
    ) -> ProviderSelectionReportedEventPayload:
        basis = running.launch_validation
        if basis is None or basis.launch_token is None:
            return payload
        return replace(
            payload,
            submitted_token=basis.launch_token,
            requested_reasoning_effort=basis.selection.reasoning_effort,
            blocks_submission=False,
        )

    async def _on_provider_selection_reported(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        payload: ProviderSelectionReportedEventPayload,
        expected_sink: object | None = None,
    ) -> None:
        running = await self._hold_for_the_live_turn(state, turn_token)
        if running is None:
            return
        try:
            if expected_sink is not None and state.child_event_sink is not expected_sink:
                return
            if payload.provider != state.record.backend_key:
                return
            payload = self._bind_provider_selection_report(state, running, payload)
            await self._append_backend_event(state, turn_token, payload)
            state.child_reported_model = payload.model
        finally:
            state.lock.release()

    async def _on_token_usage_reported(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        payload: TokenUsageEventPayload,
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_backend_event(state, turn_token, payload)
        finally:
            state.lock.release()

    async def _on_context_compacted(self, state: _ConversationState, turn_token: TurnToken) -> None:
        running = await self._hold_for_the_live_turn(state, turn_token)
        if running is None:
            return
        try:
            await self._append_event(
                state,
                ContextCompactedEventPayload(),
                automatic_compaction_confirmed=True,
            )
            running.compaction_confirmed = True
        finally:
            state.lock.release()

    async def _on_agent_message_completed(
        self, state: _ConversationState, turn_token: TurnToken, content: MessageContent
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            completed_backend_text_event(content)
        finally:
            state.lock.release()

    async def _on_tool_call_started(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        tool_call_id: str,
        title: str,
        tool_kind: str,
        detail: str | None,
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_backend_event(
                state,
                turn_token,
                ToolCallStartedEventPayload(
                    tool_call_id=tool_call_id,
                    title=title,
                    tool_kind=tool_kind,
                    detail=detail,
                ),
            )
        finally:
            state.lock.release()

    async def _on_tool_call_finished(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        tool_call_id: str,
        tool_call_status: ToolCallStatus,
        detail: str | None,
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_backend_event(
                state,
                turn_token,
                ToolCallFinishedEventPayload(
                    tool_call_id=tool_call_id,
                    tool_call_status=tool_call_status,
                    detail=detail,
                ),
            )
        finally:
            state.lock.release()

    async def _on_plan_updated(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        entries: tuple[PlanEntry, ...],
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_backend_event(
                state, turn_token, PlanUpdatedEventPayload(entries=entries)
            )
        finally:
            state.lock.release()

    async def _on_permission_ask_raised(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        ask: BackendPermissionAsk,
    ) -> None:
        running = await self._hold_for_the_live_turn(state, turn_token)
        if running is None:
            return
        try:
            interpreted = interpret_permission_ask(turn_token, ask)
            identity = interpreted.lifecycle.identity
            if any(
                fact.kind is LifecycleKind.request and fact.identity == identity
                for fact in await self._store.lifecycle_facts(state.record.conversation_id)
            ):
                return
            await self._append_backend_event(
                state,
                turn_token,
                interpreted.payload,
                lifecycle=(interpreted.lifecycle,),
                runtime_mutation=self._runtime_mutation(
                    state,
                    PlainFieldMutation(
                        running,
                        "pending_permission_ask_ids",
                        running.pending_permission_ask_ids | {identity},
                    ),
                    PlainFieldMutation(
                        running,
                        "permission_provider_ids",
                        {**running.permission_provider_ids, identity: ask.ask_id},
                    ),
                ),
            )
            if self._signal is None:
                running.pending_permission_ask_ids.add(identity)
                running.permission_provider_ids[identity] = ask.ask_id
        finally:
            state.lock.release()

    async def _on_user_input_requested(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        request: BackendUserInputRequest,
    ) -> None:
        running = await self._hold_for_the_live_turn(state, turn_token)
        if running is None:
            return
        try:
            interpreted = interpret_user_input_request(turn_token, request)
            identity = interpreted.lifecycle.identity
            if any(
                fact.kind is LifecycleKind.request and fact.identity == identity
                for fact in await self._store.lifecycle_facts(state.record.conversation_id)
            ):
                return
            await self._append_backend_event(
                state,
                turn_token,
                interpreted.payload,
                lifecycle=(interpreted.lifecycle,),
                runtime_mutation=self._runtime_mutation(
                    state,
                    PlainFieldMutation(
                        running,
                        "pending_user_input_questions",
                        {**running.pending_user_input_questions, identity: request.questions},
                    ),
                    PlainFieldMutation(
                        running,
                        "user_input_provider_ids",
                        {**running.user_input_provider_ids, identity: request.request_id},
                    ),
                ),
            )
            if self._signal is None:
                running.pending_user_input_questions[identity] = request.questions
                running.user_input_provider_ids[identity] = request.request_id
        finally:
            state.lock.release()

    async def _on_user_input_failed(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        request_id: str,
        detail: str,
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_backend_event(
                state,
                turn_token,
                UserInputFailedEventPayload(
                    request_id=f"{turn_token.turn_number}:user_input:{request_id}", detail=detail
                ),
            )
        finally:
            state.lock.release()

    async def _on_turn_ended(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        running = await self._hold_for_the_live_turn(state, turn_token)
        if running is None:
            return
        if running.ending_is_the_cores:
            # The core asked for this ending and is part-way through recording it as the
            # interruption it was. The backend answering "that turn has stopped" is the
            # cancel landing, not a second thing that happened.
            state.lock.release()
            return
        recorded_at_sequence: int | None = None
        try:
            try:
                stored = await self._end_turn(state, running, ending, error_summary)
                recorded_at_sequence = None if stored is None else stored.sequence
            except Exception as ending_not_written:
                # Both of the ways a person finds out a turn ended are the record and the
                # log. Losing the row must not cost the line as well — that would be a
                # turn that ended and left no trace anywhere.
                LOGGER.error(
                    "conversation turn ending could not be recorded conversation_id=%s "
                    "backend=%s ending=%s error=%r",
                    state.record.conversation_id,
                    str(state.record.backend_key),
                    str(ending),
                    ending_not_written,
                )
            finally:
                # The turn is over whether or not its ending could be written down, and a
                # conversation whose turn is over is idle.
                self._settle_phase(state)
        finally:
            state.lock.release()
        if ending is ConversationTurnEnding.failed:
            # Not conditional on the row: a failed turn's line is the one an operator
            # reads, and it says plainly when there is no row to go and look at.
            self._log_failed_turn(state, recorded_at_sequence, error_summary, standard_error_tail)
        await self._drain_held_prompts(state)

    async def _on_vendor_session_cursor_rebound(
        self, state: _ConversationState, vendor_session_cursor: str
    ) -> None:
        await self._persist(
            state,
            partial(
                self._store.update_vendor_session_cursor,
                state.record.conversation_id,
                vendor_session_cursor,
            ),
        )
        state.record = replace(state.record, vendor_session_cursor=vendor_session_cursor)

    async def _on_composer_catalog_reported(
        self,
        state: _ConversationState,
        composer_catalog: tuple[ComposerCatalogEntry, ...],
    ) -> None:
        """The whole menu, as the backend has it now, put where the last one was.

        It goes onto the conversation and nowhere near its event record. The catalog is a
        current capability, not something that happened, so no transcript row shows it.
        """
        await self._persist(
            state,
            partial(
                self._store.replace_composer_catalog, state.record.conversation_id, composer_catalog
            ),
        )
        state.record = replace(state.record, composer_catalog=composer_catalog)

    def _log_failed_turn(
        self,
        state: _ConversationState,
        sequence: int | None,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        """One line for a turn that failed, with everything needed to go and look.

        The turn's failure is normally a row too; this is the conversation system's own
        record of it, in the place an operator reads. ``sequence`` says where to find that
        row, and is ``None`` when the ending could not be written — the failure still gets
        its line, saying there is nothing to look up. Both free-text pieces go in quoted,
        so a multi-line stderr tail stays one line.
        """
        LOGGER.error(
            "conversation turn failed conversation_id=%s backend=%s sequence=%s error=%r "
            "standard_error_tail=%r",
            state.record.conversation_id,
            str(state.record.backend_key),
            sequence,
            error_summary,
            _standard_error_tail(standard_error_tail),
        )

    # --- the idle janitor ---------------------------------------------------------------

    async def _sweep_idle_children_forever(self) -> None:
        while True:
            await asyncio.sleep(self._idle_child_sweep_interval_seconds)
            try:
                await self._sweep_idle_children()
            except Exception:
                LOGGER.exception("the idle conversation child sweep failed")

    async def _sweep_idle_children(self) -> None:
        """Compact due conversations, then stop children that sat idle long enough.

        A conversation whose lock is held is in the middle of something and is not idle, so
        it is left for the next sweep rather than waited on.

        Remote cleanup registers quarantine under the lock, then awaits retirement
        outside it. The fence prevents another child before confirmation. A failed
        owned attempt requires explicit shutdown to retry. Local stops retain their
        existing lock ownership.
        """
        due_ids = await self._store.conversations_due_for_automatic_compaction(
            due_at_or_before=(int(self._unix_time_now()) - AUTOMATIC_COMPACTION_AFTER_SECONDS),
            activity_after=(int(self._unix_time_now()) - AUTOMATIC_COMPACTION_BEFORE_SECONDS),
        )
        for conversation_id in due_ids:
            state = await self._conversation_state(conversation_id)
            if state is None or state.lock.locked():
                continue
            reservation: _ReservedTurn | None = None
            async with state.lock:
                if state.phase is _ConversationPhase.idle and not state.held_prompts:
                    if self._automatic_compaction_is_due(state):
                        reservation = await self._reserve_turn(state, automatic_compaction=True)
            if reservation is not None:
                await self._deliver_automatic_compaction(state, reservation)

        async with self._conversations_lock:
            states = list(self._conversations.values())
        recovered_quarantines: list[_ConversationState] = []
        from planner.executor.remote import RemoteBackendChild

        for state in states:
            if state.child is None or state.lock.locked():
                continue
            cleanup: asyncio.Task[bool] | None = None
            async with state.lock:
                child = state.child
                if child is None or state.phase is not _ConversationPhase.idle:
                    continue
                was_quarantined = state.child_is_quarantined
                if isinstance(child, RemoteBackendChild):
                    # Only an explicit operation retries failed remote cleanup.
                    if was_quarantined and (
                        state.initial_shutdown_task is not None
                        or any(
                            command.operation is ChildOperation.stop
                            for command in state.executor_initial_commands
                        )
                    ):
                        continue
                    if not was_quarantined and (
                        self._monotonic_now() - state.last_touched_monotonic
                        < self._idle_child_stop_after_seconds
                    ):
                        continue
                    cleanup = self._schedule_remote_retirement(state, child)
                else:
                    if not was_quarantined:
                        idle_for = self._monotonic_now() - state.last_touched_monotonic
                        if idle_for < self._idle_child_stop_after_seconds:
                            continue
                    state.child = None
                    if not await self._stop_child(state, child):
                        state.child = child
                        state.child_is_quarantined = True
                        continue
                    state.child_is_quarantined = False
                    if was_quarantined and state.held_prompts:
                        recovered_quarantines.append(state)
            if cleanup is not None and await asyncio.shield(cleanup) and state.held_prompts:
                recovered_quarantines.append(state)
        for state in recovered_quarantines:
            await self._drain_held_prompts(state)

    @staticmethod
    def _executor_turn_metadata(
        state: _ConversationState, running: _RunningTurn
    ) -> tuple[str, str, str] | None:
        from planner.executor.codec import encode_value
        from planner.executor.remote import RemoteBackendChild

        child = state.child
        reference = (
            child.reference
            if isinstance(child, RemoteBackendChild)
            else state.executor_child_reference
        )
        if reference is None:
            return None
        runtime = {
            "launch_validation": encode_value(running.launch_validation),
            "automatic_compaction": running.automatic_compaction,
            "compaction_confirmed": running.compaction_confirmed,
            "automatic_notice": running.automatic_notice,
            "ended": running.ended,
            "callbacks_revoked": running.callbacks_revoked,
            "permission_provider_ids": running.permission_provider_ids,
            "user_input_provider_ids": running.user_input_provider_ids,
            "pending_permission_ask_ids": list(running.pending_permission_ask_ids),
            "pending_user_input_questions": encode_value(running.pending_user_input_questions),
            "prompt_senders": encode_value(tuple(running.prompt_senders)),
            "explicit_reply_recipients": encode_value(tuple(running.explicit_reply_recipients)),
            "initial_commands": encode_value(state.executor_initial_commands),
            "delivery_uncertain": state.executor_delivery_uncertain,
        }
        return (
            json.dumps(encode_value(reference)),
            json.dumps(encode_value(running.token)),
            json.dumps(runtime),
        )

    async def _restore_executor_turn(self, state: _ConversationState) -> None:
        metadata = await self._store.read_executor_turn(state.record.conversation_id)
        if metadata is not None:
            self._restore_executor_turn_metadata(state, metadata)

    def _restore_executor_turn_metadata(
        self, state: _ConversationState, metadata: tuple[str, str, str]
    ) -> None:
        from pydantic import TypeAdapter

        from planner.executor.contracts import ChildReference

        child = TypeAdapter(ChildReference).validate_json(metadata[0])
        token = TypeAdapter(TurnToken).validate_json(metadata[1])
        runtime = json.loads(metadata[2])
        state.executor_initial_commands = tuple(
            TypeAdapter(ChildCommand).validate_python(raw)
            for raw in runtime.get("initial_commands", ())
        )
        state.executor_delivery_uncertain = runtime.get("delivery_uncertain", False)
        state.executor_child_reference = None if runtime.get("source_retired") else child
        state.next_turn_number = max(state.next_turn_number, token.turn_number + 1)
        state.has_delivered_prompt = True
        if runtime.get("source_retired"):
            state.last_ended_turn_token = token
            state.last_ended_turn_was_automatic_compaction = runtime.get(
                "automatic_compaction", False
            )
            return
        # Startup has ended the turn and closed its callbacks in one transaction.
        # Retain only stop evidence, never a reservation or running callback state.
        state.last_ended_turn_token = token
        state.last_ended_turn_was_automatic_compaction = runtime.get("automatic_compaction", False)
        state.child_is_quarantined = True

    async def interrupt_replaced_executor_runs(self, current_incarnation: str) -> None:
        """A new service process proves that children of the old service are gone.

        launchd's default process-group cleanup is the termination evidence here,
        not an empty inventory or a refusal to stop an obsolete child identity.
        """
        references = tuple(
            state.executor_child_reference
            for state in self._conversations.values()
            if state.executor_child_reference is not None
            and state.executor_child_reference.executor_incarnation != current_incarnation
        )
        for reference in references:
            state = self._conversations[reference.conversation_id]
            await self._own_settlement(
                state, self._interrupt_replaced_executor_child(state, reference)
            )

    async def _interrupt_replaced_executor_child(
        self, state: _ConversationState, reference: ChildReference
    ) -> None:
        from pydantic import TypeAdapter

        from planner.executor.remote import RemoteBackendChild

        # Pending command futures have already become uncertain. Let their normal
        # delivery writers finish; replacing the process cannot prove non-delivery.
        await self._acquire_settled(state)
        try:
            if state.executor_child_reference != reference:
                return
            if state.running_turn is not None:
                await self._end_turn(
                    state, state.running_turn, ConversationTurnEnding.interrupted, None
                )
            metadata = await self._store.read_executor_turn(reference.conversation_id)
            if (
                metadata is None
                or TypeAdapter(ChildReference).validate_json(metadata[0]) != reference
            ):
                raise ValueError("Replaced executor lacks exact retained child metadata")
            runtime = json.loads(metadata[2])
            runtime.update(source_retired=True, ended=True, callbacks_revoked=True)
            retired = await self._persist(
                state,
                partial(
                    self._store.transition_lifecycle,
                    reference.conversation_id,
                    (),
                    executor_turn=(metadata[0], metadata[1], json.dumps(runtime)),
                    retired_child=reference,
                ),
            )
            if not retired:
                raise ValueError("Replaced executor child retirement was not committed")
            if isinstance(state.child, RemoteBackendChild):
                state.child.retire_local_reference()
            state.child = None
            state.executor_child_reference = None
            state.child_is_quarantined = False
            if state.reserved_turn is not None:
                state.last_ended_turn_token = state.reserved_turn.token
                state.reserved_turn.resolved.set()
                state.reserved_turn = None
            self._settle_phase(state)
        finally:
            state.lock.release()

    async def stop_interrupted_remote_child(
        self, reference: ChildReference, child: BackendChild
    ) -> None:
        """Inventory can retire a pre-restart child, never resume its turn."""
        state = await self._conversation_state(reference.conversation_id)
        if state is None:
            raise ValueError("remote inventory names an unknown conversation")
        async with state.lock:
            if (
                state.executor_child_reference != reference
                or not state.child_is_quarantined
                or state.last_ended_turn_token is None
            ):
                raise ValueError("remote inventory lacks an exact interrupted child")
            pending = state.initial_shutdown_task
            if pending is not None and not pending.done():
                if getattr(state.initial_shutdown_child, "reference", None) != reference:
                    raise ValueError("Pending retirement belongs to another child")
                return
            state.child = child
            self._schedule_remote_retirement(state, child)

    async def receive_remote_answer_application(
        self,
        application: AnswerApplication,
        envelope: QueuedEnvelope,
        acceptor: ExecutorEffectAcceptor,
        record_application: Callable[[sqlite3.Connection, AnswerApplication], None],
    ) -> AcceptanceReceipt:
        """Commit native application, lifecycle, answer row, and receipt together."""

        state = await self._conversation_state(application.ask.child.conversation_id)
        if state is None:
            raise ValueError("answer application names unknown conversation")
        return await self._own_settlement(
            state,
            self._receive_remote_answer_application_owned(
                state, application, envelope, acceptor, record_application
            ),
        )

    async def _receive_remote_answer_application_owned(
        self,
        state: _ConversationState,
        application: AnswerApplication,
        envelope: QueuedEnvelope,
        acceptor: ExecutorEffectAcceptor,
        record_application: Callable[[sqlite3.Connection, AnswerApplication], None],
    ) -> AcceptanceReceipt:
        from pydantic import TypeAdapter

        from planner.executor.contracts import AskKind, ReceiptDisposition

        written: tuple[StoredConversationEvent, ...] = ()
        fresh = False
        async with state.lock:
            ask = application.ask
            user_input = ask.kind is AskKind.user_input
            operation = (
                LifecycleOperation.user_input if user_input else LifecycleOperation.permission
            )
            public_id = self._store.executor_public_request_id(ask)
            if public_id is None:
                raise ValueError("answer application lacks an original public request identity")
            running = state.running_turn
            predicted = None
            if running is not None and running.token == ask.turn:
                predicted = replace(
                    running,
                    pending_permission_ask_ids=(
                        set(running.pending_permission_ask_ids)
                        if user_input
                        else running.pending_permission_ask_ids - {public_id}
                    ),
                    pending_user_input_questions={
                        key: value
                        for key, value in running.pending_user_input_questions.items()
                        if not user_input or key != public_id
                    },
                )
            mutation = (
                self._runtime_mutation(state, PlainFieldMutation(state, "running_turn", predicted))
                if predicted is not None
                else None
            )

            conversation_id = state.record.conversation_id
            metadata = (
                self._executor_turn_metadata(state, predicted) if predicted is not None else None
            )

            def write(
                connection: sqlite3.Connection, accepted: QueuedEnvelope
            ) -> tuple[JsonObject, tuple[StoredConversationEvent, ...]]:
                written: tuple[StoredConversationEvent, ...] = ()
                del accepted
                answer = connection.execute(
                    "SELECT option_id,user_input_answers_json,applied_at_mac_ms "
                    "FROM executor_asks WHERE answer_id=?",
                    (application.answer_id,),
                ).fetchone()
                if answer is None:
                    raise ValueError("answer application lacks authoritative choice")
                first_application = answer[2] is None
                record_application(connection, application)
                if first_application:
                    payload: ConversationEventPayload = (
                        UserInputAnsweredEventPayload(
                            request_id=public_id,
                            answers=TypeAdapter(tuple[UserInputAnswer, ...]).validate_json(
                                str(answer[1])
                            ),
                        )
                        if user_input
                        else PermissionAnsweredEventPayload(
                            ask_id=public_id, option_id=str(answer[0])
                        )
                    )
                    previous = next(
                        (
                            fact
                            for fact in self._store.lifecycle_facts_in_transaction(
                                connection, conversation_id
                            )
                            if fact.kind is LifecycleKind.request and fact.identity == public_id
                        ),
                        None,
                    )
                    lifecycle = (
                        ()
                        if previous is not None and previous.status is LifecycleStatus.unavailable
                        else (
                            LifecycleFact(
                                LifecycleKind.request,
                                public_id,
                                ask.turn.turn_number,
                                operation,
                                LifecycleStatus.answered,
                                ask.ask_id,
                            ),
                        )
                    )
                    written = self._store.append_in_transaction(
                        connection,
                        conversation_id,
                        (payload,),
                        lifecycle=lifecycle,
                    )
                if metadata is not None:
                    self._store.save_executor_turn_in_transaction(
                        connection, conversation_id, metadata
                    )
                return {"recorded": True}, written

            result = await self._persist(
                state,
                partial(self._store.accept_executor_effect, envelope, acceptor, write, mutation),
                runtime_mutation=mutation,
            )
            receipt, written, fresh = result.receipt, result.written, result.fresh
            if fresh and receipt.disposition is ReceiptDisposition.accepted:
                if self._signal is None and predicted is not None:
                    state.running_turn = predicted
                self._take_in_written_rows(state, written)
            return receipt

    async def receive_remote_live_backend_event(self, event: BackendEvent) -> None:
        from planner.executor.codec import decode_event_arguments
        from planner.executor.remote import RemoteBackendChild

        state = await self._conversation_state(event.child.conversation_id)
        if state is None or event.turn is None:
            return
        child = state.child
        if not isinstance(child, RemoteBackendChild) or child.reference != event.child:
            return
        sink = state.child_event_sink
        if not isinstance(sink, _CoreBackendEventSink):
            return
        args = decode_event_arguments(event)
        if isinstance(args, AgentMessageDeltaArguments):
            await sink.agent_message_delta(event.turn, args.text_delta)
        elif isinstance(args, ModelThinkingHappenedArguments):
            await sink.model_thinking_happened(event.turn)
        elif isinstance(args, ToolCallProgressArguments):
            await sink.tool_call_progress(
                event.turn, tool_call_id=args.tool_call_id, detail=args.detail
            )

    async def receive_remote_backend_event(
        self,
        event: BackendEvent,
        envelope: QueuedEnvelope,
        acceptor: ExecutorEffectAcceptor,
    ) -> AcceptanceReceipt:
        """Apply native events through lifecycle publication and executor acceptance."""

        state = await self._conversation_state(event.child.conversation_id)
        if state is None:
            raise ValueError("remote event names an unknown conversation")
        return await self._own_settlement(
            state,
            self._receive_remote_backend_event_owned(state, event, envelope, acceptor),
        )

    async def _receive_remote_backend_event_owned(
        self,
        state: _ConversationState,
        event: BackendEvent,
        envelope: QueuedEnvelope,
        acceptor: ExecutorEffectAcceptor,
    ) -> AcceptanceReceipt:
        from planner.executor.codec import decode_event_arguments
        from planner.executor.contracts import BackendEventMethod, ReceiptDisposition

        reservation = state.reserved_turn
        if reservation is not None and event.turn == reservation.token:
            await reservation.resolved.wait()
        args = decode_event_arguments(event)
        written: tuple[StoredConversationEvent, ...] = ()
        fresh = False
        method, token = event.method, event.turn
        should_drain = False
        async with state.lock:
            running = state.running_turn
            live = token is None or (
                running is not None
                and running.token == token
                and not running.ended
                and not running.ending_is_the_cores
            )
            if (
                running is not None
                and running.callbacks_revoked
                and method
                in (
                    BackendEventMethod.permission_ask_raised,
                    BackendEventMethod.user_input_requested,
                )
            ):
                live = False
            payloads: tuple[ConversationEventPayload, ...] = ()
            lifecycle: tuple[LifecycleFact, ...] = ()
            compacted = live and isinstance(args, ContextCompactedArguments)
            compaction_result = None
            failure_notice = False
            predicted = (
                replace(
                    running,
                    pending_permission_ask_ids=set(running.pending_permission_ask_ids),
                    pending_user_input_questions=dict(running.pending_user_input_questions),
                    permission_provider_ids=dict(running.permission_provider_ids),
                    user_input_provider_ids=dict(running.user_input_provider_ids),
                )
                if live and running is not None
                else None
            )
            reported_payload = None
            if live:
                if isinstance(args, ToolCallStartedArguments):
                    payloads = (
                        ToolCallStartedEventPayload(
                            tool_call_id=args.tool_call_id,
                            title=args.title,
                            tool_kind=args.tool_kind,
                            detail=args.detail,
                        ),
                    )
                elif isinstance(args, ToolCallFinishedArguments):
                    payloads = (
                        ToolCallFinishedEventPayload(
                            tool_call_id=args.tool_call_id,
                            tool_call_status=args.tool_call_status,
                            detail=args.detail,
                        ),
                    )
                elif isinstance(args, PlanUpdatedArguments):
                    payloads = (PlanUpdatedEventPayload(entries=args.entries),)
                elif (
                    isinstance(args, ProviderSelectionReportedArguments)
                    and token is not None
                    and running is not None
                    and args.provider == state.record.backend_key
                ):
                    reported_payload = self._bind_provider_selection_report(
                        state,
                        running,
                        ProviderSelectionReportedEventPayload(
                            args.provider,
                            args.source_kind,
                            args.model,
                            args.reasoning_effort,
                            token.turn_number,
                            args.child_lifetime,
                        ),
                    )
                    payloads = (reported_payload,)
                elif isinstance(args, TokenUsageReportedArguments):
                    payloads = (
                        TokenUsageEventPayload(
                            input_tokens=args.input_tokens,
                            output_tokens=args.output_tokens,
                            cached_input_tokens=args.cached_input_tokens,
                            cost_usd=args.cost_usd,
                        ),
                    )
                elif compacted:
                    payloads = (ContextCompactedEventPayload(),)
                    if predicted is not None:
                        predicted.compaction_confirmed = True
                elif isinstance(args, PermissionAskRaisedArguments) and token is not None:
                    ask = args.ask
                    interpreted_request = interpret_permission_ask(token, ask)
                    identity = interpreted_request.lifecycle.identity
                    payloads = (interpreted_request.payload,)
                    lifecycle = (interpreted_request.lifecycle,)
                    if predicted is not None:
                        predicted.pending_permission_ask_ids.add(identity)
                        predicted.permission_provider_ids[identity] = ask.ask_id
                elif isinstance(args, UserInputRequestedArguments) and token is not None:
                    request = args.request
                    interpreted_request = interpret_user_input_request(token, request)
                    identity = interpreted_request.lifecycle.identity
                    payloads = (interpreted_request.payload,)
                    lifecycle = (interpreted_request.lifecycle,)
                    if predicted is not None:
                        predicted.pending_user_input_questions[identity] = request.questions
                        predicted.user_input_provider_ids[identity] = request.request_id
                elif isinstance(args, UserInputFailedArguments) and token is not None:
                    payloads = (
                        UserInputFailedEventPayload(
                            request_id=f"{token.turn_number}:user_input:{args.request_id}",
                            detail=args.detail,
                        ),
                    )
                elif isinstance(args, TurnEndedArguments) and running is not None:
                    interpreted_ending = interpret_turn_ending(
                        _ending_context(running),
                        args.ending,
                        args.error_summary,
                        await self._store.lifecycle_facts(state.record.conversation_id),
                    )
                    payloads = interpreted_ending.payloads
                    lifecycle = interpreted_ending.lifecycle
                    compaction_result = interpreted_ending.compaction_result
                    failure_notice = interpreted_ending.failure_notice
                    if predicted is not None:
                        predicted.ended = True
                        predicted.pending_permission_ask_ids.clear()
                        predicted.pending_user_input_questions.clear()
                        predicted.permission_provider_ids.clear()
                        predicted.user_input_provider_ids.clear()

            operations: list[PlainFieldMutation] = []
            if reported_payload is not None:
                operations.append(
                    PlainFieldMutation(state, "child_reported_model", reported_payload.model)
                )
            if predicted is not None:
                operations.append(
                    PlainFieldMutation(
                        state, "running_turn", None if predicted.ended else predicted
                    )
                )
                if predicted.ended:
                    operations.extend(
                        (
                            PlainFieldMutation(state, "last_ended_turn_token", predicted.token),
                            PlainFieldMutation(
                                state,
                                "last_ended_turn_was_automatic_compaction",
                                predicted.automatic_compaction,
                            ),
                        )
                    )
            mutation = self._runtime_mutation(state, *operations)

            conversation_id = state.record.conversation_id
            metadata = (
                self._executor_turn_metadata(state, predicted) if predicted is not None else None
            )
            agent_activity = (
                bool(payloads)
                and not compacted
                and running is not None
                and not running.automatic_compaction
            )
            compaction_confirmed = compacted or (
                live
                and isinstance(args, TurnEndedArguments)
                and running is not None
                and running.compaction_confirmed
            )

            def write(
                conn: sqlite3.Connection, accepted: QueuedEnvelope
            ) -> tuple[JsonObject, tuple[StoredConversationEvent, ...]]:
                del accepted
                written = self._store.append_in_transaction(
                    conn,
                    conversation_id,
                    payloads,
                    lifecycle=lifecycle,
                    agent_activity=agent_activity,
                    automatic_compaction_confirmed=compaction_confirmed,
                    automatic_compaction_result=compaction_result,
                    create_failure_notice=failure_notice,
                )
                if isinstance(args, VendorSessionCursorReboundArguments):
                    self._store.update_remote_identity_in_transaction(
                        conn,
                        conversation_id,
                        vendor_session_cursor=args.vendor_session_cursor,
                    )
                elif isinstance(args, ComposerCatalogReportedArguments):
                    self._store.update_remote_identity_in_transaction(
                        conn,
                        conversation_id,
                        composer_catalog=args.composer_catalog,
                    )
                if metadata is not None:
                    self._store.save_executor_turn_in_transaction(conn, conversation_id, metadata)
                return {"applied": live}, written

            result = await self._persist(
                state,
                partial(self._store.accept_executor_effect, envelope, acceptor, write, mutation),
                runtime_mutation=mutation,
            )
            receipt, written, fresh = result.receipt, result.written, result.fresh
            if fresh and receipt.disposition is ReceiptDisposition.accepted:
                if self._signal is None:
                    for operation in operations:
                        setattr(operation.target, operation.attribute, operation.value)
                self._take_in_written_rows(state, written)
                if (
                    written
                    and running is not None
                    and not running.automatic_compaction
                    and not compacted
                ):
                    last = written[-1]
                    state.record = replace(
                        state.record,
                        latest_agent_activity_at=last.created_at,
                        latest_agent_activity_sequence=last.sequence,
                    )
                if compacted:
                    state.record = replace(
                        state.record,
                        automatically_compacted_through_sequence=state.record.latest_agent_activity_sequence,
                        automatic_compaction_attempted_through_sequence=state.record.latest_agent_activity_sequence,
                    )
                elif compaction_result is AutomaticCompactionResult.not_compacted:
                    state.record = replace(
                        state.record,
                        automatic_compaction_attempted_through_sequence=state.record.latest_agent_activity_sequence,
                    )
                if isinstance(args, VendorSessionCursorReboundArguments):
                    state.record = replace(
                        state.record, vendor_session_cursor=args.vendor_session_cursor
                    )
                elif isinstance(args, ComposerCatalogReportedArguments):
                    state.record = replace(state.record, composer_catalog=args.composer_catalog)
                if predicted is not None and predicted.ended:
                    self._settle_phase(state)
                    should_drain = True
                if live and token is not None:
                    if isinstance(args, AgentMessageDeltaArguments):
                        self._publish_live_tail_frame(
                            state, token, AgentMessageDeltaFrame(text_delta=args.text_delta)
                        )
                    elif isinstance(args, ToolCallProgressArguments):
                        self._publish_live_tail_frame(
                            state,
                            token,
                            ToolCallProgressFrame(
                                tool_call_id=args.tool_call_id, detail=args.detail
                            ),
                        )
                    elif isinstance(args, ModelThinkingHappenedArguments):
                        self._publish_model_thinking(state, token)
                    elif isinstance(args, AgentMessageCompletedArguments):
                        completed_backend_text_event(args.content)
        if should_drain:
            asyncio.create_task(self._drain_held_prompts(state))
        return receipt

    # --- shared internals ---------------------------------------------------------------

    def require_runtime_ownership(self) -> None:
        """Authorize publication using the reconciled runtime's exclusive live lease."""
        self._require_runtime_ownership()

    def _require_runtime_ownership(self) -> None:
        if self._shutting_down or self._runtime_ownership.closed:
            raise RuntimeError("the conversation runtime is shut down")

    def _live_conversation_state(self, conversation_id: str) -> _ConversationState | None:
        """Read existing runtime state without record I/O or adoption."""
        self._require_runtime_ownership()
        return self._conversations.get(conversation_id)

    async def _conversation_state(self, conversation_id: str) -> _ConversationState | None:
        """This process's state for a conversation, read from its row the first time.

        A conversation started before this process was is picked up here with no child and
        nothing running, which is the honest answer after a restart: the record is all
        there, and the next message resumes the session.
        """
        self._require_runtime_ownership()
        async with self._conversations_lock:
            state = self._conversations.get(conversation_id)
            if state is not None:
                return state
            record = await self._store.read_conversation(conversation_id)
            if record is None:
                return None
            state = _ConversationState(record=record)
            await self._restore_executor_turn(state)
            with nullcontext() if self._signal is None else self._signal.runtime_registry():
                self._conversations[conversation_id] = state
            return state

    async def _append_event(
        self,
        state: _ConversationState,
        payload: ConversationEventPayload,
        *,
        agent_activity: bool = False,
        automatic_compaction_confirmed: bool = False,
        owner_read_through_sequence: int | None = None,
        executor_turn: tuple[str, str, str] | None = None,
        lifecycle: tuple[LifecycleFact, ...] = (),
        runtime_mutation: SignalRuntimeMutation | None = None,
        native_message_operation: NativeMessageOperation | None = None,
        native_message_admission: NativeMessageAdmission | None = None,
    ) -> StoredConversationEvent:
        if (
            isinstance(payload, PromptDeliveryRefusedEventPayload)
            and payload.recovery_references
            and payload.refusal_reason is PromptDeliveryRefusalReason.write_to_backend_failed
        ):
            # Unknown recovery admission uses the existing factual uncertainty row.
            # The public delivery fate retains its established refusal mapping.
            payload = PromptDeliveryUncertainEventPayload(
                content=payload.content,
                sender_label=payload.sender_label,
                mode=payload.mode,
                sender_message_id=payload.sender_message_id,
                sent_at_unix_milliseconds=payload.sent_at_unix_milliseconds,
                sender=payload.sender,
                recipient=payload.recipient,
                recovery_references=payload.recovery_references,
            )
        running = state.running_turn
        if executor_turn is None and running is not None:
            predicted = running
            if isinstance(payload, MessageToOwnerEventPayload):
                predicted = replace(
                    running,
                    explicit_reply_recipients=running.explicit_reply_recipients
                    | {payload.recipient},
                )
            executor_turn = self._executor_turn_metadata(state, predicted)
        stored = await self._persist(
            state,
            partial(
                self._store.append_event,
                state.record.conversation_id,
                payload,
                native_message_operation=native_message_operation,
                native_message_admission=native_message_admission,
                lifecycle=lifecycle,
                agent_activity=agent_activity,
                automatic_compaction_confirmed=automatic_compaction_confirmed,
                owner_read_through_sequence=owner_read_through_sequence,
                executor_turn=executor_turn,
            ),
            runtime_mutation=runtime_mutation,
        )
        self._take_in_written_rows(state, (stored,))
        if agent_activity:
            state.record = replace(
                state.record,
                latest_agent_activity_at=stored.created_at,
                latest_agent_activity_sequence=stored.sequence,
            )
        if automatic_compaction_confirmed:
            state.record = replace(
                state.record,
                automatically_compacted_through_sequence=(
                    state.record.latest_agent_activity_sequence
                ),
                automatic_compaction_attempted_through_sequence=(
                    state.record.latest_agent_activity_sequence
                ),
            )
        if owner_read_through_sequence is not None:
            state.record = replace(
                state.record,
                owner_read_through_sequence=max(
                    state.record.owner_read_through_sequence,
                    owner_read_through_sequence,
                ),
            )
        return stored

    async def _append_backend_event(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        payload: ConversationEventPayload,
        *,
        lifecycle: tuple[LifecycleFact, ...] = (),
        runtime_mutation: SignalRuntimeMutation | None = None,
    ) -> StoredConversationEvent:
        """Append durable backend output and move the ordinary activity watermark."""
        return await self._append_event(
            state,
            payload,
            lifecycle=lifecycle,
            runtime_mutation=runtime_mutation,
            agent_activity=not self._is_automatic_compaction_turn(state, turn_token),
        )

    @staticmethod
    def _is_automatic_compaction_turn(state: _ConversationState, turn_token: TurnToken) -> bool:
        running = state.running_turn
        if running is not None and running.token == turn_token:
            return running.automatic_compaction
        return (
            state.last_ended_turn_token == turn_token
            and state.last_ended_turn_was_automatic_compaction
        )

    def _take_in_written_rows(
        self, state: _ConversationState, written: tuple[StoredConversationEvent, ...]
    ) -> None:
        """Move the conversation on to what has just been written, and show it.

        Rows are shown only once they are committed, so a watcher never sees a row that is
        not in the record — which is what lets a reader replay and then carry straight on.
        """
        if not written:
            return
        state.record = replace(state.record, latest_sequence=written[-1].sequence)
        if self._live_tail is not None:
            for stored in written:
                try:
                    self._live_tail.publish_event(stored)
                except Exception:
                    LOGGER.exception(
                        "conversation %s could not publish committed event %s",
                        state.record.conversation_id,
                        stored.sequence,
                    )

    def _publish_live_tail_frame(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        frame: ConversationLiveTailFrame,
    ) -> None:
        """Show something that has not finished arriving. Never waits, never stores.

        A frame from a turn this conversation has moved on from is dropped rather than
        shown. Rows already answer for themselves — a stale one is refused by its token
        before it is written — and a frame that skipped that check would put a dead turn's
        half-finished text into the tail of the turn running now.

        The check takes no lock: it reads what the conversation is on at this instant,
        which is all a frame with no ordering obligations needs, and an adapter reporting
        its stream must never be made to wait behind anything.
        """
        if self._live_tail is None or not self._names_a_turn_to_show(state, turn_token):
            return
        self._live_tail.publish_frame(state.record.conversation_id, frame)

    def _publish_held_prompts_changed(self, state: _ConversationState) -> None:
        """Tell open readers that the process-owned queue snapshot changed."""
        if self._live_tail is None:
            return
        self._live_tail.publish_frame(state.record.conversation_id, HeldPromptsChangedFrame())

    def _publish_model_thinking(self, state: _ConversationState, turn_token: TurnToken) -> None:
        """Say the model is thinking, at most so often, and never say what it thought.

        The turn is checked before the rate is, so that a dead turn's reasoning cannot use
        up the moment a live turn was about to speak in. After that the first pulse goes
        out immediately — being prompt is the whole point of it — and the rest of the burst
        is dropped until the interval is up.
        """
        if self._live_tail is None or not self._names_a_turn_to_show(state, turn_token):
            return
        now = self._monotonic_now()
        if now - state.model_thinking_shown_at_monotonic < MODEL_THINKING_PULSE_INTERVAL_SECONDS:
            return
        state.model_thinking_shown_at_monotonic = now
        self._live_tail.publish_frame(state.record.conversation_id, ModelThinkingFrame())

    def _names_a_turn_to_show(self, state: _ConversationState, turn_token: TurnToken) -> bool:
        """Whether this token is the turn a watcher should be seeing text from.

        The turn that is running, and also the one being started: between reserving a turn
        and its prompt row being written, the text is already on the wire and an agent that
        answers that fast is answering the newest turn, not a dead one. What is refused is
        a token this conversation has left behind.
        """
        if self._shutting_down or self._runtime_ownership.closed:
            return False
        running = state.running_turn
        if running is not None and running.token == turn_token:
            return True
        return False

    def _settle_phase(self, state: _ConversationState) -> None:
        """Put the conversation into whichever resting phase its facts say it is in.

        Used wherever an ending finishes, including when it finishes badly: a phase left
        part-way through is a conversation nothing can ever take again, so the phase is
        put back to what is actually true rather than to what was expected.
        """
        self._set_phase(
            state,
            (_ConversationPhase.idle if state.running_turn is None else _ConversationPhase.running),
        )

    def _set_phase(self, state: _ConversationState, phase: _ConversationPhase) -> None:
        state.phase = phase
        if phase in (_ConversationPhase.idle, _ConversationPhase.running):
            state.phase_settled.set()
        else:
            state.phase_settled.clear()

    async def _acquire_settled(self, state: _ConversationState) -> None:
        """Take the lock at a moment when nobody is part-way through anything.

        Callers that kill or replace the running turn need the conversation to hold still:
        a send-now cannot kill an incumbent that is still being written, and an interrupt
        cannot stop a turn that has not started yet. Both wait for a resting phase.
        """
        while True:
            for barrier in tuple(state.steer_settlements):
                await barrier.wait()
            await state.lock.acquire()
            if state.steer_settlements:
                state.lock.release()
                continue
            if state.phase in (_ConversationPhase.idle, _ConversationPhase.running):
                return
            state.lock.release()
            await state.phase_settled.wait()


class _CoreBackendEventSink:
    """What an adapter reports its backend's news to.

    Every call hands the news to the conversation's queue and returns, so an adapter's
    reading of its wire is never held up by what the core does about it. The queue is
    worked through in order, which is why an adapter may report freely and still trust that
    a tool call it reported before a message is recorded before it.
    """

    def __init__(self, system: SqliteProcessConversationSystem, state: _ConversationState) -> None:
        self._system = system
        self._state = state

    async def _publish_live_after_admission(
        self,
        turn_token: TurnToken,
        frame: ConversationLiveTailFrame | None,
    ) -> None:
        running = await self._system._hold_for_the_live_turn(self._state, turn_token)
        if running is None:
            return
        try:
            if self._state.child_event_sink is not self:
                return
            if frame is None:
                self._system._publish_model_thinking(self._state, turn_token)
            else:
                self._system._publish_live_tail_frame(self._state, turn_token, frame)
        finally:
            self._state.lock.release()

    async def agent_message_delta(self, turn_token: TurnToken, text_delta: str) -> None:
        frame = AgentMessageDeltaFrame(text_delta=text_delta)
        if self._state.reserved_turn is not None:
            self._enqueue(partial(self._publish_live_after_admission, turn_token, frame))
        else:
            self._system._publish_live_tail_frame(self._state, turn_token, frame)

    async def model_thinking_happened(self, turn_token: TurnToken) -> None:
        if self._state.reserved_turn is not None:
            self._enqueue(partial(self._publish_live_after_admission, turn_token, None))
        else:
            self._system._publish_model_thinking(self._state, turn_token)

    async def agent_message_completed(self, turn_token: TurnToken, content: MessageContent) -> None:
        self._enqueue(
            partial(
                self._system._on_agent_message_completed,
                self._state,
                turn_token,
                content,
            )
        )

    async def provider_selection_reported(
        self,
        turn_token: TurnToken,
        *,
        provider: ConversationBackendKey,
        source_kind: str,
        model: str,
        child_lifetime: str,
        reasoning_effort: str | None = None,
    ) -> None:
        async def record() -> None:
            if self._state.child_event_sink is not self:
                return
            await self._system._on_provider_selection_reported(
                self._state,
                turn_token,
                ProviderSelectionReportedEventPayload(
                    provider,
                    source_kind,
                    model,
                    reasoning_effort,
                    turn_token.turn_number,
                    child_lifetime,
                ),
                expected_sink=self,
            )

        self._enqueue(record)

    async def token_usage_reported(
        self,
        turn_token: TurnToken,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_usd: float | None,
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_token_usage_reported,
                self._state,
                turn_token,
                TokenUsageEventPayload(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    cost_usd=cost_usd,
                ),
            )
        )

    async def context_compacted(self, turn_token: TurnToken) -> None:
        self._enqueue(partial(self._system._on_context_compacted, self._state, turn_token))

    async def tool_call_started(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        title: str,
        tool_kind: str,
        detail: str | None,
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_tool_call_started,
                self._state,
                turn_token,
                tool_call_id,
                title,
                tool_kind,
                detail,
            )
        )

    async def tool_call_progress(
        self, turn_token: TurnToken, *, tool_call_id: str, detail: str
    ) -> None:
        """Shown on the live tail and never stored: progress is not a row.

        It goes straight out to whoever is watching rather than through the queue the rows
        go through, for the same reason a message delta does: it is not a row, so there is
        nothing for it to be ordered against. The tool call's finish is what is recorded.
        A turn the conversation has moved on from is not shown.
        """
        frame = ToolCallProgressFrame(tool_call_id=tool_call_id, detail=detail)
        if self._state.reserved_turn is not None:
            self._enqueue(partial(self._publish_live_after_admission, turn_token, frame))
        else:
            self._system._publish_live_tail_frame(self._state, turn_token, frame)

    async def tool_call_finished(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        tool_call_status: ToolCallStatus,
        detail: str | None,
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_tool_call_finished,
                self._state,
                turn_token,
                tool_call_id,
                tool_call_status,
                detail,
            )
        )

    async def plan_updated(self, turn_token: TurnToken, entries: tuple[PlanEntry, ...]) -> None:
        """A row like any other: it goes through the queue, in order, and is kept.

        A plan is not a passing thing to show — somebody opening this conversation later
        still needs to see what the agent set out to do — so it is written down rather
        than shown and forgotten.
        """
        self._enqueue(partial(self._system._on_plan_updated, self._state, turn_token, entries))

    async def permission_ask_raised(self, turn_token: TurnToken, ask: BackendPermissionAsk) -> None:
        self._enqueue(partial(self._system._on_permission_ask_raised, self._state, turn_token, ask))

    async def user_input_requested(
        self, turn_token: TurnToken, request: BackendUserInputRequest
    ) -> None:
        self._enqueue(
            partial(self._system._on_user_input_requested, self._state, turn_token, request)
        )

    async def user_input_failed(
        self, turn_token: TurnToken, *, request_id: str, detail: str
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_user_input_failed,
                self._state,
                turn_token,
                request_id,
                detail,
            )
        )

    async def turn_ended(
        self,
        turn_token: TurnToken,
        *,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_turn_ended,
                self._state,
                turn_token,
                ending,
                error_summary,
                standard_error_tail,
            )
        )

    async def vendor_session_cursor_rebound(self, vendor_session_cursor: str) -> None:
        self._enqueue(
            partial(
                self._system._on_vendor_session_cursor_rebound,
                self._state,
                vendor_session_cursor,
            )
        )

    async def composer_catalog_reported(
        self, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_composer_catalog_reported,
                self._state,
                composer_catalog,
            )
        )

    def _enqueue(self, handle: _BackendEventHandler) -> None:
        self._state.backend_event_queue.put_nowait(handle)


def _standard_error_tail(standard_error: str | None) -> str | None:
    if standard_error is None:
        return None
    return standard_error[-STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS:]
