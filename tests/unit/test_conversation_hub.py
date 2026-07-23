from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from acp.schema import (
    AgentMessageChunk,
    DeniedOutcome,
    FileEditToolCallContent,
    LoadSessionRequest,
    PermissionOption,
    PromptRequest,
    PromptResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
    TextContentBlock,
    ToolCallStart,
    ToolCallUpdate,
)
from acp.transports import default_environment

from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ConversationIngressReplayBatch,
    ReverseServiceCapabilities,
)
from planner.conversation.codex_session_notification_normalizer import (
    normalize_codex_session_notification,
)
from planner.conversation.contracts import (
    ConversationEmployee,
    ConversationSessionBinding,
    QueuedPrompt,
    TurnDeliveryReceipt,
)
from planner.conversation.employee_registry import (
    AcpEmployeeRecord,
    ConversationIngressSource,
)
from planner.conversation.hub import (
    REPLAY_UNAVAILABLE_CLOSE_REASON,
    SLOW_CONSUMER_CLOSE_REASON,
    BrowserSubscription,
    ConversationHub,
    _OrdinaryReplayEntry,
    _OrdinaryReplayEntryKey,
)
from planner.conversation.permission_broker import ConversationPermissionBroker
from planner.conversation.runtime_ports import ConversationRuntimeHandle
from planner.conversation.sdk_child import (
    SdkAcpEmployeeChildFactory,
    build_panels_initialize_request,
)
from planner.conversation.sqlite_binding_repository import (
    SqliteConversationBindingRepository,
)
from planner.conversation.turn_broker import (
    ConversationTurnAttachState,
)
from planner.conversation.wire_contracts import CancelAction, HumanEcho, PromptAction
from planner.core.db import connect, create_schema
from planner.tickets.contracts import EmployeeLaunchConfiguration
from planner.tickets.conversation_projection import TicketConversationProjection
from planner.worker_types.configuration import PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS

SCRIPTED_AGENT = Path(__file__).resolve().parents[1] / "support" / "acp_scripted_agent.py"


def _set_replay_snapshot(stream: Any, snapshot: list[str] | tuple[str, ...]) -> None:
    stream.replay_entries = {
        _OrdinaryReplayEntryKey(index): _OrdinaryReplayEntry(
            sequence=json.loads(serialized)["sequence"],
            serialized=serialized,
            stored_bytes=len(serialized.encode("utf-8")),
        )
        for index, serialized in enumerate(snapshot)
    }
    stream.reset_buffer_bytes = sum(
        len(serialized.encode("utf-8")) for serialized in snapshot
    )


class _Strategy:
    def classify_replay(
        self, _binding: Any, replay: tuple[Any, ...], _boundaries: tuple[Any, ...]
    ) -> tuple[Any, ...]:
        return replay

    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class _MalformedReplayStrategy(_Strategy):
    def classify_replay(
        self, _binding: Any, _replay: tuple[Any, ...], _boundaries: tuple[Any, ...]
    ) -> tuple[Any, ...]:
        raise ValueError("sentinel malformed replay content")


class _GatedProjection(TicketConversationProjection):
    def __init__(self, db_path: str) -> None:
        super().__init__(db_path, now=lambda: 2)
        self.activity_started = threading.Event()
        self.activity_release = threading.Event()
        self.reset_started = threading.Event()
        self.reset_release = threading.Event()
        self.activity_calls = 0
        self.reset_calls = 0

    def record_activity(self, ticket_id: str, state: str) -> bool:
        self.activity_calls += 1
        self.activity_started.set()
        if not self.activity_release.wait(timeout=5):
            raise AssertionError("timed out waiting to release activity projection")
        return super().record_activity(ticket_id, state)

    def reset(self, ticket_id: str) -> bool:
        self.reset_calls += 1
        self.reset_started.set()
        if not self.reset_release.wait(timeout=5):
            raise AssertionError("timed out waiting to release reset projection")
        return super().reset(ticket_id)


class _TrackingAsyncLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.waiting = asyncio.Event()

    async def __aenter__(self) -> _TrackingAsyncLock:
        if self._lock.locked():
            self.waiting.set()
        await self._lock.acquire()
        return self

    async def __aexit__(self, *_args: object) -> None:
        self._lock.release()


class _Child:
    generation = 1

    def __init__(self) -> None:
        self.alive = True
        self.closed = asyncio.Event()

    async def prompt(self, request: Any) -> PromptResponse:
        del request
        return PromptResponse(stop_reason="end_turn")

    async def close(self) -> None:
        self.alive = False
        self.closed.set()


class _Registry:
    def __init__(self, record: AcpEmployeeRecord, handle: ConversationRuntimeHandle) -> None:
        self.record = record
        self.handle = handle
        self.attach_calls = 0

    async def attach(self, employee: ConversationEmployee) -> AcpEmployeeRecord:
        assert employee == self.record.employee
        self.attach_calls += 1
        return self.record

    async def get_or_spawn(self, employee: ConversationEmployee) -> AcpEmployeeRecord:
        return await self.attach(employee)

    async def resolve_runtime_handle(
        self, employee_id: str, binding_generation: int
    ) -> ConversationRuntimeHandle:
        assert (employee_id, binding_generation) == (
            self.handle.employee.employee_id,
            self.handle.binding.binding_generation,
        )
        return self.handle

    async def resolve_runtime_handle_for_generation(
        self, employee_id: str, child_generation: int
    ) -> ConversationRuntimeHandle:
        assert (employee_id, child_generation) == (
            self.handle.employee.employee_id,
            self.handle.child_generation,
        )
        return self.handle

    async def retire_conversation(
        self, employee_id: str, binding_generation: int
    ) -> None:
        assert (employee_id, binding_generation) == (
            self.record.employee.employee_id,
            self.record.binding.binding_generation,
        )
        await self.record.child.close()


class _ReplacementRegistry(_Registry):
    def __init__(
        self,
        record: AcpEmployeeRecord,
        handle: ConversationRuntimeHandle,
        repository: SqliteConversationBindingRepository,
        replacement_record: AcpEmployeeRecord | None = None,
        replacement_handle: ConversationRuntimeHandle | None = None,
        *,
        fail_new_conversation: bool = False,
    ) -> None:
        super().__init__(record, handle)
        self.repository = repository
        self.replacement_record = replacement_record
        self.replacement_handle = replacement_handle
        self.fail_new_conversation = fail_new_conversation

    async def new_conversation(self, employee: ConversationEmployee) -> AcpEmployeeRecord:
        if self.fail_new_conversation:
            raise RuntimeError("replacement failed")
        assert self.replacement_record is not None
        assert self.replacement_handle is not None
        await self.repository.compare_and_swap(
            self.handle.binding,
            self.replacement_handle.binding,
        )
        self.record = self.replacement_record
        self.handle = self.replacement_handle
        assert employee == self.record.employee
        return self.record

    async def attach(self, employee: ConversationEmployee) -> AcpEmployeeRecord:
        assert employee == self.record.employee
        self.attach_calls += 1
        return self.record


class _Broker:
    def __init__(self) -> None:
        self.notifications: list[SessionNotification] = []
        self.phase = "idle"
        self.deaths: list[tuple[Any, ...]] = []

    def set_prompt_ingress_hooks(self, **kwargs: Any) -> None:
        self.prompt_ingress_hooks = kwargs

    async def attach_state(self, handle: ConversationRuntimeHandle) -> ConversationTurnAttachState:
        del handle
        return ConversationTurnAttachState(self.phase, ())  # type: ignore[arg-type]

    async def observe_session_notification(
        self, handle: ConversationRuntimeHandle, notification: SessionNotification
    ) -> None:
        del handle
        self.notifications.append(notification)

    async def child_died(
        self,
        employee_id: str,
        binding_generation: int,
        child_generation: int,
        error: BaseException | None,
    ) -> None:
        self.deaths.append((employee_id, binding_generation, child_generation, error))

    async def prepare_new_conversation(
        self, _handle: ConversationRuntimeHandle, _deadline: float
    ) -> None:
        return None


class _DeliveryBroker(_Broker):
    def __init__(self) -> None:
        super().__init__()
        self.deliveries: list[tuple[ConversationRuntimeHandle, str, str, PromptRequest]] = []

    async def deliver(
        self,
        handle: ConversationRuntimeHandle,
        client_message_id: str,
        delivery_choice: str,
        prompt: PromptRequest,
    ) -> None:
        self.deliveries.append((handle, client_message_id, delivery_choice, prompt))


class _ActivatingRegistry(_Registry):
    def __init__(
        self,
        record: AcpEmployeeRecord,
        handle: ConversationRuntimeHandle,
        repository: SqliteConversationBindingRepository,
    ) -> None:
        super().__init__(record, handle)
        self.repository = repository

    async def get_or_spawn(self, employee: ConversationEmployee) -> AcpEmployeeRecord:
        self.attach_calls += 1
        await self.repository.compare_and_swap_initial(
            self.record.binding,
            EmployeeLaunchConfiguration(
                employee_backend=employee.backend_key,
                employee_launch_model=employee.employee_launch_model,
                employee_launch_reasoning_effort=employee.employee_launch_reasoning_effort,
            ),
        )
        return self.record


class _ClosingBroker(_Broker):
    def __init__(self, permission_broker: Any) -> None:
        super().__init__()
        self.permission_broker = permission_broker
        self.prepare_started = asyncio.Event()
        self.permission_settled = asyncio.Event()

    async def prepare_new_conversation(
        self, handle: ConversationRuntimeHandle, deadline: float
    ) -> None:
        self.prepare_started.set()
        await self.permission_broker.cancel_binding(
            handle.employee.employee_id,
            handle.binding.binding_generation,
            "New conversation",
            deadline=deadline,
        )
        self.permission_settled.set()


class _PermissionObservedProjection(TicketConversationProjection):
    def __init__(self, db_path: str) -> None:
        super().__init__(db_path, now=lambda: 2)
        self.pending_permission_recorded = threading.Event()

    def record_permission(self, ticket_id: str, pending: bool) -> bool:
        result = super().record_permission(ticket_id, pending)
        if pending:
            self.pending_permission_recorded.set()
        return result


class _Permissions:
    def __init__(self, *, block_detach: bool = False) -> None:
        self.attached: set[str] = set()
        self.detach_calls: list[str] = []
        self.detach_started = asyncio.Event()
        self.detach_release = asyncio.Event()
        if not block_detach:
            self.detach_release.set()

    async def attach_browser(
        self, employee: ConversationEmployee, browser_connection_id: str
    ) -> None:
        del employee
        self.attached.add(browser_connection_id)

    async def detach_browser(self, browser_connection_id: str) -> None:
        self.detach_calls.append(browser_connection_id)
        self.detach_started.set()
        await self.detach_release.wait()
        self.attached.discard(browser_connection_id)


class _CancellationObservedQueue(asyncio.Queue[str]):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.finished = asyncio.Event()

    async def get(self) -> str:
        self.started.set()
        try:
            return await super().get()
        finally:
            self.finished.set()


class _CancellationObservedEvent(asyncio.Event):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.finished = asyncio.Event()

    async def wait(self) -> bool:
        self.started.set()
        try:
            return await super().wait()
        finally:
            self.finished.set()


class _WriterWebSocket:
    async def send_text(self, serialized: str) -> None:
        raise AssertionError(f"writer unexpectedly sent {serialized!r}")

    async def close(self, *, code: int, reason: str) -> None:
        raise AssertionError(f"writer unexpectedly closed {code}: {reason}")


def test_websocket_writer_cancellation_awaits_both_iteration_waits() -> None:
    async def exercise() -> None:
        queue = _CancellationObservedQueue()
        closed = _CancellationObservedEvent()
        subscription = BrowserSubscription(
            connection_id="browser-cancelled-writer",
            employee_id="t_hub",
            queue=queue,
            closed=closed,
        )
        hub = ConversationHub(repository=None)  # type: ignore[arg-type]
        writer = asyncio.create_task(
            hub._websocket_writer(  # noqa: SLF001
                _WriterWebSocket(),  # type: ignore[arg-type]
                subscription,
            )
        )
        await asyncio.wait_for(
            asyncio.gather(queue.started.wait(), closed.started.wait()), timeout=1
        )

        writer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer

        assert writer.cancelled()
        assert queue.finished.is_set()
        assert closed.finished.is_set()

    asyncio.run(exercise())


