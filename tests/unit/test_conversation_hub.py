from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import pytest
from acp.schema import (
    AgentMessageChunk,
    PromptRequest,
    PromptResponse,
    SessionNotification,
    TextContentBlock,
)

from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.contracts import (
    ConversationEmployee,
    ConversationSessionBinding,
    QueuedPrompt,
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
)
from planner.conversation.runtime_ports import ConversationRuntimeHandle
from planner.conversation.sqlite_binding_repository import (
    SqliteConversationBindingRepository,
)
from planner.conversation.turn_broker import (
    ConversationTurnAttachState,
)
from planner.conversation.wire_contracts import CancelAction, HumanEcho
from planner.core.db import connect, create_schema
from planner.worker_types.configuration import PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS


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


class _Permissions:
    def __init__(self) -> None:
        self.attached: set[str] = set()

    async def attach_browser(
        self, employee: ConversationEmployee, browser_connection_id: str
    ) -> None:
        del employee
        self.attached.add(browser_connection_id)

    async def detach_browser(self, browser_connection_id: str) -> None:
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
        "VALUES ('t_hub', 'Hub', 'coding', 'hermes', 'needs_kickoff', 'needs_kickoff', ?, 1, 1)",
        (json.dumps(fields, separators=(",", ":")),),
    )
    conn.close()
    repository = SqliteConversationBindingRepository(
        db_path,
        workspace_root=tmp_path,
        integer_now=lambda: 2,
        employee_backend_catalog=(PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS.employee_backend_catalog),
        chief_backend_key="hermes",
    )
    binding = ConversationSessionBinding(
        employee_id="t_hub",
        acp_session_id="session-hub",
        backend_key="hermes",
        binding_generation=1,
    )
    await repository.compare_and_swap(None, binding)
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
        hub = ConversationHub(repository)
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
            envelopes = [json.loads(subscription.queue.get_nowait()) for _ in range(9)]
            assert [item["type"] for item in envelopes] == [
                "acp_session_update",
                "connection",
                "acp_session_update",
                "acp_session_update",
                "acp_session_update",
                "connection",
                "human_echo",
                "queue_snapshot",
                "acp_session_update",
            ]
            assert envelopes[0]["sequence"] == 4
            assert [item["sequence"] for item in envelopes[1:]] == list(range(1, 9))
            assert [item["acpSessionId"] for item in envelopes] == [
                "session-hub",
                *(["session-fork"] * 8),
            ]
            assert [
                envelopes[1]["payload"]["state"],
                envelopes[5]["payload"]["state"],
            ] == ["reset", "ready"]
            assert [
                envelopes[index]["payload"]["update"]["content"]["text"]
                for index in (0, 2, 3, 4, 8)
            ] == [
                "ordinary N",
                "durable summary",
                "source candidate",
                "fresh candidate",
                "later N+1",
            ]
            assert envelopes[6]["payload"]["prompt"]["sessionId"] == "session-fork"
            assert envelopes[7]["payload"]["items"][0]["clientMessageId"] == ("queued-1")
        assert not hub._compaction_transitions["t_hub"].quarantined_ingress  # noqa: SLF001
        await hub.complete_compaction_transition(token, replacement_handle)
        attached = await asyncio.wait_for(waiting_attach, timeout=1)
        assert attached.connection_id == "browser-after-compaction"
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
        hub = ConversationHub(repository)
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
            (replay,),
            (queued,),
            successor_echo,
        )
        attached = await asyncio.wait_for(waiting_attach, timeout=1)
        assert attached.connection_id == "browser-after-recovery"
        for subscription in (first, second):
            envelopes = [json.loads(subscription.queue.get_nowait()) for _ in range(6)]
            assert [item["type"] for item in envelopes] == [
                "connection",
                "acp_session_update",
                "connection",
                "human_echo",
                "human_echo",
                "queue_snapshot",
            ]
            assert [item["sequence"] for item in envelopes] == [4, 5, 6, 7, 8, 9]
            assert [
                envelopes[0]["payload"]["state"],
                envelopes[2]["payload"]["state"],
            ] == ["reset", "ready"]
            assert envelopes[0]["payload"]["detail"] == ("Conversation runtime recovered")
            assert envelopes[1]["payload"]["update"]["content"]["text"] == ("durable replay")
            assert envelopes[3]["payload"]["clientMessageId"] == "queued-1"
            assert envelopes[3]["payload"]["prompt"] == queued.prompt.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            assert envelopes[4]["payload"]["clientMessageId"] == ("send-now-successor")
            assert envelopes[4]["payload"]["prompt"] == (
                successor_echo.prompt.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
            assert envelopes[5]["payload"]["items"][0]["clientMessageId"] == ("queued-1")
            assert all(
                item["clientMessageId"] != "send-now-successor"
                for item in envelopes[5]["payload"]["items"]
            )

        # Attaching the waiter publishes its ordinary ready state to all browsers.
        assert json.loads(first.queue.get_nowait())["payload"]["state"] == "ready"
        assert json.loads(second.queue.get_nowait())["payload"]["state"] == "ready"

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


def test_stale_old_session_ingress_does_not_close_replacement_child(tmp_path: Path) -> None:
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
        await subscription.queue.get()
        await subscription.queue.get()
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
        await hub._publish_connection(  # noqa: SLF001
            record.employee, replacement_binding, "ready", "Ready"
        )
        envelopes = [json.loads(await subscription.queue.get()) for _ in range(2)]
        assert [item["payload"]["state"] for item in envelopes] == ["reset", "ready"]
        assert all(item["acpSessionId"] == "session-replacement" for item in envelopes)
        assert record.child.alive is True
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_active_attach_reserves_queue_slot_for_ready(tmp_path: Path) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        broker = _Broker()
        hub = ConversationHub(repository, browser_capacity=2)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=broker,
            permission_broker=_Permissions(),
        )
        original = await hub.attach_browser("t_hub", connection_id="browser-a")
        await original.queue.get()
        await original.queue.get()
        broker.phase = "running"

        active = await hub.attach_browser("t_hub", connection_id="browser-b")
        assert active.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert active.queue.empty()
        assert active.closed.is_set() is False
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_slow_browser_is_evicted_without_blocking_healthy_browser(tmp_path: Path) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        broker = _Broker()
        hub = ConversationHub(repository, browser_capacity=3)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=broker,
            permission_broker=_Permissions(),
        )
        healthy = await hub.attach_browser("t_hub", connection_id="browser-healthy")
        await healthy.queue.get()
        await healthy.queue.get()
        broker.phase = "running"
        slow = await hub.attach_browser("t_hub", connection_id="browser-slow")
        assert slow.queue.qsize() == 3
        await healthy.queue.get()

        await hub.publish_activity(record.employee, record.binding, "thinking", "live")
        delivered = json.loads(await healthy.queue.get())
        assert delivered["type"] == "activity"
        assert slow.closed.is_set()
        assert slow.close_reason == SLOW_CONSUMER_CLOSE_REASON
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_reset_overflow_rejects_active_replay_without_partial_prefix(tmp_path: Path) -> None:
    async def exercise() -> None:
        _db_path, repository = await _ticket_database(tmp_path)
        record, handle = _runtime(tmp_path)
        broker = _Broker()
        hub = ConversationHub(repository, reset_buffer_byte_limit=1)
        hub.bind_owners(  # type: ignore[arg-type]
            registry=_Registry(record, handle),
            broker=broker,
            permission_broker=_Permissions(),
        )
        live = await hub.attach_browser("t_hub", connection_id="browser-live")
        assert json.loads(await live.queue.get())["payload"]["state"] == "reset"
        assert json.loads(await live.queue.get())["payload"]["state"] == "ready"
        broker.phase = "running"

        replay = await hub.attach_browser("t_hub", connection_id="browser-replay")
        assert replay.close_reason == REPLAY_UNAVAILABLE_CLOSE_REASON
        assert replay.queue.empty()
        await hub.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())
