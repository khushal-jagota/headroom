from __future__ import annotations

import asyncio
import contextlib
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest
from acp.connection import StreamDirection, StreamEvent
from acp.schema import (
    AgentCapabilities,
    AgentThoughtChunk,
    CancelNotification,
    CreateTerminalRequest,
    ForkSessionRequest,
    ForkSessionResponse,
    Implementation,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PermissionOption,
    PromptRequest,
    PromptResponse,
    RequestPermissionRequest,
    SessionInfoUpdate,
    SessionNotification,
    TerminalOutputRequest,
    TextContentBlock,
    ToolCallStart,
    ToolCallUpdate,
)
from tests.support.acp_conformance import (
    assert_compaction_visibility,
    assert_delivery_capabilities,
    assert_permission_exactly_once,
    mutate_probe_evidence,
)
from tests.support.acp_in_memory_binding_repository import InMemoryAcpBindingRepository
from tests.support.acp_runtime_subject import ProductionAcp02ConformanceSubject

from planner.conversation.backend_catalog import (
    EmployeeBackendCatalog,
    EmployeeBackendRegistration,
    MaterializedEmployeeBackendRegistration,
)
from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.codex_turn_strategy import CodexAcpTurnStrategy
from planner.conversation.configuration import (
    ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS,
    ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS,
)
from planner.conversation.contracts import (
    ContextCompaction,
    ConversationActivity,
    ConversationEmployee,
    ConversationPermissionOutcome,
    ConversationPermissionRequest,
    ConversationSessionBinding,
)
from planner.conversation.employee_registry import AcpEmployeeRegistry
from planner.conversation.ordered_ingress import OrderedAcpConversationIngress
from planner.conversation.permission_broker import ConversationPermissionBroker
from planner.conversation.reverse_services.terminal import (
    AcpReverseTerminalError,
    ScopedAcpTerminalService,
)
from planner.conversation.runtime_ports import (
    CompactionTransitionToken,
    ConversationRuntimeHandle,
    ConversationRuntimeUnavailable,
    RequestedCancelRecoveryTransitionToken,
    RequestedCancelRuntimeReplacement,
)
from planner.conversation.sdk_child import AcpChildProcessExited
from planner.conversation.turn_broker import (
    ConversationTurnBroker,
    ConversationTurnBrokerError,
    ConversationTurnBrokerShutdownError,
    PromptIngressBarrierResult,
)
from planner.conversation.wire_contracts import HumanEcho


def _single_backend_runtime(
    definition: AgentBackendDefinition, child_factory: Any
) -> tuple[
    EmployeeBackendCatalog,
    tuple[MaterializedEmployeeBackendRegistration, ...],
]:
    materialized = MaterializedEmployeeBackendRegistration(
        definition=definition,
        child_factory=child_factory,
        is_executable=lambda: True,
    )
    catalog = EmployeeBackendCatalog(
        (
            EmployeeBackendRegistration(
                backend_key=definition.backend_key,
                runtime_builder=lambda _context: materialized,
            ),
        )
    )
    return catalog, (materialized,)


def test_production_compaction_capture_budget_is_distinct_from_shutdown() -> None:
    capture_default = inspect.signature(ConversationTurnBroker).parameters[
        "capture_timeout_seconds"
    ].default

    assert ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS == 300
    assert ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS == 10
    assert capture_default == ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS
    assert capture_default != ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS


class _Strategy:
    def classify_replay(
        self, _binding: Any, replay: tuple[Any, ...], _boundaries: tuple[Any, ...]
    ) -> tuple[Any, ...]:
        return replay

    async def steer(self, binding: Any, prompt: Any, client_message_id: str):
        del binding, prompt
        from planner.conversation.contracts import TurnDeliveryReceipt

        return TurnDeliveryReceipt(
            client_message_id=client_message_id, choice="steer", state="accepted"
        )

    def observe_compaction(self, binding: Any, notification: Any):
        del binding, notification
        return None

    async def capture_compaction(self, binding: Any):
        raise AssertionError(binding)


class _Child:
    generation = 1
    alive = True

    def __init__(self) -> None:
        self.alive = True
        self.prompts: list[PromptRequest] = []
        self.prompt_observer: Any = None
        self.cancelled: list[CancelNotification] = []
        self.responses: asyncio.Queue[PromptResponse | BaseException] = asyncio.Queue()
        self.cancel_gate: asyncio.Event | None = None

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        self.prompts.append(request)
        if self.prompt_observer is not None:
            self.prompt_observer(request)
        response = await self.responses.get()
        if isinstance(response, BaseException):
            raise response
        return response

    async def cancel(self, notification: CancelNotification) -> None:
        self.cancelled.append(notification)
        if self.cancel_gate is not None:
            await self.cancel_gate.wait()


class _Publisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.sequence = 0
        self.permission_requests: list[ConversationPermissionRequest] = []
        self.permission_outcomes: list[ConversationPermissionOutcome] = []
        self.terminal_states: list[Any] = []
        self.programmatic_prompts: list[Any] = []

    async def publish_activity(self, employee: Any, binding: Any, state: Any, detail: str):
        del employee, binding
        self.sequence += 1
        value = ConversationActivity(state=state, detail=detail, sequence=self.sequence)
        self.events.append(("activity", value))
        return value

    async def publish_delivery_receipt(self, employee: Any, binding: Any, receipt: Any):
        del employee, binding
        self.events.append(("receipt", receipt))

    async def publish_queue_snapshot(self, employee: Any, binding: Any, prompts: Any):
        del employee, binding
        self.events.append(("queue", prompts))

    async def publish_compaction(self, employee: Any, binding: Any, compaction: Any):
        del employee, binding
        self.events.append(("compaction", compaction))

    async def publish_programmatic_prompt(self, employee: Any, binding: Any, prompt: Any):
        del employee, binding
        self.programmatic_prompts.append(prompt)

    async def publish_permission_request(
        self,
        employee: Any,
        binding: Any,
        request_id: str,
        backend_key: str,
        request: Any,
        deadline_at: int,
    ) -> ConversationPermissionRequest:
        del binding
        self.sequence += 1
        value = ConversationPermissionRequest(
            request_id=request_id,
            employee_id=employee.employee_id,
            backend_key=backend_key,
            request=request,
            lifecycle="pending",
            deadline_at=deadline_at,
            opened_sequence=self.sequence,
        )
        self.permission_requests.append(value)
        return value

    async def publish_permission_outcome(
        self,
        employee: Any,
        binding: Any,
        request_id: str,
        response: Any,
        cancellation_reason: str | None,
    ) -> ConversationPermissionOutcome:
        del employee, binding
        self.sequence += 1
        value = ConversationPermissionOutcome(
            request_id=request_id,
            response=response,
            cancellation_reason=cancellation_reason,
            settled_sequence=self.sequence,
        )
        self.permission_outcomes.append(value)
        return value

    async def publish_terminal_state(self, employee: Any, binding: Any, state: Any):
        assert employee.employee_id == binding.employee_id
        self.terminal_states.append(state)


class _Runtime:
    def __init__(self, handle: ConversationRuntimeHandle) -> None:
        self.handle = handle
        self.retired = 0
        self.replacements: list[RequestedCancelRuntimeReplacement] = []
        self.replacement_started = asyncio.Event()
        self.replacement_gate: asyncio.Event | None = None
        self.replacement_error: BaseException | None = None
        self.invalid_replacement = False
        self.retirement_started = asyncio.Event()
        self.retirement_finished = asyncio.Event()
        self.retirement_gate: asyncio.Event | None = None
        self.retirement_error: BaseException | None = None
        self.retirement_resists_cancellation = False
        self.retirement_deadlines: list[float] = []
        self.failed_handles: list[ConversationRuntimeHandle] = []

    async def resolve_runtime_handle(self, employee_id: str, binding_generation: int):
        assert employee_id == self.handle.employee.employee_id
        assert binding_generation == self.handle.binding.binding_generation
        if not self.handle.child.alive:
            raise ConversationRuntimeUnavailable("runtime is not live")
        return self.handle

    async def acquire_runtime_lease(self, handle: ConversationRuntimeHandle):
        from planner.conversation.runtime_ports import ConversationRuntimeLease

        if handle is not self.handle or not handle.child.alive:
            raise RuntimeError("stale")
        return ConversationRuntimeLease(handle)

    async def retire_runtime_lease(self, lease: Any, deadline: float) -> None:
        assert lease.handle is self.handle
        self.retired += 1
        self.retirement_deadlines.append(deadline)
        self.retirement_started.set()
        try:
            if self.retirement_gate is not None:
                try:
                    await self.retirement_gate.wait()
                except asyncio.CancelledError:
                    if not self.retirement_resists_cancellation:
                        if lease.handle not in self.failed_handles:
                            self.failed_handles.append(lease.handle)
                        self.handle.child.alive = False
                        raise
                    await self.retirement_gate.wait()
            if self.retirement_error is not None:
                raise self.retirement_error
            self.handle.child.alive = False
        finally:
            self.retirement_finished.set()

    async def fail_runtime_handle(self, handle: ConversationRuntimeHandle) -> None:
        assert handle is self.handle
        if handle not in self.failed_handles:
            self.failed_handles.append(handle)
        self.retired += 1
        self.handle.child.alive = False

    async def replace_runtime_after_requested_cancel(
        self, lease: Any, deadline: float
    ) -> RequestedCancelRuntimeReplacement:
        assert lease.handle is self.handle
        assert deadline > asyncio.get_running_loop().time()
        self.replacement_started.set()
        if self.replacement_gate is not None:
            await self.replacement_gate.wait()
        if self.replacement_error is not None:
            raise self.replacement_error
        if self.invalid_replacement:
            replacement = RequestedCancelRuntimeReplacement(self.handle, ())
            self.replacements.append(replacement)
            return replacement
        replacement_child = _Child()
        replacement_handle = ConversationRuntimeHandle(
            employee=self.handle.employee,
            binding=self.handle.binding,
            child_generation=self.handle.child_generation + 1,
            child=replacement_child,
            definition=self.handle.definition,
            record_identity=object(),
        )
        replacement = RequestedCancelRuntimeReplacement(replacement_handle, ())
        self.replacements.append(replacement)
        self.handle = replacement_handle
        return replacement

    def strategy_for_lease(self, lease: Any):
        del lease
        return self.handle.definition.turn_strategy


class _RequestedCancelTransitionPort:
    def __init__(self) -> None:
        self.begun: list[RequestedCancelRecoveryTransitionToken] = []
        self.resumed: list[RequestedCancelRecoveryTransitionToken] = []
        self.committed: list[tuple[Any, ...]] = []
        self.failed: list[tuple[RequestedCancelRecoveryTransitionToken, str]] = []
        self.begin_started = asyncio.Event()
        self.begin_gate: asyncio.Event | None = None
        self.commit_started = asyncio.Event()
        self.commit_gate: asyncio.Event | None = None
        self.commit_error: BaseException | None = None

    async def begin_requested_cancel_recovery_transition(
        self, handle: ConversationRuntimeHandle, deadline: float
    ) -> RequestedCancelRecoveryTransitionToken:
        self.begin_started.set()
        if self.begin_gate is not None:
            await self.begin_gate.wait()
        token = RequestedCancelRecoveryTransitionToken(object(), handle, deadline)
        self.begun.append(token)
        return token

    async def resume_requested_cancelled_runtime(
        self, token: RequestedCancelRecoveryTransitionToken
    ) -> None:
        self.resumed.append(token)

    async def commit_requested_cancel_recovery_transition(
        self,
        token: RequestedCancelRecoveryTransitionToken,
        replacement_handle: ConversationRuntimeHandle,
        replay: tuple[Any, ...],
        queued_prompts: tuple[Any, ...],
        send_now_successor_human_echo: HumanEcho | None,
    ) -> None:
        self.commit_started.set()
        if self.commit_gate is not None:
            await self.commit_gate.wait()
        if self.commit_error is not None:
            raise self.commit_error
        self.committed.append(
            (
                token,
                replacement_handle,
                replay,
                queued_prompts,
                send_now_successor_human_echo,
            )
        )

    async def fail_requested_cancel_recovery_transition(
        self, token: RequestedCancelRecoveryTransitionToken, reason: str
    ) -> None:
        self.failed.append((token, reason))


