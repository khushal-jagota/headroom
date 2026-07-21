"""Official-SDK scripted reference subject for the reusable ACP probes."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION
from acp.connection import StreamDirection, StreamEvent
from acp.interfaces import Agent
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    ClientCapabilities,
    Implementation,
    PermissionOption,
    PromptResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionInfoUpdate,
    SessionNotification,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UserMessageChunk,
)
from acp.stdio import spawn_agent_process
from tests.support.acp_conformance import (
    CallbackOrderEvidence,
    CompactionEvidence,
    DeliveryCapabilityEvidence,
    LoadReplayEvidence,
    MessageGroupingEvidence,
    PermissionSettlementEvidence,
    PlanToolEvidence,
    ProtocolRejectionEvidence,
    RefreshEvidence,
    ThoughtEvidence,
)

from planner.conversation import (
    ConversationEmployee,
    ConversationSessionBinding,
    OrderedAcpConversationIngress,
    ProtocolUpdateRejectedEnvelope,
    ProtocolUpdateRejectedPayload,
    TurnDeliveryReceipt,
    session_update_envelope_or_rejection,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTED_AGENT_PATH = REPOSITORY_ROOT / "tests" / "support" / "acp_scripted_agent.py"


@dataclass(frozen=True, slots=True)
class CallbackRecord:
    notification: SessionNotification
    trace_position: int


class ScriptedAcpClient:
    def __init__(self) -> None:
        self.trace: list[str] = []
        self.callback_records: list[CallbackRecord] = []
        self.broadcast_notifications: list[SessionNotification] = []
        self.permission_requests: list[RequestPermissionRequest] = []
        self.raw_stream_events: list[StreamEvent] = []
        self.protocol_rejections: list[ProtocolUpdateRejectedPayload] = []
        self._ordered_ingress: OrderedAcpConversationIngress | None = None
        self._notification_condition = asyncio.Condition()
        self._blocked_consumption_entered: asyncio.Event | None = None
        self._blocked_consumption_release: asyncio.Event | None = None
        self._observed_load_request_id: object | None = None
        self.load_response_observed = asyncio.Event()

    def start_ingress(self) -> None:
        if self._ordered_ingress is not None:
            raise RuntimeError("ACP callback ingress is already running")
        self._ordered_ingress = OrderedAcpConversationIngress(self._consume_ingress)
        self._ordered_ingress.start()

    async def stop_ingress(self) -> None:
        if self._ordered_ingress is None:
            return
        await self._ordered_ingress.close()
        self._ordered_ingress = None

    def on_connect(self, agent: Agent) -> None:
        del agent

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        notification = SessionNotification(
            session_id=session_id,
            update=update,
            field_meta=kwargs or None,
        )
        if self._ordered_ingress is None:
            raise RuntimeError("ACP callback ingress is not running")
        self._ordered_ingress.fulfill_typed(notification)

    async def _consume_ingress(
        self, item: SessionNotification | ProtocolUpdateRejectedPayload
    ) -> None:
        async with self._notification_condition:
            if self._blocked_consumption_entered is not None:
                entered = self._blocked_consumption_entered
                release = self._blocked_consumption_release
                self._blocked_consumption_entered = None
                self._blocked_consumption_release = None
                entered.set()
                assert release is not None
                await release.wait()
            if isinstance(item, ProtocolUpdateRejectedPayload):
                self.protocol_rejections.append(item)
                self._notification_condition.notify_all()
                return
            trace_position = len(self.trace)
            self.trace.append(f"consume:{trace_position}")
            reduced_record = self._reduce(item, trace_position)
            self.callback_records.append(reduced_record)
            self._broadcast(reduced_record.notification)
            self._notification_condition.notify_all()

    def _broadcast(self, notification: SessionNotification) -> None:
        self.broadcast_notifications.append(notification)

    def _reduce(
        self, notification: SessionNotification, trace_position: int
    ) -> CallbackRecord:
        return CallbackRecord(notification=notification, trace_position=trace_position)

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        self.permission_requests.append(
            RequestPermissionRequest(
                session_id=session_id,
                tool_call=tool_call,
                options=options,
                field_meta=kwargs or None,
            )
        )
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=options[0].option_id)
        )

    async def wait_for_callback_count(self, expected_count: int) -> None:
        async with self._notification_condition:
            await asyncio.wait_for(
                self._notification_condition.wait_for(
                    lambda: len(self.callback_records) >= expected_count
                ),
                timeout=3,
            )

    def observe_stream(self, event: StreamEvent) -> None:
        self.raw_stream_events.append(event)
        if (
            event.direction is StreamDirection.OUTGOING
            and event.message.get("method") == "session/load"
        ):
            self._observed_load_request_id = event.message.get("id")
            self.load_response_observed.clear()
        elif (
            event.direction is StreamDirection.INCOMING
            and self._observed_load_request_id is not None
            and event.message.get("id") == self._observed_load_request_id
            and "method" not in event.message
        ):
            self.load_response_observed.set()
        if self._ordered_ingress is not None:
            self._ordered_ingress.observe_stream(event)

    def block_next_consumption(
        self, entered: asyncio.Event, release: asyncio.Event
    ) -> None:
        if self._blocked_consumption_entered is not None:
            raise RuntimeError("a reference consumption block is already armed")
        self._blocked_consumption_entered = entered
        self._blocked_consumption_release = release

    async def load_session(self, connection: Any, **kwargs: Any) -> Any:
        if self._ordered_ingress is None:
            raise RuntimeError("ACP callback ingress is not running")
        self._ordered_ingress.begin_load_epoch()
        try:
            response = await connection.load_session(**kwargs)
        except BaseException:
            self._ordered_ingress.abort_load_epoch()
            raise
        await self._ordered_ingress.finish_load_epoch()
        return response


@dataclass(frozen=True, slots=True)
class ScriptedProcessExercise:
    agent_name: str
    agent_version: str
    session_id: str
    default_prompt_response: PromptResponse
    delayed_prompt_response: PromptResponse
    cancel_prompt_response: PromptResponse
    live_default: tuple[SessionNotification, ...]
    replay_default: tuple[SessionNotification, ...]
    load_replay_positions: tuple[int, ...]
    load_response_position: int
    missing_live: tuple[SessionNotification, ...]
    missing_replay: tuple[SessionNotification, ...]
    original_binding: ConversationSessionBinding
    refreshed_binding: ConversationSessionBinding
    concurrent_notifications: tuple[SessionNotification, ...]
    concurrent_broadcast_notifications: tuple[SessionNotification, ...]
    explicit_compaction_notifications: tuple[SessionNotification, ...]
    automatic_compaction_notifications: tuple[SessionNotification, ...]
    permission_requests: tuple[RequestPermissionRequest, ...]
    malformed_notifications: tuple[dict[str, Any], ...]
    stream_events: tuple[StreamEvent, ...]
    stderr_text: str
    process_return_code: int


@dataclass(frozen=True, slots=True)
class DeterministicDeathEvidence:
    process_return_code: int
    prompt_failed: bool
    stderr_text: str


class _StoredBindingHolder:
    def __init__(self, binding: ConversationSessionBinding) -> None:
        self._binding = binding

    @property
    def binding(self) -> ConversationSessionBinding:
        return self._binding

    async def refresh(
        self, client: ScriptedAcpClient, connection: Any, *, cwd: Path
    ) -> ConversationSessionBinding:
        await client.load_session(
            connection,
            cwd=str(cwd),
            session_id=self._binding.acp_session_id,
        )
        return self._binding


def _text(text: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


async def run_scripted_process_exercise() -> ScriptedProcessExercise:
    client = ScriptedAcpClient()
    client.start_ingress()
    stderr_task: asyncio.Task[bytes] | None = None
    initialize_response = None
    session_id = ""
    default_response = None
    delayed_response = None
    cancel_response = None
    live_default: tuple[SessionNotification, ...] = ()
    replay_default: tuple[SessionNotification, ...] = ()
    load_positions: tuple[int, ...] = ()
    load_response_position = 0
    missing_live: tuple[SessionNotification, ...] = ()
    missing_replay: tuple[SessionNotification, ...] = ()
    original_binding: ConversationSessionBinding | None = None
    refreshed_binding: ConversationSessionBinding | None = None
    concurrent_notifications: tuple[SessionNotification, ...] = ()
    concurrent_broadcast_notifications: tuple[SessionNotification, ...] = ()
    explicit_notifications: tuple[SessionNotification, ...] = ()
    automatic_notifications: tuple[SessionNotification, ...] = ()

    async with spawn_agent_process(
        client,
        sys.executable,
        str(SCRIPTED_AGENT_PATH),
        cwd=REPOSITORY_ROOT,
        observers=[client.observe_stream],
    ) as (connection, process):
        assert process.stderr is not None
        stderr_task = asyncio.create_task(process.stderr.read())
        initialize_response = await connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(name="panels-conformance-client", version="1.0.0"),
        )
        new_response = await connection.new_session(cwd=str(REPOSITORY_ROOT))
        session_id = new_response.session_id
        binding_holder = _StoredBindingHolder(
            ConversationSessionBinding(
                employee_id="employee-acp-00",
                acp_session_id=session_id,
                backend_key="scripted",
                binding_generation=1,
            )
        )
        original_binding = binding_holder.binding

        start = len(client.callback_records)
        default_response = await connection.prompt(
            session_id=session_id,
            prompt=[_text("default prompt")],
            script="default",
        )
        await client.wait_for_callback_count(start + 9)
        live_default = tuple(record.notification for record in client.callback_records[start:])

        replay_start = len(client.callback_records)
        refreshed_binding = await binding_holder.refresh(
            client, connection, cwd=REPOSITORY_ROOT
        )
        load_response_position = len(client.trace)
        client.trace.append("load_response")
        await client.wait_for_callback_count(replay_start + len(live_default))
        replay_records = client.callback_records[replay_start : replay_start + len(live_default)]
        replay_default = tuple(record.notification for record in replay_records)
        load_positions = tuple(record.trace_position for record in replay_records)

        missing_start = len(client.callback_records)
        await connection.prompt(
            session_id=session_id,
            prompt=[_text("missing IDs")],
            script="missing_ids",
        )
        await client.wait_for_callback_count(missing_start + 9)
        missing_live = tuple(
            record.notification for record in client.callback_records[missing_start:]
        )

        missing_replay_start = len(client.callback_records)
        expected_history_count = len(live_default) + len(missing_live)
        await client.load_session(
            connection, cwd=str(REPOSITORY_ROOT), session_id=session_id
        )
        await client.wait_for_callback_count(missing_replay_start + expected_history_count)
        replayed_history = client.callback_records[
            missing_replay_start : missing_replay_start + expected_history_count
        ]
        missing_replay = tuple(
            record.notification
            for record in replayed_history
            if getattr(record.notification.update, "message_id", "present") is None
        )

        permission_callback_start = len(client.callback_records)
        permission_start = len(client.permission_requests)
        await connection.prompt(
            session_id=session_id,
            prompt=[_text("permission")],
            script="permission",
        )
        assert len(client.permission_requests) == permission_start + 1
        await client.wait_for_callback_count(permission_callback_start + 3)

        cancel_start = len(client.callback_records)
        pending_prompt = asyncio.create_task(
            connection.prompt(
                session_id=session_id,
                prompt=[_text("wait")],
                script="wait_for_cancel",
            )
        )
        await client.wait_for_callback_count(cancel_start + 1)
        await connection.cancel(session_id=session_id)
        cancel_response = await pending_prompt

        concurrent_start = len(client.callback_records)
        concurrent_broadcast_start = len(client.broadcast_notifications)
        await connection.prompt(
            session_id=session_id,
            prompt=[_text("concurrent")],
            script="concurrent",
        )
        await client.wait_for_callback_count(concurrent_start + 4)
        concurrent_notifications = tuple(
            record.notification for record in client.callback_records[concurrent_start:]
        )
        concurrent_broadcast_notifications = tuple(
            client.broadcast_notifications[concurrent_broadcast_start:]
        )

        automatic_start = len(client.callback_records)
        await connection.prompt(
            session_id=session_id,
            prompt=[_text("automatic compaction")],
            script="automatic_compaction",
        )
        await client.wait_for_callback_count(automatic_start + 11)
        automatic_notifications = tuple(
            record.notification for record in client.callback_records[automatic_start:]
        )

        explicit_start = len(client.callback_records)
        await connection.prompt(
            session_id=session_id,
            prompt=[_text("explicit compaction")],
            script="explicit_compaction",
        )
        await client.wait_for_callback_count(explicit_start + 11)
        explicit_notifications = tuple(
            record.notification for record in client.callback_records[explicit_start:]
        )

        delayed_start = len(client.callback_records)
        delayed_response = await connection.prompt(
            session_id=session_id,
            prompt=[_text("delayed callback")],
            script="delayed",
        )
        await client.wait_for_callback_count(delayed_start + 9)

        await connection.ext_method(
            "emit_malformed_update", {"sessionId": session_id, "partial": False}
        )
        await connection.ext_method(
            "emit_malformed_update", {"sessionId": session_id, "partial": True}
        )

    await client.stop_ingress()
    assert process is not None
    assert stderr_task is not None
    stderr_text = (await stderr_task).decode()
    assert initialize_response is not None
    assert initialize_response.agent_info is not None
    assert default_response is not None
    assert delayed_response is not None
    assert cancel_response is not None
    assert original_binding is not None
    assert refreshed_binding is not None
    malformed_notifications = _malformed_notifications_from_stream(
        tuple(client.raw_stream_events)
    )
    return ScriptedProcessExercise(
        agent_name=initialize_response.agent_info.name,
        agent_version=initialize_response.agent_info.version,
        session_id=session_id,
        default_prompt_response=default_response,
        delayed_prompt_response=delayed_response,
        cancel_prompt_response=cancel_response,
        live_default=live_default,
        replay_default=replay_default,
        load_replay_positions=load_positions,
        load_response_position=load_response_position,
        missing_live=missing_live,
        missing_replay=missing_replay,
        original_binding=original_binding,
        refreshed_binding=refreshed_binding,
        concurrent_notifications=concurrent_notifications,
        concurrent_broadcast_notifications=concurrent_broadcast_notifications,
        explicit_compaction_notifications=explicit_notifications,
        automatic_compaction_notifications=automatic_notifications,
        permission_requests=tuple(client.permission_requests),
        malformed_notifications=malformed_notifications,
        stream_events=tuple(client.raw_stream_events),
        stderr_text=stderr_text,
        process_return_code=process.returncode or 0,
    )


async def run_deterministic_death_exercise() -> DeterministicDeathEvidence:
    client = ScriptedAcpClient()
    client.start_ingress()
    stderr_task: asyncio.Task[bytes] | None = None
    prompt_failed = False
    async with spawn_agent_process(
        client,
        sys.executable,
        str(SCRIPTED_AGENT_PATH),
        cwd=REPOSITORY_ROOT,
        observers=[client.observe_stream],
    ) as (connection, process):
        assert process.stderr is not None
        stderr_task = asyncio.create_task(process.stderr.read())
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        new_response = await connection.new_session(cwd=str(REPOSITORY_ROOT))
        try:
            await connection.prompt(
                session_id=new_response.session_id,
                prompt=[_text("die")],
                script="die",
            )
        except (ConnectionError, asyncio.CancelledError):
            prompt_failed = True
        await asyncio.wait_for(process.wait(), timeout=3)
    await client.stop_ingress()
    assert process is not None
    assert stderr_task is not None
    return DeterministicDeathEvidence(
        process_return_code=process.returncode or 0,
        prompt_failed=prompt_failed,
        stderr_text=(await stderr_task).decode(),
    )


class _ReferencePermissionBroker:
    def __init__(self) -> None:
        self._pending_request_ids: set[str] = set()
        self._settlement_counts: dict[tuple[str, str], int] = {}

    def open(self, request_id: str) -> None:
        self._pending_request_ids.add(request_id)

    def settle(self, request_id: str, cause: str) -> None:
        if request_id not in self._pending_request_ids:
            return
        self._pending_request_ids.remove(request_id)
        key = (request_id, cause)
        self._settlement_counts[key] = self._settlement_counts.get(key, 0) + 1

    def settlement_count(self, request_id: str, cause: str) -> int:
        return self._settlement_counts.get((request_id, cause), 0)


class _DeclaredSteerOperation:
    supports_steer = True

    def __init__(self) -> None:
        self.receipts: list[TurnDeliveryReceipt] = []

    def steer(self, client_message_id: str) -> TurnDeliveryReceipt:
        if not self.supports_steer:
            receipt = TurnDeliveryReceipt(
                client_message_id=client_message_id,
                choice="steer",
                state="rejected",
                reason="Steer unavailable",
            )
        else:
            receipt = TurnDeliveryReceipt(
                client_message_id=client_message_id,
                choice="steer",
                state="accepted",
            )
        self.receipts.append(receipt)
        return receipt


class _ReferenceTurnBroker:
    def __init__(self) -> None:
        self.operations: list[str] = []
        self.receipts: list[TurnDeliveryReceipt] = []

    def queue(self, client_message_id: str) -> TurnDeliveryReceipt:
        self.operations.append("queue")
        receipt = TurnDeliveryReceipt(
            client_message_id=client_message_id,
            choice="queue",
            state="queued",
            queue_position=1,
        )
        self.receipts.append(receipt)
        return receipt

    def send_now(self, client_message_id: str) -> TurnDeliveryReceipt:
        self.operations.append("send_now")
        receipt = TurnDeliveryReceipt(
            client_message_id=client_message_id,
            choice="send_now",
            state="started",
        )
        self.receipts.append(receipt)
        return receipt


@dataclass(frozen=True, slots=True)
class _ObservedCompaction:
    states: tuple[str, ...]


class _ReferenceCompactionObserver:
    def observe_explicit(
        self, notifications: tuple[SessionNotification, ...]
    ) -> _ObservedCompaction:
        states: list[str] = []
        for notification in notifications:
            update = notification.update
            if not isinstance(update, SessionInfoUpdate) or not update.field_meta:
                continue
            scripted = update.field_meta.get("scripted")
            if not isinstance(scripted, dict):
                continue
            compaction = scripted.get("compaction")
            if not isinstance(compaction, dict) or compaction.get("trigger") != "explicit":
                continue
            state = compaction.get("state")
            if isinstance(state, str):
                states.append(state)
        return _ObservedCompaction(states=tuple(states))

    def observe_automatic(
        self, notifications: tuple[SessionNotification, ...]
    ) -> _ObservedCompaction:
        states: list[str] = []
        for notification in notifications:
            update = notification.update
            if not isinstance(update, SessionInfoUpdate) or not update.field_meta:
                continue
            hermes = update.field_meta.get("hermes")
            if not isinstance(hermes, dict):
                continue
            provenance = hermes.get("sessionProvenance")
            if not isinstance(provenance, dict) or provenance.get("reason") != "compression":
                continue
            state = provenance.get("state")
            if isinstance(state, str):
                states.append(state)
        return _ObservedCompaction(states=tuple(states))


@dataclass(frozen=True, slots=True)
class _TypedTranscriptReduction:
    thought_chunks: tuple[str, ...]
    assistant_outputs: tuple[str, ...]


class ReferenceAcpConformanceSubject:
    def __init__(self, exercise: ScriptedProcessExercise) -> None:
        self._exercise = exercise
        self._steer_operation = _DeclaredSteerOperation()
        self._steer_receipt = self._steer_operation.steer("steer-client-message")
        self._turn_broker = _ReferenceTurnBroker()
        self._turn_broker.queue("queued-client-message")
        self._turn_broker.send_now("send-now-client-message")
        compaction_observer = _ReferenceCompactionObserver()
        self._explicit_compaction = compaction_observer.observe_explicit(
            exercise.explicit_compaction_notifications
        )
        self._automatic_compaction = compaction_observer.observe_automatic(
            exercise.automatic_compaction_notifications
        )

    @classmethod
    async def create(cls) -> ReferenceAcpConformanceSubject:
        return cls(await run_scripted_process_exercise())

    @property
    def process_exercise(self) -> ScriptedProcessExercise:
        return self._exercise

    def observe_load_replay(self) -> LoadReplayEvidence:
        return LoadReplayEvidence(
            replay_callback_positions=self._exercise.load_replay_positions,
            load_response_position=self._exercise.load_response_position,
        )

    def observe_typed_thought(self) -> ThoughtEvidence:
        live_updates = tuple(item.update for item in self._exercise.live_default)
        replay_updates = tuple(item.update for item in self._exercise.replay_default)
        live_reduction = _reduce_typed_transcript(self._exercise.live_default)
        replay_reduction = _reduce_typed_transcript(self._exercise.replay_default)
        return ThoughtEvidence(
            live_update_types=tuple(update.session_update for update in live_updates),
            replay_update_types=tuple(update.session_update for update in replay_updates),
            live_thought_chunks=live_reduction.thought_chunks,
            replay_thought_chunks=replay_reduction.thought_chunks,
            live_assistant_outputs=live_reduction.assistant_outputs,
            replay_assistant_outputs=replay_reduction.assistant_outputs,
        )

    def observe_message_grouping(self) -> MessageGroupingEvidence:
        stable_ids = tuple(
            update.message_id
            for notification in self._exercise.live_default
            if isinstance(update := notification.update, (AgentThoughtChunk, AgentMessageChunk))
            and update.message_id is not None
        )
        live_groups, boundary_groups = _missing_id_groups(self._exercise.missing_live)
        replay_groups, _ = _missing_id_groups(self._exercise.missing_replay)
        return MessageGroupingEvidence(
            stable_group_ids=stable_ids,
            missing_id_live_group_ids=live_groups,
            missing_id_replay_group_ids=replay_groups,
            boundary_reset_group_ids=boundary_groups,
        )

    def observe_plan_tool_reconciliation(self) -> PlanToolEvidence:
        plans: list[tuple[str, ...]] = []
        tools: dict[str, str] = {}
        for notification in self._exercise.live_default:
            update = notification.update
            if isinstance(update, AgentPlanUpdate):
                plans.append(tuple(entry.content for entry in update.entries))
            elif isinstance(update, ToolCallStart):
                tools[update.tool_call_id] = update.status or "pending"
            elif isinstance(update, ToolCallProgress):
                tools[update.tool_call_id] = update.status or tools[update.tool_call_id]
        return PlanToolEvidence(
            observed_plan_snapshots=tuple(plans),
            final_plan=plans[-1],
            tool_call_ids=tuple(tools),
            final_tool_status=tools["tool-1"],
        )

    def observe_permission_settlement(self) -> PermissionSettlementEvidence:
        assert self._exercise.permission_requests
        broker = _ReferencePermissionBroker()
        settlements: list[tuple[str, int]] = []
        for cause in ("cancel", "death", "last_browser_disconnect", "timeout"):
            request_id = f"permission-{cause}"
            broker.open(request_id)
            broker.settle(request_id, cause)
            broker.settle(request_id, cause)
            settlements.append((cause, broker.settlement_count(request_id, cause)))
        return PermissionSettlementEvidence(settlements_by_cause=tuple(settlements))

    def observe_refresh_binding(self) -> RefreshEvidence:
        original = self._exercise.original_binding
        refreshed = self._exercise.refreshed_binding
        return RefreshEvidence(
            employee_id=original.employee_id,
            original_session_id=original.acp_session_id,
            refreshed_session_id=refreshed.acp_session_id,
            original_binding_generation=original.binding_generation,
            refreshed_binding_generation=refreshed.binding_generation,
        )

    def observe_delivery_capabilities(self) -> DeliveryCapabilityEvidence:
        steer_result = (
            "accepted_native"
            if self._steer_receipt.state == "accepted"
            else "rejected_unavailable"
        )
        return DeliveryCapabilityEvidence(
            supports_steer=self._steer_operation.supports_steer,
            steer_result=steer_result,
            unsupported_steer_visible=self._steer_receipt.state == "rejected",
            broker_operations=tuple(self._turn_broker.operations),
        )

    def observe_compaction(self) -> CompactionEvidence:
        return CompactionEvidence(
            explicit_states=self._explicit_compaction.states,
            automatic_states=self._automatic_compaction.states,
        )

    def observe_callback_order(self) -> CallbackOrderEvidence:
        reduced_order = _wire_numbers_from_notifications(
            self._exercise.concurrent_notifications
        )
        wire_order = _wire_numbers_from_stream(self._exercise.stream_events)
        return CallbackOrderEvidence(
            wire_order=wire_order,
            reduced_order=reduced_order,
            broadcast_order=_wire_numbers_from_notifications(
                self._exercise.concurrent_broadcast_notifications
            ),
        )

    def observe_protocol_rejection(self) -> ProtocolRejectionEvidence:
        employee = ConversationEmployee(
            employee_id="employee-acp-00",
            entity_kind="ticket",
            entity_id="ticket-acp-00",
            workspace_roots=(REPOSITORY_ROOT,),
            backend_key="scripted",
        )
        rejected: list[ProtocolUpdateRejectedEnvelope] = []
        for sequence, raw_notification in enumerate(
            self._exercise.malformed_notifications, start=1
        ):
            envelope = session_update_envelope_or_rejection(
                raw_notification=raw_notification,
                employee=employee,
                acp_session_id=self._exercise.session_id,
                binding_generation=1,
                sequence=sequence,
            )
            assert isinstance(envelope, ProtocolUpdateRejectedEnvelope)
            rejected.append(envelope)
        return ProtocolRejectionEvidence(
            rejected_discriminators=tuple(
                envelope.payload.rejected_session_update for envelope in rejected
            ),
            visible_statuses=tuple(envelope.payload.status for envelope in rejected),
            assistant_text_fallbacks=(),
        )


def _missing_id_groups(
    notifications: tuple[SessionNotification, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    group_counter = 0
    active_role: str | None = None
    active_group: str | None = None
    thought_groups: list[str] = []
    boundary_groups: list[str] = []
    for notification in notifications:
        update = notification.update
        if isinstance(update, (ToolCallStart, ToolCallProgress, AgentPlanUpdate)):
            active_role = None
            active_group = None
            continue
        if not isinstance(update, (UserMessageChunk, AgentThoughtChunk, AgentMessageChunk)):
            continue
        if update.message_id is not None:
            continue
        role = "user" if isinstance(update, UserMessageChunk) else "agent"
        if active_role != role or active_group is None:
            group_counter += 1
            active_group = f"fallback-{role}-{group_counter}"
            active_role = role
            boundary_groups.append(active_group)
        if isinstance(update, AgentThoughtChunk):
            thought_groups.append(active_group)
    return tuple(thought_groups), tuple(boundary_groups)


def _reduce_typed_transcript(
    notifications: tuple[SessionNotification, ...],
) -> _TypedTranscriptReduction:
    thought_chunks: list[str] = []
    assistant_group_order: list[str] = []
    assistant_chunks_by_group: dict[str, list[str]] = {}
    missing_id_group_number = 0
    active_missing_assistant_group: str | None = None

    for notification in notifications:
        update = notification.update
        if isinstance(update, (UserMessageChunk, ToolCallStart, ToolCallProgress, AgentPlanUpdate)):
            active_missing_assistant_group = None
        if isinstance(update, AgentThoughtChunk):
            if isinstance(update.content, TextContentBlock):
                thought_chunks.append(update.content.text)
            continue
        if not isinstance(update, AgentMessageChunk) or not isinstance(
            update.content, TextContentBlock
        ):
            continue
        if update.message_id is not None:
            group_id = f"message:{update.message_id}"
            active_missing_assistant_group = None
        else:
            if active_missing_assistant_group is None:
                missing_id_group_number += 1
                active_missing_assistant_group = (
                    f"missing-assistant:{missing_id_group_number}"
                )
            group_id = active_missing_assistant_group
        if group_id not in assistant_chunks_by_group:
            assistant_group_order.append(group_id)
            assistant_chunks_by_group[group_id] = []
        assistant_chunks_by_group[group_id].append(update.content.text)

    return _TypedTranscriptReduction(
        thought_chunks=tuple(thought_chunks),
        assistant_outputs=tuple(
            "".join(assistant_chunks_by_group[group_id])
            for group_id in assistant_group_order
        ),
    )


def _wire_numbers_from_notifications(
    notifications: tuple[SessionNotification, ...],
) -> tuple[int, ...]:
    numbers: list[int] = []
    for notification in notifications:
        update = notification.update
        if not isinstance(update, AgentThoughtChunk) or not isinstance(
            update.content, TextContentBlock
        ):
            continue
        if update.content.text.startswith("wire-"):
            numbers.append(int(update.content.text.removeprefix("wire-")))
    return tuple(numbers)


def _wire_numbers_from_stream(events: tuple[StreamEvent, ...]) -> tuple[int, ...]:
    numbers: list[int] = []
    for event in events:
        if event.direction is not StreamDirection.INCOMING:
            continue
        params = event.message.get("params")
        if event.message.get("method") != "session/update" or not isinstance(params, dict):
            continue
        update = params.get("update")
        if not isinstance(update, dict):
            continue
        content = update.get("content")
        if not isinstance(content, dict):
            continue
        text = content.get("text")
        if isinstance(text, str) and text.startswith("wire-"):
            numbers.append(int(text.removeprefix("wire-")))
    return tuple(numbers)


def _malformed_notifications_from_stream(
    events: tuple[StreamEvent, ...],
) -> tuple[dict[str, Any], ...]:
    malformed: list[dict[str, Any]] = []
    for event in events:
        if event.direction is not StreamDirection.INCOMING:
            continue
        if event.message.get("method") != "session/update":
            continue
        params = event.message.get("params")
        if not isinstance(params, dict):
            continue
        update = params.get("update")
        if not isinstance(update, dict):
            continue
        discriminator = update.get("sessionUpdate")
        if discriminator == "future_update" or (
            discriminator == "agent_thought_chunk" and "content" not in update
        ):
            malformed.append(params)
    return tuple(malformed)
