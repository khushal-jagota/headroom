"""Production ACP-01 runtime subject for the reusable conformance probes."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from acp.schema import (
    AgentThoughtChunk,
    AllowedOutcome,
    PermissionOption,
    PromptRequest,
    PromptResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionInfoUpdate,
    SessionNotification,
    TextContentBlock,
    ToolCallUpdate,
)
from acp.transports import default_environment
from tests.support.acp_conformance import (
    CallbackOrderEvidence,
    CompactionEvidence,
    DeliveryCapabilityEvidence,
    LoadReplayEvidence,
    PermissionSettlementEvidence,
    RefreshEvidence,
)
from tests.support.acp_in_memory_binding_repository import (
    InMemoryAcpBindingRepository,
)

from planner.conversation import (
    AcpEmployeeRegistry,
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ContextCompaction,
    ConversationActivity,
    ConversationEmployee,
    ConversationPermissionBroker,
    ConversationPermissionOutcome,
    ConversationPermissionRequest,
    ConversationRuntimeHandle,
    ConversationRuntimeLease,
    ConversationSessionBinding,
    ConversationTurnBroker,
    EmployeeBackendBuildContext,
    EmployeeBackendCatalog,
    ProtocolUpdateRejectedPayload,
    ReverseServiceCapabilities,
    SdkAcpEmployeeChildFactory,
    static_employee_backend_registration,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTED_AGENT_PATH = REPOSITORY_ROOT / "tests" / "support" / "acp_scripted_agent.py"


class _TurnStrategy:
    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class ProductionAcp01ConformanceSubject:
    def __init__(
        self,
        load_replay: LoadReplayEvidence,
        refresh: RefreshEvidence,
        callback_order: CallbackOrderEvidence,
    ) -> None:
        self._load_replay = load_replay
        self._refresh = refresh
        self._callback_order = callback_order

    @classmethod
    async def create(cls) -> ProductionAcp01ConformanceSubject:
        definition = AgentBackendDefinition(
            backend_key="scripted-production",
            argv=(sys.executable, str(SCRIPTED_AGENT_PATH)),
            inherited_environment_names=tuple(default_environment()),
            environment_overrides=(),
            expected_agent_name="panels-scripted-agent",
            expected_agent_version="1.0.0",
            turn_capabilities=BackendTurnCapabilities(
                supports_steer=False, observes_compaction=False
            ),
            reverse_service_capabilities=ReverseServiceCapabilities(
                filesystem=False, terminal=False, permission=True
            ),
            working_directory_resolver=lambda employee: employee.workspace_roots[0],
            turn_strategy=_TurnStrategy(),
        )
        factory = SdkAcpEmployeeChildFactory(definition)
        repository = InMemoryAcpBindingRepository()
        notifications: list[SessionNotification] = []
        trace: list[str] = []
        changed = asyncio.Condition()

        async def ingress(
            item: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            if isinstance(item, ProtocolUpdateRejectedPayload):
                raise AssertionError(item.reason)
            async with changed:
                notifications.append(item)
                trace.append(f"consume:{len(trace)}")
                changed.notify_all()

        async def permission(
            request: RequestPermissionRequest,
        ) -> RequestPermissionResponse:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=request.options[0].option_id)
            )

        employee = ConversationEmployee(
            employee_id="employee-production-acp-01",
            entity_kind="ticket",
            entity_id="ticket-production-acp-01",
            workspace_roots=(REPOSITORY_ROOT,),
            backend_key=definition.backend_key,
        )
        catalog = EmployeeBackendCatalog(
            (static_employee_backend_registration(definition, factory),)
        )
        registry = AcpEmployeeRegistry(
            backend_catalog=catalog,
            materialized_backends=catalog.materialize(
                EmployeeBackendBuildContext(data_directory=REPOSITORY_ROOT)
            ),
            resolve_binding=repository.resolve,
            compare_and_swap_binding=repository.compare_and_swap,
            conversation_ingress=ingress,
            permission_callback=permission,
        )

        async def wait_for_count(count: int) -> None:
            async with changed:
                await asyncio.wait_for(
                    changed.wait_for(lambda: len(notifications) >= count), timeout=3
                )

        try:
            original_record = await registry.get_or_spawn(employee)
            await original_record.child.prompt(
                PromptRequest(
                    session_id=original_record.binding.acp_session_id,
                    prompt=[TextContentBlock(type="text", text="populate")],
                    field_meta={"script": "default"},
                )
            )
            await wait_for_count(9)
            replay_start = len(notifications)
            refreshed_record = await registry.attach(employee)
            replay_end = len(notifications)
            trace.append("attach_return")
            load_position = len(trace) - 1
            replay_positions = tuple(range(replay_start, replay_end))

            concurrent_start = len(notifications)
            await refreshed_record.child.prompt(
                PromptRequest(
                    session_id=refreshed_record.binding.acp_session_id,
                    prompt=[TextContentBlock(type="text", text="concurrent")],
                    field_meta={"script": "concurrent"},
                )
            )
            await wait_for_count(concurrent_start + 4)
            concurrent = notifications[concurrent_start : concurrent_start + 4]
            reduced_order = tuple(
                int(update.content.text.removeprefix("wire-"))
                for notification in concurrent
                if isinstance(update := notification.update, AgentThoughtChunk)
                and isinstance(update.content, TextContentBlock)
                and update.content.text.startswith("wire-")
            )
            return cls(
                LoadReplayEvidence(
                    replay_callback_positions=replay_positions,
                    load_response_position=load_position,
                ),
                RefreshEvidence(
                    employee_id=employee.employee_id,
                    original_session_id=original_record.binding.acp_session_id,
                    refreshed_session_id=refreshed_record.binding.acp_session_id,
                    original_binding_generation=original_record.binding.binding_generation,
                    refreshed_binding_generation=refreshed_record.binding.binding_generation,
                ),
                CallbackOrderEvidence(
                    wire_order=(1, 2, 3),
                    reduced_order=reduced_order,
                    broadcast_order=reduced_order,
                ),
            )
        finally:
            await registry.shutdown(asyncio.get_running_loop().time() + 5)

    def observe_load_replay(self) -> LoadReplayEvidence:
        return self._load_replay

    def observe_refresh_binding(self) -> RefreshEvidence:
        return self._refresh

    def observe_callback_order(self) -> CallbackOrderEvidence:
        return self._callback_order


class _Acp02Publisher:
    def __init__(self) -> None:
        self.sequence = 0
        self.activities: list[ConversationActivity] = []
        self.receipts: list[Any] = []
        self.compactions: list[ContextCompaction] = []
        self.permission_requests: list[ConversationPermissionRequest] = []
        self.permission_outcomes: list[ConversationPermissionOutcome] = []

    async def publish_activity(
        self, employee: Any, binding: Any, state: Any, detail: str
    ) -> ConversationActivity:
        del employee, binding
        self.sequence += 1
        activity = ConversationActivity(state=state, detail=detail, sequence=self.sequence)
        self.activities.append(activity)
        return activity

    async def publish_delivery_receipt(self, employee: Any, binding: Any, receipt: Any) -> None:
        del employee, binding
        self.receipts.append(receipt)

    async def publish_queue_snapshot(self, employee: Any, binding: Any, prompts: Any) -> None:
        del employee, binding, prompts

    async def publish_compaction(
        self, employee: Any, binding: Any, compaction: ContextCompaction
    ) -> None:
        del employee, binding
        self.compactions.append(compaction)

    async def publish_permission_request(
        self,
        employee: ConversationEmployee,
        binding: Any,
        request_id: str,
        backend_key: str,
        request: RequestPermissionRequest,
        deadline_at: int,
    ) -> ConversationPermissionRequest:
        del binding
        self.sequence += 1
        opened = ConversationPermissionRequest(
            request_id=request_id,
            employee_id=employee.employee_id,
            backend_key=backend_key,
            request=request,
            lifecycle="pending",
            deadline_at=deadline_at,
            opened_sequence=self.sequence,
        )
        self.permission_requests.append(opened)
        return opened

    async def publish_permission_outcome(
        self,
        employee: Any,
        binding: Any,
        request_id: str,
        response: RequestPermissionResponse,
        cancellation_reason: str | None,
    ) -> ConversationPermissionOutcome:
        del employee, binding
        self.sequence += 1
        outcome = ConversationPermissionOutcome(
            request_id=request_id,
            response=response,
            cancellation_reason=cancellation_reason,
            settled_sequence=self.sequence,
        )
        self.permission_outcomes.append(outcome)
        return outcome

    async def publish_terminal_state(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError((args, kwargs))


class _Acp02Child:
    generation = 1

    def __init__(self) -> None:
        self.alive = True
        self.prompts: list[PromptRequest] = []
        self.responses: asyncio.Queue[PromptResponse] = asyncio.Queue()

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        self.prompts.append(request)
        return await self.responses.get()

    async def cancel(self, notification: Any) -> None:
        del notification


class _Acp02Runtime:
    def __init__(self, handle: ConversationRuntimeHandle) -> None:
        self.handle = handle

    async def resolve_runtime_handle(
        self, employee_id: str, binding_generation: int
    ) -> ConversationRuntimeHandle:
        if (
            employee_id != self.handle.employee.employee_id
            or binding_generation != self.handle.binding.binding_generation
        ):
            raise RuntimeError("unknown runtime")
        return self.handle

    async def acquire_runtime_lease(
        self, handle: ConversationRuntimeHandle
    ) -> ConversationRuntimeLease:
        if handle is not self.handle or not handle.child.alive:
            raise RuntimeError("stale runtime")
        return ConversationRuntimeLease(handle)

    async def retire_runtime_lease(self, lease: ConversationRuntimeLease, deadline: float) -> None:
        del lease, deadline
        self.handle.child.alive = False

    async def replace_runtime_for_capture(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError((args, kwargs))

    def strategy_for_lease(self, lease: ConversationRuntimeLease) -> Any:
        del lease
        return self.handle.definition.turn_strategy


class _ObservedCompactionStrategy(_TurnStrategy):
    def observe_compaction(
        self, binding: Any, notification: SessionNotification
    ) -> ContextCompaction | None:
        del binding
        if (
            isinstance(notification.update, SessionInfoUpdate)
            and notification.update.title == "automatic"
        ):
            return ContextCompaction(
                boundary_id="automatic-observation",
                state="compacting",
                trigger="automatic",
            )
        return None

    async def capture_compaction(self, binding: Any) -> ContextCompaction:
        del binding
        return ContextCompaction(
            boundary_id="capture",
            state="compacted",
            trigger="automatic",
        )


class ProductionAcp02ConformanceSubject:
    """Evidence produced by the actual ACP-02 brokers and strategy seams."""

    def __init__(
        self,
        permission: PermissionSettlementEvidence,
        delivery: DeliveryCapabilityEvidence,
        compaction: CompactionEvidence,
    ) -> None:
        self._permission = permission
        self._delivery = delivery
        self._compaction = compaction

    @classmethod
    async def create(cls) -> ProductionAcp02ConformanceSubject:
        return cls(
            await cls._produce_permission_evidence(),
            await cls._produce_delivery_evidence(),
            await cls._produce_compaction_evidence(),
        )

    @staticmethod
    def _employee() -> ConversationEmployee:
        return ConversationEmployee(
            employee_id="employee-production-acp-02",
            entity_kind="ticket",
            entity_id="ticket-production-acp-02",
            workspace_roots=(REPOSITORY_ROOT,),
            backend_key="production-acp-02",
        )

    @staticmethod
    def _permission_request(session_id: str) -> RequestPermissionRequest:
        return RequestPermissionRequest(
            session_id=session_id,
            tool_call=ToolCallUpdate(
                session_update="tool_call",
                tool_call_id="permission-tool",
                title="Inspect file",
                kind="read",
                status="pending",
            ),
            options=[PermissionOption(option_id="allow", name="Allow", kind="allow_once")],
        )

    @classmethod
    async def _produce_permission_evidence(
        cls,
    ) -> PermissionSettlementEvidence:
        publisher = _Acp02Publisher()
        request_ids = iter(("cancel", "death", "last_browser_disconnect", "timeout"))
        owner = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: next(request_ids),
            integer_now=lambda: 10,
            timeout_seconds=0.02,
        )
        employee = cls._employee()
        binding = ConversationSessionBinding(
            employee_id=employee.employee_id,
            acp_session_id="permission-session",
            backend_key=employee.backend_key,
            binding_generation=1,
        )
        await owner.attach_browser(employee, "browser")

        async def open_request(identity: object, epoch: int) -> asyncio.Task[Any]:
            expected_count = len(publisher.permission_requests) + 1
            task = asyncio.create_task(
                owner.request_permission(
                    employee,
                    binding,
                    1,
                    identity,
                    epoch,
                    cls._permission_request(binding.acp_session_id),
                    permission_declared=True,
                )
            )
            while len(publisher.permission_requests) < expected_count:
                await asyncio.sleep(0)
            return task

        cancel_identity = object()
        cancel = await open_request(cancel_identity, 1)
        await owner.cancel_prompt_epoch(
            employee.employee_id,
            binding.binding_generation,
            1,
            cancel_identity,
            1,
            "cancel",
        )
        await cancel
        death = await open_request(object(), 2)
        await owner.cancel_child_generation(
            employee.employee_id,
            binding.binding_generation,
            1,
            "death",
        )
        await death
        disconnected = await open_request(object(), 3)
        await owner.detach_browser("browser")
        await disconnected
        await owner.attach_browser(employee, "browser")
        timed_out = await open_request(object(), 4)
        await timed_out
        evidence = PermissionSettlementEvidence(
            settlements_by_cause=tuple(
                (
                    request_id,
                    sum(
                        outcome.request_id == request_id
                        for outcome in publisher.permission_outcomes
                    ),
                )
                for request_id in (
                    "cancel",
                    "death",
                    "last_browser_disconnect",
                    "timeout",
                )
            )
        )
        await owner.shutdown(asyncio.get_running_loop().time() + 1)
        return evidence

    @classmethod
    def _runtime_fixture(
        cls, strategy: Any, *, observes_compaction: bool
    ) -> tuple[_Acp02Child, _Acp02Runtime, ConversationRuntimeHandle]:
        employee = cls._employee()
        binding = ConversationSessionBinding(
            employee_id=employee.employee_id,
            acp_session_id="delivery-session",
            backend_key=employee.backend_key,
            binding_generation=1,
        )
        definition = AgentBackendDefinition(
            backend_key=employee.backend_key,
            argv=("/production-acp-02",),
            inherited_environment_names=(),
            environment_overrides=(),
            expected_agent_name="production-acp-02",
            expected_agent_version="1",
            turn_capabilities=BackendTurnCapabilities(
                supports_steer=False,
                observes_compaction=observes_compaction,
            ),
            reverse_service_capabilities=ReverseServiceCapabilities(
                filesystem=False, terminal=False, permission=False
            ),
            working_directory_resolver=lambda candidate: candidate.workspace_roots[0],
            turn_strategy=strategy,
        )
        child = _Acp02Child()
        handle = ConversationRuntimeHandle(
            employee=employee,
            binding=binding,
            child_generation=1,
            child=child,
            definition=definition,
            record_identity=object(),
        )
        return child, _Acp02Runtime(handle), handle

    @classmethod
    async def _produce_delivery_evidence(cls) -> DeliveryCapabilityEvidence:
        child, runtime, handle = cls._runtime_fixture(_TurnStrategy(), observes_compaction=False)
        publisher = _Acp02Publisher()
        broker = ConversationTurnBroker(runtime, publisher, integer_now=lambda: 10)
        prompt = PromptRequest(
            session_id=handle.binding.acp_session_id,
            prompt=[TextContentBlock(type="text", text="delivery")],
        )
        await broker.deliver(handle, "active", "normal", prompt)
        await broker.deliver(handle, "queued", "queue", prompt)
        unsupported = await broker.deliver(handle, "steer", "steer", prompt)
        await broker.deliver(handle, "send-now", "send_now", prompt)
        child.responses.put_nowait(PromptResponse(stop_reason="cancelled"))
        while len(child.prompts) < 2:
            await asyncio.sleep(0)
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        while len(child.prompts) < 3:
            await asyncio.sleep(0)
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        while not publisher.activities or publisher.activities[-1].state != "idle":
            await asyncio.sleep(0)
        operations = tuple(
            choice
            for choice in ("queue", "send_now")
            if any(receipt.choice == choice for receipt in publisher.receipts)
        )
        await broker.shutdown(asyncio.get_running_loop().time() + 1)
        return DeliveryCapabilityEvidence(
            supports_steer=False,
            steer_result="rejected_unavailable",
            unsupported_steer_visible=unsupported.state == "rejected",
            broker_operations=operations,
        )

    @classmethod
    async def _produce_compaction_evidence(cls) -> CompactionEvidence:
        strategy = _ObservedCompactionStrategy()
        child, runtime, handle = cls._runtime_fixture(strategy, observes_compaction=True)
        publisher = _Acp02Publisher()
        broker = ConversationTurnBroker(runtime, publisher, integer_now=lambda: 10)
        explicit = PromptRequest(
            session_id=handle.binding.acp_session_id,
            prompt=[TextContentBlock(type="text", text="/compact")],
        )
        await broker.deliver(handle, "explicit", "normal", explicit)
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        while len(publisher.compactions) < 2:
            await asyncio.sleep(0)
        ordinary = explicit.model_copy(
            update={"prompt": [TextContentBlock(type="text", text="ordinary")]}
        )
        await broker.deliver(handle, "automatic", "normal", ordinary)
        await broker.observe_session_notification(
            handle,
            SessionNotification(
                session_id=handle.binding.acp_session_id,
                update=SessionInfoUpdate(session_update="session_info_update", title="automatic"),
            ),
        )
        child.responses.put_nowait(PromptResponse(stop_reason="end_turn"))
        while len(publisher.compactions) < 4:
            await asyncio.sleep(0)
        explicit_updates = tuple(
            item for item in publisher.compactions if item.trigger == "explicit"
        )
        automatic_updates = tuple(
            item for item in publisher.compactions if item.trigger == "automatic"
        )
        await broker.shutdown(asyncio.get_running_loop().time() + 1)
        return CompactionEvidence(
            explicit_states=tuple(item.state for item in explicit_updates),
            automatic_states=tuple(item.state for item in automatic_updates),
        )

    def observe_permission_settlement(self) -> PermissionSettlementEvidence:
        return self._permission

    def observe_delivery_capabilities(self) -> DeliveryCapabilityEvidence:
        return self._delivery

    def observe_compaction(self) -> CompactionEvidence:
        return self._compaction