class _RequestedCancelCleanupFailure:
    def __init__(self) -> None:
        self.cleanup_calls = 0

    async def cleanup_child_generation(
        self,
        employee_id: str,
        session_id: str,
        child_generation: int,
        deadline: float,
    ) -> tuple[str, ...]:
        del employee_id, session_id, child_generation, deadline
        self.cleanup_calls += 1
        if self.cleanup_calls == 1:
            raise RuntimeError("reverse-service cleanup failed")
        return ()

    async def prepare_new_conversation(
        self, binding: ConversationSessionBinding, deadline: float
    ) -> tuple[str, ...]:
        del binding, deadline
        return ()

    async def shutdown(self, deadline: float) -> tuple[str, ...]:
        del deadline
        return ()


def _fixture(
    *,
    cancel_timeout_seconds: float = 10,
    supports_steer: bool = True,
    observes_compaction: bool = False,
    requires_fresh_child_after_requested_cancel: bool = False,
    strategy: Any | None = None,
    permission_broker: ConversationPermissionBroker | None = None,
    terminal_service: ScopedAcpTerminalService | None = None,
    reverse_permission: bool = False,
    reverse_terminal: bool = False,
    workspace_root: Path = Path("/tmp"),
    publisher: _Publisher | None = None,
    requested_cancel_recovery_timeout_seconds: float = 10,
    backend_key: str = "fake",
) -> tuple[
    ConversationTurnBroker,
    _Child,
    _Publisher,
    ConversationRuntimeHandle,
    _Runtime,
]:
    employee = ConversationEmployee(
        employee_id="employee-a",
        entity_kind="ticket",
        entity_id="ticket-a",
        workspace_roots=(workspace_root,),
        backend_key=backend_key,
    )
    binding = ConversationSessionBinding(
        employee_id=employee.employee_id,
        acp_session_id="session-a",
        backend_key=backend_key,
        binding_generation=1,
    )
    child = _Child()
    definition = AgentBackendDefinition(
        backend_key=backend_key,
        argv=("/fake",),
        inherited_environment_names=(),
        environment_overrides=(),
        expected_agent_name="fake",
        expected_agent_version="1",
        turn_capabilities=BackendTurnCapabilities(
            supports_steer=supports_steer,
            observes_compaction=observes_compaction,
            requires_fresh_child_after_requested_cancel=(
                requires_fresh_child_after_requested_cancel
            ),
        ),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False,
            terminal=reverse_terminal,
            permission=reverse_permission,
        ),
        working_directory_resolver=lambda employee: employee.workspace_roots[0],
        turn_strategy=strategy or _Strategy(),
    )
    handle = ConversationRuntimeHandle(
        employee=employee,
        binding=binding,
        child_generation=1,
        child=child,
        definition=definition,
        record_identity=object(),
    )
    publisher = publisher or _Publisher()
    runtime = _Runtime(handle)
    broker = ConversationTurnBroker(
        runtime,
        publisher,
        integer_now=lambda: 100,
        cancel_timeout_seconds=cancel_timeout_seconds,
        requested_cancel_recovery_timeout_seconds=(
            requested_cancel_recovery_timeout_seconds
        ),
        permission_broker=permission_broker,
        terminal_service=terminal_service,
    )
    transition_port = _RequestedCancelTransitionPort()
    broker.set_requested_cancel_recovery_transition_port(transition_port)
    runtime.requested_cancel_transition_port = transition_port
    return broker, child, publisher, handle, runtime


def test_tracked_rejection_tears_down_before_queued_successor_can_start() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, _runtime = _fixture()
        prompt_started = asyncio.Event()
        release_barrier = asyncio.Event()
        terminal_order: list[str] = []

        def started(runtime_handle: ConversationRuntimeHandle, epoch: int) -> None:
            assert runtime_handle is handle
            assert epoch == 1
            prompt_started.set()

        async def barrier(
            runtime_handle: ConversationRuntimeHandle, epoch: int
        ) -> PromptIngressBarrierResult:
            assert runtime_handle is handle
            assert epoch == 1
            await release_barrier.wait()
            return PromptIngressBarrierResult(
                rejection_reason="Agent sent an unsupported update"
            )

        broker.set_prompt_ingress_hooks(
            prompt_started=started,
            settlement_barrier=barrier,
        )
        tracked = await broker.deliver_tracked_normal(
            handle,
            "worker-1",
            PromptRequest(
                session_id=handle.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="worker")],
            ),
            before_prompt_started=lambda _handle: terminal_order.append("collector-installed"),
            after_prompt_settled=lambda _handle: terminal_order.append("collector-removed"),
        )
        await prompt_started.wait()
        assert [
            (item.prompt_id, item.source, item.prompt.prompt[0].text)
            for item in publisher.programmatic_prompts
        ] == [("worker-1", "worker", "worker")]
        await broker.deliver(
            handle,
            "queued-1",
            "queue",
            PromptRequest(
                session_id=handle.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="queued")],
            ),
        )
        await child.responses.put(PromptResponse(stop_reason="end_turn"))
        release_barrier.set()

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.status == "errored"
        assert terminal_order == ["collector-installed", "collector-removed"]
        assert [request.prompt[0].text for request in child.prompts] == ["worker"]
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def _prompt(text: str = "hello") -> PromptRequest:
    return PromptRequest(session_id="session-a", prompt=[TextContentBlock(type="text", text=text)])


def _permission_request() -> RequestPermissionRequest:
    return RequestPermissionRequest(
        session_id="session-a",
        tool_call=ToolCallUpdate(
            session_update="tool_call",
            tool_call_id="permission-tool",
            title="Inspect file",
            kind="read",
            status="pending",
        ),
        options=[
            PermissionOption(
                option_id="allow-once", name="Allow once", kind="allow_once"
            )
        ],
    )


async def _wait_until(predicate: Any) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