async def _ticket_database(
    tmp_path: Path,
    *,
    backend_key: str = "hermes",
    bound: bool = True,
) -> tuple[str, SqliteConversationBindingRepository]:
    db_path = str(tmp_path / "hub.db")
    conn = connect(db_path)
    create_schema(conn)
    fields = {
        field: {"value": None, "proposal": None, "user_note": None}
        for field in (
            "kickoff",
            "success",
            "approach",
            "plan",
            "implementation",
            "closeout",
        )
    }
    conn.execute(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, stage, ceiling, fields, "
        "created_at, updated_at) "
        "VALUES ('t_hub', 'Hub', 'coding', ?, 'needs_kickoff', 'needs_kickoff', ?, 1, 1)",
        (backend_key, json.dumps(fields, separators=(",", ":"))),
    )
    conn.close()
    repository = SqliteConversationBindingRepository(
        db_path,
        workspace_root=tmp_path,
        integer_now=lambda: 2,
        employee_backend_catalog=(PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS.employee_backend_catalog),
        chief_backend_key="hermes",
    )
    if bound:
        binding = ConversationSessionBinding(
            employee_id="t_hub",
            acp_session_id="session-hub",
            backend_key=backend_key,
            binding_generation=1,
        )
        employee = await repository.resolve_employee("t_hub")
        await repository.compare_and_swap_initial(
            binding,
            EmployeeLaunchConfiguration(
                employee_backend=employee.backend_key,
                employee_launch_model=employee.employee_launch_model,
                employee_launch_reasoning_effort=(
                    employee.employee_launch_reasoning_effort
                ),
            ),
        )
    return db_path, repository


def _runtime(tmp_path: Path) -> tuple[AcpEmployeeRecord, ConversationRuntimeHandle]:
    employee = ConversationEmployee(
        employee_id="t_hub",
        entity_kind="ticket",
        entity_id="t_hub",
        workspace_roots=(tmp_path,),
        backend_key="hermes",
    )
    binding = ConversationSessionBinding(
        employee_id="t_hub",
        acp_session_id="session-hub",
        backend_key="hermes",
        binding_generation=1,
    )
    child = _Child()
    identity = object()
    definition = AgentBackendDefinition(
        backend_key="hermes",
        argv=("/tmp/hermes", "acp"),
        inherited_environment_names=(),
        environment_overrides=(),
        expected_agent_name="hermes-agent",
        expected_agent_version="0.18.2",
        turn_capabilities=BackendTurnCapabilities(supports_steer=True, observes_compaction=True),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False, terminal=False, permission=True
        ),
        working_directory_resolver=lambda value: value.workspace_roots[0],
        turn_strategy=_Strategy(),
    )
    record = AcpEmployeeRecord(employee, binding, 1, child, identity)  # type: ignore[arg-type]
    handle = ConversationRuntimeHandle(
        employee,
        binding,
        1,
        child,
        definition,
        identity,  # type: ignore[arg-type]
    )
    return record, handle


