"""One typed ACP browser stream, action coordinator, and runtime publisher."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, cast

from acp.schema import (
    PromptRequest,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
)
from fastapi import WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from planner.tickets.conversation_projection import TicketConversationProjection

from .backend_contracts import (
    ConversationIngressReplayBatch,
    ConversationIngressTransition,
    SessionNotificationReplayMaterializer,
)
from .configuration import (
    ACP_BROWSER_LIVE_QUEUE_MAX_ENVELOPES,
    ACP_NEW_CONVERSATION_TIMEOUT_SECONDS,
)
from .contracts import (
    ContextCompaction,
    ConversationActivity,
    ConversationActivityState,
    ConversationCompactionBoundaryProvenance,
    ConversationEmployee,
    ConversationEntityKind,
    ConversationPermissionOutcome,
    ConversationPermissionRequest,
    ConversationSessionBinding,
    ConversationTerminalState,
    EmployeeConversation,
    ProgrammaticPrompt,
    QueuedPrompt,
    TurnDeliveryChoice,
    TurnDeliveryReceipt,
)
from .employee_registry import (
    AcpEmployeeRecord,
    AcpEmployeeRegistry,
    ConversationIngressSource,
)
from .permission_broker import ConversationPermissionBroker
from .runtime_ports import (
    CompactionTransitionToken,
    ConversationRuntimeHandle,
    RequestedCancelRecoveryTransitionToken,
)
from .sqlite_binding_repository import SqliteConversationBindingRepository
from .turn_broker import (
    ConversationTurnBroker,
    ConversationTurnBrokerError,
    PromptIngressBarrierResult,
    TrackedTurnHandle,
)
from .wire_contracts import (
    SERVER_ENVELOPE_ADAPTER,
    AcpSessionUpdateEnvelope,
    ActivityEnvelope,
    AttachAction,
    BrowserAction,
    CancelAction,
    ConnectionEnvelope,
    ConnectionPayload,
    ContextCompactionEnvelope,
    DeliveryReceiptEnvelope,
    HumanEcho,
    HumanEchoEnvelope,
    NewConversationAction,
    PermissionOutcomeEnvelope,
    PermissionRequestEnvelope,
    PermissionResponseAction,
    ProgrammaticPromptEnvelope,
    PromptAction,
    ProtocolUpdateRejectedEnvelope,
    ProtocolUpdateRejectedPayload,
    QueueSnapshot,
    QueueSnapshotEnvelope,
    ServerEnvelope,
    TerminalStateEnvelope,
    validate_browser_action,
)

REPLAY_UNAVAILABLE_CLOSE_REASON = "conversation replay unavailable; retry"
SLOW_CONSUMER_CLOSE_REASON = "conversation client is too slow"
INVALID_ACTION_CLOSE_REASON = "invalid conversation action"
_LOGGER = logging.getLogger(__name__)


class _EnvelopeBase(TypedDict):
    wire_version: Literal[1]
    employee_id: str
    entity_kind: ConversationEntityKind
    entity_id: str
    acp_session_id: str | None
    binding_generation: int
    sequence: int


@dataclass(frozen=True, slots=True)
class _ReplayCutover:
    envelopes: tuple[str, ...]


class _BrowserOutboundQueue:
    """Subscriber-local replay followed by a bounded queue of live items."""

    def __init__(self, capacity: int) -> None:
        self.maxsize = capacity
        self._bootstrap: deque[str] = deque()
        self._live: asyncio.Queue[str] = asyncio.Queue(maxsize=capacity)
        self._cutovers: deque[tuple[int, _ReplayCutover]] = deque()
        self._live_put_count = 0
        self._live_get_count = 0
        self._available = asyncio.Event()

    def set_initial_bootstrap(self, envelopes: tuple[str, ...]) -> None:
        if self._bootstrap or not self._live.empty():
            raise RuntimeError("initial replay bootstrap must precede live delivery")
        self._bootstrap.extend(envelopes)

    def put_replay_cutover_nowait(self, envelopes: tuple[str, ...]) -> None:
        if self._live.full():
            raise asyncio.QueueFull
        self._cutovers.append(
            (self._live_put_count, _ReplayCutover(envelopes))
        )
        self._available.set()

    def put_nowait(self, serialized: str) -> None:
        self._live.put_nowait(serialized)
        self._live_put_count += 1
        self._available.set()

    async def get(self) -> str:
        while True:
            try:
                return self.get_nowait()
            except asyncio.QueueEmpty:
                self._available.clear()
                try:
                    return self.get_nowait()
                except asyncio.QueueEmpty:
                    await self._available.wait()

    def get_nowait(self) -> str:
        if self._bootstrap:
            return self._bootstrap.popleft()
        if (
            self._cutovers
            and self._cutovers[0][0] == self._live_get_count
        ):
            _cutoff, cutover = self._cutovers.popleft()
            self._bootstrap.extend(cutover.envelopes)
            return self.get_nowait()
        item = self._live.get_nowait()
        self._live_get_count += 1
        return item

    def empty(self) -> bool:
        return not self._bootstrap and not self._cutovers and self._live.empty()

    def qsize(self) -> int:
        return (
            len(self._bootstrap)
            + sum(len(item.envelopes) for _cutoff, item in self._cutovers)
            + self._live.qsize()
        )

    def live_qsize(self) -> int:
        return self._live.qsize()


@dataclass(slots=True)
class BrowserSubscription:
    connection_id: str
    employee_id: str
    queue: asyncio.Queue[str] | _BrowserOutboundQueue
    close_reason: str | None = None
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    permission_detach_task: asyncio.Task[None] | None = None
    closure_logged: bool = False


@dataclass(frozen=True, slots=True)
class _OrdinaryReplayEntryKey:
    sequence: int


@dataclass(frozen=True, slots=True)
class _MaterializedReplayEntryKey:
    backend_slot_key: Hashable


@dataclass(slots=True)
class _OrdinaryReplayEntry:
    sequence: int
    serialized: str
    stored_bytes: int


@dataclass(slots=True)
class _MaterializedReplayEntry:
    sequence: int
    envelope: AcpSessionUpdateEnvelope
    notifications: list[SessionNotification]
    accumulated_serialized_value_bytes: int
    stored_bytes: int


type _ReplayEntry = _OrdinaryReplayEntry | _MaterializedReplayEntry


@dataclass(slots=True)
class _StreamState:
    employee: ConversationEmployee
    binding: ConversationSessionBinding
    source_key: tuple[str, int, int]
    runtime_handle: ConversationRuntimeHandle | None = None
    sequence: int = 0
    ready: bool = False
    reset_buffer: list[str] = field(default_factory=list)
    reset_buffer_bytes: int = 0
    reset_buffer_envelope_count: int = 0
    reset_buffer_attempted_bytes: int = 0
    reset_buffer_available: bool = True
    replay_materializer: SessionNotificationReplayMaterializer | None = None
    replay_entries: dict[
        _OrdinaryReplayEntryKey | _MaterializedReplayEntryKey, _ReplayEntry
    ] = field(default_factory=dict)
    persistent_rejection: ProtocolUpdateRejectedPayload | None = None
    browsers: dict[str, BrowserSubscription] = field(default_factory=dict)
    supports_steer: bool = False


@dataclass(slots=True)
class _SequencedOperation:
    operation: Callable[[], Any]
    future: asyncio.Future[Any] | None
    failure: Callable[[Exception], None] | None = None


@dataclass(slots=True)
class _EmployeeSequencer:
    queue: asyncio.Queue[_SequencedOperation]
    task: asyncio.Task[None]


@dataclass(slots=True)
class _PromptIngressState:
    notifications: list[SessionNotification] = field(default_factory=list)
    rejection_reason: str | None = None


@dataclass(slots=True)
class _CompactionTransitionState:
    token: CompactionTransitionToken
    settled: asyncio.Event
    timeout_handle: asyncio.TimerHandle
    failure_reason: str | None = None
    committed_handle: ConversationRuntimeHandle | None = None
    aborted: bool = False
    quarantined_ingress: deque[_CompactionQuarantinedIngress] = field(
        default_factory=deque
    )


@dataclass(frozen=True, slots=True)
class _CompactionQuarantinedIngress:
    source_key: tuple[str, int, int]
    session_id: str
    payload: SessionNotification


@dataclass(slots=True)
class _RequestedCancelRecoveryTransitionState:
    token: RequestedCancelRecoveryTransitionToken
    settled: asyncio.Event
    timeout_handle: asyncio.TimerHandle
    held_source_payloads: deque[
        SessionNotification | ProtocolUpdateRejectedPayload
    ] = field(default_factory=deque)
    failure_reason: str | None = None


class ConversationHub:
    """The sole allocator of browser envelope sequence and browser subscription state."""

    def __init__(
        self,
        repository: SqliteConversationBindingRepository,
        *,
        ingress_capacity: int = 256,
        browser_capacity: int = ACP_BROWSER_LIVE_QUEUE_MAX_ENVELOPES,
        reset_buffer_byte_limit: int = 1_048_576,
        new_conversation_timeout_seconds: float = ACP_NEW_CONVERSATION_TIMEOUT_SECONDS,
        connection_id_factory: Callable[[], str] | None = None,
        worker_client_message_id_factory: Callable[[], str] | None = None,
        ticket_conversation_projection: TicketConversationProjection | None = None,
    ) -> None:
        if (
            min(
                ingress_capacity,
                browser_capacity,
                reset_buffer_byte_limit,
                new_conversation_timeout_seconds,
            )
            <= 0
        ):
            raise ValueError("conversation capacities must be positive")
        self.repository = repository
        self._ingress_capacity = ingress_capacity
        self._browser_capacity = browser_capacity
        self._reset_buffer_byte_limit = reset_buffer_byte_limit
        self._new_conversation_timeout_seconds = new_conversation_timeout_seconds
        self._connection_counter = 0
        self._worker_message_counter = 0
        self._connection_id_factory = connection_id_factory or self._next_connection_id
        self._worker_client_message_id_factory = (
            worker_client_message_id_factory or self._next_worker_message_id
        )
        self._ticket_conversation_projection = ticket_conversation_projection
        self._registry: AcpEmployeeRegistry | None = None
        self._broker: ConversationTurnBroker | None = None
        self._permission_broker: ConversationPermissionBroker | None = None
        self._terminal_service: Any | None = None
        self._sequencers: dict[str, _EmployeeSequencer] = {}
        self._ticket_projection_locks: dict[str, asyncio.Lock] = {}
        self._streams: dict[str, _StreamState] = {}
        self._subscriptions: dict[str, BrowserSubscription] = {}
        self._empty_browsers: dict[str, dict[str, BrowserSubscription]] = {}
        self._source_capture: dict[
            tuple[str, int, int], deque[SessionNotification | ProtocolUpdateRejectedPayload]
        ] = {}
        self._source_load_replay: dict[
            tuple[str, int, int],
            tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
        ] = {}
        self._prompt_ingress: dict[tuple[str, int, int, int, int], _PromptIngressState] = {}
        self._compaction_transitions: dict[str, _CompactionTransitionState] = {}
        self._requested_cancel_recovery_transitions: dict[
            str, _RequestedCancelRecoveryTransitionState
        ] = {}
        self._closing = False
        self._owner_thread_id: int | None = None

    def bind_owners(
        self,
        *,
        registry: AcpEmployeeRegistry,
        broker: ConversationTurnBroker,
        permission_broker: ConversationPermissionBroker,
        terminal_service: Any | None = None,
    ) -> None:
        if self._registry is not None:
            raise RuntimeError("conversation hub owners are already bound")
        self._registry = registry
        self._broker = broker
        self._permission_broker = permission_broker
        self._terminal_service = terminal_service
        broker.set_prompt_ingress_hooks(
            prompt_started=self.prompt_started,
            settlement_barrier=self.prompt_settlement_barrier,
        )

    @property
    def accepting(self) -> bool:
        return not self._closing and self._registry is not None

    def next_worker_client_message_id(self) -> str:
        return self._worker_client_message_id_factory()

    async def registry_conversation_ingress(
        self,
        source: ConversationIngressSource,
        payload: ConversationIngressTransition,
    ) -> None:
        operation: Callable[[], None]
        if isinstance(payload, ConversationIngressReplayBatch):
            if not all(
                isinstance(item, (SessionNotification, ProtocolUpdateRejectedPayload))
                for item in payload.items
            ):
                raise TypeError("replay batch must contain typed ACP updates or rejections")

            def ingest_replay_batch_operation() -> None:
                self._ingest_replay_batch(source, payload)

            operation = ingest_replay_batch_operation
        elif isinstance(payload, (SessionNotification, ProtocolUpdateRejectedPayload)):

            def ingest_source_operation() -> None:
                self._ingest_source(source, payload)

            operation = ingest_source_operation
        else:
            raise TypeError("registry ingress must be a typed ACP update or rejection")
        await self._admit(
            source.employee.employee_id,
            operation,
            on_failure=lambda error: self._handle_ingress_failure(source, payload, error),
        )

    async def registry_child_died(
        self,
        source: ConversationIngressSource,
        error: BaseException | None,
    ) -> None:
        employee_id = source.employee.employee_id

        def classify() -> int | None:
            source_key = self._source_key(source)
            self._source_load_replay.pop(source_key, None)
            self._source_capture.pop(source_key, None)
            stream = self._streams.get(employee_id)
            transition = self._requested_cancel_recovery_transitions.get(
                employee_id
            )
            if transition is not None:
                original = transition.token.original_handle
                original_key = (
                    employee_id,
                    original.child_generation,
                    id(original.record_identity),
                )
                if source_key == original_key:
                    binding_generation = original.binding.binding_generation
                    self._fail_requested_cancel_recovery_transition_state(
                        employee_id,
                        transition,
                        "Employee connection failed",
                    )
                    return binding_generation
            if stream is None or stream.source_key != source_key:
                return None
            self._publish_child_death(source)
            return stream.binding.binding_generation

        binding_generation = cast(
            int | None, await self._enqueue_and_wait(employee_id, classify)
        )
        if binding_generation is not None:
            await self._require_broker().child_died(
                employee_id,
                binding_generation,
                source.child_generation,
                error,
            )

    async def ensure_employee_stream(
        self, employee_id: str
    ) -> tuple[ConversationEmployee, ConversationSessionBinding, ConversationRuntimeHandle]:
        await self._wait_for_compaction_and_requested_cancel_recovery(employee_id)
        conversation = await self.repository.ensure_conversation(employee_id)
        employee = await self.repository.resolve_employee(employee_id)
        binding = await self.repository.resolve(employee_id)
        registry = self._require_registry()
        if binding is None:
            try:
                record = await registry.get_or_spawn(employee)
                binding = record.binding
                employee = record.employee
                await self._establish_stream(
                    record,
                    sequence_floor=(
                        2
                        if binding.binding_generation
                        == conversation.conversation_generation
                        else 0
                    ),
                )
                await self._adopt_empty_browsers(employee_id)
            except Exception:
                _LOGGER.exception(
                    "conversation backend activation failed",
                    extra={
                        "conversation_operation": "activate_on_first_prompt",
                        "conversation_employee_id": employee_id,
                        "conversation_generation": conversation.conversation_generation,
                        "conversation_backend_key": conversation.backend_key,
                    },
                )
                raise
        else:
            handle = await self.ensure_stream_ready(employee_id, binding)
            return employee, binding, handle
        handle = await registry.resolve_runtime_handle(employee_id, binding.binding_generation)
        return employee, binding, handle

    async def ensure_stream_ready(
        self,
        employee_id: str,
        binding: ConversationSessionBinding,
    ) -> ConversationRuntimeHandle:
        await self._wait_for_compaction_and_requested_cancel_recovery(employee_id)
        if self._closing:
            raise RuntimeError("conversation hub is closing")
        employee = await self.repository.resolve_employee(employee_id)
        durable = await self.repository.resolve(employee_id)
        if durable != binding or binding.employee_id != employee_id:
            raise RuntimeError("requested ACP binding is not the durable employee binding")
        stream = self._streams.get(employee_id)
        if stream is not None and stream.binding == binding and stream.ready:
            return await self._require_registry().resolve_runtime_handle(
                employee_id, binding.binding_generation
            )
        try:
            record = await self._require_registry().attach(employee)
            if record.binding != binding:
                raise RuntimeError("registry loaded a different ACP binding")
            await self._establish_stream(record, sequence_floor=0)
        except Exception:
            _LOGGER.exception(
                "conversation backend load failed",
                extra={
                    "conversation_operation": "load_bound_session",
                    "conversation_employee_id": employee_id,
                    "conversation_generation": binding.binding_generation,
                    "conversation_backend_key": binding.backend_key,
                    "conversation_acp_session_id": binding.acp_session_id,
                },
            )
            raise
        return await self._require_registry().resolve_runtime_handle(
            employee_id, binding.binding_generation
        )

    async def attach_browser(
        self,
        employee_id: str,
        *,
        last_seen_binding_generation: int | None = None,
        last_seen_sequence: int | None = None,
        connection_id: str | None = None,
    ) -> BrowserSubscription:
        await self._wait_for_compaction_and_requested_cancel_recovery(employee_id)
        if self._closing:
            raise RuntimeError("conversation hub is closing")
        if (last_seen_binding_generation is None) != (last_seen_sequence is None):
            raise ValueError("conversation cursor fields must be supplied together")
        conversation = await self.repository.ensure_conversation(employee_id)
        employee = await self.repository.resolve_employee(employee_id)
        binding = await self.repository.resolve(employee_id)
        if (
            last_seen_binding_generation is not None
            and last_seen_binding_generation != conversation.conversation_generation
        ):
            last_seen_binding_generation = None
            last_seen_sequence = None
        connection_id = connection_id or self._connection_id_factory()
        subscription = BrowserSubscription(
            connection_id=connection_id,
            employee_id=employee_id,
            queue=_BrowserOutboundQueue(self._browser_capacity),
        )
        await self._require_permission_broker().attach_browser(employee, connection_id)
        self._subscriptions[connection_id] = subscription
        try:
            if binding is None:
                cast(_BrowserOutboundQueue, subscription.queue).set_initial_bootstrap(
                    self._empty_conversation_bootstrap(
                        employee, conversation.conversation_generation
                    )
                )
                self._empty_browsers.setdefault(employee_id, {})[
                    connection_id
                ] = subscription
                return subscription

            stream = self._streams.get(employee_id)
            if stream is not None and last_seen_sequence is not None:
                if last_seen_sequence > stream.sequence:
                    raise ValueError("conversation cursor sequence is in the future")
            if stream is None or stream.binding != binding or not stream.ready:
                try:
                    record = await self._require_registry().attach(employee)
                    if stream is not None:
                        await self._enqueue_and_wait(
                            employee_id,
                            lambda: self._open_capture(stream),
                        )
                    await self._establish_stream(
                        record,
                        sequence_floor=last_seen_sequence or 0,
                        initial_subscription=subscription,
                    )
                except Exception:
                    _LOGGER.exception(
                        "conversation backend load failed",
                        extra={
                            "conversation_operation": "attach_bound_session",
                            "conversation_employee_id": employee_id,
                            "conversation_generation": binding.binding_generation,
                            "conversation_backend_key": binding.backend_key,
                            "conversation_acp_session_id": binding.acp_session_id,
                        },
                    )
                    raise
                return subscription

            await self._enqueue_and_wait(
                employee_id,
                lambda: self._attach_subscription_from_ready_stream(
                    stream, subscription
                ),
            )
            if subscription.close_reason is not None:
                await self._detach_subscription_permission(subscription)
            return subscription
        except BaseException:
            await self._detach_subscription_permission(subscription)
            raise

    async def detach_browser(self, connection_id: str) -> None:
        subscription = self._subscriptions.get(connection_id)
        employee_id: str | None = None
        for candidate_id, stream in self._streams.items():
            if connection_id in stream.browsers:
                employee_id = candidate_id
                break
        if employee_id is not None:
            await self._enqueue_and_wait(
                employee_id,
                lambda: self._streams[employee_id].browsers.pop(connection_id, None),
            )
        for empty_employee_id, browsers in tuple(self._empty_browsers.items()):
            if connection_id in browsers:
                browsers.pop(connection_id, None)
                if not browsers:
                    self._empty_browsers.pop(empty_employee_id, None)
                break
        if subscription is not None:
            await self._detach_subscription_permission(subscription)
            self._subscriptions.pop(connection_id, None)
        else:
            await self._require_permission_broker().detach_browser(connection_id)

    async def dispatch_action(self, connection_id: str, action: BrowserAction) -> None:
        if self._closing:
            raise RuntimeError("conversation hub is closing")
        empty_employee_id = self._empty_employee_for_connection(connection_id)
        if empty_employee_id is not None:
            await self._dispatch_empty_action(connection_id, empty_employee_id, action)
            return
        stream = self._stream_for_connection(connection_id)
        transitioned_from = await self._wait_for_compaction_and_requested_cancel_recovery(
            stream.employee.employee_id
        )
        stream = self._stream_for_connection(connection_id)
        if action.employee_id != stream.employee.employee_id:
            raise ValueError("browser action changed employee identity")
        handle = await self._require_registry().resolve_runtime_handle(
            stream.employee.employee_id, stream.binding.binding_generation
        )
        if isinstance(action, PromptAction):
            prompt = PromptRequest(
                session_id=stream.binding.acp_session_id,
                prompt=action.prompt,
                field_meta=action.prompt_meta,
            )
            if prompt.session_id != stream.binding.acp_session_id:
                if (
                    transitioned_from is None
                    or prompt.session_id != transitioned_from.acp_session_id
                ):
                    raise ValueError("prompt session does not match attached binding")
                prompt = prompt.model_copy(
                    update={"session_id": stream.binding.acp_session_id}
                )
            await self._publish_human_echo(
                stream.employee,
                stream.binding,
                action.client_message_id,
                prompt,
            )
            await self._deliver_with_logging(
                handle,
                action.client_message_id,
                action.delivery_choice,
                prompt,
            )
            return
        if isinstance(action, CancelAction):
            await self._require_broker().cancel(handle, action.queued_client_message_id)
            return
        if isinstance(action, PermissionResponseAction):
            result = await self._require_permission_broker().respond_to_permission(
                connection_id, action.request_id, action.option_id
            )
            if result.disposition == "rejected":
                raise ValueError("permission response was rejected")
            return
        if isinstance(action, NewConversationAction):
            await self.new_conversation(stream.employee.employee_id)
            return
        if isinstance(action, AttachAction):
            raise ValueError("browser is already attached")
        raise AssertionError(type(action))

    async def new_conversation(self, employee_id: str) -> EmployeeConversation:
        await self._wait_for_compaction_and_requested_cancel_recovery(employee_id)
        stream = self._streams.get(employee_id)
        binding = (
            await self.repository.resolve(employee_id)
            if stream is None
            else stream.binding
        )
        employee = await self.repository.resolve_employee(employee_id)
        browsers = (
            dict(self._empty_browsers.get(employee_id, {}))
            if stream is None
            else dict(stream.browsers)
        )
        if stream is not None:
            if binding is None:
                raise RuntimeError("live conversation stream has no durable binding")
            handle = await self._require_registry().resolve_runtime_handle(
                employee_id, binding.binding_generation
            )
            deadline = asyncio.get_running_loop().time() + self._new_conversation_timeout_seconds
            await self._require_broker().prepare_new_conversation(handle, deadline)
        conversation = await self.repository.start_new_conversation(employee_id, binding)
        if stream is not None:
            self._streams.pop(employee_id, None)
        if binding is not None:
            await self._require_registry().retire_conversation(
                employee_id, binding.binding_generation
            )
        self._empty_browsers[employee_id] = browsers
        replay = self._empty_conversation_bootstrap(
            await self.repository.resolve_employee(employee_id),
            conversation.conversation_generation,
        )
        for browser in browsers.values():
            cast(_BrowserOutboundQueue, browser.queue).put_replay_cutover_nowait(replay)
        await self._reset_ticket_conversation_projection(employee)
        return conversation

    async def _dispatch_empty_action(
        self,
        connection_id: str,
        employee_id: str,
        action: BrowserAction,
    ) -> None:
        if action.employee_id != employee_id:
            raise ValueError("browser action changed employee identity")
        if isinstance(action, PromptAction):
            employee, binding, handle = await self.ensure_employee_stream(employee_id)
            prompt = PromptRequest(
                session_id=binding.acp_session_id,
                prompt=action.prompt,
                field_meta=action.prompt_meta,
            )
            await self._publish_human_echo(
                employee,
                binding,
                action.client_message_id,
                prompt,
            )
            await self._deliver_with_logging(
                handle,
                action.client_message_id,
                action.delivery_choice,
                prompt,
            )
            return
        if isinstance(action, NewConversationAction):
            await self.new_conversation(employee_id)
            return
        if isinstance(action, CancelAction):
            return
        if isinstance(action, AttachAction):
            raise ValueError("browser is already attached")
        raise ValueError("empty conversation has no permission request")

    async def _deliver_with_logging(
        self,
        handle: ConversationRuntimeHandle,
        client_message_id: str,
        delivery_choice: TurnDeliveryChoice,
        prompt: PromptRequest,
    ) -> None:
        try:
            await self._require_broker().deliver(
                handle,
                client_message_id,
                delivery_choice,
                prompt,
            )
        except Exception:
            _LOGGER.exception(
                "conversation prompt delivery failed",
                extra={
                    "conversation_operation": "deliver_prompt",
                    "conversation_employee_id": handle.employee.employee_id,
                    "conversation_generation": handle.binding.binding_generation,
                    "conversation_backend_key": handle.binding.backend_key,
                    "conversation_acp_session_id": handle.binding.acp_session_id,
                    "conversation_client_message_id": client_message_id,
                },
            )
            raise

    async def _adopt_empty_browsers(self, employee_id: str) -> None:
        browsers = self._empty_browsers.pop(employee_id, {})
        if not browsers:
            return

        def adopt() -> None:
            stream = self._streams[employee_id]
            replay = self._subscriber_snapshot_bootstrap(stream)
            if replay is None:
                raise ConversationTurnBrokerError(REPLAY_UNAVAILABLE_CLOSE_REASON)
            for browser in browsers.values():
                cast(_BrowserOutboundQueue, browser.queue).put_replay_cutover_nowait(replay)
                stream.browsers[browser.connection_id] = browser

        await self._enqueue_and_wait(employee_id, adopt)

    @staticmethod
    def _empty_conversation_bootstrap(
        employee: ConversationEmployee,
        conversation_generation: int,
    ) -> tuple[str, ...]:
        envelopes = (
            ConnectionEnvelope(
                wire_version=1,
                type="connection",
                employee_id=employee.employee_id,
                entity_kind=employee.entity_kind,
                entity_id=employee.entity_id,
                acp_session_id=None,
                binding_generation=conversation_generation,
                sequence=1,
                payload=ConnectionPayload(
                    state="reset",
                    detail="New conversation",
                    supports_steer=False,
                    reset_binding_generation=conversation_generation,
                ),
            ),
            ConnectionEnvelope(
                wire_version=1,
                type="connection",
                employee_id=employee.employee_id,
                entity_kind=employee.entity_kind,
                entity_id=employee.entity_id,
                acp_session_id=None,
                binding_generation=conversation_generation,
                sequence=2,
                payload=ConnectionPayload(
                    state="ready",
                    detail="Ready",
                    supports_steer=False,
                ),
            ),
        )
        serialized: list[str] = []
        for envelope in envelopes:
            payload = envelope.model_dump(by_alias=True, exclude_none=True)
            payload["acpSessionId"] = None
            serialized.append(json.dumps(payload, separators=(",", ":")))
        return tuple(serialized)

    def _empty_employee_for_connection(self, connection_id: str) -> str | None:
        for employee_id, browsers in self._empty_browsers.items():
            if connection_id in browsers:
                return employee_id
        return None

    async def _new_conversation_from_stream(
        self,
        stream: _StreamState,
        binding: ConversationSessionBinding,
    ) -> ConversationSessionBinding:
        handle = await self._require_registry().resolve_runtime_handle(
            stream.employee.employee_id, binding.binding_generation
        )
        await self._enqueue_and_wait(
            stream.employee.employee_id, lambda: self._open_capture(stream)
        )
        deadline = asyncio.get_running_loop().time() + self._new_conversation_timeout_seconds
        await self._require_broker().prepare_new_conversation(handle, deadline)
        record = await self._require_registry().new_conversation(stream.employee)
        # session/new leaves the replacement live on this child. Loading it again is
        # both redundant and invalid for providers that persist only after a first turn.
        async def establish_and_reset() -> None:
            established = await self._establish_stream(
                record, sequence_floor=0, force_new_binding=True
            )
            if not established:
                raise ConversationTurnBrokerError(REPLAY_UNAVAILABLE_CLOSE_REASON)
            await self._reset_ticket_conversation_projection(record.employee)

        projection_lock = self._ticket_projection_lock(record.employee)
        if projection_lock is None:
            await establish_and_reset()
        else:
            async with projection_lock:
                await establish_and_reset()
        return record.binding

    def prompt_started(self, handle: ConversationRuntimeHandle, prompt_epoch: int) -> None:
        key = self._prompt_ingress_key(handle, prompt_epoch)
        if key in self._prompt_ingress:
            raise RuntimeError("prompt ingress epoch is already registered")
        self._prompt_ingress[key] = _PromptIngressState()

    async def prompt_settlement_barrier(
        self, handle: ConversationRuntimeHandle, prompt_epoch: int
    ) -> PromptIngressBarrierResult:
        key = self._prompt_ingress_key(handle, prompt_epoch)

        def close_epoch() -> PromptIngressBarrierResult:
            state = self._prompt_ingress.pop(key, _PromptIngressState())
            return PromptIngressBarrierResult(
                notifications=tuple(state.notifications),
                rejection_reason=state.rejection_reason,
            )

        return cast(
            PromptIngressBarrierResult,
            await self._enqueue_and_wait(handle.employee.employee_id, close_epoch),
        )

    async def begin_compaction_transition(
        self,
        handle: ConversationRuntimeHandle,
        deadline: float,
    ) -> CompactionTransitionToken:
        def begin() -> CompactionTransitionToken:
            employee_id = handle.employee.employee_id
            if employee_id in self._compaction_transitions:
                raise RuntimeError("compaction transition is already active")
            if employee_id in self._requested_cancel_recovery_transitions:
                raise RuntimeError("requested-cancel recovery transition is active")
            stream = self._streams.get(employee_id)
            if stream is None or not self._runtime_handles_match(
                self._runtime_handle_for_stream(stream), handle
            ):
                raise RuntimeError("compaction transition names a stale stream")
            loop = asyncio.get_running_loop()
            if deadline <= loop.time():
                raise TimeoutError("compaction transition deadline already expired")
            token = CompactionTransitionToken(
                transaction_identity=object(),
                original_handle=handle,
                deadline=deadline,
            )
            timeout_handle = loop.call_at(
                deadline,
                self._expire_compaction_transition,
                employee_id,
                token.transaction_identity,
            )
            self._compaction_transitions[employee_id] = _CompactionTransitionState(
                token=token,
                settled=asyncio.Event(),
                timeout_handle=timeout_handle,
            )
            return token

        return cast(
            CompactionTransitionToken,
            await self._enqueue_and_wait(handle.employee.employee_id, begin),
        )

    async def commit_compaction_transition(
        self,
        token: CompactionTransitionToken,
        replacement_handle: ConversationRuntimeHandle,
        replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
        queued_prompts: tuple[QueuedPrompt, ...],
    ) -> None:
        employee_id = token.original_handle.employee.employee_id
        compacted_boundaries = await self.repository.resolve_compaction_boundaries(
            replacement_handle.binding
        )

        def commit() -> None:
            state = self._require_compaction_transition(token)
            if state.committed_handle is not None:
                raise RuntimeError("compaction transition was already committed")
            if state.aborted:
                raise RuntimeError("aborted compaction transition cannot be committed")
            if asyncio.get_running_loop().time() >= token.deadline:
                self._fail_compaction_transition_state(
                    employee_id, state, "compaction transition deadline expired"
                )
                raise TimeoutError("compaction transition deadline expired")
            old_stream = self._streams.get(employee_id)
            if old_stream is None or not self._runtime_handles_match(
                self._runtime_handle_for_stream(old_stream), token.original_handle
            ):
                raise RuntimeError("compaction transition lost its original stream")
            if (
                replacement_handle.employee.employee_id != employee_id
                or replacement_handle.binding.employee_id != employee_id
                or replacement_handle.binding.binding_generation
                != token.original_handle.binding.binding_generation + 1
            ):
                raise RuntimeError("compaction replacement handle is invalid")
            for queued in queued_prompts:
                if queued.prompt.session_id != replacement_handle.binding.acp_session_id:
                    raise RuntimeError("queued prompt was not retargeted to replacement")
            stream = _StreamState(
                employee=replacement_handle.employee,
                binding=replacement_handle.binding,
                source_key=(
                    employee_id,
                    replacement_handle.child_generation,
                    id(replacement_handle.record_identity),
                ),
                runtime_handle=replacement_handle,
                replay_materializer=(
                    replacement_handle.definition.session_notification_replay_materializer
                ),
                supports_steer=(
                    replacement_handle.definition.turn_capabilities.supports_steer
                ),
            )
            state.committed_handle = replacement_handle
            def build_replay() -> None:
                self._publish_connection_now(stream, "reset", "Conversation compacted")
                self._publish_replay_batch_now(
                    stream,
                    replacement_handle,
                    replay,
                    compacted_boundaries,
                )
                permitted_sources = {
                    (
                        employee_id,
                        token.original_handle.child_generation,
                        id(token.original_handle.record_identity),
                    ),
                    stream.source_key,
                }
                while state.quarantined_ingress:
                    quarantined = state.quarantined_ingress.popleft()
                    if (
                        quarantined.session_id
                        == replacement_handle.binding.acp_session_id
                        and quarantined.source_key in permitted_sources
                    ):
                        self._publish_source_payload(stream, quarantined.payload)
                self._publish_connection_now(stream, "ready", "Ready")

            replay_available = self._commit_detached_stream_candidate(
                stream, old_stream.browsers, build_replay
            )
            if not replay_available:
                return
            for queued in queued_prompts:
                self._publish_human_echo_now(
                    stream,
                    queued.client_message_id,
                    queued.prompt,
                )
            self._publish_queue_snapshot_now(stream, queued_prompts)

        projection_lock = self._ticket_projection_lock(token.original_handle.employee)
        if projection_lock is None:
            await self._enqueue_and_wait(employee_id, commit)
        else:
            async with projection_lock:
                await self._enqueue_and_wait(employee_id, commit)

    async def complete_compaction_transition(
        self,
        token: CompactionTransitionToken,
        settled_handle: ConversationRuntimeHandle,
    ) -> None:
        employee_id = token.original_handle.employee.employee_id

        def complete() -> None:
            state = self._require_compaction_transition(token)
            stream = self._streams.get(employee_id)
            expected_handle = (
                token.original_handle if state.aborted else state.committed_handle
            )
            if expected_handle is None:
                raise RuntimeError("compaction transition was not committed or aborted")
            if (
                not self._runtime_handles_match(expected_handle, settled_handle)
                or stream is None
                or not self._runtime_handles_match(
                    self._runtime_handle_for_stream(stream), settled_handle
                )
            ):
                raise RuntimeError("compaction completion lost its settled stream")
            self._settle_compaction_transition_state(employee_id, state)

        await self._enqueue_and_wait(employee_id, complete)

    async def fail_compaction_transition(
        self, token: CompactionTransitionToken, reason: str
    ) -> None:
        if not reason.strip():
            raise ValueError("compaction transition failure reason must not be blank")
        employee_id = token.original_handle.employee.employee_id

        def fail() -> None:
            state = self._require_compaction_transition(token)
            self._fail_compaction_transition_state(employee_id, state, reason)

        await self._enqueue_and_wait(employee_id, fail)

    async def abort_compaction_transition(
        self, token: CompactionTransitionToken
    ) -> None:
        employee_id = token.original_handle.employee.employee_id

        def abort() -> None:
            state = self._require_compaction_transition(token)
            if state.committed_handle is not None:
                raise RuntimeError("committed compaction transition cannot be aborted")
            if state.aborted:
                raise RuntimeError("compaction transition was already aborted")
            stream = self._streams.get(employee_id)
            if stream is None or not self._runtime_handles_match(
                self._runtime_handle_for_stream(stream), token.original_handle
            ):
                raise RuntimeError("compaction abort lost its original stream")
            state.quarantined_ingress.clear()
            state.aborted = True

        await self._enqueue_and_wait(employee_id, abort)

    async def begin_requested_cancel_recovery_transition(
        self,
        handle: ConversationRuntimeHandle,
        deadline: float,
    ) -> RequestedCancelRecoveryTransitionToken:
        def begin() -> RequestedCancelRecoveryTransitionToken:
            employee_id = handle.employee.employee_id
            if employee_id in self._requested_cancel_recovery_transitions:
                raise RuntimeError(
                    "requested-cancel recovery transition is already active"
                )
            if employee_id in self._compaction_transitions:
                raise RuntimeError("compaction transition is active")
            stream = self._streams.get(employee_id)
            if stream is None or not self._runtime_handles_match(
                self._runtime_handle_for_stream(stream), handle
            ):
                raise RuntimeError(
                    "requested-cancel recovery transition names a stale stream"
                )
            loop = asyncio.get_running_loop()
            if deadline <= loop.time():
                raise TimeoutError(
                    "requested-cancel recovery transition deadline already expired"
                )
            token = RequestedCancelRecoveryTransitionToken(
                transaction_identity=object(),
                original_handle=handle,
                deadline=deadline,
            )
            timeout_handle = loop.call_at(
                deadline,
                self._expire_requested_cancel_recovery_transition,
                employee_id,
                token.transaction_identity,
            )
            self._requested_cancel_recovery_transitions[employee_id] = (
                _RequestedCancelRecoveryTransitionState(
                    token=token,
                    settled=asyncio.Event(),
                    timeout_handle=timeout_handle,
                )
            )
            return token

        return cast(
            RequestedCancelRecoveryTransitionToken,
            await self._enqueue_and_wait(handle.employee.employee_id, begin),
        )

    async def resume_requested_cancelled_runtime(
        self, token: RequestedCancelRecoveryTransitionToken
    ) -> None:
        employee_id = token.original_handle.employee.employee_id

        def resume() -> None:
            state = self._require_requested_cancel_recovery_transition(token)
            stream = self._streams.get(employee_id)
            if stream is None or not self._runtime_handles_match(
                self._runtime_handle_for_stream(stream), token.original_handle
            ):
                raise RuntimeError(
                    "requested-cancel resume lost its original stream"
                )
            while state.held_source_payloads:
                self._publish_source_payload(
                    stream, state.held_source_payloads.popleft()
                )
            self._settle_requested_cancel_recovery_transition_state(
                employee_id, state
            )

        await self._enqueue_and_wait(employee_id, resume)

    async def commit_requested_cancel_recovery_transition(
        self,
        token: RequestedCancelRecoveryTransitionToken,
        replacement_handle: ConversationRuntimeHandle,
        replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
        queued_prompts: tuple[QueuedPrompt, ...],
        send_now_successor_human_echo: HumanEcho | None,
    ) -> None:
        employee_id = token.original_handle.employee.employee_id
        compacted_boundaries = await self.repository.resolve_compaction_boundaries(
            replacement_handle.binding
        )

        def commit() -> None:
            state = self._require_requested_cancel_recovery_transition(token)
            if asyncio.get_running_loop().time() >= token.deadline:
                self._fail_requested_cancel_recovery_transition_state(
                    employee_id,
                    state,
                    "requested-cancel recovery transition deadline expired",
                )
                raise TimeoutError(
                    "requested-cancel recovery transition deadline expired"
                )
            old_stream = self._streams.get(employee_id)
            original = token.original_handle
            if old_stream is None or not self._runtime_handles_match(
                self._runtime_handle_for_stream(old_stream), original
            ):
                raise RuntimeError(
                    "requested-cancel recovery lost its original stream"
                )
            if (
                replacement_handle.employee != original.employee
                or replacement_handle.binding != original.binding
                or replacement_handle.child_generation <= original.child_generation
                or replacement_handle.child is original.child
                or replacement_handle.record_identity is original.record_identity
            ):
                raise RuntimeError(
                    "requested-cancel recovery replacement handle is invalid"
                )
            for queued in queued_prompts:
                if queued.prompt.session_id != original.binding.acp_session_id:
                    raise RuntimeError(
                        "requested-cancel queued prompt changed durable session"
                    )
            if (
                send_now_successor_human_echo is not None
                and send_now_successor_human_echo.prompt.session_id
                != original.binding.acp_session_id
            ):
                raise RuntimeError(
                    "requested-cancel successor prompt changed durable session"
                )
            stream = _StreamState(
                employee=replacement_handle.employee,
                binding=replacement_handle.binding,
                source_key=(
                    employee_id,
                    replacement_handle.child_generation,
                    id(replacement_handle.record_identity),
                ),
                runtime_handle=replacement_handle,
                replay_materializer=(
                    replacement_handle.definition.session_notification_replay_materializer
                ),
                sequence=old_stream.sequence,
                persistent_rejection=old_stream.persistent_rejection,
                supports_steer=(
                    replacement_handle.definition.turn_capabilities.supports_steer
                ),
            )
            def build_replay() -> None:
                self._publish_connection_now(
                    stream, "reset", "Conversation runtime recovered"
                )
                self._publish_replay_batch_now(
                    stream,
                    replacement_handle,
                    replay,
                    compacted_boundaries,
                )
                self._publish_connection_now(stream, "ready", "Ready")

            replay_available = self._commit_detached_stream_candidate(
                stream, old_stream.browsers, build_replay
            )
            if not replay_available:
                state.held_source_payloads.clear()
                self._settle_requested_cancel_recovery_transition_state(
                    employee_id, state
                )
                return
            for queued in queued_prompts:
                self._publish_human_echo_now(
                    stream,
                    queued.client_message_id,
                    queued.prompt,
                )
            if send_now_successor_human_echo is not None:
                self._publish_human_echo_now(
                    stream,
                    send_now_successor_human_echo.client_message_id,
                    send_now_successor_human_echo.prompt,
                )
            self._publish_queue_snapshot_now(stream, queued_prompts)
            state.held_source_payloads.clear()
            self._settle_requested_cancel_recovery_transition_state(
                employee_id, state
            )

        projection_lock = self._ticket_projection_lock(token.original_handle.employee)
        if projection_lock is None:
            await self._enqueue_and_wait(employee_id, commit)
        else:
            async with projection_lock:
                await self._enqueue_and_wait(employee_id, commit)

    async def fail_requested_cancel_recovery_transition(
        self, token: RequestedCancelRecoveryTransitionToken, reason: str
    ) -> None:
        if not reason.strip():
            raise ValueError(
                "requested-cancel recovery transition failure reason must not be blank"
            )
        employee_id = token.original_handle.employee.employee_id

        def fail() -> None:
            state = self._require_requested_cancel_recovery_transition(token)
            self._fail_requested_cancel_recovery_transition_state(
                employee_id, state, reason
            )

        await self._enqueue_and_wait(employee_id, fail)

    async def publish_activity(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        state: ConversationActivityState,
        detail: str,
    ) -> ConversationActivity:
        def publish() -> ConversationActivity:
            stream = self._require_stream(employee, binding)
            sequence = stream.sequence + 1
            activity = ConversationActivity(
                state=state,
                detail=detail,
                sequence=sequence,
            )
            self._publish_envelope_now(
                stream,
                ActivityEnvelope(
                    **self._base(employee, binding, sequence),
                    type="activity",
                    payload=activity,
                ),
            )
            if state == "failed" and stream.ready:
                self._publish_connection_now(
                    stream,
                    "error",
                    "Employee connection failed",
                )
                stream.ready = False
            return activity

        async def publish_and_record() -> ConversationActivity:
            if projection_lock is not None:
                self._require_stream(employee, binding)
                await self._record_ticket_activity(employee, state)
            return cast(
                ConversationActivity,
                await self._enqueue_and_wait(employee.employee_id, publish),
            )

        projection_lock = self._ticket_projection_lock(employee)
        if projection_lock is None:
            return await publish_and_record()
        async with projection_lock:
            return await publish_and_record()

    async def publish_delivery_receipt(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        receipt: TurnDeliveryReceipt,
    ) -> None:
        async def publish_and_record() -> None:
            if projection_lock is not None:
                self._require_stream(employee, binding)
                await self._record_ticket_activity(employee, "thinking")
            await self._publish(
                employee,
                binding,
                lambda sequence: DeliveryReceiptEnvelope(
                    **self._base(employee, binding, sequence),
                    type="delivery_receipt",
                    payload=receipt,
                ),
            )

        projection_lock = (
            self._ticket_projection_lock(employee)
            if receipt.state == "accepted"
            else None
        )
        if projection_lock is None:
            await publish_and_record()
            return
        async with projection_lock:
            await publish_and_record()

    async def publish_programmatic_prompt(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        prompt: ProgrammaticPrompt,
    ) -> None:
        await self._publish(
            employee,
            binding,
            lambda sequence: ProgrammaticPromptEnvelope(
                **self._base(employee, binding, sequence),
                type="programmatic_prompt",
                payload=prompt,
            ),
        )

    async def publish_queue_snapshot(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        prompts: tuple[QueuedPrompt, ...],
    ) -> None:
        await self._publish(
            employee,
            binding,
            lambda sequence: QueueSnapshotEnvelope(
                **self._base(employee, binding, sequence),
                type="queue_snapshot",
                payload=QueueSnapshot(items=prompts),
            ),
        )

    async def publish_compaction(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        compaction: ContextCompaction,
    ) -> None:
        await self._publish(
            employee,
            binding,
            lambda sequence: ContextCompactionEnvelope(
                **self._base(employee, binding, sequence),
                type="context_compaction",
                payload=compaction,
            ),
        )

    async def publish_permission_request(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        request_id: str,
        backend_key: str,
        request: RequestPermissionRequest,
        deadline_at: int,
    ) -> ConversationPermissionRequest:
        async def publish_and_record() -> ConversationPermissionRequest:
            if projection_lock is not None:
                self._require_stream(employee, binding)
                await self._record_ticket_permission(employee, True)
            try:
                payload = cast(
                    ConversationPermissionRequest,
                    await self._publish(
                        employee,
                        binding,
                        lambda sequence: PermissionRequestEnvelope(
                            **self._base(employee, binding, sequence),
                            type="permission_request",
                            payload=ConversationPermissionRequest(
                                request_id=request_id,
                                employee_id=employee.employee_id,
                                backend_key=backend_key,
                                request=request,
                                lifecycle="pending",
                                deadline_at=deadline_at,
                                opened_sequence=sequence,
                            ),
                        ),
                        return_payload=True,
                    ),
                )
            except BaseException:
                await self._record_ticket_permission(employee, False)
                raise
            return payload

        projection_lock = self._ticket_projection_lock(employee)
        if projection_lock is None:
            return await publish_and_record()
        async with projection_lock:
            return await publish_and_record()

    async def publish_permission_outcome(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        request_id: str,
        response: RequestPermissionResponse,
        cancellation_reason: str | None,
    ) -> ConversationPermissionOutcome:
        async def publish_and_record() -> ConversationPermissionOutcome:
            if projection_lock is not None:
                self._require_stream(employee, binding)
                await self._record_ticket_permission(employee, False)
            payload = cast(
                ConversationPermissionOutcome,
                await self._publish(
                    employee,
                    binding,
                    lambda sequence: PermissionOutcomeEnvelope(
                        **self._base(employee, binding, sequence),
                        type="permission_outcome",
                        payload=ConversationPermissionOutcome(
                            request_id=request_id,
                            response=response,
                            cancellation_reason=cancellation_reason,
                            settled_sequence=sequence,
                        ),
                    ),
                    return_payload=True,
                ),
            )
            return payload

        projection_lock = self._ticket_projection_lock(employee)
        if projection_lock is None:
            return await publish_and_record()
        async with projection_lock:
            return await publish_and_record()

    async def _record_ticket_activity(
        self, employee: ConversationEmployee, state: ConversationActivityState
    ) -> None:
        projection = self._ticket_conversation_projection
        if projection is None or employee.entity_kind != "ticket":
            return
        await asyncio.to_thread(
            projection.record_activity, employee.entity_id, state
        )

    async def _record_ticket_permission(
        self, employee: ConversationEmployee, pending: bool
    ) -> None:
        projection = self._ticket_conversation_projection
        if projection is None or employee.entity_kind != "ticket":
            return
        await asyncio.to_thread(
            projection.record_permission, employee.entity_id, pending
        )

    async def _reset_ticket_conversation_projection(
        self, employee: ConversationEmployee
    ) -> None:
        projection = self._ticket_conversation_projection
        if projection is None or employee.entity_kind != "ticket":
            return
        await asyncio.to_thread(projection.reset, employee.entity_id)

    def _ticket_projection_lock(
        self, employee: ConversationEmployee
    ) -> asyncio.Lock | None:
        if (
            self._ticket_conversation_projection is None
            or employee.entity_kind != "ticket"
            or employee.employee_id not in self._streams
        ):
            return None
        lock = self._ticket_projection_locks.get(employee.employee_id)
        if lock is None:
            lock = asyncio.Lock()
            self._ticket_projection_locks[employee.employee_id] = lock
        return lock

    async def publish_terminal_state(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        state: ConversationTerminalState,
    ) -> None:
        await self._publish(
            employee,
            binding,
            lambda sequence: TerminalStateEnvelope(
                **self._base(employee, binding, sequence),
                type="terminal_state",
                payload=state,
            ),
        )

    async def close_admission(self) -> None:
        self._closing = True
        for employee_id, compaction_state in tuple(
            self._compaction_transitions.items()
        ):
            self._fail_compaction_transition_state(
                employee_id,
                compaction_state,
                "conversation service is shutting down",
            )
        for employee_id, cancel_state in tuple(
            self._requested_cancel_recovery_transitions.items()
        ):
            self._fail_requested_cancel_recovery_transition_state(
                employee_id, cancel_state, "conversation service is shutting down"
            )
        for stream in self._streams.values():
            for browser in stream.browsers.values():
                browser.close_reason = "conversation service is shutting down"
                browser.closed.set()
        for browsers in self._empty_browsers.values():
            for browser in browsers.values():
                browser.close_reason = "conversation service is shutting down"
                browser.closed.set()

    async def shutdown(self, deadline: float) -> None:
        self._closing = True
        for employee_id, compaction_state in tuple(
            self._compaction_transitions.items()
        ):
            self._fail_compaction_transition_state(
                employee_id,
                compaction_state,
                "conversation service is shutting down",
            )
        for employee_id, cancel_state in tuple(
            self._requested_cancel_recovery_transitions.items()
        ):
            self._fail_requested_cancel_recovery_transition_state(
                employee_id, cancel_state, "conversation service is shutting down"
            )
        for stream in self._streams.values():
            for browser in stream.browsers.values():
                browser.close_reason = "conversation service is shutting down"
                browser.closed.set()
        for browsers in self._empty_browsers.values():
            for browser in browsers.values():
                browser.close_reason = "conversation service is shutting down"
                browser.closed.set()
        sequencers = tuple(self._sequencers.values())
        for sequencer in sequencers:
            sequencer.task.cancel()
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        if sequencers and remaining > 0:
            done, pending = await asyncio.wait(
                {item.task for item in sequencers}, timeout=remaining
            )
            for task in done:
                with contextlib.suppress(BaseException):
                    task.result()
            for task in pending:
                task.cancel()
        self._sequencers.clear()

    async def websocket(self, websocket: WebSocket) -> None:
        await websocket.accept()
        subscription: BrowserSubscription | None = None
        writer: asyncio.Task[None] | None = None
        requested_employee_id: str | None = None
        try:
            first = await self._receive_action(websocket)
            if not isinstance(first, AttachAction):
                await websocket.close(code=1008, reason=INVALID_ACTION_CLOSE_REASON)
                return
            requested_employee_id = first.employee_id
            subscription = await self.attach_browser(
                first.employee_id,
                last_seen_binding_generation=first.last_seen_binding_generation,
                last_seen_sequence=first.last_seen_sequence,
            )
            if subscription.close_reason is not None:
                await websocket.close(code=1013, reason=subscription.close_reason)
                return
            writer = asyncio.create_task(
                self._websocket_writer(websocket, subscription),
                name=f"panels.acp.websocket-writer.{subscription.connection_id}",
            )
            while True:
                action = await self._receive_action(websocket)
                await self.dispatch_action(subscription.connection_id, action)
        except WebSocketDisconnect:
            pass
        except (ValueError, ValidationError, json.JSONDecodeError):
            with contextlib.suppress(Exception):
                await websocket.close(code=1008, reason=INVALID_ACTION_CLOSE_REASON)
        except Exception:
            _LOGGER.exception(
                "conversation websocket failed",
                extra={
                    "conversation_employee_id": requested_employee_id,
                    "conversation_connection_id": (
                        subscription.connection_id
                        if subscription is not None
                        else None
                    ),
                },
            )
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="conversation transport failed")
        finally:
            if writer is not None:
                writer.cancel()
                with contextlib.suppress(BaseException):
                    await writer
            if subscription is not None:
                await self.detach_browser(subscription.connection_id)

    async def _receive_action(self, websocket: WebSocket) -> BrowserAction:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(code=int(message.get("code", 1000)))
        text = message.get("text")
        if not isinstance(text, str) or message.get("bytes") is not None:
            raise ValueError("conversation websocket accepts text JSON only")
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("conversation action must be a JSON object")
        return validate_browser_action(raw)

    async def _websocket_writer(
        self, websocket: WebSocket, subscription: BrowserSubscription
    ) -> None:
        while True:
            receive = asyncio.create_task(subscription.queue.get())
            closed = asyncio.create_task(subscription.closed.wait())
            try:
                done, _pending = await asyncio.wait(
                    {receive, closed}, return_when=asyncio.FIRST_COMPLETED
                )
                if closed in done and subscription.closed.is_set():
                    await websocket.close(
                        code=1013,
                        reason=(
                            subscription.close_reason
                            or "conversation subscription closed"
                        ),
                    )
                    return
                serialized = receive.result()
                await websocket.send_text(serialized)
            finally:
                for task in (receive, closed):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(receive, closed, return_exceptions=True)

    async def _establish_stream(
        self,
        record: AcpEmployeeRecord,
        *,
        sequence_floor: int,
        force_new_binding: bool = False,
        initial_subscription: BrowserSubscription | None = None,
    ) -> bool:
        source = ConversationIngressSource(
            employee=record.employee,
            child_generation=record.child_generation,
            record_identity=record.record_identity,
        )
        runtime_handle = await self._require_registry().resolve_runtime_handle(
            record.employee.employee_id, record.binding.binding_generation
        )
        compacted_boundaries = await self.repository.resolve_compaction_boundaries(
            record.binding
        )
        return bool(await self._enqueue_and_wait(
            record.employee.employee_id,
            lambda: self._bind_stream(
                source,
                record.binding,
                runtime_handle,
                compacted_boundaries,
                sequence_floor=sequence_floor,
                force_new_binding=force_new_binding,
                initial_subscription=initial_subscription,
            ),
        ))

    def _bind_stream(
        self,
        source: ConversationIngressSource,
        binding: ConversationSessionBinding,
        runtime_handle: ConversationRuntimeHandle,
        compacted_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ],
        *,
        sequence_floor: int,
        force_new_binding: bool,
        initial_subscription: BrowserSubscription | None = None,
    ) -> bool:
        employee_id = source.employee.employee_id
        previous = self._streams.get(employee_id)
        same_binding = (
            previous is not None and previous.binding == binding and not force_new_binding
        )
        browsers = {} if previous is None else previous.browsers
        persistent = (
            previous.persistent_rejection if same_binding and previous is not None else None
        )
        sequence = max(
            sequence_floor,
            previous.sequence if same_binding and previous is not None else 0,
        )
        stream = _StreamState(
            employee=source.employee,
            binding=binding,
            source_key=self._source_key(source),
            runtime_handle=runtime_handle,
            replay_materializer=(
                runtime_handle.definition.session_notification_replay_materializer
            ),
            sequence=sequence,
            persistent_rejection=persistent,
            browsers={},
            supports_steer=runtime_handle.definition.turn_capabilities.supports_steer,
        )
        def build_replay() -> None:
            self._publish_connection_now(stream, "reset", "Conversation loaded")
            if persistent is not None:
                self._publish_rejection_now(stream, persistent)
            load_replay = self._source_load_replay.pop(stream.source_key, ())
            captured_live = self._source_capture.pop(stream.source_key, deque())
            self._publish_replay_batch_now(
                stream,
                runtime_handle,
                load_replay + tuple(captured_live),
                compacted_boundaries,
            )
            self._publish_connection_now(stream, "ready", "Ready")

        return self._commit_detached_stream_candidate(
            stream,
            browsers,
            build_replay,
            initial_subscription=initial_subscription,
        )

    def _commit_detached_stream_candidate(
        self,
        stream: _StreamState,
        existing_browsers: dict[str, BrowserSubscription],
        build_replay: Callable[[], None],
        *,
        initial_subscription: BrowserSubscription | None = None,
    ) -> bool:
        try:
            build_replay()
        except Exception:
            stream.reset_buffer_available = False
            stream.reset_buffer = []
            stream.reset_buffer_bytes = 0
        stream.browsers = dict(existing_browsers)
        if initial_subscription is not None:
            stream.browsers[initial_subscription.connection_id] = initial_subscription
        self._streams[stream.employee.employee_id] = stream
        if not stream.reset_buffer_available:
            for browser in tuple(stream.browsers.values()):
                self._close_browser_subscription(
                    stream, browser, REPLAY_UNAVAILABLE_CLOSE_REASON, "replay"
                )
            return False

        replay = self._subscriber_snapshot_bootstrap(stream)
        if replay is None:
            stream.reset_buffer_available = False
            for browser in tuple(stream.browsers.values()):
                self._close_browser_subscription(
                    stream, browser, REPLAY_UNAVAILABLE_CLOSE_REASON, "replay"
                )
            return False
        for browser in tuple(existing_browsers.values()):
            try:
                queue = cast(_BrowserOutboundQueue, browser.queue)
                queue.put_replay_cutover_nowait(replay)
            except asyncio.QueueFull:
                self._close_browser_subscription(
                    stream, browser, SLOW_CONSUMER_CLOSE_REASON, "live"
                )
        if initial_subscription is not None:
            queue = cast(_BrowserOutboundQueue, initial_subscription.queue)
            queue.set_initial_bootstrap(replay)
        return True

    def _publish_replay_batch_now(
        self,
        stream: _StreamState,
        runtime_handle: ConversationRuntimeHandle,
        replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
        compacted_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ],
    ) -> None:
        classified = runtime_handle.definition.turn_strategy.classify_replay(
            stream.binding,
            replay,
            compacted_boundaries,
        )
        for item in classified:
            if isinstance(item, ContextCompaction):
                self._publish_envelope_now(
                    stream,
                    ContextCompactionEnvelope(
                        **self._base(
                            stream.employee,
                            stream.binding,
                            stream.sequence + 1,
                        ),
                        type="context_compaction",
                        payload=item,
                    ),
                )
            else:
                self._publish_source_payload(stream, item)

    def _open_capture(self, stream: _StreamState) -> None:
        stream.ready = False
        stream.source_key = (stream.employee.employee_id, -1, -1)
        stream.runtime_handle = None

    def _attach_subscription_from_ready_stream(
        self,
        stream: _StreamState,
        subscription: BrowserSubscription,
    ) -> None:
        if not stream.reset_buffer_available:
            self._close_browser_subscription(
                stream, subscription, REPLAY_UNAVAILABLE_CLOSE_REASON, "replay"
            )
            return
        queue = cast(_BrowserOutboundQueue, subscription.queue)
        bootstrap = self._subscriber_snapshot_bootstrap(stream)
        if bootstrap is None:
            self._close_browser_subscription(
                stream, subscription, REPLAY_UNAVAILABLE_CLOSE_REASON, "replay"
            )
            return
        queue.set_initial_bootstrap(bootstrap)
        stream.browsers[subscription.connection_id] = subscription

    def _subscriber_snapshot_bootstrap(
        self,
        stream: _StreamState,
    ) -> tuple[str, ...] | None:
        snapshot = self._materialized_replay_snapshot(stream)
        if not snapshot:
            return None
        try:
            decoded = tuple(
                SERVER_ENVELOPE_ADAPTER.validate_json(
                    serialized,
                    strict=True,
                    by_alias=True,
                    by_name=False,
                )
                for serialized in snapshot
            )
        except (ValidationError, ValueError, TypeError):
            return None
        reset_envelopes = tuple(
            envelope
            for envelope in decoded
            if envelope.type == "connection" and envelope.payload.state == "reset"
        )
        if (
            len(reset_envelopes) != 1
            or decoded[0] is not reset_envelopes[0]
            or any(
                envelope.employee_id != stream.employee.employee_id
                or envelope.entity_kind != stream.employee.entity_kind
                or envelope.entity_id != stream.employee.entity_id
                or envelope.acp_session_id != stream.binding.acp_session_id
                or envelope.binding_generation
                != stream.binding.binding_generation
                for envelope in decoded
            )
            or any(
                current.sequence >= following.sequence
                for current, following in zip(decoded, decoded[1:], strict=False)
            )
            or decoded[-1].sequence != stream.sequence
            or (
                stream.replay_materializer is None
                and tuple(envelope.sequence for envelope in decoded)
                != tuple(
                    range(
                        stream.sequence - len(decoded) + 1,
                        stream.sequence + 1,
                    )
                )
            )
        ):
            return None
        ready_envelopes = tuple(
            envelope
            for envelope in decoded
            if envelope.type == "connection" and envelope.payload.state == "ready"
        )
        if not ready_envelopes:
            return None
        snapshot_bytes = sum(len(serialized.encode("utf-8")) for serialized in snapshot)
        canonical_sequences_are_contiguous = tuple(
            envelope.sequence for envelope in decoded
        ) == tuple(range(stream.sequence - len(decoded) + 1, stream.sequence + 1))
        if (
            len(ready_envelopes) == 1
            and decoded[-1] is ready_envelopes[0]
            and canonical_sequences_are_contiguous
            and snapshot_bytes <= self._reset_buffer_byte_limit
        ):
            return snapshot

        ready = ready_envelopes[-1]
        bootstrap = [
            envelope
            for envelope in decoded
            if not (
                envelope.type == "connection" and envelope.payload.state == "ready"
            )
        ]
        bootstrap.append(ready)
        first_sequence = stream.sequence - len(bootstrap) + 1
        try:
            normalized = tuple(
                SERVER_ENVELOPE_ADAPTER.validate_python(
                    self._resequence_server_envelope(
                        envelope, first_sequence + offset
                    ).model_dump(by_alias=True, exclude_none=True),
                    strict=True,
                    by_alias=True,
                    by_name=False,
                ).model_dump_json(by_alias=True, exclude_none=True)
                for offset, envelope in enumerate(bootstrap)
            )
        except (ValidationError, ValueError, TypeError):
            return None
        if (
            sum(len(serialized.encode("utf-8")) for serialized in normalized)
            > self._reset_buffer_byte_limit
        ):
            return None
        return normalized

    def _materialized_replay_snapshot(self, stream: _StreamState) -> tuple[str, ...]:
        materializer = stream.replay_materializer
        if materializer is None:
            return tuple(stream.reset_buffer)
        snapshot: list[str] = []
        for entry in stream.replay_entries.values():
            if isinstance(entry, _OrdinaryReplayEntry):
                snapshot.append(entry.serialized)
                continue
            notification = materializer.materialize(tuple(entry.notifications))
            envelope = entry.envelope.model_copy(update={"payload": notification})
            validated = SERVER_ENVELOPE_ADAPTER.validate_python(
                envelope.model_dump(by_alias=True, exclude_none=True),
                strict=True,
                by_alias=True,
                by_name=False,
            )
            snapshot.append(validated.model_dump_json(by_alias=True, exclude_none=True))
        return tuple(snapshot)

    @staticmethod
    def _resequence_server_envelope(
        envelope: ServerEnvelope,
        sequence: int,
    ) -> ServerEnvelope:
        payload = envelope.payload
        if isinstance(envelope, ActivityEnvelope):
            payload = envelope.payload.model_copy(update={"sequence": sequence})
        elif isinstance(envelope, PermissionRequestEnvelope):
            payload = envelope.payload.model_copy(
                update={"opened_sequence": sequence}
            )
        elif isinstance(envelope, PermissionOutcomeEnvelope):
            payload = envelope.payload.model_copy(
                update={"settled_sequence": sequence}
            )
        return envelope.model_copy(update={"sequence": sequence, "payload": payload})

    def _ingest_source(
        self,
        source: ConversationIngressSource,
        payload: SessionNotification | ProtocolUpdateRejectedPayload,
    ) -> None:
        source_key = self._source_key(source)
        transition = self._requested_cancel_recovery_transitions.get(
            source.employee.employee_id
        )
        if transition is not None:
            original = transition.token.original_handle
            original_key = (
                original.employee.employee_id,
                original.child_generation,
                id(original.record_identity),
            )
            if source_key == original_key:
                if len(transition.held_source_payloads) >= self._ingress_capacity:
                    self._fail_requested_cancel_recovery_transition_state(
                        source.employee.employee_id,
                        transition,
                        "requested-cancel quarantine exceeded its capacity",
                    )
                    raise RuntimeError(
                        "requested-cancel quarantine exceeded its capacity"
                    )
                transition.held_source_payloads.append(payload)
                return
        compaction_transition = self._compaction_transitions.get(
            source.employee.employee_id
        )
        if (
            compaction_transition is not None
            and compaction_transition.committed_handle is None
            and not compaction_transition.aborted
        ):
            original = compaction_transition.token.original_handle
            original_key = (
                original.employee.employee_id,
                original.child_generation,
                id(original.record_identity),
            )
            is_original_session = (
                isinstance(payload, SessionNotification)
                and payload.session_id == original.binding.acp_session_id
            )
            if source_key != original_key or not is_original_session:
                if not isinstance(payload, SessionNotification):
                    reason = (
                        "compaction candidate ingress did not name an exact session"
                    )
                    self._fail_compaction_transition_state(
                        source.employee.employee_id,
                        compaction_transition,
                        reason,
                    )
                    raise RuntimeError(reason)
                if (
                    len(compaction_transition.quarantined_ingress)
                    >= self._ingress_capacity
                ):
                    reason = "compaction transition quarantine exceeded its capacity"
                    self._fail_compaction_transition_state(
                        source.employee.employee_id,
                        compaction_transition,
                        reason,
                    )
                    raise RuntimeError(reason)
                compaction_transition.quarantined_ingress.append(
                    _CompactionQuarantinedIngress(
                        source_key=source_key,
                        session_id=payload.session_id,
                        payload=payload,
                    )
                )
                return
        stream = self._streams.get(source.employee.employee_id)
        if stream is None or stream.source_key != source_key:
            if stream is not None and stream.ready:
                return
            captured = self._source_capture.setdefault(source_key, deque())
            if len(captured) >= self._ingress_capacity:
                raise RuntimeError("pre-binding ACP ingress exceeded its capacity")
            captured.append(payload)
            return
        self._publish_source_payload(stream, payload)

    def _ingest_replay_batch(
        self,
        source: ConversationIngressSource,
        batch: ConversationIngressReplayBatch,
    ) -> None:
        source_key = self._source_key(source)
        stream = self._streams.get(source.employee.employee_id)
        if stream is None or stream.source_key != source_key:
            if stream is not None and stream.ready:
                return
            self._source_capture.pop(source_key, None)
            self._source_load_replay[source_key] = batch.items
            return
        for payload in batch.items:
            self._ingest_source(source, payload)

    def _publish_source_payload(
        self,
        stream: _StreamState,
        payload: SessionNotification | ProtocolUpdateRejectedPayload,
    ) -> None:
        if (
            isinstance(payload, SessionNotification)
            and payload.session_id != stream.binding.acp_session_id
        ):
            return
        if isinstance(payload, ProtocolUpdateRejectedPayload):
            stream.persistent_rejection = payload
            self._record_prompt_ingress(stream, payload)
            self._publish_rejection_now(stream, payload)
            return
        sequence = stream.sequence + 1
        envelope = AcpSessionUpdateEnvelope(
            **self._base(stream.employee, stream.binding, sequence),
            type="acp_session_update",
            payload=payload,
        )
        self._record_prompt_ingress(stream, payload)
        self._publish_envelope_now(stream, envelope)

    def _publish_rejection_now(
        self, stream: _StreamState, payload: ProtocolUpdateRejectedPayload
    ) -> None:
        envelope = ProtocolUpdateRejectedEnvelope(
            **self._base(stream.employee, stream.binding, stream.sequence + 1),
            type="protocol_update_rejected",
            payload=payload,
        )
        self._publish_envelope_now(stream, envelope)

    def _publish_child_death(self, source: ConversationIngressSource) -> None:
        stream = self._streams.get(source.employee.employee_id)
        if stream is None or stream.source_key != self._source_key(source):
            return
        self._publish_connection_now(stream, "error", "Employee connection failed")
        stream.ready = False

    async def _publish_human_echo(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        client_message_id: str,
        prompt: PromptRequest,
    ) -> None:
        await self._publish(
            employee,
            binding,
            lambda sequence: HumanEchoEnvelope(
                **self._base(employee, binding, sequence),
                type="human_echo",
                payload=HumanEcho(client_message_id=client_message_id, prompt=prompt),
            ),
        )

    def _publish_human_echo_now(
        self,
        stream: _StreamState,
        client_message_id: str,
        prompt: PromptRequest,
    ) -> None:
        self._publish_envelope_now(
            stream,
            HumanEchoEnvelope(
                **self._base(stream.employee, stream.binding, stream.sequence + 1),
                type="human_echo",
                payload=HumanEcho(
                    client_message_id=client_message_id,
                    prompt=prompt,
                ),
            ),
        )

    def _publish_queue_snapshot_now(
        self,
        stream: _StreamState,
        prompts: tuple[QueuedPrompt, ...],
    ) -> None:
        self._publish_envelope_now(
            stream,
            QueueSnapshotEnvelope(
                **self._base(stream.employee, stream.binding, stream.sequence + 1),
                type="queue_snapshot",
                payload=QueueSnapshot(items=prompts),
            ),
        )

    async def _publish_connection(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        state: str,
        detail: str,
    ) -> None:
        await self._enqueue_and_wait(
            employee.employee_id,
            lambda: self._publish_connection_now(
                self._require_stream(employee, binding), state, detail
            ),
        )

    def _publish_connection_now(self, stream: _StreamState, state: str, detail: str) -> None:
        envelope = ConnectionEnvelope(
            **self._base(stream.employee, stream.binding, stream.sequence + 1),
            type="connection",
            payload=ConnectionPayload(
                state=cast(Any, state),
                detail=detail,
                supports_steer=stream.supports_steer,
                reset_binding_generation=(
                    stream.binding.binding_generation if state == "reset" else None
                ),
            ),
        )
        self._publish_envelope_now(stream, envelope)
        if state == "ready":
            stream.ready = True

    async def _publish(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        builder: Callable[[int], ServerEnvelope],
        *,
        return_payload: bool = False,
    ) -> object | None:
        def operation() -> object | None:
            stream = self._require_stream(employee, binding)
            envelope = builder(stream.sequence + 1)
            self._publish_envelope_now(stream, envelope)
            return envelope.payload if return_payload else None

        return cast(
            object | None,
            await self._enqueue_and_wait(employee.employee_id, operation),
        )

    def _publish_envelope_now(self, stream: _StreamState, envelope: ServerEnvelope) -> None:
        validated = SERVER_ENVELOPE_ADAPTER.validate_python(
            envelope.model_dump(by_alias=True, exclude_none=True),
            strict=True,
            by_alias=True,
            by_name=False,
        )
        serialized = validated.model_dump_json(by_alias=True, exclude_none=True)
        stream.sequence = validated.sequence
        if validated.type == "connection" and validated.payload.state == "reset":
            stream.reset_buffer = []
            stream.reset_buffer_bytes = 0
            stream.reset_buffer_envelope_count = 0
            stream.reset_buffer_attempted_bytes = 0
            stream.reset_buffer_available = True
            stream.replay_entries = {}
        encoded_size = len(serialized.encode("utf-8"))
        stream.reset_buffer_envelope_count += 1
        stream.reset_buffer_attempted_bytes += encoded_size
        if stream.replay_materializer is not None:
            self._admit_materialized_replay_envelope(stream, validated, serialized)
        elif stream.reset_buffer_available:
            if stream.reset_buffer_bytes + encoded_size <= self._reset_buffer_byte_limit:
                stream.reset_buffer.append(serialized)
                stream.reset_buffer_bytes += encoded_size
            else:
                stream.reset_buffer_available = False
                stream.reset_buffer = []
                stream.reset_buffer_bytes = 0
        for browser in tuple(stream.browsers.values()):
            try:
                browser.queue.put_nowait(serialized)
            except asyncio.QueueFull:
                self._close_browser_subscription(
                    stream, browser, SLOW_CONSUMER_CLOSE_REASON, "live"
                )

    def _admit_materialized_replay_envelope(
        self,
        stream: _StreamState,
        envelope: ServerEnvelope,
        serialized: str,
    ) -> None:
        materializer = stream.replay_materializer
        assert materializer is not None
        admission = None
        if isinstance(envelope, AcpSessionUpdateEnvelope) and isinstance(
            envelope.payload, SessionNotification
        ):
            admission = materializer.classify(envelope.payload)
        if admission is None:
            ordinary_key = _OrdinaryReplayEntryKey(envelope.sequence)
            stored_bytes = len(serialized.encode("utf-8"))
            stream.replay_entries[ordinary_key] = _OrdinaryReplayEntry(
                sequence=envelope.sequence,
                serialized=serialized,
                stored_bytes=stored_bytes,
            )
            stream.reset_buffer_bytes += stored_bytes
            stream.reset_buffer_available = (
                stream.reset_buffer_bytes <= self._reset_buffer_byte_limit
            )
            return
        materialized_envelope = cast(AcpSessionUpdateEnvelope, envelope)
        notification = materialized_envelope.payload
        materialized_key = _MaterializedReplayEntryKey(admission.slot_key)
        existing = stream.replay_entries.pop(materialized_key, None)
        if isinstance(existing, _MaterializedReplayEntry):
            stream.reset_buffer_bytes -= existing.stored_bytes
        notifications = (
            existing.notifications
            if isinstance(existing, _MaterializedReplayEntry)
            and admission.disposition == "accumulate"
            else []
        )
        notifications.append(notification)
        if admission.disposition == "replace":
            accumulated_serialized_value_bytes = 0
            stored_bytes = len(serialized.encode("utf-8"))
        else:
            accumulated_serialized_value_bytes = (
                existing.accumulated_serialized_value_bytes
                if isinstance(existing, _MaterializedReplayEntry)
                else 0
            ) + admission.serialized_accumulation_fragment_bytes
            serialized_envelope_bytes = len(serialized.encode("utf-8"))
            envelope_bytes_without_notification = (
                serialized_envelope_bytes - admission.serialized_notification_bytes
            )
            stored_bytes = (
                envelope_bytes_without_notification
                + admission.serialized_materialized_base_bytes
                + accumulated_serialized_value_bytes
            )
        stream.replay_entries[materialized_key] = _MaterializedReplayEntry(
            sequence=envelope.sequence,
            envelope=materialized_envelope,
            notifications=notifications,
            accumulated_serialized_value_bytes=accumulated_serialized_value_bytes,
            stored_bytes=stored_bytes,
        )
        stream.reset_buffer_bytes += stored_bytes
        stream.reset_buffer_available = (
            stream.reset_buffer_bytes <= self._reset_buffer_byte_limit
        )

    def _close_browser_subscription(
        self,
        stream: _StreamState,
        browser: BrowserSubscription,
        reason: str,
        phase: Literal["replay", "live"],
    ) -> None:
        stream.browsers.pop(browser.connection_id, None)
        browser.close_reason = reason
        browser.closed.set()
        if not browser.closure_logged:
            browser.closure_logged = True
            _LOGGER.warning(
                json.dumps(
                    {
                        "event": "conversation_browser_subscription_closed",
                        "closure_phase": phase,
                        "employee_id": stream.employee.employee_id,
                        "acp_session_id": stream.binding.acp_session_id,
                        "binding_generation": stream.binding.binding_generation,
                        "connection_id": browser.connection_id,
                        "close_reason": reason,
                        "replay_envelope_count": stream.reset_buffer_envelope_count,
                        "replay_byte_count": stream.reset_buffer_attempted_bytes,
                        "replay_byte_limit": self._reset_buffer_byte_limit,
                        "live_queue_envelope_count": (
                            browser.queue.live_qsize()
                            if isinstance(browser.queue, _BrowserOutboundQueue)
                            else browser.queue.qsize()
                        ),
                        "live_queue_capacity": browser.queue.maxsize,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        self._claim_permission_detach(browser)

    def _claim_permission_detach(
        self, subscription: BrowserSubscription
    ) -> asyncio.Task[None]:
        if subscription.permission_detach_task is None:
            subscription.permission_detach_task = asyncio.create_task(
                self._require_permission_broker().detach_browser(
                    subscription.connection_id
                ),
                name=(
                    "panels.acp.permission-browser-detach."
                    f"{subscription.connection_id}"
                ),
            )
        return subscription.permission_detach_task

    async def _detach_subscription_permission(
        self, subscription: BrowserSubscription
    ) -> None:
        await self._claim_permission_detach(subscription)

    async def _wait_for_compaction_transition(
        self, employee_id: str
    ) -> ConversationSessionBinding | None:
        state = self._compaction_transitions.get(employee_id)
        if state is None:
            return None
        original_binding = state.token.original_handle.binding
        await state.settled.wait()
        if state.failure_reason is not None:
            raise RuntimeError(state.failure_reason)
        return original_binding

    async def _wait_for_compaction_and_requested_cancel_recovery(
        self, employee_id: str
    ) -> ConversationSessionBinding | None:
        transitioned_from = await self._wait_for_compaction_transition(employee_id)
        state = self._requested_cancel_recovery_transitions.get(employee_id)
        if state is None:
            return transitioned_from
        await state.settled.wait()
        if state.failure_reason is not None:
            raise RuntimeError(state.failure_reason)
        return transitioned_from

    def _require_requested_cancel_recovery_transition(
        self, token: RequestedCancelRecoveryTransitionToken
    ) -> _RequestedCancelRecoveryTransitionState:
        employee_id = token.original_handle.employee.employee_id
        state = self._requested_cancel_recovery_transitions.get(employee_id)
        if state is None or state.token is not token:
            raise RuntimeError(
                "requested-cancel recovery transition token is stale or settled"
            )
        if state.failure_reason is not None:
            raise RuntimeError(state.failure_reason)
        return state

    def _expire_requested_cancel_recovery_transition(
        self, employee_id: str, transaction_identity: object
    ) -> None:
        state = self._requested_cancel_recovery_transitions.get(employee_id)
        if (
            state is None
            or state.token.transaction_identity is not transaction_identity
            or state.settled.is_set()
        ):
            return
        self._fail_requested_cancel_recovery_transition_state(
            employee_id,
            state,
            "requested-cancel recovery transition deadline expired",
        )

    def _fail_requested_cancel_recovery_transition_state(
        self,
        employee_id: str,
        state: _RequestedCancelRecoveryTransitionState,
        reason: str,
    ) -> None:
        current = self._requested_cancel_recovery_transitions.get(employee_id)
        if current is not state or state.settled.is_set():
            return
        state.failure_reason = reason
        state.held_source_payloads.clear()
        stream = self._streams.get(employee_id)
        if stream is not None and self._runtime_handles_match(
            self._runtime_handle_for_stream(stream), state.token.original_handle
        ):
            self._publish_connection_now(
                stream, "error", "Employee connection failed"
            )
            stream.ready = False
            stream.runtime_handle = None
            stream.source_key = (employee_id, -1, -1)
        state.timeout_handle.cancel()
        self._requested_cancel_recovery_transitions.pop(employee_id, None)
        state.settled.set()

    def _settle_requested_cancel_recovery_transition_state(
        self, employee_id: str, state: _RequestedCancelRecoveryTransitionState
    ) -> None:
        current = self._requested_cancel_recovery_transitions.get(employee_id)
        if current is not state or state.settled.is_set():
            raise RuntimeError(
                "requested-cancel recovery transition was already settled"
            )
        state.timeout_handle.cancel()
        self._requested_cancel_recovery_transitions.pop(employee_id, None)
        state.settled.set()

    def _require_compaction_transition(
        self, token: CompactionTransitionToken
    ) -> _CompactionTransitionState:
        employee_id = token.original_handle.employee.employee_id
        state = self._compaction_transitions.get(employee_id)
        if state is None or state.token is not token:
            raise RuntimeError("compaction transition token is stale or already settled")
        if state.failure_reason is not None:
            raise RuntimeError(state.failure_reason)
        return state

    def _expire_compaction_transition(
        self, employee_id: str, transaction_identity: object
    ) -> None:
        state = self._compaction_transitions.get(employee_id)
        if (
            state is None
            or state.token.transaction_identity is not transaction_identity
            or state.settled.is_set()
        ):
            return
        self._fail_compaction_transition_state(
            employee_id,
            state,
            "compaction transition deadline expired",
        )

    def _fail_compaction_transition_state(
        self,
        employee_id: str,
        state: _CompactionTransitionState,
        reason: str,
    ) -> None:
        current = self._compaction_transitions.get(employee_id)
        if current is not state or state.settled.is_set():
            return
        state.failure_reason = reason
        state.quarantined_ingress.clear()
        state.timeout_handle.cancel()
        self._compaction_transitions.pop(employee_id, None)
        state.settled.set()

    def _settle_compaction_transition_state(
        self, employee_id: str, state: _CompactionTransitionState
    ) -> None:
        current = self._compaction_transitions.get(employee_id)
        if current is not state or state.settled.is_set():
            raise RuntimeError("compaction transition was already settled")
        state.quarantined_ingress.clear()
        state.timeout_handle.cancel()
        self._compaction_transitions.pop(employee_id, None)
        state.settled.set()

    @staticmethod
    def _runtime_handles_match(
        first: ConversationRuntimeHandle | None,
        second: ConversationRuntimeHandle,
    ) -> bool:
        return (
            first is not None
            and first.employee == second.employee
            and first.binding == second.binding
            and first.child_generation == second.child_generation
            and first.child is second.child
            and first.definition is second.definition
            and first.record_identity is second.record_identity
        )

    async def _enqueue_and_wait(self, employee_id: str, operation: Callable[[], Any]) -> Any:
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        sequencer = self._sequencer(employee_id)
        await sequencer.queue.put(_SequencedOperation(operation, future))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            future.add_done_callback(self._consume_abandoned_sequencer_result)
            raise

    @staticmethod
    def _consume_abandoned_sequencer_result(future: asyncio.Future[Any]) -> None:
        if future.cancelled():
            return
        with contextlib.suppress(BaseException):
            future.result()

    async def _admit(
        self,
        employee_id: str,
        operation: Callable[[], Any],
        *,
        on_failure: Callable[[Exception], None] | None = None,
    ) -> None:
        sequencer = self._sequencer(employee_id)
        await sequencer.queue.put(_SequencedOperation(operation, None, on_failure))

    def _sequencer(self, employee_id: str) -> _EmployeeSequencer:
        sequencer = self._sequencers.get(employee_id)
        if sequencer is not None:
            return sequencer
        queue: asyncio.Queue[_SequencedOperation] = asyncio.Queue(maxsize=self._ingress_capacity)
        sequencer = _EmployeeSequencer(
            queue=queue,
            task=asyncio.create_task(
                self._drain(employee_id, queue),
                name=f"panels.acp.hub-sequencer.{employee_id}",
            ),
        )
        self._sequencers[employee_id] = sequencer
        return sequencer

    async def _drain(self, employee_id: str, queue: asyncio.Queue[_SequencedOperation]) -> None:
        del employee_id
        while True:
            item = await queue.get()
            try:
                result = item.operation()
            except Exception as error:
                if item.future is not None and not item.future.done():
                    item.future.set_exception(error)
                elif item.failure is not None:
                    item.failure(error)
            else:
                if item.future is not None and not item.future.done():
                    item.future.set_result(result)

    def _require_stream(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
    ) -> _StreamState:
        stream = self._streams.get(employee.employee_id)
        if stream is None or stream.binding != binding or stream.employee != employee:
            raise RuntimeError("conversation publication names a stale stream")
        return stream

    def _stream_for_connection(self, connection_id: str) -> _StreamState:
        for stream in self._streams.values():
            if connection_id in stream.browsers:
                return stream
        raise ValueError("browser connection is not attached")

    def _runtime_handle_for_stream(self, stream: _StreamState) -> ConversationRuntimeHandle | None:
        handle = stream.runtime_handle
        if handle is None:
            return None
        if (
            handle.employee != stream.employee
            or handle.binding != stream.binding
            or (
                handle.employee.employee_id,
                handle.child_generation,
                id(handle.record_identity),
            )
            != stream.source_key
        ):
            return None
        return handle

    def _record_prompt_ingress(
        self,
        stream: _StreamState,
        payload: SessionNotification | ProtocolUpdateRejectedPayload,
    ) -> None:
        prefix = (
            stream.employee.employee_id,
            stream.binding.binding_generation,
            stream.source_key[1],
            stream.source_key[2],
        )
        for key, state in tuple(self._prompt_ingress.items()):
            if key[:4] != prefix:
                continue
            if isinstance(payload, ProtocolUpdateRejectedPayload):
                state.rejection_reason = payload.status
            else:
                state.notifications.append(payload)

    def _schedule_source_failure(self, source: ConversationIngressSource, error: Exception) -> None:
        async def close_exact_source() -> None:
            registry = self._require_registry()
            try:
                handle = await registry.resolve_runtime_handle_for_generation(
                    source.employee.employee_id, source.child_generation
                )
            except Exception:
                return
            if handle.record_identity is not source.record_identity:
                return
            await handle.child.close()

        task = asyncio.create_task(
            close_exact_source(),
            name=(
                f"panels.acp.source-failure.{source.employee.employee_id}.{source.child_generation}"
            ),
        )
        task.add_done_callback(lambda completed: self._consume_background(completed, error))

    def _handle_ingress_failure(
        self,
        source: ConversationIngressSource,
        payload: ConversationIngressTransition,
        error: Exception,
    ) -> None:
        if isinstance(payload, ConversationIngressReplayBatch):
            transition_kind = "replay"
            transition_count = len(payload.items)
        else:
            transition_kind = "ingress"
            transition_count = 1
        _LOGGER.error(
            json.dumps(
                {
                    "event": "conversation_ingress_operation_failed",
                    "phase": "sequencer_drain",
                    "employee_id": source.employee.employee_id,
                    "child_generation": source.child_generation,
                    "transition_kind": transition_kind,
                    "transition_count": transition_count,
                    "exception_type": type(error).__name__,
                },
                separators=(",", ":"),
            )
        )
        self._schedule_source_failure(source, error)

    @staticmethod
    def _tracked_key(handle: TrackedTurnHandle) -> tuple[str, int, int, int]:
        return (
            handle.employee_id,
            handle.binding_generation,
            handle.child_generation,
            handle.prompt_epoch,
        )

    @staticmethod
    def _prompt_ingress_key(
        handle: ConversationRuntimeHandle, prompt_epoch: int
    ) -> tuple[str, int, int, int, int]:
        return (
            handle.employee.employee_id,
            handle.binding.binding_generation,
            handle.child_generation,
            id(handle.record_identity),
            prompt_epoch,
        )

    @staticmethod
    def _source_key(source: ConversationIngressSource) -> tuple[str, int, int]:
        return (
            source.employee.employee_id,
            source.child_generation,
            id(source.record_identity),
        )

    @staticmethod
    def _source_key_from_record(record: AcpEmployeeRecord) -> tuple[str, int, int]:
        return (
            record.employee.employee_id,
            record.child_generation,
            id(record.record_identity),
        )

    @staticmethod
    def _base(
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        sequence: int,
    ) -> _EnvelopeBase:
        return {
            "wire_version": 1,
            "employee_id": employee.employee_id,
            "entity_kind": employee.entity_kind,
            "entity_id": employee.entity_id,
            "acp_session_id": binding.acp_session_id,
            "binding_generation": binding.binding_generation,
            "sequence": sequence,
        }

    def _require_registry(self) -> AcpEmployeeRegistry:
        if self._registry is None:
            raise RuntimeError("conversation hub registry is not bound")
        return self._registry

    def _require_broker(self) -> ConversationTurnBroker:
        if self._broker is None:
            raise RuntimeError("conversation hub broker is not bound")
        return self._broker

    def _require_permission_broker(self) -> ConversationPermissionBroker:
        if self._permission_broker is None:
            raise RuntimeError("conversation hub permission broker is not bound")
        return self._permission_broker

    def _next_connection_id(self) -> str:
        self._connection_counter += 1
        return f"browser-{self._connection_counter}"

    def _next_worker_message_id(self) -> str:
        self._worker_message_counter += 1
        return f"worker-{self._worker_message_counter}"

    @staticmethod
    def _consume_background(task: asyncio.Task[None], original: Exception) -> None:
        del original
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            task.result()