def test_stop_prompt_exception_recovers_fresh_child_before_idle_and_later_prompt() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, runtime = _fixture()
        tracked = await broker.deliver_tracked_normal(
            handle,
            "cancelled-worker",
            _prompt("cancel me"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        transition_port = runtime.requested_cancel_transition_port
        transition_port.begin_gate = asyncio.Event()
        runtime.replacement_gate = asyncio.Event()
        transition_port.commit_gate = asyncio.Event()
        cancelling = asyncio.create_task(broker.cancel(handle))
        await transition_port.begin_started.wait()
        assert child.cancelled == []
        transition_port.begin_gate.set()
        await cancelling
        await _wait_until(lambda: len(child.cancelled) == 1)
        child.responses.put_nowait(RuntimeError("prompt unwound after cancellation"))
        await runtime.replacement_started.wait()
        assert not tracked.completion.done()
        assert not any(
            event[0] == "activity" and event[1].state == "idle"
            for event in publisher.events
        )
        runtime.replacement_gate.set()
        await transition_port.commit_started.wait()
        assert not tracked.completion.done()
        transition_port.commit_gate.set()
        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.status == "interrupted"
        assert result.response is None
        assert result.error is None
        assert [
            event[1].state for event in publisher.events if event[0] == "activity"
        ][-2:] == ["interrupted", "idle"]
        assert runtime.retired == 0
        assert len(runtime.replacements) == 1
        replacement = runtime.replacements[0].replacement_handle
        assert replacement.binding == handle.binding
        assert replacement.child_generation == handle.child_generation + 1
        assert replacement.child is not child
        assert replacement.record_identity is not handle.record_identity
        assert (await broker.attach_state(replacement)).phase == "idle"

        await broker.deliver(replacement, "after-cancel", "normal", _prompt("after cancel"))
        replacement.child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(
            lambda: sum(
                event[0] == "activity" and event[1].state == "idle"
                for event in publisher.events
            )
            == 2
        )
        assert [request.prompt[0].text for request in child.prompts] == ["cancel me"]
        assert [request.prompt[0].text for request in replacement.child.prompts] == [
            "after cancel"
        ]
        assert (await broker.attach_state(replacement)).phase == "idle"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_stop_terminal_cancel_replaces_capability_child_before_advancing_queue() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, runtime = _fixture(
            requires_fresh_child_after_requested_cancel=True
        )
        tracked = await broker.deliver_tracked_normal(
            handle,
            "cancelled-worker",
            _prompt("cancel me"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        await broker.deliver(handle, "queued", "queue", _prompt("queued"))

        await broker.cancel(handle)
        response = PromptResponse(stop_reason="cancelled")
        child.responses.put_nowait(response)
        await _wait_until(lambda: len(runtime.replacements) == 1)
        replacement = runtime.replacements[0].replacement_handle
        await _wait_until(lambda: len(replacement.child.prompts) == 1)
        result = await asyncio.wait_for(tracked.completion, timeout=1)

        assert result.status == "interrupted"
        assert result.response is response
        assert result.error is None
        assert runtime.requested_cancel_transition_port.resumed == []
        assert len(runtime.requested_cancel_transition_port.committed) == 1
        assert replacement.binding == handle.binding
        assert replacement.binding.acp_session_id == handle.binding.acp_session_id
        assert replacement.child_generation == handle.child_generation + 1
        assert [request.prompt[0].text for request in child.prompts] == ["cancel me"]
        assert [request.prompt[0].text for request in replacement.child.prompts] == [
            "queued"
        ]
        assert not any(
            event[0] == "activity" and event[1].state == "failed"
            for event in publisher.events
        )

        replacement.child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(
            lambda: sum(
                event[0] == "activity" and event[1].state == "idle"
                for event in publisher.events
            )
            == 2
        )
        assert (await broker.attach_state(replacement)).phase == "idle"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_stop_prompt_exception_advances_existing_fifo_once_on_replacement() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, runtime = _fixture()
        await broker.deliver(handle, "predecessor", "normal", _prompt("predecessor"))
        await broker.deliver(handle, "queued", "queue", _prompt("queued"))

        await broker.cancel(handle)
        child.responses.put_nowait(RuntimeError("prompt unwound after cancellation"))
        await _wait_until(lambda: len(runtime.replacements) == 1)
        replacement = runtime.replacements[0].replacement_handle
        await _wait_until(lambda: len(replacement.child.prompts) == 1)

        assert [request.prompt[0].text for request in child.prompts] == ["predecessor"]
        assert [request.prompt[0].text for request in replacement.child.prompts] == [
            "queued"
        ]
        assert len(runtime.requested_cancel_transition_port.committed) == 1
        committed = runtime.requested_cancel_transition_port.committed[0]
        assert [item.client_message_id for item in committed[3]] == ["queued"]
        assert committed[4] is None
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert sum(
            item.client_message_id == "queued" and item.state == "started"
            for item in receipts
        ) == 1
        replacement.child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(
            lambda: sum(
                event[0] == "activity" and event[1].state == "idle"
                for event in publisher.events
            )
            == 2
        )
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_send_now_prompt_exception_retargets_fresh_child_and_starts_successor_once() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, runtime = _fixture()
        await broker.deliver(handle, "predecessor", "normal", _prompt("predecessor"))
        transition_port = runtime.requested_cancel_transition_port
        transition_port.begin_gate = asyncio.Event()
        runtime.replacement_gate = asyncio.Event()
        transition_port.commit_gate = asyncio.Event()
        send_now = asyncio.create_task(
            broker.deliver(handle, "successor", "send_now", _prompt("successor"))
        )
        await transition_port.begin_started.wait()
        assert child.cancelled == []
        transition_port.begin_gate.set()
        await send_now

        await _wait_until(lambda: len(child.cancelled) == 1)
        child.responses.put_nowait(RuntimeError("prompt unwound after cancellation"))
        await runtime.replacement_started.wait()
        assert len(child.prompts) == 1
        runtime.replacement_gate.set()
        await transition_port.commit_started.wait()
        assert len(child.prompts) == 1
        transition_port.commit_gate.set()
        await _wait_until(lambda: len(runtime.replacements) == 1)
        replacement = runtime.replacements[0].replacement_handle
        await _wait_until(lambda: len(replacement.child.prompts) == 1)

        assert [request.prompt[0].text for request in child.prompts] == ["predecessor"]
        assert [request.prompt[0].text for request in replacement.child.prompts] == [
            "successor"
        ]
        assert len(transition_port.committed) == 1
        assert transition_port.committed[0][4] == HumanEcho(
            client_message_id="successor",
            prompt=_prompt("successor"),
        )
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert sum(
            receipt.client_message_id == "predecessor"
            and receipt.state == "interrupted"
            for receipt in receipts
        ) == 1
        assert sum(
            receipt.client_message_id == "successor" and receipt.state == "started"
            for receipt in receipts
        ) == 1
        assert not any(
            event[0] == "activity" and event[1].state == "failed"
            for event in publisher.events
        )
        assert runtime.retired == 0

        replacement.child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(
            lambda: any(
                event[0] == "activity" and event[1].state == "idle"
                for event in publisher.events
            )
        )
        assert (await broker.attach_state(replacement)).phase == "idle"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_send_now_terminal_cancel_retargets_capability_successor_to_fresh_child() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, runtime = _fixture(
            requires_fresh_child_after_requested_cancel=True
        )
        tracked = await broker.deliver_tracked_normal(
            handle,
            "predecessor",
            _prompt("predecessor"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        await broker.deliver(handle, "successor", "send_now", _prompt("successor"))

        response = PromptResponse(stop_reason="cancelled")
        child.responses.put_nowait(response)
        await _wait_until(lambda: len(runtime.replacements) == 1)
        replacement = runtime.replacements[0].replacement_handle
        await _wait_until(lambda: len(replacement.child.prompts) == 1)
        result = await asyncio.wait_for(tracked.completion, timeout=1)

        assert result.status == "interrupted"
        assert result.response is response
        assert result.error is None
        assert runtime.requested_cancel_transition_port.resumed == []
        assert len(runtime.requested_cancel_transition_port.committed) == 1
        assert runtime.requested_cancel_transition_port.committed[0][4] == HumanEcho(
            client_message_id="successor",
            prompt=_prompt("successor"),
        )
        assert replacement.binding == handle.binding
        assert replacement.binding.acp_session_id == handle.binding.acp_session_id
        assert replacement.child_generation == handle.child_generation + 1
        assert [request.prompt[0].text for request in child.prompts] == ["predecessor"]
        assert [request.prompt[0].text for request in replacement.child.prompts] == [
            "successor"
        ]
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert sum(
            receipt.client_message_id == "successor" and receipt.state == "started"
            for receipt in receipts
        ) == 1

        replacement.child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(
            lambda: any(
                event[0] == "activity" and event[1].state == "idle"
                for event in publisher.events
            )
        )
        assert (await broker.attach_state(replacement)).phase == "idle"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "failure_point",
    [
        "reverse_cleanup",
        "replacement",
        "invalid_replacement",
        "hub_commit",
        "deadline",
    ],
)
def test_requested_cancel_recovery_failure_rejects_all_frozen_intent_without_idle(
    failure_point: str,
) -> None:
    async def exercise() -> None:
        terminal_service = (
            _RequestedCancelCleanupFailure()
            if failure_point == "reverse_cleanup"
            else None
        )
        broker, child, publisher, handle, runtime = _fixture(
            terminal_service=terminal_service,  # type: ignore[arg-type]
            requested_cancel_recovery_timeout_seconds=(
                0.04 if failure_point == "deadline" else 10
            ),
        )
        port = runtime.requested_cancel_transition_port
        if failure_point == "replacement":
            runtime.replacement_error = RuntimeError("private load failed")
        elif failure_point == "invalid_replacement":
            runtime.invalid_replacement = True
        elif failure_point == "hub_commit":
            port.commit_error = RuntimeError("hub commit failed")
        elif failure_point == "deadline":
            runtime.replacement_gate = asyncio.Event()
        await broker.deliver(handle, "predecessor", "normal", _prompt("predecessor"))
        await broker.deliver(handle, "fifo", "queue", _prompt("fifo"))
        await broker.deliver(handle, "successor", "send_now", _prompt("successor"))

        child.responses.put_nowait(RuntimeError("prompt unwound after cancellation"))
        await _wait_until(
            lambda: any(
                event[0] == "activity" and event[1].state == "failed"
                for event in publisher.events
            )
        )

        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert sum(
            item.client_message_id == "predecessor" and item.state == "interrupted"
            for item in receipts
        ) == 1
        assert sum(
            item.client_message_id == "successor" and item.state == "rejected"
            for item in receipts
        ) == 1
        assert sum(
            item.client_message_id == "fifo" and item.state == "rejected"
            for item in receipts
        ) == 1
        assert not any(
            event[0] == "activity" and event[1].state == "idle"
            for event in publisher.events
        )
        assert len(port.failed) <= 1
        if failure_point != "deadline":
            assert len(port.failed) == 1
        assert runtime.retired == 1
        assert (await broker.attach_state(runtime.handle)).phase == "failed"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_uncancelled_prompt_exception_preserves_backend_reason() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, _runtime = _fixture()
        tracked = await broker.deliver_tracked_normal(
            handle,
            "failed-worker",
            _prompt("fail without cancellation"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )

        child.responses.put_nowait(RuntimeError("prompt transport failed"))

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.status == "errored"
        assert result.response is None
        assert result.error == "prompt transport failed"
        assert result.failure_provenance == "backend"
        assert [
            event[1].state for event in publisher.events if event[0] == "activity"
        ][-1] == "failed"
        assert (await broker.attach_state(handle)).phase == "failed"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_explicit_codex_compaction_prompt_exception_uses_exact_reason_once() -> None:
    async def exercise() -> None:
        strategy = CodexAcpTurnStrategy()
        broker, child, publisher, handle, _runtime = _fixture(
            observes_compaction=True,
            strategy=strategy,
            backend_key="codex",
        )
        tracked_terminal_calls: list[str] = []
        tracked = await broker.deliver_tracked_normal(
            handle,
            "failed-explicit-compaction",
            _prompt("/compact"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: tracked_terminal_calls.append("settled"),
        )
        await broker.deliver(
            handle,
            "queued-after-compaction",
            "queue",
            _prompt("must not run"),
        )

        child.responses.put_nowait(RuntimeError("explicit compaction rejected"))

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        exact_reason = "RuntimeError: explicit compaction rejected"
        assert result.status == "errored"
        assert result.error == exact_reason
        assert tracked_terminal_calls == ["settled"]
        compactions = [event[1] for event in publisher.events if event[0] == "compaction"]
        assert [item.state for item in compactions] == ["compacting", "failed"]
        assert [item.trigger for item in compactions] == ["explicit", "explicit"]
        assert compactions[-1].reason == exact_reason
        rejected = [
            event[1]
            for event in publisher.events
            if event[0] == "receipt"
            and event[1].client_message_id == "queued-after-compaction"
            and event[1].state == "rejected"
        ]
        assert len(rejected) == 1
        assert rejected[0].reason == exact_reason
        assert [
            event[1].detail
            for event in publisher.events
            if event[0] == "activity" and event[1].state == "failed"
        ] == [exact_reason]
        assert [request.prompt[0].text for request in child.prompts] == ["/compact"]
        assert (await broker.attach_state(handle)).phase == "failed"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_active_automatic_codex_compaction_prompt_exception_uses_exact_reason() -> None:
    async def exercise() -> None:
        strategy = CodexAcpTurnStrategy()
        broker, child, publisher, handle, _runtime = _fixture(
            observes_compaction=True,
            strategy=strategy,
            backend_key="codex",
        )
        tracked = await broker.deliver_tracked_normal(
            handle,
            "failed-automatic-compaction",
            _prompt("ordinary prompt while automatic compaction starts"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        await broker.observe_session_notification(
            handle,
            SessionNotification(
                session_id="session-a",
                update=ToolCallStart(
                    session_update="tool_call",
                    tool_call_id="automatic-compaction",
                    kind="other",
                    title="Context compacting",
                    status="in_progress",
                    field_meta={"contextCompaction": True},
                ),
            ),
        )

        child.responses.put_nowait(ValueError("automatic compaction rejected"))

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        exact_reason = "ValueError: automatic compaction rejected"
        assert result.status == "errored"
        assert result.error == exact_reason
        compactions = [event[1] for event in publisher.events if event[0] == "compaction"]
        assert [item.state for item in compactions] == ["compacting", "failed"]
        assert [item.trigger for item in compactions] == ["automatic", "automatic"]
        assert compactions[-1].reason == exact_reason
        assert [
            event[1].detail
            for event in publisher.events
            if event[0] == "activity" and event[1].state == "failed"
        ] == [exact_reason]
        assert (await broker.attach_state(handle)).phase == "failed"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


class _PromptFailureReasonStrategy(_Strategy):
    def __init__(self, result: Any = None, *, raises: bool = False) -> None:
        self.result = result
        self.raises = raises
        self.calls: list[tuple[Any, Any, BaseException]] = []

    def prompt_failure_reason(
        self, binding: Any, prompt: Any, error: BaseException
    ) -> Any:
        self.calls.append((binding, prompt, error))
        if self.raises:
            raise RuntimeError("failure-reason hook failed")
        return self.result


@pytest.mark.parametrize(
    ("hook_result", "hook_raises"),
    (
        (None, False),
        ("", False),
        ("   ", False),
        (" not trimmed ", False),
        (123, False),
        (None, True),
    ),
)
def test_invalid_or_raising_prompt_failure_reason_hook_uses_backend_exception(
    hook_result: Any, hook_raises: bool
) -> None:
    async def exercise() -> None:
        strategy = _PromptFailureReasonStrategy(hook_result, raises=hook_raises)
        broker, child, publisher, handle, _runtime = _fixture(
            strategy=strategy,
            observes_compaction=True,
        )
        prompt = _prompt("/compact")
        tracked = await broker.deliver_tracked_normal(
            handle,
            "invalid-provider-reason",
            prompt,
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        error = RuntimeError("private transport detail")

        child.responses.put_nowait(error)

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.status == "errored"
        assert result.error == "private transport detail"
        assert result.failure_provenance == "backend"
        assert len(strategy.calls) == 1
        binding, seen_prompt, seen_error = strategy.calls[0]
        assert binding is handle.binding
        assert seen_prompt is prompt
        assert seen_error is error
        assert [
            event[1].detail
            for event in publisher.events
            if event[0] == "activity" and event[1].state == "failed"
        ] == ["private transport detail"]
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_prompt_exception_without_hook_preserves_backend_exception() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, _runtime = _fixture(
            strategy=_Strategy(),
            observes_compaction=True,
        )
        tracked = await broker.deliver_tracked_normal(
            handle,
            "compaction-without-hook",
            _prompt("/compact"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )

        child.responses.put_nowait(RuntimeError("private compaction error"))

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.status == "errored"
        assert result.error == "private compaction error"
        assert result.failure_provenance == "backend"
        compactions = [event[1] for event in publisher.events if event[0] == "compaction"]
        assert [item.state for item in compactions] == ["compacting", "failed"]
        assert compactions[-1].reason == "private compaction error"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_valid_prompt_failure_reason_hook_is_not_consulted_without_boundary() -> None:
    async def exercise() -> None:
        strategy = _PromptFailureReasonStrategy("Private exact detail")
        broker, child, publisher, handle, _runtime = _fixture(strategy=strategy)
        tracked = await broker.deliver_tracked_normal(
            handle,
            "ordinary-provider-failure",
            _prompt("ordinary prompt"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )

        child.responses.put_nowait(RuntimeError("private ordinary error"))

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.status == "errored"
        assert result.error == "private ordinary error"
        assert result.failure_provenance == "backend"
        assert strategy.calls == []
        assert not [event for event in publisher.events if event[0] == "compaction"]
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_ordinary_codex_prompt_exception_preserves_backend_exception() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, _runtime = _fixture(
            strategy=CodexAcpTurnStrategy(),
            backend_key="codex",
        )
        tracked = await broker.deliver_tracked_normal(
            handle,
            "ordinary-codex-failure",
            _prompt("ordinary prompt"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )

        child.responses.put_nowait(RuntimeError("private ordinary error"))

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.status == "errored"
        assert result.error == "private ordinary error"
        assert result.failure_provenance == "backend"
        assert not [event for event in publisher.events if event[0] == "compaction"]
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_fifo_publishes_exact_positions_and_delivers_each_prompt_once() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, _runtime = _fixture()
        await broker.deliver(handle, "m1", "normal", _prompt("one"))
        await broker.deliver(handle, "m2", "queue", _prompt("two"))
        await broker.deliver(handle, "m3", "queue", _prompt("three"))
        assert [event[1].state for event in publisher.events if event[0] == "receipt"] == [
            "accepted",
            "started",
            "queued",
            "queued",
        ]
        assert [event[1].queue_position for event in publisher.events if event[0] == "receipt"] == [
            None,
            None,
            1,
            2,
        ]
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(lambda: len(child.prompts) == 2)
        assert [request.prompt[0].text for request in child.prompts] == ["one", "two"]
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(lambda: len(child.prompts) == 3)
        assert [request.prompt[0].text for request in child.prompts] == ["one", "two", "three"]
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_send_now_interrupts_active_before_starting_submission_and_preserves_fifo() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, _runtime = _fixture()
        await broker.deliver(handle, "old", "normal", _prompt("old"))
        await broker.deliver(handle, "queued", "queue", _prompt("queued"))
        await broker.deliver(handle, "now", "send_now", _prompt("now"))
        assert len(child.cancelled) == 1
        child.responses.put_nowait(PromptResponse(stop_reason="cancelled"))
        await _wait_until(lambda: len(child.prompts) == 2)
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert [(item.client_message_id, item.state) for item in receipts][-2:] == [
            ("old", "interrupted"),
            ("now", "started"),
        ]
        assert [request.prompt[0].text for request in child.prompts] == ["old", "now"]
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(lambda: len(child.prompts) == 3)
        assert [request.prompt[0].text for request in child.prompts] == ["old", "now", "queued"]
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_duplicate_and_wrong_session_are_rejected_without_delivery() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, _runtime = _fixture()
        wrong = _prompt().model_copy(update={"session_id": "other"})
        await broker.deliver(handle, "wrong", "normal", wrong)
        await broker.deliver(handle, "wrong", "normal", _prompt())
        assert not child.prompts
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert [item.state for item in receipts] == ["rejected", "rejected"]
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_idle_and_active_choice_rejections_never_fall_back() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, _runtime = _fixture(supports_steer=False)
        await broker.deliver(handle, "idle-queue", "queue", _prompt())
        await broker.deliver(handle, "idle-steer", "steer", _prompt())
        await broker.deliver_tracked_normal(
            handle,
            "active",
            _prompt("active"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        await broker.deliver(handle, "second-normal", "normal", _prompt())
        await broker.deliver(handle, "unsupported-steer", "steer", _prompt())
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert [(item.client_message_id, item.state) for item in receipts] == [
            ("idle-queue", "rejected"),
            ("idle-steer", "rejected"),
            ("active", "accepted"),
            ("active", "started"),
            ("second-normal", "rejected"),
            ("unsupported-steer", "rejected"),
        ]
        assert [request.prompt[0].text for request in child.prompts] == ["active"]
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(
            lambda: any(
                event[0] == "activity" and event[1].state == "idle" for event in publisher.events
            )
        )
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_queued_cancel_and_user_cancel_advance_only_remaining_head() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, runtime = _fixture()
        await broker.deliver_tracked_normal(
            handle,
            "active",
            _prompt("active"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        await broker.deliver(handle, "q1", "queue", _prompt("one"))
        await broker.deliver(handle, "q2", "queue", _prompt("two"))
        await broker.cancel(handle, "q1")
        await broker.cancel(handle, "missing")
        snapshots = [event[1] for event in publisher.events if event[0] == "queue"]
        assert [item.client_message_id for item in snapshots[-1]] == ["q2"]
        await broker.cancel(handle)
        child.responses.put_nowait(PromptResponse(stop_reason="cancelled"))
        await _wait_until(lambda: len(child.prompts) == 2)
        transition_port = runtime.requested_cancel_transition_port
        assert len(transition_port.begun) == 1
        assert transition_port.resumed == transition_port.begun
        assert runtime.replacements == []
        assert (
            handle.definition.turn_capabilities.requires_fresh_child_after_requested_cancel
            is False
        )
        assert [request.prompt[0].text for request in child.prompts] == ["active", "two"]
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(
            lambda: any(
                event[0] == "activity" and event[1].state == "idle" for event in publisher.events
            )
        )
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_send_now_timeout_retires_generation_and_rejects_all_intent() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, runtime = _fixture(cancel_timeout_seconds=0.01)
        child.cancel_gate = asyncio.Event()
        tracked = await broker.deliver_tracked_normal(
            handle,
            "active",
            _prompt("active"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        await broker.deliver(handle, "queued", "queue", _prompt("queued"))
        await broker.deliver(handle, "now", "send_now", _prompt("now"))
        await _wait_until(lambda: runtime.retired == 1)
        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.failure_provenance == "conversation"
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert [(item.client_message_id, item.state) for item in receipts][-3:] == [
            ("active", "interrupted"),
            ("now", "rejected"),
            ("queued", "rejected"),
        ]
        assert [request.prompt[0].text for request in child.prompts] == ["active"]
        child.responses.put_nowait(PromptResponse(stop_reason="cancelled"))
        await asyncio.sleep(0)
        assert [request.prompt[0].text for request in child.prompts] == ["active"]
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_requested_cancel_timeout_reuses_original_deadline_and_invalidates_runtime(
) -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, runtime = _fixture(
            cancel_timeout_seconds=0.01,
            requested_cancel_recovery_timeout_seconds=0.04,
        )
        child.cancel_gate = asyncio.Event()
        runtime.retirement_gate = asyncio.Event()
        await broker.deliver(handle, "active", "normal", _prompt("active"))
        await broker.deliver(handle, "fifo", "queue", _prompt("fifo"))
        await broker.deliver(handle, "successor", "send_now", _prompt("successor"))
        token = runtime.requested_cancel_transition_port.begun[0]
        assert token.deadline < asyncio.get_running_loop().time() + 0.05

        async def wait_for_failure() -> None:
            await _wait_until(
                lambda: runtime.failed_handles == [handle]
            )

        failure_waiter = asyncio.create_task(wait_for_failure())
        done, _pending = await asyncio.wait({failure_waiter}, timeout=0.15)
        if failure_waiter not in done:
            runtime.retirement_gate.set()
            with contextlib.suppress(BaseException):
                await failure_waiter
            pytest.fail(
                "requested cancellation timeout exceeded its actor deadline: "
                f"now={asyncio.get_running_loop().time()!r}, "
                f"token_deadline={token.deadline!r}, "
                f"retirement_deadlines={runtime.retirement_deadlines!r}, "
                f"failed_handles={runtime.failed_handles!r}, events={publisher.events!r}"
            )
        failure_waiter.result()

        assert asyncio.get_running_loop().time() < token.deadline + 0.04
        assert runtime.retirement_deadlines == [token.deadline]
        assert runtime.failed_handles == [handle]
        assert child.alive is False
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert sum(
            item.client_message_id == "active" and item.state == "interrupted"
            for item in receipts
        ) == 1
        assert sum(
            item.client_message_id == "successor" and item.state == "rejected"
            for item in receipts
        ) == 1
        assert sum(
            item.client_message_id == "fifo" and item.state == "rejected"
            for item in receipts
        ) == 1
        assert (await broker.attach_state(handle)).phase == "failed"
        runtime.retirement_gate.set()
        await asyncio.wait_for(runtime.retirement_finished.wait(), timeout=1)
        await _wait_until(
            lambda: any(
                event[0] == "activity" and event[1].state == "failed"
                for event in publisher.events
            )
        )
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_child_death_rejects_fifo_once_and_stale_death_is_ignored() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, _runtime = _fixture()
        await broker.deliver(handle, "active", "normal", _prompt("active"))
        await broker.deliver(handle, "q1", "queue", _prompt("one"))
        await broker.deliver(handle, "q2", "queue", _prompt("two"))
        await broker.child_died("employee-a", 1, 99, RuntimeError("stale"))
        await broker.child_died("employee-a", 1, 1, RuntimeError("died"))
        await broker.child_died("employee-a", 1, 1, RuntimeError("duplicate"))
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        pairs = [(item.client_message_id, item.state) for item in receipts]
        assert pairs.count(("active", "interrupted")) == 1
        assert pairs[-3:] == [
            ("active", "interrupted"),
            ("q1", "rejected"),
            ("q2", "rejected"),
        ]
        assert [event[1] for event in publisher.events if event[0] == "queue"][-1] == ()
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await asyncio.sleep(0)
        assert len(child.prompts) == 1
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_child_process_death_preserves_backend_reason_for_tracked_turn() -> None:
    async def exercise() -> None:
        broker, _child, _publisher, handle, _runtime = _fixture()
        tracked = await broker.deliver_tracked_normal(
            handle,
            "active",
            _prompt("active"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        error = AcpChildProcessExited(17, "fatal startup detail")

        await broker.child_died("employee-a", 1, 1, error)

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.status == "errored"
        assert result.error == str(error)
        assert result.failure_provenance == "backend"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


@pytest.mark.parametrize("error", [None, RuntimeError("")])
def test_child_death_without_concrete_reason_uses_backend_fallback(
    error: BaseException | None,
) -> None:
    async def exercise() -> None:
        broker, _child, _publisher, handle, _runtime = _fixture()
        tracked = await broker.deliver_tracked_normal(
            handle,
            "active",
            _prompt("active"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )

        await broker.child_died("employee-a", 1, 1, error)

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.status == "errored"
        assert result.error == "Employee connection failed"
        assert result.failure_provenance == "backend"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_new_conversation_freezes_no_successor_before_racing_response() -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, _runtime = _fixture()
        await broker.deliver(handle, "active", "normal", _prompt("active"))
        await broker.deliver(handle, "queued", "queue", _prompt("queued"))
        closing = asyncio.create_task(
            broker.prepare_new_conversation(handle, asyncio.get_running_loop().time() + 1)
        )
        await _wait_until(lambda: len(child.cancelled) == 1)
        child.responses.put_nowait(PromptResponse(stop_reason="cancelled"))
        await closing
        assert [request.prompt[0].text for request in child.prompts] == ["active"]
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert ("queued", "rejected") in [(item.client_message_id, item.state) for item in receipts]

    asyncio.run(exercise())


@pytest.mark.parametrize("close_mode", ["new_conversation", "shutdown"])
def test_close_prompt_exception_retires_exact_lease_without_requested_cancel_recovery(
    close_mode: str,
) -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, runtime = _fixture()
        tracked = await broker.deliver_tracked_normal(
            handle,
            "closing",
            _prompt("closing"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        deadline = asyncio.get_running_loop().time() + 1
        if close_mode == "new_conversation":
            closing = asyncio.create_task(
                broker.prepare_new_conversation(handle, deadline)
            )
        else:
            closing = asyncio.create_task(broker.shutdown(deadline))
        await _wait_until(lambda: len(child.cancelled) == 1)

        child.responses.put_nowait(RuntimeError("prompt unwound while closing"))
        await asyncio.wait_for(closing, timeout=1)
        result = await asyncio.wait_for(tracked.completion, timeout=1)

        assert result.status == "interrupted"
        assert runtime.retired == 1
        assert runtime.replacements == []
        assert runtime.requested_cancel_transition_port.begun == []
        receipts = [event[1] for event in publisher.events if event[0] == "receipt"]
        assert sum(
            item.client_message_id == "closing" and item.state == "interrupted"
            for item in receipts
        ) == 1
        if close_mode == "new_conversation":
            await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


@pytest.mark.parametrize("close_mode", ["new_conversation", "shutdown"])
@pytest.mark.parametrize("retirement_failure", ["error", "hang"])
def test_close_retirement_failure_propagates_without_reusing_exceptional_child(
    close_mode: str,
    retirement_failure: str,
) -> None:
    async def exercise() -> None:
        broker, child, publisher, handle, runtime = _fixture()
        if retirement_failure == "error":
            runtime.retirement_error = RuntimeError("retirement failed")
        else:
            runtime.retirement_gate = asyncio.Event()
        tracked = await broker.deliver_tracked_normal(
            handle,
            "closing-failure",
            _prompt("closing failure"),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.2
        if close_mode == "new_conversation":
            closing = asyncio.create_task(
                broker.prepare_new_conversation(handle, deadline)
            )
        else:
            closing = asyncio.create_task(broker.shutdown(deadline))
        await _wait_until(lambda: len(child.cancelled) == 1)
        child.responses.put_nowait(RuntimeError("prompt unwound while closing"))

        with pytest.raises(
            (ConversationTurnBrokerError, ConversationTurnBrokerShutdownError, TimeoutError)
        ):
            await asyncio.wait_for(closing, timeout=0.5)
        assert loop.time() < deadline + 0.05
        await _wait_until(lambda: runtime.failed_handles == [handle])
        result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert result.status == "errored"
        assert runtime.requested_cancel_transition_port.begun == []
        assert runtime.replacements == []
        assert runtime.retirement_deadlines == [deadline]
        assert runtime.failed_handles == [handle]
        assert child.alive is False
        assert not any(
            event[0] == "activity" and event[1].state == "idle"
            for event in publisher.events
        )
        with pytest.raises(ConversationRuntimeUnavailable):
            await runtime.resolve_runtime_handle("employee-a", 1)
        if runtime.retirement_gate is not None:
            runtime.retirement_gate.set()
        await asyncio.wait_for(runtime.retirement_finished.wait(), timeout=1)
        if close_mode == "new_conversation":
            with contextlib.suppress(BaseException):
                await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_accepted_generation_n_delivery_never_redirects_to_concurrent_n_plus_one() -> None:
    class HoldingPublisher(_Publisher):
        def __init__(self) -> None:
            super().__init__()
            self.accepted = asyncio.Event()
            self.release = asyncio.Event()

        async def publish_delivery_receipt(
            self, employee: Any, binding: Any, receipt: Any
        ) -> None:
            if receipt.client_message_id == "generation-n" and receipt.state == "accepted":
                self.accepted.set()
                await self.release.wait()
            await super().publish_delivery_receipt(employee, binding, receipt)

    class ExactRuntime(_Runtime):
        def __init__(
            self,
            handle: ConversationRuntimeHandle,
            handles: tuple[ConversationRuntimeHandle, ...],
        ) -> None:
            super().__init__(handle)
            self.handles = handles

        async def acquire_runtime_lease(self, handle: ConversationRuntimeHandle):
            from planner.conversation.runtime_ports import ConversationRuntimeLease

            if not any(handle is candidate for candidate in self.handles):
                raise RuntimeError("stale")
            return ConversationRuntimeLease(handle)

        def strategy_for_lease(self, lease: Any):
            return lease.handle.definition.turn_strategy

    async def exercise() -> None:
        _broker, child_n, _publisher, handle_n, _runtime = _fixture()
        child_n_plus_one = _Child()
        handle_n_plus_one = ConversationRuntimeHandle(
            employee=handle_n.employee,
            binding=handle_n.binding,
            child_generation=2,
            child=child_n_plus_one,
            definition=handle_n.definition,
            record_identity=object(),
        )
        runtime = ExactRuntime(handle_n, (handle_n, handle_n_plus_one))
        publisher = HoldingPublisher()
        broker = ConversationTurnBroker(runtime, publisher, integer_now=lambda: 100)
        first = asyncio.create_task(
            broker.deliver(handle_n, "generation-n", "normal", _prompt("first"))
        )
        await publisher.accepted.wait()
        second = asyncio.create_task(
            broker.deliver(
                handle_n_plus_one,
                "generation-n-plus-one",
                "normal",
                _prompt("second"),
            )
        )
        publisher.release.set()
        assert (await first).state == "started"
        with pytest.raises(ConversationTurnBrokerError, match="work is active"):
            await second
        assert [request.prompt[0].text for request in child_n.prompts] == ["first"]
        assert not child_n_plus_one.prompts
        child_n.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(
            lambda: any(
                event[0] == "activity" and event[1].state == "idle"
                for event in publisher.events
            )
        )
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


@pytest.mark.parametrize("close_mode", ["new_conversation", "shutdown"])
def test_late_generation_callbacks_after_actor_exit_settle_without_hanging(
    close_mode: str,
) -> None:
    async def exercise() -> None:
        broker, _child, _publisher, handle, _runtime = _fixture()
        await broker.bind_runtime(handle)
        if close_mode == "new_conversation":
            await broker.prepare_new_conversation(
                handle, asyncio.get_running_loop().time() + 1
            )
        else:
            await broker.shutdown(asyncio.get_running_loop().time() + 1)
        async with asyncio.timeout(0.1):
            await broker.child_died(
                handle.employee.employee_id,
                handle.binding.binding_generation,
                handle.child_generation,
                RuntimeError("late death"),
            )
            await broker.permission_publication_failed(
                handle.employee,
                handle.binding,
                handle.child_generation,
                handle.record_identity,
                1,
                RuntimeError("late permission failure"),
            )
            await broker.terminal_publication_failed(
                handle.employee,
                handle.binding,
                handle.child_generation,
                RuntimeError("late terminal failure"),
            )

    asyncio.run(exercise())


class _RegistryCaptureChild:
    def __init__(self, generation: int, ingress: Any, death: Any) -> None:
        self.generation = generation
        self.alive = True
        self._ingress = ingress
        self._death = death
        self.fail_capture = False
        self.fail_capture_session_ids: set[str] = set()
        self.prompts: list[PromptRequest] = []
        self.prompt_gate: asyncio.Event | None = None
        self.capture_phase_observer: Any = None

    @property
    def supports_session_fork(self) -> bool:
        return True

    async def initialize(self, request: Any) -> InitializeResponse:
        del request
        return InitializeResponse(
            protocol_version=1,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(name="fake", version="1"),
        )

    async def new_session(self, request: Any) -> NewSessionResponse:
        del request
        return NewSessionResponse(session_id="session-a")

    async def load_session(self, request: Any) -> LoadSessionResponse:
        del request
        return LoadSessionResponse()

    async def fork_session(
        self, request: ForkSessionRequest
    ) -> ForkSessionResponse:
        if self.capture_phase_observer is not None:
            self.capture_phase_observer("fork")
        return ForkSessionResponse(session_id=f"{request.session_id}-fork")

    async def capture_load_session(self, request: Any, private_ingress: Any):
        if self.capture_phase_observer is not None:
            self.capture_phase_observer("private fork load")
        if self.fail_capture or request.session_id in self.fail_capture_session_ids:
            raise RuntimeError("capture failed")
        await private_ingress(
            SessionNotification(
                session_id=request.session_id,
                update=AgentThoughtChunk(
                    session_update="agent_thought_chunk",
                    content=TextContentBlock(type="text", text="private"),
                ),
            )
        )
        return LoadSessionResponse()

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        self.prompts.append(request)
        if self.prompt_gate is not None:
            await self.prompt_gate.wait()
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, notification: CancelNotification) -> None:
        del notification

    async def close(self) -> None:
        if not self.alive:
            return
        self.alive = False
        await self._death(None)


class _RegistryCaptureFactory:
    def __init__(self) -> None:
        self.children: list[_RegistryCaptureChild] = []
        self.fail_next_capture = False
        self.capture_phase_observer: Any = None

    async def create(
        self,
        employee: Any,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _RegistryCaptureChild:
        del employee, permission_callback
        child = _RegistryCaptureChild(generation, update_ingress, death_callback)
        child.fail_capture = self.fail_next_capture
        child.capture_phase_observer = self.capture_phase_observer
        self.fail_next_capture = False
        self.children.append(child)
        return child


class _ProxyCaptureStrategy(_Strategy):
    def capture_compaction_from_updates(
        self, binding: ConversationSessionBinding, updates: tuple[Any, ...]
    ) -> ContextCompaction:
        assert binding.acp_session_id == "session-a-fork"
        assert len(updates) == 1
        return ContextCompaction(
            boundary_id="private-result",
            state="compacted",
            trigger="automatic",
        )


class _NormalizationFailureCaptureStrategy(_Strategy):
    def capture_compaction_from_updates(
        self, binding: ConversationSessionBinding, updates: tuple[Any, ...]
    ) -> ContextCompaction:
        del binding, updates
        raise RuntimeError("scripted normalization failure")


class _CompactionTransitionPort:
    def __init__(self) -> None:
        self.begun: list[CompactionTransitionToken] = []
        self.committed: list[tuple[Any, ...]] = []
        self.completed: list[tuple[CompactionTransitionToken, ConversationRuntimeHandle]] = []
        self.failed: list[tuple[CompactionTransitionToken, str]] = []
        self.aborted: list[CompactionTransitionToken] = []
        self.commit_started = asyncio.Event()
        self.commit_gate: asyncio.Event | None = None
        self.complete_observer: Any = None
        self.capture_phase_observer: Any = None

    async def begin_compaction_transition(
        self, handle: ConversationRuntimeHandle, deadline: float
    ) -> CompactionTransitionToken:
        token = CompactionTransitionToken(object(), handle, deadline)
        self.begun.append(token)
        return token

    async def commit_compaction_transition(
        self,
        token: CompactionTransitionToken,
        replacement_handle: ConversationRuntimeHandle,
        replay: tuple[Any, ...],
        queued_prompts: tuple[Any, ...],
    ) -> None:
        self.commit_started.set()
        if self.capture_phase_observer is not None:
            self.capture_phase_observer("browser commit")
        if self.commit_gate is not None:
            await self.commit_gate.wait()
        self.committed.append((token, replacement_handle, replay, queued_prompts))

    async def complete_compaction_transition(
        self,
        token: CompactionTransitionToken,
        replacement_handle: ConversationRuntimeHandle,
    ) -> None:
        if self.complete_observer is not None:
            self.complete_observer()
        self.completed.append((token, replacement_handle))

    async def fail_compaction_transition(
        self, token: CompactionTransitionToken, reason: str
    ) -> None:
        self.failed.append((token, reason))

    async def abort_compaction_transition(
        self, token: CompactionTransitionToken
    ) -> None:
        self.aborted.append(token)


def test_broker_capture_adopts_exact_replacement_handle_before_fifo_advances() -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        real_monotonic = loop.time
        fake_monotonic = [real_monotonic()]
        capture_phases: list[str] = []

        def advance_capture_phase(phase: str) -> None:
            capture_phases.append(phase)
            fake_monotonic[0] += 4

        async def wait_without_clock_timeout(predicate: Any) -> None:
            for _attempt in range(100):
                if predicate():
                    return
                await asyncio.sleep(0)
            raise AssertionError("capture did not settle within 100 event-loop turns")

        employee = ConversationEmployee(
            employee_id="employee-capture-adoption",
            entity_kind="ticket",
            entity_id="ticket-capture-adoption",
            workspace_roots=(Path("/tmp"),),
            backend_key="fake",
        )
        strategy = _ProxyCaptureStrategy()
        definition = AgentBackendDefinition(
            backend_key="fake",
            argv=("/fake",),
            inherited_environment_names=(),
            environment_overrides=(),
            expected_agent_name="fake",
            expected_agent_version="1",
            turn_capabilities=BackendTurnCapabilities(
                supports_steer=False, observes_compaction=True
            ),
            reverse_service_capabilities=ReverseServiceCapabilities(
                filesystem=False, terminal=False, permission=False
            ),
            working_directory_resolver=lambda value: value.workspace_roots[0],
            turn_strategy=strategy,
        )
        factory = _RegistryCaptureFactory()
        factory.capture_phase_observer = advance_capture_phase
        repository = InMemoryAcpBindingRepository()
        ordinary: list[Any] = []

        async def ingress(item: Any) -> None:
            ordinary.append(item)

        async def permission(request: Any) -> Any:
            raise AssertionError(request)

        async def compare_and_swap(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            if expected is not None:
                advance_capture_phase("durable CAS/resolve")
            return await repository.compare_and_swap(expected, candidate)

        async def compare_compaction(
            expected: ConversationSessionBinding,
            expected_boundaries: tuple[Any, ...],
            candidate: ConversationSessionBinding,
            candidate_boundaries: tuple[Any, ...],
        ) -> ConversationSessionBinding:
            advance_capture_phase("durable CAS/resolve")
            return await repository.compare_and_swap_compaction(
                expected, expected_boundaries, candidate, candidate_boundaries
            )

        backend_catalog, materialized_backends = _single_backend_runtime(
            definition, factory
        )
        registry = AcpEmployeeRegistry(
            backend_catalog=backend_catalog,
            materialized_backends=materialized_backends,
            resolve_binding=repository.resolve,
            compare_and_swap_binding=compare_and_swap,
            compare_and_swap_initial_binding=repository.compare_and_swap_initial,
            resolve_compaction_boundaries=repository.resolve_compaction_boundaries,
            compare_and_swap_compaction=compare_compaction,
            conversation_ingress=ingress,
            permission_callback=permission,
        )
        publisher = _Publisher()
        broker = ConversationTurnBroker(
            registry, publisher, integer_now=lambda: 100
        )
        transition_port = _CompactionTransitionPort()
        transition_port.capture_phase_observer = advance_capture_phase
        broker.set_compaction_transition_port(transition_port)
        record = await registry.get_or_spawn(employee)
        factory.children[0].prompt_gate = asyncio.Event()
        handle_n = await registry.resolve_runtime_handle(
            employee.employee_id, record.binding.binding_generation
        )
        tracked = await broker.deliver_tracked_normal(
            handle_n,
            "compact",
            PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="/compact")],
            ),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        completion_observations: list[tuple[bool, bool, bool, bool]] = []
        transition_port.complete_observer = lambda: completion_observations.append(
            (
                tracked.completion.done(),
                any(
                    event[0] == "compaction" and event[1].state == "compacted"
                    for event in publisher.events
                ),
                any(
                    event[0] == "activity" and event[1].state == "idle"
                    for event in publisher.events
                ),
                any(
                    event[0] == "receipt"
                    and event[1].client_message_id == "queued-after-compact"
                    and event[1].state == "started"
                    for event in publisher.events
                ),
            )
        )
        queued_receipt = await broker.deliver(
            handle_n,
            "queued-after-compact",
            "queue",
            PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="queued")],
            ),
        )
        assert queued_receipt.state == "queued"
        capture_started_at = fake_monotonic[0]
        loop.time = lambda: fake_monotonic[0]  # type: ignore[method-assign]
        try:
            factory.children[0].prompt_gate.set()
            await wait_without_clock_timeout(
                lambda: len(transition_port.committed) == 1
            )
            await wait_without_clock_timeout(
                lambda: len(transition_port.completed) == 1
            )
        finally:
            loop.time = real_monotonic  # type: ignore[method-assign]
        assert capture_phases == [
            "fork",
            "private fork load",
            "durable CAS/resolve",
            "browser commit",
        ]
        assert fake_monotonic[0] - capture_started_at == 16
        assert (
            fake_monotonic[0] - capture_started_at
            > ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS
        )
        assert (
            fake_monotonic[0] - capture_started_at
            < ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS
        )
        assert transition_port.begun[0].deadline == (
            capture_started_at + ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS
        )
        handle_n_plus_one = await registry.resolve_runtime_handle(
            employee.employee_id, record.binding.binding_generation + 1
        )
        assert handle_n_plus_one.child_generation == handle_n.child_generation + 1
        assert handle_n_plus_one.child is factory.children[1]
        assert handle_n_plus_one.child is not handle_n.child
        assert handle_n_plus_one.record_identity is not handle_n.record_identity
        assert set(broker._actors) == {(employee.employee_id, 2)}  # noqa: SLF001
        assert len(transition_port.begun) == len(transition_port.committed) == 1
        assert len(transition_port.completed) == 1
        assert completion_observations == [(True, False, True, True)]
        committed_token, committed_handle, replay, committed_queue = (
            transition_port.committed[0]
        )
        assert committed_token is transition_port.begun[0]
        assert committed_token.deadline == transition_port.begun[0].deadline
        assert committed_handle == handle_n_plus_one
        assert replay[0].session_id == handle_n_plus_one.binding.acp_session_id
        assert committed_queue[0].client_message_id == "queued-after-compact"
        assert committed_queue[0].prompt.session_id == (
            handle_n_plus_one.binding.acp_session_id
        )
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.acquire_runtime_lease(handle_n)
        await _wait_until(
            lambda: len(factory.children[1].prompts) == 1
            and broker._actors[(employee.employee_id, 2)].active is None  # noqa: SLF001
        )
        await broker.deliver(
            handle_n_plus_one,
            "after-capture",
            "normal",
            PromptRequest(
                session_id=handle_n_plus_one.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="after")],
            ),
        )
        await _wait_until(lambda: len(factory.children[1].prompts) == 2)
        assert [prompt.prompt[0].text for prompt in factory.children[0].prompts] == [
            "/compact"
        ]
        assert [prompt.prompt[0].text for prompt in factory.children[1].prompts] == [
            "queued",
            "after",
        ]
        assert factory.children[1].prompts[0].session_id == (
            handle_n_plus_one.binding.acp_session_id
        )
        assert not ordinary
        await broker.shutdown(asyncio.get_running_loop().time() + 1)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_hub_commit_timeout_surfaces_phase_budget_and_committed_binding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> None:
        employee = ConversationEmployee(
            employee_id="employee-hub-timeout",
            entity_kind="ticket",
            entity_id="ticket-hub-timeout",
            workspace_roots=(Path("/tmp"),),
            backend_key="fake",
        )
        strategy = _ProxyCaptureStrategy()
        definition = AgentBackendDefinition(
            backend_key="fake",
            argv=("/fake",),
            inherited_environment_names=(),
            environment_overrides=(),
            expected_agent_name="fake",
            expected_agent_version="1",
            turn_capabilities=BackendTurnCapabilities(
                supports_steer=False, observes_compaction=True
            ),
            reverse_service_capabilities=ReverseServiceCapabilities(
                filesystem=False, terminal=False, permission=False
            ),
            working_directory_resolver=lambda value: value.workspace_roots[0],
            turn_strategy=strategy,
        )
        factory = _RegistryCaptureFactory()
        repository = InMemoryAcpBindingRepository()

        async def ingress(item: Any) -> None:
            del item

        async def permission(request: Any) -> Any:
            raise AssertionError(request)

        backend_catalog, materialized_backends = _single_backend_runtime(
            definition, factory
        )
        registry = AcpEmployeeRegistry(
            backend_catalog=backend_catalog,
            materialized_backends=materialized_backends,
            resolve_binding=repository.resolve,
            compare_and_swap_binding=repository.compare_and_swap,
            compare_and_swap_initial_binding=repository.compare_and_swap_initial,
            resolve_compaction_boundaries=repository.resolve_compaction_boundaries,
            compare_and_swap_compaction=repository.compare_and_swap_compaction,
            conversation_ingress=ingress,
            permission_callback=permission,
        )
        publisher = _Publisher()
        broker = ConversationTurnBroker(
            registry,
            publisher,
            integer_now=lambda: 100,
            capture_timeout_seconds=0.02,
        )
        transition_port = _CompactionTransitionPort()
        transition_port.commit_gate = asyncio.Event()
        broker.set_compaction_transition_port(transition_port)
        record = await registry.get_or_spawn(employee)
        handle_n = await registry.resolve_runtime_handle(
            employee.employee_id, record.binding.binding_generation
        )

        tracked = await broker.deliver_tracked_normal(
            handle_n,
            "compact-timeout",
            PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="/compact")],
            ),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        await asyncio.wait_for(transition_port.commit_started.wait(), timeout=1)
        await _wait_until(
            lambda: broker._actors[(employee.employee_id, 2)].lifecycle == "failed"  # noqa: SLF001
        )

        durable = await repository.resolve(employee.employee_id)
        assert durable is not None
        assert durable.binding_generation == 2
        expected_reason = (
            "Conversation compaction capture exceeded its configured 0.02-second budget "
            "during browser commit; durable binding committed generation 2; the uncertain "
            "runtime child was invalidated; fresh attach will resolve and load the "
            "authoritative binding"
        )
        tracked_result = await asyncio.wait_for(tracked.completion, timeout=1)
        assert tracked_result.error == expected_reason
        assert any(
            event[0] == "compaction"
            and event[1].state == "failed"
            and event[1].reason == expected_reason
            for event in publisher.events
        )
        assert expected_reason in caplog.text
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle(employee.employee_id, 2)
        assert broker._actors[(employee.employee_id, 2)].lifecycle == "failed"  # noqa: SLF001

        await broker.shutdown(asyncio.get_running_loop().time() + 1)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_durable_cas_failure_invalidates_source_actor_and_queued_intent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> None:
        employee = ConversationEmployee(
            employee_id="employee-fatal-restore",
            entity_kind="ticket",
            entity_id="ticket-fatal-restore",
            workspace_roots=(Path("/tmp"),),
            backend_key="fake",
        )
        definition = AgentBackendDefinition(
            backend_key="fake",
            argv=("/fake",),
            inherited_environment_names=(),
            environment_overrides=(),
            expected_agent_name="fake",
            expected_agent_version="1",
            turn_capabilities=BackendTurnCapabilities(
                supports_steer=False, observes_compaction=True
            ),
            reverse_service_capabilities=ReverseServiceCapabilities(
                filesystem=False, terminal=False, permission=False
            ),
            working_directory_resolver=lambda value: value.workspace_roots[0],
            turn_strategy=_ProxyCaptureStrategy(),
        )
        factory = _RegistryCaptureFactory()
        repository = InMemoryAcpBindingRepository()

        async def compare_and_swap(
            expected: ConversationSessionBinding | None,
            candidate: ConversationSessionBinding,
        ) -> ConversationSessionBinding:
            if expected is None:
                return await repository.compare_and_swap(expected, candidate)
            raise RuntimeError("scripted compaction CAS failure")

        async def compare_compaction(*_args: Any) -> ConversationSessionBinding:
            raise RuntimeError("scripted compaction CAS failure")

        async def ingress(item: Any) -> None:
            del item

        async def permission(request: Any) -> Any:
            raise AssertionError(request)

        backend_catalog, materialized_backends = _single_backend_runtime(
            definition, factory
        )
        registry = AcpEmployeeRegistry(
            backend_catalog=backend_catalog,
            materialized_backends=materialized_backends,
            resolve_binding=repository.resolve,
            compare_and_swap_binding=compare_and_swap,
            compare_and_swap_initial_binding=repository.compare_and_swap_initial,
            resolve_compaction_boundaries=repository.resolve_compaction_boundaries,
            compare_and_swap_compaction=compare_compaction,
            conversation_ingress=ingress,
            permission_callback=permission,
        )
        publisher = _Publisher()
        broker = ConversationTurnBroker(
            registry, publisher, integer_now=lambda: 100
        )
        transition_port = _CompactionTransitionPort()
        broker.set_compaction_transition_port(transition_port)
        record = await registry.get_or_spawn(employee)
        child = factory.children[0]
        child.prompt_gate = asyncio.Event()
        handle = await registry.resolve_runtime_handle(
            employee.employee_id, record.binding.binding_generation
        )
        tracked = await broker.deliver_tracked_normal(
            handle,
            "fatal-compact",
            PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="/compact")],
            ),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        queued = await broker.deliver(
            handle,
            "must-not-run",
            "queue",
            PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="must not run")],
            ),
        )
        assert queued.state == "queued"
        child.prompt_gate.set()

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        await _wait_until(lambda: not child.alive)
        expected_reason = (
            "Conversation compaction capture failed during durable CAS/resolve: "
            "RuntimeError: scripted compaction CAS failure"
        )
        assert result.status == "errored"
        assert result.error == expected_reason
        assert transition_port.failed[-1][1] == expected_reason
        assert expected_reason in caplog.text
        assert "exceeded its configured" not in expected_reason
        assert transition_port.failed and not transition_port.aborted
        assert [prompt.prompt[0].text for prompt in child.prompts] == ["/compact"]
        assert any(
            event[0] == "receipt"
            and event[1].client_message_id == "must-not-run"
            and event[1].state == "rejected"
            for event in publisher.events
        )
        activity_states = [
            event[1].state for event in publisher.events if event[0] == "activity"
        ]
        assert activity_states[-1] == "failed"
        assert "idle" not in activity_states
        assert broker._actors[(employee.employee_id, 1)].lifecycle == "failed"  # noqa: SLF001
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle(employee.employee_id, 1)

        await broker.shutdown(asyncio.get_running_loop().time() + 1)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_fatal_abort_after_normalization_failure_fails_actor_and_queued_intent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> None:
        employee = ConversationEmployee(
            employee_id="employee-fatal-abort",
            entity_kind="ticket",
            entity_id="ticket-fatal-abort",
            workspace_roots=(Path("/tmp"),),
            backend_key="fake",
        )
        definition = AgentBackendDefinition(
            backend_key="fake",
            argv=("/fake",),
            inherited_environment_names=(),
            environment_overrides=(),
            expected_agent_name="fake",
            expected_agent_version="1",
            turn_capabilities=BackendTurnCapabilities(
                supports_steer=False, observes_compaction=True
            ),
            reverse_service_capabilities=ReverseServiceCapabilities(
                filesystem=False, terminal=False, permission=False
            ),
            working_directory_resolver=lambda value: value.workspace_roots[0],
            turn_strategy=_NormalizationFailureCaptureStrategy(),
        )
        factory = _RegistryCaptureFactory()
        repository = InMemoryAcpBindingRepository()

        async def ingress(item: Any) -> None:
            del item

        async def permission(request: Any) -> Any:
            raise AssertionError(request)

        backend_catalog, materialized_backends = _single_backend_runtime(
            definition, factory
        )
        registry = AcpEmployeeRegistry(
            backend_catalog=backend_catalog,
            materialized_backends=materialized_backends,
            resolve_binding=repository.resolve,
            compare_and_swap_binding=repository.compare_and_swap,
            compare_and_swap_initial_binding=repository.compare_and_swap_initial,
            resolve_compaction_boundaries=repository.resolve_compaction_boundaries,
            compare_and_swap_compaction=repository.compare_and_swap_compaction,
            conversation_ingress=ingress,
            permission_callback=permission,
        )
        publisher = _Publisher()
        broker = ConversationTurnBroker(
            registry, publisher, integer_now=lambda: 100
        )
        transition_port = _CompactionTransitionPort()
        broker.set_compaction_transition_port(transition_port)
        record = await registry.get_or_spawn(employee)
        child = factory.children[0]
        child.prompt_gate = asyncio.Event()
        handle = await registry.resolve_runtime_handle(
            employee.employee_id, record.binding.binding_generation
        )
        tracked = await broker.deliver_tracked_normal(
            handle,
            "fatal-abort-compact",
            PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="/compact")],
            ),
            before_prompt_started=lambda _handle: None,
            after_prompt_settled=lambda _handle: None,
        )
        queued = await broker.deliver(
            handle,
            "must-not-run-after-fatal-abort",
            "queue",
            PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="must not run")],
            ),
        )
        assert queued.state == "queued"
        child.prompt_gate.set()

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        await _wait_until(
            lambda: broker._actors[(employee.employee_id, 1)].lifecycle == "failed"  # noqa: SLF001
        )
        expected_reason = (
            "Conversation compaction capture failed during normalization: RuntimeError: "
            "scripted normalization failure"
        )
        assert result.status == "errored"
        assert result.error == expected_reason
        assert transition_port.failed[-1][1] == expected_reason
        assert expected_reason in caplog.text
        assert "exceeded its configured" not in expected_reason
        assert transition_port.failed and not transition_port.aborted
        assert [prompt.prompt[0].text for prompt in child.prompts] == ["/compact"]
        assert any(
            event[0] == "receipt"
            and event[1].client_message_id == "must-not-run-after-fatal-abort"
            and event[1].state == "rejected"
            for event in publisher.events
        )
        activity_states = [
            event[1].state for event in publisher.events if event[0] == "activity"
        ]
        assert activity_states[-1] == "failed"
        assert "idle" not in activity_states
        assert not child.alive
        durable = await repository.resolve(employee.employee_id)
        assert durable == record.binding
        with pytest.raises(ConversationRuntimeUnavailable):
            await registry.resolve_runtime_handle(employee.employee_id, 1)

        await broker.shutdown(asyncio.get_running_loop().time() + 1)
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


class _CompactionStrategy(_Strategy):
    def __init__(self) -> None:
        self.capture_started = asyncio.Event()
        self.capture_gate = asyncio.Event()

    def observe_compaction(self, binding: Any, notification: SessionNotification):
        from planner.conversation.contracts import ContextCompaction

        del binding
        identity = notification.update.title
        if identity is None:
            return None
        return ContextCompaction(
            boundary_id=identity,
            state="compacting",
            trigger="automatic",
        )

    async def capture_compaction(self, binding: Any):
        from planner.conversation.contracts import ContextCompaction

        del binding
        self.capture_started.set()
        await self.capture_gate.wait()
        return ContextCompaction(
            boundary_id="strategy-result",
            state="compacted",
            trigger="automatic",
        )


class _FailingCompactionStrategy(_Strategy):
    async def capture_compaction(self, binding: Any):
        del binding
        raise RuntimeError("capture failed")


def test_tracked_capture_failure_tears_down_before_queued_successor_starts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> None:
        order: list[str] = []
        broker, child, publisher, handle, _runtime = _fixture(
            strategy=_FailingCompactionStrategy(), observes_compaction=True
        )
        transition_port = _CompactionTransitionPort()
        broker.set_compaction_transition_port(transition_port)
        child.prompt_observer = lambda request: (
            order.append("successor-started")
            if request.prompt[0].text == "after capture"
            else None
        )
        tracked = await broker.deliver_tracked_normal(
            handle,
            "worker-compact",
            _prompt("/compact"),
            before_prompt_started=lambda _handle: order.append("collector-installed"),
            after_prompt_settled=lambda _handle: order.append("collector-removed"),
        )
        completion_observations: list[tuple[bool, bool, bool, bool]] = []
        transition_port.complete_observer = lambda: completion_observations.append(
            (
                tracked.completion.done(),
                bool(transition_port.aborted),
                any(
                    event[0] == "activity" and event[1].state == "idle"
                    for event in publisher.events
                ),
                any(
                    event[0] == "receipt"
                    and event[1].client_message_id == "queued-after"
                    and event[1].state == "started"
                    for event in publisher.events
                ),
            )
        )
        await broker.deliver(handle, "queued-after", "queue", _prompt("after capture"))
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))

        result = await asyncio.wait_for(tracked.completion, timeout=1)
        expected_reason = (
            "Conversation compaction capture failed during normalization: "
            "RuntimeError: capture failed"
        )
        assert (result.status, result.error) == ("errored", expected_reason)
        assert any(
            event[0] == "compaction"
            and event[1].state == "failed"
            and event[1].reason == expected_reason
            for event in publisher.events
        )
        assert expected_reason in caplog.text
        assert "exceeded its configured" not in expected_reason
        await _wait_until(lambda: len(child.prompts) == 2)
        assert order == [
            "collector-installed",
            "collector-removed",
            "successor-started",
        ]
        assert completion_observations == [(True, True, True, True)]
        assert transition_port.completed == [(transition_port.begun[0], handle)]
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_explicit_and_distinct_automatic_compactions_share_one_private_capture() -> None:
    async def exercise() -> None:
        strategy = _CompactionStrategy()
        broker, child, publisher, handle, _runtime = _fixture(
            strategy=strategy, observes_compaction=True
        )
        await broker.deliver(handle, "compact", "normal", _prompt(" /compact "))
        compacting_index = next(
            index for index, event in enumerate(publisher.events) if event[0] == "compaction"
        )
        started_index = next(
            index
            for index, event in enumerate(publisher.events)
            if event[0] == "receipt" and event[1].state == "started"
        )
        assert compacting_index < started_index
        await broker.deliver(handle, "queued", "queue", _prompt("after"))
        for title in ("transition-a", "transition-a", "transition-b", "transition-b"):
            await broker.observe_session_notification(
                handle,
                SessionNotification(
                    session_id="session-a",
                    update=SessionInfoUpdate(session_update="session_info_update", title=title),
                ),
            )
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await strategy.capture_started.wait()
        assert [request.prompt[0].text for request in child.prompts] == [" /compact "]
        strategy.capture_gate.set()
        await _wait_until(lambda: len(child.prompts) == 2)
        compactions = [event[1] for event in publisher.events if event[0] == "compaction"]
        assert [item.state for item in compactions] == [
            "compacting",
            "compacting",
            "compacted",
            "compacted",
        ]
        assert len({item.boundary_id for item in compactions}) == 2
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await _wait_until(
            lambda: any(
                event[0] == "activity" and event[1].state == "idle" for event in publisher.events
            )
        )
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


class _RecordingTerminalOwner:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def cleanup_child_generation(
        self,
        employee_id: str,
        session_id: str,
        child_generation: int,
        deadline: float,
    ) -> tuple[str, ...]:
        assert deadline >= asyncio.get_running_loop().time()
        self.calls.append(
            ("cleanup", employee_id, session_id, child_generation)
        )
        return ()

    async def prepare_new_conversation(
        self, binding: ConversationSessionBinding, deadline: float
    ) -> tuple[str, ...]:
        assert deadline >= asyncio.get_running_loop().time()
        self.calls.append(("prepare", binding.acp_session_id))
        return ()

    async def shutdown(self, deadline: float) -> tuple[str, ...]:
        assert deadline >= asyncio.get_running_loop().time()
        self.calls.append(("shutdown",))
        return ()


@pytest.mark.parametrize(
    "cause", ["cancel_timeout", "child_death", "new_conversation", "shutdown"]
)
def test_turn_actor_owns_permission_tombstone_and_terminal_cleanup_for_terminal_causes(
    cause: str,
) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        permission_owner = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: f"permission-{cause}",
            integer_now=lambda: 100,
            timeout_seconds=300,
        )
        terminal_owner = _RecordingTerminalOwner()
        broker, child, _publisher, handle, _runtime = _fixture(
            cancel_timeout_seconds=0.01,
            permission_broker=permission_owner,
            terminal_service=terminal_owner,  # type: ignore[arg-type]
            reverse_permission=True,
            reverse_terminal=True,
            publisher=publisher,
        )
        await permission_owner.attach_browser(handle.employee, "browser-a")
        await broker.deliver(handle, "active", "normal", _prompt("active"))
        pending = asyncio.create_task(
            broker.request_permission(handle, _permission_request())
        )
        await _wait_until(lambda: len(publisher.permission_requests) == 1)

        deadline = asyncio.get_running_loop().time() + 1
        if cause == "cancel_timeout":
            child.cancel_gate = asyncio.Event()
            await broker.cancel(handle)
            await _wait_until(
                lambda: any(call[0] == "cleanup" for call in terminal_owner.calls)
            )
        elif cause == "child_death":
            await broker.child_died("employee-a", 1, 1, RuntimeError("died"))
        elif cause == "new_conversation":
            await broker.prepare_new_conversation(handle, deadline)
        else:
            await broker.shutdown(deadline)

        response = await pending
        assert response.outcome.outcome == "cancelled"
        assert len(publisher.permission_outcomes) == 1
        late = await broker.request_permission(handle, _permission_request())
        assert late.outcome.outcome == "cancelled"
        assert len(publisher.permission_requests) == 1
        if cause == "new_conversation":
            assert ("prepare", "session-a") in terminal_owner.calls
        else:
            assert ("cleanup", "employee-a", "session-a", 1) in terminal_owner.calls
        if cause == "shutdown":
            assert ("shutdown",) in terminal_owner.calls
        else:
            await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_compaction_settles_generation_reverse_owners_before_capture(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        permission_owner = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "permission-capture",
            integer_now=lambda: 100,
            timeout_seconds=300,
        )

        async def resolve(
            employee: ConversationEmployee, generation: int
        ) -> ConversationSessionBinding | None:
            if employee.employee_id == "employee-a" and generation == 1:
                return ConversationSessionBinding(
                    employee_id="employee-a",
                    acp_session_id="session-a",
                    backend_key="fake",
                    binding_generation=1,
                )
            return None

        terminal_owner = ScopedAcpTerminalService(
            resolve,
            publisher,
            terminal_enabled=True,
            terminal_id_factory=lambda: "capture-terminal",
            base_environment={},
            default_working_directory=lambda employee: employee.workspace_roots[0],
            cleanup_timeout_seconds=1,
        )
        strategy = _CompactionStrategy()
        broker, child, _publisher, handle, _runtime = _fixture(
            strategy=strategy,
            observes_compaction=True,
            permission_broker=permission_owner,
            terminal_service=terminal_owner,
            reverse_permission=True,
            reverse_terminal=True,
            workspace_root=tmp_path,
            publisher=publisher,
        )
        await permission_owner.attach_browser(handle.employee, "browser-capture")
        await broker.deliver(handle, "compact", "normal", _prompt("/compact"))
        terminal_fixture = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "acp"
            / "terminal_fixture.py"
        )
        await terminal_owner.create_terminal(
            handle.employee,
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(terminal_fixture), "hold"],
            ),
        )
        permission = asyncio.create_task(
            broker.request_permission(handle, _permission_request())
        )
        await _wait_until(lambda: len(publisher.permission_requests) == 1)
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        await strategy.capture_started.wait()
        assert (await permission).outcome.outcome == "cancelled"
        assert publisher.terminal_states[-1].lifecycle == "released"
        output = publisher.terminal_states[-1].terminal_output
        assert output.exit_status is not None
        strategy.capture_gate.set()
        await _wait_until(
            lambda: any(
                event[0] == "compaction" and event[1].state == "compacted"
                for event in publisher.events
            )
        )
        await broker.shutdown(asyncio.get_running_loop().time() + 2)

    asyncio.run(exercise())