def test_empty_conversation_survives_restart_and_first_prompt_activates_it(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path, bound=False)
        record, handle = _runtime(tmp_path)
        first_registry = _ActivatingRegistry(record, handle, repository)
        first_hub = ConversationHub(repository)
        first_hub.bind_owners(  # type: ignore[arg-type]
            registry=first_registry,
            broker=_DeliveryBroker(),
            permission_broker=_Permissions(),
        )

        first = await first_hub.attach_browser("t_hub", connection_id="browser-first")
        first_reset = json.loads(await first.queue.get())
        first_ready = json.loads(await first.queue.get())
        assert first_reset["acpSessionId"] is None
        assert first_reset["bindingGeneration"] == 1
        assert first_ready["payload"]["state"] == "ready"
        assert first_registry.attach_calls == 0
        assert await repository.resolve("t_hub") is None
        await first_hub.detach_browser(first.connection_id)
        await first_hub.shutdown(asyncio.get_running_loop().time() + 1)

        registry = _ActivatingRegistry(record, handle, repository)
        broker = _DeliveryBroker()
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry,
            broker=broker,
            permission_broker=_Permissions(),
        )
        browser = await hub.attach_browser("t_hub", connection_id="browser-after-restart")
        await browser.queue.get()
        await browser.queue.get()
        assert registry.attach_calls == 0

        await hub.dispatch_action(
            browser.connection_id,
            PromptAction(
                type="prompt",
                employee_id="t_hub",
                client_message_id="message-first",
                prompt=[TextContentBlock(type="text", text="hello")],
                delivery_choice="normal",
            ),
        )

        activated_reset = json.loads(await browser.queue.get())
        activated_ready = json.loads(await browser.queue.get())
        echo = json.loads(await browser.queue.get())
        assert activated_reset["acpSessionId"] == "session-hub"
        assert activated_reset["sequence"] == 3
        assert activated_ready["sequence"] == 4
        assert echo["type"] == "human_echo"
        assert registry.attach_calls == 1
        assert await repository.resolve("t_hub") == record.binding
        assert len(broker.deliveries) == 1
        assert broker.deliveries[0][3].session_id == "session-hub"
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_queue_choice_withholds_human_echo_at_submit(tmp_path: Path) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        registry = _ActivatingRegistry(record, handle, repository)
        broker = _DeliveryBroker()
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry,
            broker=broker,
            permission_broker=_Permissions(),
        )
        browser = await hub.attach_browser("t_hub", connection_id="browser-queue")
        await browser.queue.get()  # reset
        await browser.queue.get()  # ready

        # A normal prompt activates the stream and echoes into the transcript at submit; a queued
        # prompt that follows is delivered but must NOT echo until it is actually dequeued and sent.
        await hub.dispatch_action(
            browser.connection_id,
            PromptAction(
                type="prompt",
                employee_id="t_hub",
                client_message_id="normal-1",
                prompt=[TextContentBlock(type="text", text="hello")],
                delivery_choice="normal",
            ),
        )
        await hub.dispatch_action(
            browser.connection_id,
            PromptAction(
                type="prompt",
                employee_id="t_hub",
                client_message_id="queued-1",
                prompt=[TextContentBlock(type="text", text="later")],
                delivery_choice="queue",
            ),
        )

        envelopes = [
            json.loads(browser.queue.get_nowait())
            for _ in range(browser.queue.qsize())
        ]
        echoes = [
            item["payload"]["clientMessageId"]
            for item in envelopes
            if item["type"] == "human_echo"
        ]
        assert echoes == ["normal-1"]
        assert [
            (delivery[1], delivery[2]) for delivery in broker.deliveries
        ] == [("normal-1", "normal"), ("queued-1", "queue")]
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_stale_browser_generation_receives_current_empty_reset(tmp_path: Path) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        await repository.start_new_conversation("t_hub", record.binding)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )

        browser = await hub.attach_browser(
            "t_hub",
            connection_id="browser-stale-generation",
            last_seen_binding_generation=1,
            last_seen_sequence=99,
        )
        reset = json.loads(await browser.queue.get())
        ready = json.loads(await browser.queue.get())
        assert reset["bindingGeneration"] == 2
        assert reset["acpSessionId"] is None
        assert ready["payload"]["state"] == "ready"
        assert browser.closed.is_set() is False
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_publish_allocates_and_serializes_sequence_once(tmp_path: Path) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        registry = _Registry(record, handle)
        broker = _Broker()
        permissions = _Permissions()
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry, broker=broker, permission_broker=permissions
        )

        subscription = await hub.attach_browser("t_hub", connection_id="browser-a")
        await hub.publish_activity(record.employee, record.binding, "thinking", "Working")
        envelopes = [json.loads(await subscription.queue.get()) for _ in range(3)]

        assert [(item["type"], item["sequence"]) for item in envelopes] == [
            ("connection", 1),
            ("connection", 2),
            ("activity", 3),
        ]
        assert envelopes[2]["payload"]["sequence"] == 3
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_replay_batch_larger_than_ingress_capacity_finishes_before_racing_live_update(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository, ingress_capacity=2)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        source = ConversationIngressSource(
            record.employee, handle.child_generation, handle.record_identity
        )
        replay = tuple(
            SessionNotification(
                session_id=record.binding.acp_session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id=f"replay-{index}",
                    content=TextContentBlock(type="text", text=f"replay-{index}"),
                ),
            )
            for index in range(3)
        )
        live = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="live",
                content=TextContentBlock(type="text", text="live-after-load"),
            ),
        )

        await hub.registry_conversation_ingress(
            source, ConversationIngressReplayBatch(items=replay)
        )
        await hub.registry_conversation_ingress(source, live)
        browser = await hub.attach_browser("t_hub", connection_id="browser-a")

        envelopes = [
            json.loads(browser.queue.get_nowait())
            for _ in range(browser.queue.qsize())
        ]
        updates = [item for item in envelopes if item["type"] == "acp_session_update"]
        assert [item["payload"]["update"]["content"]["text"] for item in updates] == [
            "replay-0",
            "replay-1",
            "replay-2",
            "live-after-load",
        ]
        assert [item["sequence"] for item in updates] == [2, 3, 4, 5]
        assert [(item["type"], item["payload"].get("state")) for item in envelopes] == [
            ("connection", "reset"),
            ("acp_session_update", None),
            ("acp_session_update", None),
            ("acp_session_update", None),
            ("acp_session_update", None),
            ("connection", "ready"),
        ]
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_retried_pre_binding_replay_batch_replaces_cancelled_attach_snapshot(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository, ingress_capacity=1)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        source = ConversationIngressSource(
            record.employee, handle.child_generation, handle.record_identity
        )
        first = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="cancelled-attach",
                content=TextContentBlock(type="text", text="obsolete snapshot"),
            ),
        )
        replacement = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="retried-attach",
                content=TextContentBlock(type="text", text="replacement snapshot"),
            ),
        )
        pre_replacement_live = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="before-retried-attach",
                content=TextContentBlock(type="text", text="obsolete captured live"),
            ),
        )
        post_replacement_live = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="after-retried-attach",
                content=TextContentBlock(type="text", text="live after replacement"),
            ),
        )

        await hub.registry_conversation_ingress(
            source, ConversationIngressReplayBatch(items=(first,))
        )
        await hub.registry_conversation_ingress(source, pre_replacement_live)
        await hub.registry_conversation_ingress(
            source, ConversationIngressReplayBatch(items=(replacement,))
        )
        await hub.registry_conversation_ingress(source, post_replacement_live)
        browser = await hub.attach_browser("t_hub", connection_id="browser-a")

        envelopes = [
            json.loads(browser.queue.get_nowait()) for _ in range(browser.queue.qsize())
        ]
        updates = [item for item in envelopes if item["type"] == "acp_session_update"]
        assert [item["payload"]["update"]["content"]["text"] for item in updates] == [
            "replacement snapshot",
            "live after replacement",
        ]
        assert handle.child.alive is True
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_unbound_source_death_clears_only_its_replay_and_live_captures(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        source = ConversationIngressSource(
            record.employee, handle.child_generation, handle.record_identity
        )
        other_source = ConversationIngressSource(
            record.employee, handle.child_generation + 1, object()
        )
        live = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="captured-live",
                content=TextContentBlock(type="text", text="captured live"),
            ),
        )

        for candidate in (source, other_source):
            await hub.registry_conversation_ingress(
                candidate, ConversationIngressReplayBatch(items=())
            )
            await hub.registry_conversation_ingress(candidate, live)

        await hub.registry_child_died(source, RuntimeError("attach source died"))

        source_key = hub._source_key(source)  # noqa: SLF001
        other_key = hub._source_key(other_source)  # noqa: SLF001
        assert source_key not in hub._source_load_replay  # noqa: SLF001
        assert source_key not in hub._source_capture  # noqa: SLF001
        assert hub._source_load_replay == {other_key: ()}  # noqa: SLF001
        assert tuple(hub._source_capture[other_key]) == (live,)  # noqa: SLF001
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_registry_ingress_saturation_backpressures_and_preserves_order(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository, ingress_capacity=3)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        drain_started = asyncio.Event()
        release_drain = asyncio.Event()
        original_drain = hub._drain  # noqa: SLF001

        async def blocked_drain(
            employee_id: str, queue: asyncio.Queue[Any]
        ) -> None:
            drain_started.set()
            await release_drain.wait()
            await original_drain(employee_id, queue)

        hub._drain = blocked_drain  # type: ignore[method-assign]  # noqa: SLF001
        attach = asyncio.create_task(hub.attach_browser("t_hub", connection_id="browser-a"))
        await asyncio.wait_for(drain_started.wait(), timeout=1)

        source = ConversationIngressSource(
            record.employee, handle.child_generation, handle.record_identity
        )

        def notification(text: str) -> SessionNotification:
            return SessionNotification(
                session_id=record.binding.acp_session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id=text,
                    content=TextContentBlock(type="text", text=text),
                ),
            )

        await hub.registry_conversation_ingress(source, notification("one"))
        await hub.registry_conversation_ingress(source, notification("two"))
        assert hub._sequencers["t_hub"].queue.qsize() == 3  # noqa: SLF001

        third = asyncio.create_task(
            hub.registry_conversation_ingress(source, notification("three"))
        )
        await asyncio.sleep(0)
        assert not third.done()

        release_drain.set()
        browser = await asyncio.wait_for(attach, timeout=1)
        await asyncio.wait_for(third, timeout=1)
        await hub._enqueue_and_wait("t_hub", lambda: None)  # noqa: SLF001

        envelopes = [
            json.loads(browser.queue.get_nowait())
            for _ in range(browser.queue.qsize())
        ]
        assert [
            item["payload"]["update"]["content"]["text"]
            for item in envelopes
            if item["type"] == "acp_session_update"
        ] == [
            "one",
            "two",
            "three",
        ]
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_enqueue_and_wait_saturation_backpressures_and_preserves_order(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        hub = ConversationHub(repository, ingress_capacity=2)
        drain_started = asyncio.Event()
        release_drain = asyncio.Event()
        original_drain = hub._drain  # noqa: SLF001

        async def blocked_drain(
            employee_id: str, queue: asyncio.Queue[Any]
        ) -> None:
            drain_started.set()
            await release_drain.wait()
            await original_drain(employee_id, queue)

        hub._drain = blocked_drain  # type: ignore[method-assign]  # noqa: SLF001
        completed: list[str] = []

        first = asyncio.create_task(
            hub._enqueue_and_wait(  # noqa: SLF001
                "t_hub", lambda: completed.append("one")
            )
        )
        await asyncio.wait_for(drain_started.wait(), timeout=1)
        second = asyncio.create_task(
            hub._enqueue_and_wait(  # noqa: SLF001
                "t_hub", lambda: completed.append("two")
            )
        )
        await asyncio.sleep(0)
        third = asyncio.create_task(
            hub._enqueue_and_wait(  # noqa: SLF001
                "t_hub", lambda: completed.append("three")
            )
        )
        await asyncio.sleep(0)
        assert not third.done()

        release_drain.set()
        await asyncio.wait_for(asyncio.gather(first, second, third), timeout=1)
        await hub._enqueue_and_wait(  # noqa: SLF001
            "t_hub", lambda: completed.append("usable")
        )
        assert completed == ["one", "two", "three", "usable"]
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_replay_ingress_failure_logs_one_safe_structured_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        source = ConversationIngressSource(
            record.employee, handle.child_generation, handle.record_identity
        )
        payload = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="private-message-id",
                content=TextContentBlock(type="text", text="private conversation content"),
            ),
        )

        def fail_replay(
            _source: ConversationIngressSource,
            _batch: ConversationIngressReplayBatch,
        ) -> None:
            raise ValueError("private conversation content")

        hub._ingest_replay_batch = fail_replay  # type: ignore[method-assign]  # noqa: SLF001
        await hub.registry_conversation_ingress(
            source,
            ConversationIngressReplayBatch(items=(payload, payload)),
        )
        await asyncio.wait_for(handle.child.closed.wait(), timeout=1)

        records = [
            json.loads(record.message)
            for record in caplog.records
            if "conversation_ingress_operation_failed" in record.message
        ]
        assert records == [
            {
                "event": "conversation_ingress_operation_failed",
                "phase": "sequencer_drain",
                "employee_id": "t_hub",
                "child_generation": 1,
                "transition_kind": "replay",
                "transition_count": 2,
                "exception_type": "ValueError",
            }
        ]
        assert "private conversation content" not in caplog.text
        assert "private-message-id" not in caplog.text
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_ticket_activity_projection_is_committed_before_envelope_is_visible(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        projection = _GatedProjection(db_path)
        hub = ConversationHub(repository, ticket_conversation_projection=projection)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        subscription = await hub.attach_browser("t_hub", connection_id="browser-a")
        while not subscription.queue.empty():
            subscription.queue.get_nowait()

        publication = asyncio.create_task(
            hub.publish_activity(record.employee, record.binding, "thinking", "Working")
        )
        assert await asyncio.to_thread(projection.activity_started.wait, 5)
        assert subscription.queue.empty()
        assert projection.activity_calls == 1

        projection.activity_release.set()
        await publication
        envelope = json.loads(await asyncio.wait_for(subscription.queue.get(), timeout=1))
        assert envelope["type"] == "activity"
        assert envelope["payload"]["state"] == "thinking"
        assert projection.read("t_hub").latest_activity_state == "thinking"
        assert subscription.queue.empty()
        assert projection.activity_calls == 1
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_ticket_activity_and_permission_publication_updates_workspace_projection(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        projection = TicketConversationProjection(db_path, now=lambda: 2)
        hub = ConversationHub(repository, ticket_conversation_projection=projection)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        await hub.attach_browser("t_hub", connection_id="browser-a")

        await hub.publish_delivery_receipt(
            record.employee,
            record.binding,
            TurnDeliveryReceipt(
                client_message_id="human-prompt",
                choice="send_now",
                state="accepted",
            ),
        )
        assert projection.read("t_hub").latest_activity_state == "thinking"
        await hub.publish_activity(record.employee, record.binding, "thinking", "Working")
        assert projection.read("t_hub").latest_activity_state == "thinking"
        await hub.publish_activity(record.employee, record.binding, "idle", "Ready")
        assert projection.read("t_hub").has_completed_response_awaiting_user is True

        request = RequestPermissionRequest(
            session_id=record.binding.acp_session_id,
            tool_call=ToolCallUpdate(
                session_update="tool_call",
                tool_call_id="tool-projection",
                title="Write file",
                kind="edit",
                status="pending",
            ),
            options=[
                PermissionOption(
                    option_id="once", name="Allow once", kind="allow_once"
                )
            ],
        )
        await hub.publish_permission_request(
            record.employee,
            record.binding,
            "permission-projection",
            record.employee.backend_key,
            request,
            20,
        )
        assert projection.read("t_hub").has_pending_permission is True
        await hub.publish_permission_outcome(
            record.employee,
            record.binding,
            "permission-projection",
            RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled")),
            "test",
        )
        assert projection.read("t_hub").has_pending_permission is False
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_stale_publication_does_not_update_ticket_projection(tmp_path: Path) -> None:
    async def exercise() -> None:
        db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        projection = TicketConversationProjection(db_path, now=lambda: 2)
        projection.record_activity(record.employee.entity_id, "thinking")
        hub = ConversationHub(repository, ticket_conversation_projection=projection)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        await hub.attach_browser("t_hub", connection_id="browser-a")
        before = projection.read("t_hub")

        stale_binding = record.binding.model_copy(update={"binding_generation": 2})
        with pytest.raises(RuntimeError, match="stale stream"):
            await hub.publish_activity(record.employee, stale_binding, "idle", "stale")

        assert projection.read("t_hub") == before
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_new_conversation_becomes_empty_without_creating_a_replacement_session(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        projection = TicketConversationProjection(db_path, now=lambda: 2)
        projection.record_activity("t_hub", "thinking")
        projection.record_activity("t_hub", "idle")
        hub = ConversationHub(repository, ticket_conversation_projection=projection)
        broker = _Broker()
        registry = _ReplacementRegistry(record, handle, repository)
        hub.bind_owners(
            registry=registry,  # type: ignore[arg-type]
            broker=broker,  # type: ignore[arg-type]
            permission_broker=_Permissions(),
        )
        await hub.attach_browser("t_hub", connection_id="browser-a")

        conversation = await hub.new_conversation("t_hub")

        assert conversation.conversation_generation == 2
        assert await repository.resolve("t_hub") is None
        assert "t_hub" not in hub._streams  # noqa: SLF001
        assert projection.read("t_hub").has_completed_response_awaiting_user is False
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_new_conversation_after_restart_retires_durable_binding_without_loading_it(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        registry = _Registry(record, handle)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry,
            broker=_Broker(),
            permission_broker=_Permissions(),
        )

        conversation = await hub.new_conversation("t_hub")

        assert conversation.conversation_generation == 2
        assert await repository.resolve("t_hub") is None
        assert registry.attach_calls == 0
        assert record.child.alive is False
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_new_conversation_sends_an_empty_reset_to_the_connected_browser(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        registry = _ReplacementRegistry(record, handle, repository)
        hub = ConversationHub(repository)

        class _OrderingProjection(TicketConversationProjection):
            def reset(self, ticket_id: str) -> bool:
                assert "t_hub" not in hub._streams  # noqa: SLF001
                return super().reset(ticket_id)

        projection = _OrderingProjection(db_path, now=lambda: 2)
        projection.record_activity("t_hub", "thinking")
        projection.record_activity("t_hub", "idle")
        hub._ticket_conversation_projection = projection  # noqa: SLF001
        hub.bind_owners(
            registry=registry,  # type: ignore[arg-type]
            broker=_Broker(),  # type: ignore[arg-type]
            permission_broker=_Permissions(),
        )
        browser = await hub.attach_browser("t_hub", connection_id="browser-a")
        await browser.queue.get()
        await browser.queue.get()

        await hub.new_conversation("t_hub")

        assert registry.attach_calls == 1
        reset = json.loads(await browser.queue.get())
        ready = json.loads(await browser.queue.get())
        assert reset["acpSessionId"] is None
        assert reset["bindingGeneration"] == 2
        assert ready["payload"]["state"] == "ready"
        assert projection.read("t_hub").has_completed_response_awaiting_user is False
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_new_conversation_closes_pending_permission_before_projection_cutover(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        replacement_binding = record.binding.model_copy(
            update={
                "acp_session_id": "session-replacement",
                "binding_generation": 2,
            }
        )
        replacement_child = _Child()
        replacement_identity = object()
        replacement_record = AcpEmployeeRecord(
            record.employee,
            replacement_binding,
            2,
            replacement_child,
            replacement_identity,
        )
        replacement_handle = ConversationRuntimeHandle(
            record.employee,
            replacement_binding,
            2,
            replacement_child,
            handle.definition,
            replacement_identity,
        )
        projection = _PermissionObservedProjection(db_path)
        hub = ConversationHub(
            repository,
            new_conversation_timeout_seconds=1,
            ticket_conversation_projection=projection,
        )
        permission_broker = ConversationPermissionBroker(
            hub,
            request_id_factory=lambda: "permission-close",
            integer_now=lambda: 2,
            timeout_seconds=300,
        )
        close_broker = _ClosingBroker(permission_broker)
        registry = _ReplacementRegistry(
            record,
            handle,
            repository,
            replacement_record,
            replacement_handle,
        )
        hub.bind_owners(
            registry=registry,  # type: ignore[arg-type]
            broker=close_broker,  # type: ignore[arg-type]
            permission_broker=permission_broker,
        )
        await hub.attach_browser("t_hub", connection_id="browser-a")

        pending_permission = asyncio.create_task(
            permission_broker.request_permission(
                record.employee,
                record.binding,
                handle.child_generation,
                handle.record_identity,
                1,
                RequestPermissionRequest(
                    session_id=record.binding.acp_session_id,
                    tool_call=ToolCallUpdate(
                        session_update="tool_call",
                        tool_call_id="tool-close",
                        title="Write file",
                        kind="edit",
                        status="pending",
                    ),
                    options=[
                        PermissionOption(
                            option_id="once", name="Allow once", kind="allow_once"
                        )
                    ],
                ),
                permission_declared=True,
            )
        )
        assert await asyncio.to_thread(
            projection.pending_permission_recorded.wait, 5
        )
        assert len(await permission_broker.pending_snapshot("t_hub")) == 1

        replacement = asyncio.create_task(hub.new_conversation("t_hub"))
        await asyncio.wait_for(close_broker.prepare_started.wait(), timeout=1)
        await asyncio.wait_for(close_broker.permission_settled.wait(), timeout=1)
        empty = await asyncio.wait_for(replacement, timeout=1)
        assert empty.conversation_generation == 2
        assert await repository.resolve("t_hub") is None
        response = await asyncio.wait_for(pending_permission, timeout=1)

        assert response.outcome.outcome == "cancelled"
        assert projection.read("t_hub").has_pending_permission is False
        await permission_broker.shutdown(asyncio.get_running_loop().time() + 1)
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_old_binding_cannot_publish_after_new_conversation_becomes_empty(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        projection = TicketConversationProjection(db_path, now=lambda: 2)
        registry = _ReplacementRegistry(record, handle, repository)
        hub = ConversationHub(repository, ticket_conversation_projection=projection)
        hub.bind_owners(
            registry=registry,  # type: ignore[arg-type]
            broker=_Broker(),  # type: ignore[arg-type]
            permission_broker=_Permissions(),
        )
        await hub.attach_browser("t_hub", connection_id="browser-a")

        await hub.new_conversation("t_hub")
        with pytest.raises(RuntimeError, match="stale stream"):
            await hub.publish_activity(record.employee, record.binding, "idle", "old")
        assert projection.read("t_hub").latest_activity_state is None
        assert projection.read("t_hub").has_completed_response_awaiting_user is False
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_replacement_waits_for_ticket_projection_publication(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        replacement_binding = record.binding.model_copy(
            update={
                "acp_session_id": "session-compacted",
                "binding_generation": 2,
            }
        )
        replacement_handle = ConversationRuntimeHandle(
            record.employee,
            replacement_binding,
            handle.child_generation + 1,
            _Child(),
            handle.definition,
            object(),
        )
        projection = _GatedProjection(db_path)
        projection.activity_release.set()
        projection.record_activity("t_hub", "thinking")
        projection.activity_release.clear()
        hub = ConversationHub(repository, ticket_conversation_projection=projection)
        lock = _TrackingAsyncLock()
        hub._ticket_projection_locks["t_hub"] = lock  # type: ignore[assignment]  # noqa: SLF001
        hub.bind_owners(
            registry=_Registry(record, handle),  # type: ignore[arg-type]
            broker=_Broker(),  # type: ignore[arg-type]
            permission_broker=_Permissions(),  # type: ignore[arg-type]
        )
        subscription = await hub.attach_browser("t_hub", connection_id="browser-a")
        while not subscription.queue.empty():
            subscription.queue.get_nowait()
        token = await hub.begin_compaction_transition(
            handle, asyncio.get_running_loop().time() + 1
        )
        await repository.compare_and_swap(handle.binding, replacement_binding)

        publication = asyncio.create_task(
            hub.publish_activity(record.employee, record.binding, "idle", "Ready")
        )
        assert await asyncio.to_thread(projection.activity_started.wait, 5)
        replacement = asyncio.create_task(
            hub.commit_compaction_transition(token, replacement_handle, (), ())
        )
        await asyncio.wait_for(lock.waiting.wait(), timeout=1)
        assert hub._streams["t_hub"].binding == record.binding  # noqa: SLF001

        projection.activity_release.set()
        await publication
        await replacement

        activity = json.loads(subscription.queue.get_nowait())
        reset = json.loads(subscription.queue.get_nowait())
        ready = json.loads(subscription.queue.get_nowait())
        assert activity["type"] == "activity"
        assert activity["payload"]["state"] == "idle"
        assert reset["type"] == "connection"
        assert reset["payload"]["state"] == "reset"
        assert ready["payload"]["state"] == "ready"
        assert projection.read("t_hub").has_completed_response_awaiting_user is True
        await hub.complete_compaction_transition(token, replacement_handle)
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_recovery_waits_for_ticket_projection_publication(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        replacement_handle = ConversationRuntimeHandle(
            record.employee,
            record.binding,
            handle.child_generation + 1,
            _Child(),
            handle.definition,
            object(),
        )
        projection = _GatedProjection(db_path)
        projection.activity_release.set()
        projection.record_activity("t_hub", "thinking")
        projection.activity_release.clear()
        hub = ConversationHub(repository, ticket_conversation_projection=projection)
        lock = _TrackingAsyncLock()
        hub._ticket_projection_locks["t_hub"] = lock  # type: ignore[assignment]  # noqa: SLF001
        hub.bind_owners(
            registry=_Registry(record, handle),  # type: ignore[arg-type]
            broker=_Broker(),  # type: ignore[arg-type]
            permission_broker=_Permissions(),  # type: ignore[arg-type]
        )
        subscription = await hub.attach_browser("t_hub", connection_id="browser-a")
        while not subscription.queue.empty():
            subscription.queue.get_nowait()
        token = await hub.begin_requested_cancel_recovery_transition(
            handle, asyncio.get_running_loop().time() + 1
        )

        publication = asyncio.create_task(
            hub.publish_activity(record.employee, record.binding, "idle", "Ready")
        )
        assert await asyncio.to_thread(projection.activity_started.wait, 5)
        replacement = asyncio.create_task(
            hub.commit_requested_cancel_recovery_transition(
                token, replacement_handle, (), (), None
            )
        )
        await asyncio.wait_for(lock.waiting.wait(), timeout=1)
        assert hub._streams["t_hub"].runtime_handle is handle  # noqa: SLF001

        projection.activity_release.set()
        await publication
        await replacement

        activity = json.loads(subscription.queue.get_nowait())
        reset = json.loads(subscription.queue.get_nowait())
        ready = json.loads(subscription.queue.get_nowait())
        assert activity["type"] == "activity"
        assert activity["payload"]["state"] == "idle"
        assert reset["type"] == "connection"
        assert reset["payload"]["state"] == "reset"
        assert ready["payload"]["state"] == "ready"
        assert projection.read("t_hub").has_completed_response_awaiting_user is True
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_permission_request_projection_rolls_back_when_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        projection = TicketConversationProjection(db_path, now=lambda: 2)
        projection.record_activity("t_hub", "thinking")
        projection.record_activity("t_hub", "idle")
        hub = ConversationHub(repository, ticket_conversation_projection=projection)
        hub.bind_owners(
            registry=_Registry(record, handle),  # type: ignore[arg-type]
            broker=_Broker(),  # type: ignore[arg-type]
            permission_broker=_Permissions(),  # type: ignore[arg-type]
        )
        await hub.attach_browser("t_hub", connection_id="browser-a")

        def fail_publication(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("permission publication failed")

        monkeypatch.setattr(hub, "_publish_envelope_now", fail_publication)
        request = RequestPermissionRequest(
            session_id=record.binding.acp_session_id,
            tool_call=ToolCallUpdate(
                session_update="tool_call",
                tool_call_id="tool-failed-publication",
                title="Write file",
                kind="edit",
                status="pending",
            ),
            options=[
                PermissionOption(
                    option_id="once", name="Allow once", kind="allow_once"
                )
            ],
        )
        with pytest.raises(RuntimeError, match="permission publication failed"):
            await hub.publish_permission_request(
                record.employee,
                record.binding,
                "permission-failed-publication",
                record.employee.backend_key,
                request,
                20,
            )

        snapshot = projection.read("t_hub")
        assert snapshot.has_pending_permission is False
        assert snapshot.has_completed_response_awaiting_user is True
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_failed_activity_closes_a_ready_stream_with_one_connection_error(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )

        subscription = await hub.attach_browser("t_hub", connection_id="browser-a")
        while not subscription.queue.empty():
            subscription.queue.get_nowait()

        activity = await hub.publish_activity(
            record.employee,
            record.binding,
            "failed",
            "generation failed",
        )
        envelopes = [json.loads(subscription.queue.get_nowait()) for _ in range(2)]

        assert activity.state == "failed"
        assert [(item["type"], item["payload"]["state"]) for item in envelopes] == [
            ("activity", "failed"),
            ("connection", "error"),
        ]
        assert not hub._streams["t_hub"].ready  # noqa: SLF001

        await hub.publish_activity(
            record.employee,
            record.binding,
            "failed",
            "duplicate generation failure",
        )
        duplicate = json.loads(subscription.queue.get_nowait())
        assert duplicate["type"] == "activity"
        assert subscription.queue.empty()
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_transition_orders_both_candidate_origins_once_before_ready(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        registry = _Registry(record, handle)
        broker = _Broker()
        permissions = _Permissions()
        hub = ConversationHub(repository, browser_capacity=4)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry, broker=broker, permission_broker=permissions
        )
        first = await hub.attach_browser("t_hub", connection_id="browser-a")
        broker.phase = "running"
        second = await hub.attach_browser("t_hub", connection_id="browser-b")
        for subscription in (first, second):
            while not subscription.queue.empty():
                subscription.queue.get_nowait()

        deadline = asyncio.get_running_loop().time() + 1
        token = await hub.begin_compaction_transition(handle, deadline)
        original_source = ConversationIngressSource(
            record.employee, handle.child_generation, handle.record_identity
        )
        replacement_binding = ConversationSessionBinding(
            employee_id="t_hub",
            acp_session_id="session-fork",
            backend_key="hermes",
            binding_generation=2,
        )
        replacement_handle = ConversationRuntimeHandle(
            handle.employee,
            replacement_binding,
            handle.child_generation + 1,
            _Child(),
            handle.definition,
            object(),
        )
        replacement_source = ConversationIngressSource(
            record.employee,
            replacement_handle.child_generation,
            replacement_handle.record_identity,
        )
        ordinary_n = SessionNotification(
            session_id="session-hub",
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="ordinary-n",
                content=TextContentBlock(type="text", text="ordinary N"),
            ),
        )
        source_candidate = SessionNotification(
            session_id="session-fork",
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="source-candidate",
                content=TextContentBlock(type="text", text="source candidate"),
            ),
        )
        fresh_candidate = SessionNotification(
            session_id="session-fork",
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="fresh-candidate",
                content=TextContentBlock(type="text", text="fresh candidate"),
            ),
        )
        replay = SessionNotification(
            session_id="session-fork",
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="summary",
                content=TextContentBlock(type="text", text="durable summary"),
            ),
        )
        queued = QueuedPrompt(
            client_message_id="queued-1",
            prompt=PromptRequest(
                session_id="session-fork",
                prompt=[TextContentBlock(type="text", text="after compact")],
            ),
            enqueue_sequence=1,
            enqueued_at=10,
        )

        await hub.registry_conversation_ingress(original_source, ordinary_n)
        await hub.registry_conversation_ingress(original_source, source_candidate)
        await hub.registry_conversation_ingress(replacement_source, fresh_candidate)
        await hub._enqueue_and_wait("t_hub", lambda: None)  # noqa: SLF001
        for subscription in (first, second):
            ordinary = json.loads(subscription.queue.get_nowait())
            assert ordinary["sequence"] == 3
            assert ordinary["payload"]["update"]["content"]["text"] == "ordinary N"

        await repository.compare_and_swap(handle.binding, replacement_binding)
        registry.record = AcpEmployeeRecord(
            handle.employee,
            replacement_binding,
            replacement_handle.child_generation,
            replacement_handle.child,
            replacement_handle.record_identity,
        )
        registry.handle = replacement_handle
        waiting_attach = asyncio.create_task(
            hub.attach_browser("t_hub", connection_id="browser-after-compaction")
        )
        await asyncio.sleep(0)
        assert not waiting_attach.done()

        await hub.commit_compaction_transition(token, replacement_handle, (replay,), (queued,))
        later_n_plus_one = SessionNotification(
            session_id="session-fork",
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="later-n-plus-one",
                content=TextContentBlock(type="text", text="later N+1"),
            ),
        )
        await hub.registry_conversation_ingress(replacement_source, later_n_plus_one)
        await hub._enqueue_and_wait("t_hub", lambda: None)  # noqa: SLF001
        await asyncio.sleep(0)
        assert not waiting_attach.done()

        for subscription in (first, second):
            # A still-queued prompt is not echoed into the transcript on a compaction replay; it
            # resurfaces in the queue snapshot alone and echoes only when it is dequeued and sent.
            envelopes = [json.loads(subscription.queue.get_nowait()) for _ in range(7)]
            assert [item["type"] for item in envelopes] == [
                "connection",
                "acp_session_update",
                "acp_session_update",
                "acp_session_update",
                "connection",
                "queue_snapshot",
                "acp_session_update",
            ]
            assert [item["sequence"] for item in envelopes] == list(range(1, 8))
            assert all(item["acpSessionId"] == "session-fork" for item in envelopes)
            assert [
                envelopes[0]["payload"]["state"],
                envelopes[4]["payload"]["state"],
            ] == ["reset", "ready"]
            assert [
                envelopes[index]["payload"]["update"]["content"]["text"]
                for index in (1, 2, 3, 6)
            ] == [
                "durable summary",
                "source candidate",
                "fresh candidate",
                "later N+1",
            ]
            assert envelopes[5]["payload"]["items"][0]["clientMessageId"] == ("queued-1")
        assert not hub._compaction_transitions["t_hub"].quarantined_ingress  # noqa: SLF001
        await hub.complete_compaction_transition(token, replacement_handle)
        attached = await asyncio.wait_for(waiting_attach, timeout=1)
        assert attached.connection_id == "browser-after-compaction"
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_malformed_compaction_replay_closes_without_prefix_logs_safely_and_detaches_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        permissions = _Permissions(block_detach=True)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=permissions,
        )
        browser = await hub.attach_browser("t_hub", connection_id="browser-malformed")
        await asyncio.wait_for(browser.queue.get(), timeout=1)
        await asyncio.wait_for(browser.queue.get(), timeout=1)
        token = await hub.begin_compaction_transition(
            handle, asyncio.get_running_loop().time() + 1
        )
        replacement_binding = ConversationSessionBinding(
            employee_id=handle.employee.employee_id,
            acp_session_id="session-malformed",
            backend_key=handle.binding.backend_key,
            binding_generation=2,
        )
        replacement_handle = ConversationRuntimeHandle(
            handle.employee,
            replacement_binding,
            handle.child_generation + 1,
            _Child(),
            replace(handle.definition, turn_strategy=_MalformedReplayStrategy()),
            object(),
        )
        await repository.compare_and_swap(handle.binding, replacement_binding)

        await hub.commit_compaction_transition(token, replacement_handle, (), ())

        assert browser.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert browser.closed.is_set()
        assert browser.queue.empty()
        await asyncio.wait_for(permissions.detach_started.wait(), timeout=1)
        finalizer = asyncio.create_task(hub.detach_browser(browser.connection_id))
        await asyncio.sleep(0)
        permissions.detach_release.set()
        await asyncio.wait_for(finalizer, timeout=1)
        assert permissions.detach_calls == [browser.connection_id]
        records = [
            json.loads(record.message)
            for record in caplog.records
            if "conversation_browser_subscription_closed" in record.message
        ]
        assert len(records) == 1
        assert records[0]["closure_phase"] == "replay"
        assert records[0]["replay_envelope_count"] == 1
        assert records[0]["live_queue_envelope_count"] == 0
        assert "sentinel malformed replay content" not in caplog.text
        await hub.complete_compaction_transition(token, replacement_handle)
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_recovery_rebinds_two_browsers_same_binding_with_ordered_replay_and_queue(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        registry = _Registry(record, handle)
        broker = _Broker()
        hub = ConversationHub(repository, browser_capacity=4)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry,
            broker=broker,
            permission_broker=_Permissions(),
        )
        first = await hub.attach_browser("t_hub", connection_id="browser-a")
        broker.phase = "running"
        second = await hub.attach_browser("t_hub", connection_id="browser-b")
        for subscription in (first, second):
            while not subscription.queue.empty():
                subscription.queue.get_nowait()

        source = ConversationIngressSource(
            record.employee, handle.child_generation, handle.record_identity
        )
        deadline = asyncio.get_running_loop().time() + 1
        token = await hub.begin_requested_cancel_recovery_transition(handle, deadline)
        waiting_attach = asyncio.create_task(
            hub.attach_browser("t_hub", connection_id="browser-after-recovery")
        )
        await asyncio.sleep(0)
        assert not waiting_attach.done()
        late = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="late-old",
                content=TextContentBlock(type="text", text="must not render"),
            ),
        )
        await hub.registry_conversation_ingress(source, late)
        await hub._enqueue_and_wait("t_hub", lambda: None)  # noqa: SLF001

        replacement_child = _Child()
        replacement_identity = object()
        replacement_handle = ConversationRuntimeHandle(
            handle.employee,
            handle.binding,
            handle.child_generation + 1,
            replacement_child,  # type: ignore[arg-type]
            handle.definition,
            replacement_identity,
        )
        registry.record = AcpEmployeeRecord(
            handle.employee,
            handle.binding,
            handle.child_generation + 1,
            replacement_child,  # type: ignore[arg-type]
            replacement_identity,
        )
        registry.handle = replacement_handle
        replay = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text="durable replay"),
            ),
        )
        queued = QueuedPrompt(
            client_message_id="queued-1",
            prompt=PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="after recovery")],
            ),
            enqueue_sequence=1,
            enqueued_at=10,
        )
        successor_echo = HumanEcho(
            client_message_id="send-now-successor",
            prompt=PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="successor")],
            ),
        )

        await hub.commit_requested_cancel_recovery_transition(
            token,
            replacement_handle,
            (replay, replay, replay),
            (queued,),
            successor_echo,
        )
        attached = await asyncio.wait_for(waiting_attach, timeout=1)
        assert attached.connection_id == "browser-after-recovery"
        for subscription in (first, second):
            # The still-queued prompt is not echoed on recovery; only the send-now successor, which
            # is being started now, echoes. The queued prompt resurfaces in the queue snapshot only.
            envelopes = [json.loads(subscription.queue.get_nowait()) for _ in range(5)]
            assert [item["type"] for item in envelopes] == [
                "connection",
                "acp_session_update",
                "connection",
                "human_echo",
                "queue_snapshot",
            ]
            assert [item["sequence"] for item in envelopes] == list(range(5, 10))
            assert [
                envelopes[0]["payload"]["state"],
                envelopes[2]["payload"]["state"],
            ] == ["reset", "ready"]
            assert envelopes[0]["payload"]["detail"] == ("Conversation runtime recovered")
            assert envelopes[1]["payload"]["update"]["content"]["text"] == (
                "durable replay" * 3
            )
            assert envelopes[3]["payload"]["clientMessageId"] == ("send-now-successor")
            assert envelopes[3]["payload"]["prompt"] == (
                successor_echo.prompt.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
            assert envelopes[4]["payload"]["items"][0]["clientMessageId"] == ("queued-1")
            assert all(
                item["clientMessageId"] != "send-now-successor"
                for item in envelopes[4]["payload"]["items"]
            )

        assert first.queue.empty()
        assert second.queue.empty()

        await hub.registry_conversation_ingress(source, late)
        await hub._enqueue_and_wait("t_hub", lambda: None)  # noqa: SLF001
        assert first.queue.empty()
        assert second.queue.empty()
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_quarantine_resumes_held_source_for_normal_terminal_response(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        browser = await hub.attach_browser("t_hub", connection_id="browser-a")
        while not browser.queue.empty():
            browser.queue.get_nowait()
        hub.prompt_started(handle, 1)
        token = await hub.begin_requested_cancel_recovery_transition(
            handle, asyncio.get_running_loop().time() + 1
        )
        source = ConversationIngressSource(
            record.employee, handle.child_generation, handle.record_identity
        )
        held = tuple(
            SessionNotification(
                session_id=record.binding.acp_session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id="normal-cancelled",
                    content=TextContentBlock(type="text", text=text),
                ),
            )
            for text in ("held-one", "held-two")
        )
        for notification in held:
            await hub.registry_conversation_ingress(source, notification)
        await hub._enqueue_and_wait("t_hub", lambda: None)  # noqa: SLF001
        assert browser.queue.empty()

        await hub.resume_requested_cancelled_runtime(token)
        envelopes = [json.loads(browser.queue.get_nowait()) for _ in range(2)]
        assert [item["payload"]["update"]["content"]["text"] for item in envelopes] == [
            "held-one",
            "held-two",
        ]
        barrier = await hub.prompt_settlement_barrier(handle, 1)
        assert barrier.notifications == held
        assert not hub._requested_cancel_recovery_transitions  # noqa: SLF001
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_recovery_owns_unexpected_old_child_death_once(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        broker = _Broker()
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=broker,
            permission_broker=_Permissions(),
        )
        browser = await hub.attach_browser("t_hub", connection_id="browser-a")
        while not browser.queue.empty():
            browser.queue.get_nowait()
        await hub.begin_requested_cancel_recovery_transition(
            handle, asyncio.get_running_loop().time() + 1
        )
        source = ConversationIngressSource(
            record.employee, handle.child_generation, handle.record_identity
        )

        failure = RuntimeError("unexpected old child death")
        await hub.registry_child_died(source, failure)
        await hub.registry_child_died(source, RuntimeError("duplicate stale death"))

        envelopes = [json.loads(browser.queue.get_nowait())]
        assert envelopes[0]["type"] == "connection"
        assert envelopes[0]["payload"]["state"] == "error"
        assert len(broker.deaths) == 1
        assert broker.deaths[0][:3] == ("t_hub", 1, 1)
        assert broker.deaths[0][3] is failure
        assert not hub._requested_cancel_recovery_transitions  # noqa: SLF001
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_recovery_failure_and_expiry_release_all_waiters_with_one_connection_error(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        browser = await hub.attach_browser("t_hub", connection_id="browser-a")
        while not browser.queue.empty():
            browser.queue.get_nowait()
        token = await hub.begin_requested_cancel_recovery_transition(
            handle, asyncio.get_running_loop().time() + 0.02
        )
        waiters = (
            asyncio.create_task(hub.attach_browser("t_hub", connection_id="blocked-browser")),
            asyncio.create_task(
                hub.dispatch_action("browser-a", CancelAction(type="cancel", employee_id="t_hub"))
            ),
            asyncio.create_task(hub.new_conversation("t_hub")),
        )
        await asyncio.sleep(0)
        assert all(not waiter.done() for waiter in waiters)
        with pytest.raises(RuntimeError, match="requested-cancel recovery transition"):
            await hub.begin_compaction_transition(handle, asyncio.get_running_loop().time() + 1)

        results = await asyncio.wait_for(
            asyncio.gather(*waiters, return_exceptions=True), timeout=1
        )
        assert all(
            isinstance(result, RuntimeError) and "deadline expired" in str(result)
            for result in results
        )
        envelope = json.loads(browser.queue.get_nowait())
        assert envelope["type"] == "connection"
        assert envelope["payload"] == {
            "state": "error",
            "detail": "Employee connection failed",
            "supportsSteer": True,
        }
        assert browser.queue.empty()
        with pytest.raises(RuntimeError, match="stale or settled"):
            await hub.fail_requested_cancel_recovery_transition(token, "late failure")
        assert browser.queue.empty()
        assert not hub._requested_cancel_recovery_transitions  # noqa: SLF001
        assert not hub._source_capture  # noqa: SLF001
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_transition_barrier_releases_attach_on_abort_and_expiry(
    tmp_path: Path,
) -> None:
    def candidate_update() -> SessionNotification:
        return SessionNotification(
            session_id="session-fork",
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="candidate",
                content=TextContentBlock(type="text", text="candidate"),
            ),
        )

    async def exercise_abort() -> None:
        root = tmp_path / "abort"
        root.mkdir()
        _db_path, repository = await _ticket_database(root)
        record, handle = _runtime(root)
        registry = _Registry(record, handle)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry,
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        await hub.attach_browser("t_hub", connection_id="browser-a")
        token = await hub.begin_compaction_transition(handle, asyncio.get_running_loop().time() + 1)
        state = hub._compaction_transitions["t_hub"]  # noqa: SLF001
        await hub.registry_conversation_ingress(
            ConversationIngressSource(
                record.employee, handle.child_generation, handle.record_identity
            ),
            candidate_update(),
        )
        await hub._enqueue_and_wait("t_hub", lambda: None)  # noqa: SLF001
        assert len(state.quarantined_ingress) == 1
        waiter = asyncio.create_task(hub.attach_browser("t_hub", connection_id="browser-waiting"))
        await asyncio.sleep(0)
        assert not waiter.done()
        await hub.abort_compaction_transition(token)
        assert not state.quarantined_ingress
        await asyncio.sleep(0)
        assert not waiter.done()
        await hub.complete_compaction_transition(token, handle)
        attached = await asyncio.wait_for(waiter, timeout=1)
        assert attached.connection_id == "browser-waiting"
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    async def exercise_expiry() -> None:
        root = tmp_path / "expiry"
        root.mkdir()
        _db_path, repository = await _ticket_database(root)
        record, handle = _runtime(root)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        await hub.attach_browser("t_hub", connection_id="browser-a")
        token = await hub.begin_compaction_transition(
            handle, asyncio.get_running_loop().time() + 0.02
        )
        state = hub._compaction_transitions["t_hub"]  # noqa: SLF001
        await hub.registry_conversation_ingress(
            ConversationIngressSource(
                record.employee, handle.child_generation, handle.record_identity
            ),
            candidate_update(),
        )
        await hub._enqueue_and_wait("t_hub", lambda: None)  # noqa: SLF001
        assert state.token is token
        assert len(state.quarantined_ingress) == 1
        waiter = asyncio.create_task(hub.ensure_employee_stream("t_hub"))
        with pytest.raises(RuntimeError, match="deadline expired"):
            await asyncio.wait_for(waiter, timeout=1)
        assert not state.quarantined_ingress
        assert not hub._compaction_transitions  # noqa: SLF001
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    async def exercise_fatal_failure() -> None:
        root = tmp_path / "fatal"
        root.mkdir()
        _db_path, repository = await _ticket_database(root)
        record, handle = _runtime(root)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        await hub.attach_browser("t_hub", connection_id="browser-a")
        token = await hub.begin_compaction_transition(handle, asyncio.get_running_loop().time() + 1)
        state = hub._compaction_transitions["t_hub"]  # noqa: SLF001
        await hub.registry_conversation_ingress(
            ConversationIngressSource(
                record.employee, handle.child_generation, handle.record_identity
            ),
            candidate_update(),
        )
        await hub._enqueue_and_wait("t_hub", lambda: None)  # noqa: SLF001
        assert len(state.quarantined_ingress) == 1
        waiter = asyncio.create_task(hub.ensure_employee_stream("t_hub"))
        await asyncio.sleep(0)
        assert not waiter.done()
        await hub.fail_compaction_transition(token, "runtime generation failed during compaction")
        with pytest.raises(RuntimeError, match="runtime generation failed"):
            await asyncio.wait_for(waiter, timeout=1)
        assert not state.quarantined_ingress
        assert not hub._compaction_transitions  # noqa: SLF001
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    async def exercise_shutdown() -> None:
        root = tmp_path / "shutdown"
        root.mkdir()
        _db_path, repository = await _ticket_database(root)
        record, handle = _runtime(root)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        await hub.attach_browser("t_hub", connection_id="browser-a")
        await hub.begin_compaction_transition(handle, asyncio.get_running_loop().time() + 1)
        state = hub._compaction_transitions["t_hub"]  # noqa: SLF001
        await hub.registry_conversation_ingress(
            ConversationIngressSource(
                record.employee, handle.child_generation, handle.record_identity
            ),
            candidate_update(),
        )
        await hub._enqueue_and_wait("t_hub", lambda: None)  # noqa: SLF001
        assert len(state.quarantined_ingress) == 1
        await hub.shutdown(asyncio.get_running_loop().time() + 1)
        assert not state.quarantined_ingress
        assert not hub._compaction_transitions  # noqa: SLF001

    asyncio.run(exercise_abort())
    asyncio.run(exercise_expiry())
    asyncio.run(exercise_fatal_failure())
    asyncio.run(exercise_shutdown())


def test_source_ingress_before_binding_flushes_between_reset_and_ready(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        registry = _Registry(record, handle)
        broker = _Broker()
        permissions = _Permissions()
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry, broker=broker, permission_broker=permissions
        )
        source = ConversationIngressSource(record.employee, 1, record.record_identity)
        notification = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="answer",
                content=TextContentBlock(type="text", text="hello"),
            ),
        )
        await hub.registry_conversation_ingress(source, notification)
        subscription = await hub.attach_browser("t_hub", connection_id="browser-a")
        envelopes = [json.loads(await subscription.queue.get()) for _ in range(3)]

        assert [item["type"] for item in envelopes] == [
            "connection",
            "acp_session_update",
            "connection",
        ]
        assert [item["sequence"] for item in envelopes] == [1, 2, 3]
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_first_load_bootstrap_survives_one_live_update_before_writer_starts(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository, browser_capacity=1)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )

        subscription = await hub.attach_browser("t_hub", connection_id="browser-first")
        await hub.publish_activity(record.employee, record.binding, "thinking", "interleaved")

        assert not subscription.closed.is_set()
        assert subscription.queue.live_qsize() == 1  # type: ignore[union-attr]
        envelopes = [
            json.loads(await asyncio.wait_for(subscription.queue.get(), timeout=1))
            for _ in range(3)
        ]
        assert [item["sequence"] for item in envelopes] == [1, 2, 3]
        assert envelopes[-1]["payload"]["detail"] == "interleaved"
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_idle_refresh_bootstrap_survives_one_live_update_before_writer_starts(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository, browser_capacity=1)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        snapshot_before_attach = hub._materialized_replay_snapshot(stream)  # noqa: SLF001

        refreshing = await hub.attach_browser("t_hub", connection_id="browser-refreshing")
        await hub.publish_activity(record.employee, record.binding, "thinking", "after refresh")

        assert not refreshing.closed.is_set()
        assert refreshing.queue.live_qsize() == 1  # type: ignore[union-attr]
        serialized = [
            await asyncio.wait_for(refreshing.queue.get(), timeout=1) for _ in range(3)
        ]
        envelopes = [json.loads(item) for item in serialized]
        assert tuple(serialized[:2]) == snapshot_before_attach
        assert [item["payload"].get("state") for item in envelopes[:2]] == [
            "reset",
            "ready",
        ]
        assert envelopes[-1]["payload"]["detail"] == "after refresh"
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_idle_same_binding_attach_uses_ready_stream_snapshot_without_session_load(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        registry = _Registry(record, handle)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry,
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        browser_a = await hub.attach_browser("t_hub", connection_id="browser-a")
        await asyncio.wait_for(browser_a.queue.get(), timeout=1)
        await asyncio.wait_for(browser_a.queue.get(), timeout=1)
        await hub.publish_activity(
            record.employee, record.binding, "thinking", "historical"
        )
        historical_for_a = json.loads(
            await asyncio.wait_for(browser_a.queue.get(), timeout=1)
        )
        assert historical_for_a["sequence"] == 3
        attach_calls = registry.attach_calls
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        sequence_before_attach = stream.sequence
        reset_buffer_before_attach = hub._materialized_replay_snapshot(stream)  # noqa: SLF001

        browser_b = await hub.attach_browser("t_hub", connection_id="browser-b")
        assert registry.attach_calls == attach_calls
        assert stream.sequence == sequence_before_attach
        assert hub._materialized_replay_snapshot(stream) == reset_buffer_before_attach  # noqa: SLF001
        assert browser_a.queue.empty()
        await hub.publish_activity(record.employee, record.binding, "thinking", "live")
        browser_b_envelopes = [
            json.loads(await asyncio.wait_for(browser_b.queue.get(), timeout=1))
            for _ in range(4)
        ]
        assert [item["sequence"] for item in browser_b_envelopes] == [1, 2, 3, 4]
        assert [item["type"] for item in browser_b_envelopes] == [
            "connection",
            "activity",
            "connection",
            "activity",
        ]
        assert [
            item["payload"].get("state") for item in browser_b_envelopes[:3]
        ] == [
            "reset",
            "thinking",
            "ready",
        ]
        assert browser_b_envelopes[-1]["payload"]["detail"] == "live"
        live_for_a = json.loads(await asyncio.wait_for(browser_a.queue.get(), timeout=1))
        assert live_for_a["sequence"] == 4
        assert live_for_a["payload"]["detail"] == "live"

        stream.reset_buffer_available = False
        stream.replay_entries = {}
        unavailable = await hub.attach_browser(
            "t_hub", connection_id="browser-unavailable"
        )
        assert unavailable.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert unavailable.queue.empty()
        assert registry.attach_calls == attach_calls
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_same_binding_attach_fails_closed_for_empty_snapshot(tmp_path: Path) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        stream.replay_entries = {}
        stream.reset_buffer_bytes = 0

        malformed = await hub.attach_browser(
            "t_hub", connection_id="browser-empty-snapshot"
        )

        assert malformed.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert malformed.closed.is_set()
        assert malformed.queue.empty()
        assert malformed.connection_id not in stream.browsers
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_same_binding_attach_fails_closed_for_snapshot_without_ready(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        snapshot = hub._materialized_replay_snapshot(stream)  # noqa: SLF001
        _set_replay_snapshot(stream, snapshot[:1])

        malformed = await hub.attach_browser(
            "t_hub", connection_id="browser-no-ready"
        )

        assert malformed.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert malformed.closed.is_set()
        assert malformed.queue.empty()
        assert malformed.connection_id not in stream.browsers
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_same_binding_attach_fails_closed_for_snapshot_without_reset(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        snapshot = hub._materialized_replay_snapshot(stream)  # noqa: SLF001
        _set_replay_snapshot(stream, snapshot[-1:])

        malformed = await hub.attach_browser(
            "t_hub", connection_id="browser-no-reset"
        )

        assert malformed.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert malformed.closed.is_set()
        assert malformed.queue.empty()
        assert malformed.connection_id not in stream.browsers
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_same_binding_attach_fails_closed_for_malformed_envelope(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        snapshot = list(hub._materialized_replay_snapshot(stream))  # noqa: SLF001
        malformed_reset = json.loads(snapshot[0])
        malformed_reset["unexpected"] = True
        snapshot[0] = json.dumps(malformed_reset, separators=(",", ":"))
        _set_replay_snapshot(stream, snapshot)

        malformed = await hub.attach_browser(
            "t_hub", connection_id="browser-malformed-envelope"
        )

        assert malformed.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert malformed.closed.is_set()
        assert malformed.queue.empty()
        assert malformed.connection_id not in stream.browsers
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_same_binding_attach_fails_closed_when_normalized_snapshot_exceeds_limit(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        await hub.publish_activity(
            record.employee, record.binding, "thinking", "historical"
        )
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        snapshot_before_attach = hub._materialized_replay_snapshot(stream)  # noqa: SLF001
        raw_snapshot_bytes = sum(
            len(item.encode("utf-8")) for item in snapshot_before_attach
        )
        stream.sequence = 1_000
        hub._reset_buffer_byte_limit = raw_snapshot_bytes  # noqa: SLF001

        unavailable = await hub.attach_browser(
            "t_hub", connection_id="browser-normalized-overflow"
        )

        assert unavailable.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert unavailable.closed.is_set()
        assert unavailable.queue.empty()
        assert unavailable.connection_id not in stream.browsers
        assert stream.sequence == 1_000
        assert hub._materialized_replay_snapshot(stream) == snapshot_before_attach  # noqa: SLF001
        assert existing.queue.empty()
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_same_binding_attach_collapses_multiple_ready_markers_to_latest(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        first_ready_serialized = await asyncio.wait_for(existing.queue.get(), timeout=1)
        await hub.publish_activity(
            record.employee, record.binding, "thinking", "historical"
        )
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        latest_ready = json.loads(first_ready_serialized)
        latest_ready["sequence"] = 4
        latest_ready["payload"]["detail"] = "Latest ready"
        snapshot = list(hub._materialized_replay_snapshot(stream))  # noqa: SLF001
        snapshot.append(json.dumps(latest_ready, separators=(",", ":")))
        _set_replay_snapshot(stream, snapshot)
        stream.sequence = 4

        reconnect = await hub.attach_browser(
            "t_hub", connection_id="browser-multiple-ready"
        )
        await hub.publish_activity(record.employee, record.binding, "thinking", "live")
        envelopes = [
            json.loads(await asyncio.wait_for(reconnect.queue.get(), timeout=1))
            for _ in range(4)
        ]

        assert [item["sequence"] for item in envelopes] == [2, 3, 4, 5]
        ready = [
            item
            for item in envelopes
            if item["type"] == "connection" and item["payload"]["state"] == "ready"
        ]
        assert len(ready) == 1
        assert ready[0]["payload"]["detail"] == "Latest ready"
        assert envelopes[-2] == ready[0]
        assert envelopes[-1]["payload"]["detail"] == "live"
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_same_binding_attach_fails_closed_for_history_before_reset(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        reset_serialized = await asyncio.wait_for(existing.queue.get(), timeout=1)
        ready_serialized = await asyncio.wait_for(existing.queue.get(), timeout=1)
        await hub.publish_activity(
            record.employee, record.binding, "thinking", "historical"
        )
        activity = json.loads(await asyncio.wait_for(existing.queue.get(), timeout=1))
        reset = json.loads(reset_serialized)
        ready = json.loads(ready_serialized)
        activity["sequence"] = 1
        activity["payload"]["sequence"] = 1
        reset["sequence"] = 2
        ready["sequence"] = 3
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        snapshot = [
            json.dumps(item, separators=(",", ":"))
            for item in (activity, reset, ready)
        ]
        _set_replay_snapshot(stream, snapshot)

        unavailable = await hub.attach_browser(
            "t_hub", connection_id="browser-history-before-reset"
        )

        assert unavailable.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert unavailable.closed.is_set()
        assert unavailable.queue.empty()
        assert unavailable.connection_id not in stream.browsers
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_same_binding_attach_fails_closed_for_repeated_reset(tmp_path: Path) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        first_reset = json.loads(
            await asyncio.wait_for(existing.queue.get(), timeout=1)
        )
        ready = json.loads(await asyncio.wait_for(existing.queue.get(), timeout=1))
        repeated_reset = dict(first_reset)
        repeated_reset["sequence"] = 2
        ready["sequence"] = 3
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        stream.sequence = 3
        snapshot = [
            json.dumps(item, separators=(",", ":"))
            for item in (first_reset, repeated_reset, ready)
        ]
        _set_replay_snapshot(stream, snapshot)

        unavailable = await hub.attach_browser(
            "t_hub", connection_id="browser-repeated-reset"
        )

        assert unavailable.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert unavailable.closed.is_set()
        assert unavailable.queue.empty()
        assert unavailable.connection_id not in stream.browsers
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("ready_sequence", "current_sequence"),
    ((2, 3),),
    ids=("does-not-end-at-current",),
)
def test_same_binding_attach_fails_closed_for_noncontiguous_or_stale_snapshot(
    tmp_path: Path,
    ready_sequence: int,
    current_sequence: int,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        ready = json.loads(await asyncio.wait_for(existing.queue.get(), timeout=1))
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        ready["sequence"] = ready_sequence
        snapshot = list(hub._materialized_replay_snapshot(stream))  # noqa: SLF001
        snapshot[-1] = json.dumps(ready, separators=(",", ":"))
        _set_replay_snapshot(stream, snapshot)
        stream.sequence = current_sequence

        unavailable = await hub.attach_browser(
            "t_hub", connection_id=f"browser-invalid-sequences-{ready_sequence}"
        )

        assert unavailable.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert unavailable.closed.is_set()
        assert unavailable.queue.empty()
        assert unavailable.connection_id not in stream.browsers
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("envelope_type", "nested_sequence_key"),
    (
        ("activity", "sequence"),
        ("permission_request", "openedSequence"),
        ("permission_outcome", "settledSequence"),
    ),
)
def test_same_binding_attach_fails_closed_for_nested_sequence_mismatch(
    tmp_path: Path,
    envelope_type: str,
    nested_sequence_key: str,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        await asyncio.wait_for(existing.queue.get(), timeout=1)
        ready_serialized = await asyncio.wait_for(existing.queue.get(), timeout=1)
        if envelope_type == "activity":
            await hub.publish_activity(
                record.employee, record.binding, "thinking", "historical"
            )
        elif envelope_type == "permission_request":
            await hub.publish_permission_request(
                record.employee,
                record.binding,
                "permission-integrity",
                record.employee.backend_key,
                RequestPermissionRequest(
                    session_id=record.binding.acp_session_id,
                    tool_call=ToolCallUpdate(
                        session_update="tool_call",
                        tool_call_id="tool-integrity",
                        title="Write file",
                        kind="edit",
                        status="pending",
                    ),
                    options=[
                        PermissionOption(
                            option_id="once", name="Allow once", kind="allow_once"
                        )
                    ],
                ),
                20,
            )
        else:
            await hub.publish_permission_outcome(
                record.employee,
                record.binding,
                "permission-integrity",
                RequestPermissionResponse(
                    outcome=DeniedOutcome(outcome="cancelled")
                ),
                "test",
            )
        published = json.loads(await asyncio.wait_for(existing.queue.get(), timeout=1))
        assert published["type"] == envelope_type
        published["payload"][nested_sequence_key] = published["sequence"] + 1
        ready = json.loads(ready_serialized)
        ready["sequence"] = published["sequence"] + 1
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        stream.sequence = ready["sequence"]
        snapshot = list(hub._materialized_replay_snapshot(stream))  # noqa: SLF001
        snapshot[-1] = json.dumps(published, separators=(",", ":"))
        snapshot.append(json.dumps(ready, separators=(",", ":")))
        _set_replay_snapshot(stream, snapshot)

        unavailable = await hub.attach_browser(
            "t_hub", connection_id=f"browser-nested-mismatch-{envelope_type}"
        )

        assert unavailable.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert unavailable.closed.is_set()
        assert unavailable.queue.empty()
        assert unavailable.connection_id not in stream.browsers
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_restart_reset_raises_same_binding_floor(tmp_path: Path) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )

        subscription = await hub.attach_browser(
            "t_hub",
            connection_id="browser-restart",
            last_seen_binding_generation=1,
            last_seen_sequence=41,
        )
        reset = json.loads(await subscription.queue.get())
        ready = json.loads(await subscription.queue.get())
        assert (reset["payload"]["state"], reset["sequence"]) == ("reset", 42)
        assert (ready["payload"]["state"], ready["sequence"]) == ("ready", 43)
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_existing_binding_ensure_stream_ready_loads_once(tmp_path: Path) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        registry = _Registry(record, handle)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry,
            broker=_Broker(),
            permission_broker=_Permissions(),
        )

        _employee, binding, resolved = await hub.ensure_employee_stream("t_hub")
        assert binding == record.binding
        assert resolved is handle
        assert registry.attach_calls == 1
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_connected_browser_crosses_replacement_replay_larger_than_live_queue(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository, browser_capacity=1)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        subscription = await hub.attach_browser("t_hub", connection_id="browser-a")
        await asyncio.wait_for(subscription.queue.get(), timeout=1)
        await asyncio.wait_for(subscription.queue.get(), timeout=1)
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        await hub._enqueue_and_wait(  # noqa: SLF001
            record.employee.employee_id,
            lambda: hub._open_capture(stream),  # noqa: SLF001
        )
        source = ConversationIngressSource(record.employee, 1, record.record_identity)
        stale = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="stale",
                content=TextContentBlock(type="text", text="stale old answer"),
            ),
        )
        replacement_binding = ConversationSessionBinding(
            employee_id=record.binding.employee_id,
            acp_session_id="session-replacement",
            backend_key=record.binding.backend_key,
            binding_generation=2,
        )
        replacement_handle = ConversationRuntimeHandle(
            record.employee,
            replacement_binding,
            handle.child_generation,
            handle.child,
            handle.definition,
            handle.record_identity,
        )

        await hub.registry_conversation_ingress(source, stale)
        await hub._enqueue_and_wait(  # noqa: SLF001
            record.employee.employee_id,
            lambda: hub._bind_stream(  # noqa: SLF001
                source,
                replacement_binding,
                replacement_handle,
                (),
                sequence_floor=0,
                force_new_binding=True,
            ),
        )
        envelopes = [
            json.loads(await asyncio.wait_for(subscription.queue.get(), timeout=1))
            for _ in range(2)
        ]
        await hub.publish_activity(
            record.employee, replacement_binding, "thinking", "after replacement"
        )
        envelopes.append(
            json.loads(await asyncio.wait_for(subscription.queue.get(), timeout=1))
        )
        assert [item["type"] for item in envelopes] == [
            "connection",
            "connection",
            "activity",
        ]
        assert [item["sequence"] for item in envelopes] == [1, 2, 3]
        assert all(item["acpSessionId"] == "session-replacement" for item in envelopes)
        assert record.child.alive is True
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_active_turn_attach_orders_replay_larger_than_live_queue_before_ready_and_live(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        broker = _Broker()
        hub = ConversationHub(repository, browser_capacity=1)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=broker,
            permission_broker=_Permissions(),
        )
        original = await hub.attach_browser("t_hub", connection_id="browser-a")
        await asyncio.wait_for(original.queue.get(), timeout=1)
        await asyncio.wait_for(original.queue.get(), timeout=1)
        await hub.publish_activity(record.employee, record.binding, "thinking", "replayed")
        await asyncio.wait_for(original.queue.get(), timeout=1)
        broker.phase = "running"
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        sequence_before_attach = stream.sequence
        reset_buffer_before_attach = hub._materialized_replay_snapshot(stream)  # noqa: SLF001

        active = await hub.attach_browser(
            "t_hub",
            connection_id="browser-b",
            last_seen_binding_generation=1,
            last_seen_sequence=3,
        )
        assert active.close_reason is None
        assert stream.sequence == sequence_before_attach
        assert hub._materialized_replay_snapshot(stream) == reset_buffer_before_attach  # noqa: SLF001
        assert original.queue.empty()
        await hub.publish_activity(record.employee, record.binding, "thinking", "live")
        envelopes = [
            json.loads(await asyncio.wait_for(active.queue.get(), timeout=1))
            for _ in range(4)
        ]
        assert [item["sequence"] for item in envelopes] == [1, 2, 3, 4]
        ready_indexes = [
            index
            for index, item in enumerate(envelopes)
            if item["type"] == "connection" and item["payload"]["state"] == "ready"
        ]
        assert ready_indexes == [2]
        assert envelopes[1]["payload"]["detail"] == "replayed"
        assert envelopes[-1]["payload"]["detail"] == "live"
        original_live = json.loads(
            await asyncio.wait_for(original.queue.get(), timeout=1)
        )
        assert original_live["sequence"] == sequence_before_attach + 1
        assert original_live["payload"]["detail"] == "live"
        assert active.closed.is_set() is False
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_normalized_codex_edit_over_one_megabyte_replays_through_ready(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path, backend_key="codex")
        employee = ConversationEmployee(
            employee_id="t_hub",
            entity_kind="ticket",
            entity_id="t_hub",
            workspace_roots=(tmp_path,),
            backend_key="codex",
        )
        binding = ConversationSessionBinding(
            employee_id="t_hub",
            acp_session_id="session-hub",
            backend_key="codex",
            binding_generation=1,
        )
        definition = AgentBackendDefinition(
            backend_key="codex",
            argv=(sys.executable, str(SCRIPTED_AGENT)),
            inherited_environment_names=tuple(default_environment()),
            environment_overrides=(("ACP_TEST_LARGE_FILE_EDIT_HISTORY", "1"),),
            expected_agent_name="panels-scripted-agent",
            expected_agent_version="1.0.0",
            turn_capabilities=BackendTurnCapabilities(
                supports_steer=False, observes_compaction=False
            ),
            reverse_service_capabilities=ReverseServiceCapabilities(
                filesystem=False, terminal=False, permission=True
            ),
            working_directory_resolver=lambda value: value.workspace_roots[0],
            turn_strategy=_Strategy(),
            session_notification_normalizer=normalize_codex_session_notification,
        )
        hub = ConversationHub(repository, reset_buffer_byte_limit=1_048_576)
        identity = object()
        source = ConversationIngressSource(employee, 1, identity)

        async def hub_ingress(item: Any) -> None:
            await hub.registry_conversation_ingress(source, item)

        async def deny_permission(_request: Any) -> RequestPermissionResponse:
            return RequestPermissionResponse(outcome={"outcome": "cancelled"})

        async def ignore_death(_cause: BaseException | None) -> None:
            return None

        child = await SdkAcpEmployeeChildFactory(definition).create(
            employee, 1, hub_ingress, deny_permission, ignore_death
        )
        record = AcpEmployeeRecord(employee, binding, 1, child, identity)
        handle = ConversationRuntimeHandle(employee, binding, 1, child, definition, identity)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        whole_file = "unchanged source line\n" * 30_000
        raw = SessionNotification(
            session_id=record.binding.acp_session_id,
            update=ToolCallStart(
                session_update="tool_call",
                tool_call_id="large-edit",
                title="Editing files",
                kind="edit",
                status="completed",
                content=[
                    FileEditToolCallContent(
                        type="diff",
                        path="PROGRESS.md",
                        old_text=whole_file,
                        new_text=whole_file + "small append\n",
                    )
                ],
            ),
        )
        assert len(raw.model_dump_json(by_alias=True, exclude_none=True).encode()) > 1_048_576
        await child.initialize(build_panels_initialize_request(definition))
        await child.capture_load_session(
            LoadSessionRequest(cwd=str(tmp_path), session_id="session-hub", mcp_servers=[]),
            hub_ingress,
        )

        browser = await hub.attach_browser("t_hub", connection_id="browser-normalized")
        envelopes = []
        while True:
            envelope = json.loads(await asyncio.wait_for(browser.queue.get(), timeout=1))
            envelopes.append(envelope)
            if envelope["type"] == "connection" and envelope["payload"]["state"] == "ready":
                break
        assert browser.close_reason is None
        assert [item["payload"]["state"] for item in envelopes if item["type"] == "connection"] == [
            "reset",
            "ready",
        ]
        assert any(item["type"] == "acp_session_update" for item in envelopes)
        edit_envelope = next(item for item in envelopes if item["type"] == "acp_session_update")
        update = edit_envelope["payload"]["update"]
        assert update["_meta"] == {"scriptedRawPayloadExceedsOneMiB": True}
        assert len(json.dumps(edit_envelope).encode()) < 65_536
        await child.close()
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_live_fragments_are_exact_while_shared_reconnect_replay_is_semantic(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        registry = _Registry(record, handle)
        hub = ConversationHub(repository, reset_buffer_byte_limit=15_000)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=registry,
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        source = ConversationIngressSource(record.employee, 1, record.record_identity)
        existing = await hub.attach_browser("t_hub", connection_id="browser-existing")
        initial = [json.loads(await existing.queue.get()) for _ in range(2)]
        assert [item["payload"]["state"] for item in initial] == ["reset", "ready"]

        message_fragments = [f"message-{index:03d}-" + ("m" * 100) for index in range(50)]
        terminal_fragments = [f"terminal-{index:03d}-" + ("t" * 100) for index in range(50)]
        live = []
        for fragment in message_fragments:
            notification = SessionNotification.model_validate(
                {
                    "sessionId": record.binding.acp_session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "messageId": "message-1",
                        "content": {"type": "text", "text": fragment},
                    },
                }
            )
            await hub.registry_conversation_ingress(source, notification)
            live.append(json.loads(await existing.queue.get()))
        for fragment in terminal_fragments:
            notification = SessionNotification.model_validate(
                {
                    "sessionId": record.binding.acp_session_id,
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "terminal-1",
                        "_meta": {
                            "terminal_output_delta": {
                                "data": fragment,
                                "terminal_id": "terminal-1",
                            }
                        },
                    },
                }
            )
            await hub.registry_conversation_ingress(source, notification)
            live.append(json.loads(await existing.queue.get()))
        final = SessionNotification.model_validate(
            {
                "sessionId": record.binding.acp_session_id,
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "terminal-1",
                    "status": "completed",
                    "rawOutput": {
                        "formatted_output": "".join(terminal_fragments),
                        "exit_code": 0,
                    },
                    "_meta": {
                        "terminal_output_delta": {
                            "data": "".join(terminal_fragments),
                            "terminal_id": "terminal-1",
                        },
                        "terminal_exit": {
                            "terminal_id": "terminal-1",
                            "exit_code": 0,
                            "signal": None,
                        }
                    },
                },
            }
        )
        await hub.registry_conversation_ingress(source, final)
        live.append(json.loads(await existing.queue.get()))

        assert [item["sequence"] for item in live] == list(range(3, 104))
        assert [
            item["payload"]["update"]["content"]["text"] for item in live[:50]
        ] == message_fragments
        assert [
            item["payload"]["update"]["_meta"]["terminal_output_delta"]["data"]
            for item in live[50:100]
        ] == terminal_fragments
        assert live[-1]["payload"] == final.model_dump(by_alias=True, exclude_none=True)
        assert not existing.closed.is_set()
        attach_calls = registry.attach_calls

        reconnect = await hub.attach_browser(
            "t_hub",
            connection_id="browser-reconnect",
            last_seen_binding_generation=record.binding.binding_generation,
            last_seen_sequence=live[-1]["sequence"],
        )

        assert reconnect.close_reason is None
        assert registry.attach_calls == attach_calls
        replay = [json.loads(await reconnect.queue.get()) for _ in range(4)]
        assert [item["type"] for item in replay] == [
            "connection",
            "acp_session_update",
            "acp_session_update",
            "connection",
        ]
        assert [item["sequence"] for item in replay] == [100, 101, 102, 103]
        assert replay[1]["payload"]["update"]["content"]["text"] == "".join(
            message_fragments
        )
        assert replay[2]["payload"] == {
            "sessionId": record.binding.acp_session_id,
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "terminal-1",
                "status": "completed",
                "_meta": {
                    "terminal_exit": {
                        "terminal_id": "terminal-1",
                        "exit_code": 0,
                        "signal": None,
                    }
                },
            },
        }
        assert all(
            "terminal_output_delta"
            not in item.get("payload", {}).get("update", {}).get("_meta", {})
            for item in replay
        )
        assert replay[-1]["payload"]["state"] == "ready"
        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        assert stream.reset_buffer_envelope_count == 103
        assert stream.reset_buffer_attempted_bytes > hub._reset_buffer_byte_limit  # noqa: SLF001
        assert stream.reset_buffer_bytes == sum(
            len(item.encode("utf-8"))
            for item in hub._materialized_replay_snapshot(stream)  # noqa: SLF001
        )
        assert stream.reset_buffer_bytes < hub._reset_buffer_byte_limit  # noqa: SLF001

        await hub.publish_activity(record.employee, record.binding, "thinking", "next")
        next_live = json.loads(await reconnect.queue.get())
        assert next_live["sequence"] == 104
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_message_replay_coalescing_does_not_cross_an_intervening_envelope(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="existing")
        await existing.queue.get()
        await existing.queue.get()
        source = ConversationIngressSource(record.employee, 1, record.record_identity)

        def message(text: str, *, kind: str = "agent_message_chunk") -> SessionNotification:
            return SessionNotification.model_validate(
                {
                    "sessionId": record.binding.acp_session_id,
                    "update": {
                        "sessionUpdate": kind,
                        "messageId": "message-1",
                        "content": {"type": "text", "text": text},
                    },
                }
            )

        await hub.registry_conversation_ingress(source, message("A"))
        await existing.queue.get()
        await hub.publish_activity(record.employee, record.binding, "thinking", "between")
        await existing.queue.get()
        await hub.registry_conversation_ingress(source, message("B"))
        await existing.queue.get()
        await hub.registry_conversation_ingress(
            source, message("C", kind="agent_thought_chunk")
        )
        await existing.queue.get()
        await hub.registry_conversation_ingress(
            source, message("D", kind="agent_thought_chunk")
        )
        await existing.queue.get()

        stream = hub._streams[record.employee.employee_id]  # noqa: SLF001
        assert stream.sequence == 7
        canonical_bytes_before_reconnect = stream.reset_buffer_bytes
        reconnect = await hub.attach_browser("t_hub", connection_id="reconnect")
        replay = [json.loads(await reconnect.queue.get()) for _ in range(6)]
        assert [item["type"] for item in replay] == [
            "connection",
            "acp_session_update",
            "activity",
            "acp_session_update",
            "acp_session_update",
            "connection",
        ]
        assert replay[1]["payload"]["update"]["content"]["text"] == "A"
        assert replay[2]["payload"]["detail"] == "between"
        assert replay[3]["payload"]["update"]["content"]["text"] == "B"
        assert replay[4]["payload"]["update"]["content"]["text"] == "CD"
        assert [item["sequence"] for item in replay] == [2, 3, 4, 5, 6, 7]
        assert stream.reset_buffer_bytes == canonical_bytes_before_reconnect
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_genuinely_oversized_semantic_message_snapshot_fails_closed(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        hub = ConversationHub(repository, reset_buffer_byte_limit=1_000)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=_Broker(),
            permission_broker=_Permissions(),
        )
        existing = await hub.attach_browser("t_hub", connection_id="existing")
        await existing.queue.get()
        await existing.queue.get()
        source = ConversationIngressSource(record.employee, 1, record.record_identity)
        notification = SessionNotification.model_validate(
            {
                "sessionId": record.binding.acp_session_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "messageId": "message-1",
                    "content": {"type": "text", "text": "x" * 2_000},
                },
            }
        )
        await hub.registry_conversation_ingress(source, notification)
        delivered = json.loads(await existing.queue.get())
        assert delivered["payload"]["update"]["content"]["text"] == "x" * 2_000

        reconnect = await hub.attach_browser("t_hub", connection_id="reconnect")
        assert reconnect.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert reconnect.queue.empty()
        assert not existing.closed.is_set()
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_slow_live_browser_is_evicted_without_blocking_healthy_browser_and_logs_safe_context(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        broker = _Broker()
        permissions = _Permissions(block_detach=True)
        hub = ConversationHub(repository, browser_capacity=3)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=broker,
            permission_broker=permissions,
        )
        healthy = await hub.attach_browser("t_hub", connection_id="browser-healthy")
        await asyncio.wait_for(healthy.queue.get(), timeout=1)
        await asyncio.wait_for(healthy.queue.get(), timeout=1)
        broker.phase = "running"
        slow = await hub.attach_browser("t_hub", connection_id="browser-slow")
        assert slow.queue.qsize() == 2
        assert healthy.queue.empty()

        delivered = None
        for index in range(4):
            await hub.publish_activity(
                record.employee, record.binding, "thinking", f"live-{index}"
            )
            delivered = json.loads(
                await asyncio.wait_for(healthy.queue.get(), timeout=1)
            )
        assert delivered is not None and delivered["type"] == "activity"
        assert slow.closed.is_set()
        assert slow.close_reason == SLOW_CONSUMER_CLOSE_REASON
        await asyncio.wait_for(permissions.detach_started.wait(), timeout=1)
        finalizer = asyncio.create_task(hub.detach_browser(slow.connection_id))
        await asyncio.sleep(0)
        permissions.detach_release.set()
        await asyncio.wait_for(finalizer, timeout=1)
        assert permissions.detach_calls.count(slow.connection_id) == 1
        records = [
            json.loads(record.message)
            for record in caplog.records
            if "conversation_browser_subscription_closed" in record.message
        ]
        assert len(records) == 1
        assert records[0]["closure_phase"] == "live"
        assert records[0]["live_queue_capacity"] == 3
        assert records[0]["live_queue_envelope_count"] == 3
        assert "live-3" not in caplog.text
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_reset_overflow_rejects_replay_without_partial_prefix_and_logs_safe_context(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        broker = _Broker()
        hub = ConversationHub(repository, reset_buffer_byte_limit=1)
        permissions = _Permissions()
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=broker,
            permission_broker=permissions,
        )
        replay = await hub.attach_browser("t_hub", connection_id="browser-replay")
        assert replay.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert replay.queue.empty()
        await hub.detach_browser(replay.connection_id)
        assert permissions.detach_calls == [replay.connection_id]
        records = [
            json.loads(record.message)
            for record in caplog.records
            if "conversation_browser_subscription_closed" in record.message
        ]
        assert len(records) == 1
        assert records[0]["closure_phase"] == "replay"
        assert records[0]["replay_byte_limit"] == 1
        assert "Conversation loaded" not in caplog.text
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())