def test_terminal_publication_failure_signals_actor_and_starts_no_queue(
    tmp_path: Path,
) -> None:
    class FailingTerminalPublisher(_Publisher):
        async def publish_terminal_state(
            self, employee: Any, binding: Any, state: Any
        ) -> None:
            if self.terminal_states:
                raise RuntimeError("terminal publication failed")
            await super().publish_terminal_state(employee, binding, state)

    async def exercise() -> None:
        publisher = FailingTerminalPublisher()

        async def resolve(
            employee: ConversationEmployee, generation: int
        ) -> ConversationSessionBinding | None:
            if employee.employee_id == "employee-a" and generation == 1:
                return ConversationSessionBinding(
                    employee_id="employee-a",
                    acp_session_id="session-a",
                    backend_key="fake",
                    binding_generation=1,
                )
            return None

        terminal_owner = ScopedAcpTerminalService(
            resolve,
            publisher,
            terminal_enabled=True,
            terminal_id_factory=lambda: "failed-terminal",
            base_environment={},
            default_working_directory=lambda employee: employee.workspace_roots[0],
            cleanup_timeout_seconds=1,
        )
        broker, child, _publisher, handle, _runtime = _fixture(
            terminal_service=terminal_owner,
            reverse_terminal=True,
            workspace_root=tmp_path,
            publisher=publisher,
        )
        await broker.deliver(handle, "active", "normal", _prompt("active"))
        await broker.deliver(handle, "queued", "queue", _prompt("queued"))
        fixture = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "acp"
            / "terminal_fixture.py"
        )
        created = await terminal_owner.create_terminal(
            handle.employee,
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(fixture), "stream"],
            ),
        )
        await _wait_until(
            lambda: any(
                event[0] == "receipt"
                and event[1].client_message_id == "queued"
                and event[1].state == "rejected"
                for event in publisher.events
            )
        )
        assert len(child.prompts) == 1
        with pytest.raises(AcpReverseTerminalError):
            await terminal_owner.terminal_output(
                handle.employee,
                1,
                TerminalOutputRequest(
                    session_id="session-a", terminal_id=created.terminal_id
                ),
            )
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_publisher_failure_is_generation_fatal_and_starts_no_prompt() -> None:
    class FailingPublisher(_Publisher):
        async def publish_delivery_receipt(
            self, employee: Any, binding: Any, receipt: Any
        ) -> None:
            del employee, binding, receipt
            raise RuntimeError("publisher failed")

    async def exercise() -> None:
        _broker, child, _publisher, handle, runtime = _fixture()
        broker = ConversationTurnBroker(
            runtime, FailingPublisher(), integer_now=lambda: 100
        )
        with pytest.raises(RuntimeError, match="publisher failed"):
            await broker.deliver(handle, "first", "normal", _prompt())
        assert not child.prompts
        with pytest.raises(RuntimeError, match="publisher failed"):
            await broker.deliver(handle, "second", "normal", _prompt())
        assert not child.prompts
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_shutdown_has_one_hard_deadline_when_actor_publication_is_held() -> None:
    class HoldingPublisher(_Publisher):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.cancel_seen = asyncio.Event()
            self.release = asyncio.Event()
            self.exited = asyncio.Event()

        async def publish_delivery_receipt(
            self, employee: Any, binding: Any, receipt: Any
        ) -> None:
            self.entered.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_seen.set()
            await super().publish_delivery_receipt(employee, binding, receipt)
            self.exited.set()

    async def exercise() -> None:
        broker, child, _publisher, handle, runtime = _fixture()
        publisher = HoldingPublisher()
        broker = ConversationTurnBroker(
            runtime, publisher, integer_now=lambda: 100
        )
        delivery = asyncio.create_task(
            broker.deliver(handle, "held", "normal", _prompt())
        )
        await publisher.entered.wait()
        started = asyncio.get_running_loop().time()
        deadline = started + 0.03
        first, second = await asyncio.gather(
            broker.shutdown(deadline),
            broker.shutdown(deadline),
            return_exceptions=True,
        )
        assert isinstance(first, ConversationTurnBrokerShutdownError)
        assert isinstance(second, ConversationTurnBrokerShutdownError)
        assert first.unfinished_employee_ids == second.unfinished_employee_ids == (
            "employee-a",
        )
        assert asyncio.get_running_loop().time() - started < 0.2
        assert publisher.cancel_seen.is_set()
        assert not broker._actors
        assert not child.prompts
        with pytest.raises(asyncio.CancelledError):
            await delivery
        publisher.release.set()
        await asyncio.wait_for(publisher.exited.wait(), timeout=0.2)
        assert not child.prompts

    asyncio.run(exercise())


def test_response_consumption_epoch_waits_for_typed_sink_and_private_capture_isolated() -> None:
    async def exercise() -> None:
        ordinary: list[SessionNotification] = []
        private: list[SessionNotification] = []
        release = asyncio.Event()

        async def ordinary_sink(item: Any) -> None:
            await release.wait()
            ordinary.append(item)

        async def private_sink(item: Any) -> None:
            private.append(item)

        ingress = OrderedAcpConversationIngress(ordinary_sink)
        ingress.start()
        prompt_epoch = ingress.begin_response_consumption_epoch("session/prompt")
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.OUTGOING,
                {"jsonrpc": "2.0", "id": 1, "method": "session/prompt", "params": {}},
            )
        )
        notification = SessionNotification(
            session_id="session-a",
            update=AgentThoughtChunk(
                session_update="agent_thought_chunk",
                content=TextContentBlock(type="text", text="before response"),
            ),
        )
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": notification.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                },
            )
        )
        ingress.fulfill_typed(notification)
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "end_turn"}},
            )
        )
        waiter = asyncio.create_task(ingress.finish_response_consumption_epoch(prompt_epoch))
        await asyncio.sleep(0)
        assert not waiter.done()
        release.set()
        await waiter

        capture = ingress.begin_response_consumption_epoch(
            "session/load",
            private_ingress=private_sink,
            private_session_id="session-a",
        )
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.OUTGOING,
                {"jsonrpc": "2.0", "id": 2, "method": "session/load", "params": {}},
            )
        )
        replay = notification.model_copy(
            update={
                "update": AgentThoughtChunk(
                    session_update="agent_thought_chunk",
                    content=TextContentBlock(type="text", text="private replay"),
                )
            }
        )
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": replay.model_dump(mode="json", by_alias=True, exclude_none=True),
                },
            )
        )
        ingress.fulfill_typed(replay)
        ingress.observe_stream(
            StreamEvent(StreamDirection.INCOMING, {"jsonrpc": "2.0", "id": 2, "result": {}})
        )
        await ingress.finish_response_consumption_epoch(capture)
        assert [item.update.content.text for item in ordinary] == ["before response"]
        assert [item.update.content.text for item in private] == ["private replay"]
        await ingress.close()

    asyncio.run(exercise())


def test_acp02_production_subject_passes_required_probes_and_mutations_fail() -> None:
    async def exercise() -> None:
        subject = await ProductionAcp02ConformanceSubject.create()
        observations = (
            (
                "permission_exactly_once",
                subject.observe_permission_settlement(),
                assert_permission_exactly_once,
            ),
            (
                "delivery_capabilities",
                subject.observe_delivery_capabilities(),
                assert_delivery_capabilities,
            ),
            (
                "compaction_visibility",
                subject.observe_compaction(),
                assert_compaction_visibility,
            ),
        )
        for probe_id, evidence, assertion in observations:
            assertion(evidence)  # type: ignore[arg-type]
            with pytest.raises(AssertionError):
                assertion(  # type: ignore[arg-type]
                    mutate_probe_evidence(probe_id, evidence)  # type: ignore[arg-type]
                )

    asyncio.run(exercise())


def test_generic_runtime_has_no_backend_name_or_canonical_product_writer_branch() -> None:
    conversation_root = Path(__file__).resolve().parents[2] / "src/planner/conversation"
    generic_sources = (
        conversation_root / "turn_broker.py",
        conversation_root / "permission_broker.py",
        conversation_root / "runtime_ports.py",
        conversation_root / "reverse_services/filesystem.py",
        conversation_root / "reverse_services/path_confinement.py",
        conversation_root / "reverse_services/terminal.py",
    )
    for source_path in generic_sources:
        source = source_path.read_text().lower()
        assert "hermes" not in source, source_path
        assert "planner.chat" not in source, source_path
        assert "planner.tickets" not in source, source_path
