from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from acp.schema import (
    DeniedOutcome,
    PermissionOption,
    PromptRequest,
    RequestPermissionRequest,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallUpdate,
)
from acp.transports import default_environment
from tests.support.acp_in_memory_binding_repository import InMemoryAcpBindingRepository

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
from planner.conversation.contracts import (
    ConversationActivity,
    ConversationEmployee,
    ConversationPermissionOutcome,
    ConversationPermissionRequest,
    ConversationSessionBinding,
)
from planner.conversation.employee_registry import AcpEmployeeRegistry
from planner.conversation.permission_broker import ConversationPermissionBroker
from planner.conversation.runtime_ports import ConversationRuntimeHandle, ConversationRuntimeLease
from planner.conversation.sdk_child import SdkAcpEmployeeChildFactory
from planner.conversation.turn_broker import ConversationTurnBroker

SCRIPTED_AGENT = Path(__file__).resolve().parents[1] / "support" / "acp_scripted_agent.py"


def _employee(employee_id: str = "employee-a") -> ConversationEmployee:
    return ConversationEmployee(
        employee_id=employee_id,
        entity_kind="ticket",
        entity_id="ticket-a",
        workspace_roots=(Path("/tmp"),),
        backend_key="fake",
    )


def _binding(employee_id: str = "employee-a") -> ConversationSessionBinding:
    return ConversationSessionBinding(
        employee_id=employee_id,
        acp_session_id="session-a",
        backend_key="fake",
        binding_generation=1,
    )


def _request() -> RequestPermissionRequest:
    return RequestPermissionRequest(
        session_id="session-a",
        tool_call=ToolCallUpdate(
            session_update="tool_call",
            tool_call_id="tool-a",
            title="Write file",
            kind="edit",
            status="pending",
        ),
        options=[
            PermissionOption(option_id="once", name="Allow once", kind="allow_once"),
            PermissionOption(option_id="always", name="Always", kind="allow_always"),
            PermissionOption(option_id="deny", name="Deny", kind="reject_once"),
        ],
    )


class _Publisher:
    def __init__(self) -> None:
        self.sequence = 0
        self.requests: list[ConversationPermissionRequest] = []
        self.outcomes: list[ConversationPermissionOutcome] = []
        self.activities: list[ConversationActivity] = []
        self.receipts: list[Any] = []
        self.queue_snapshots: list[Any] = []
        self.fail_permission_outcome = False

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
        self.requests.append(value)
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
        if self.fail_permission_outcome:
            raise RuntimeError("permission outcome publisher failed")
        self.sequence += 1
        value = ConversationPermissionOutcome(
            request_id=request_id,
            response=response,
            cancellation_reason=cancellation_reason,
            settled_sequence=self.sequence,
        )
        self.outcomes.append(value)
        return value

    async def publish_activity(self, employee: Any, binding: Any, state: Any, detail: str):
        del employee, binding
        self.sequence += 1
        value = ConversationActivity(state=state, detail=detail, sequence=self.sequence)
        self.activities.append(value)
        return value

    async def publish_delivery_receipt(self, *args: Any, **kwargs: Any):
        del kwargs
        self.receipts.append(args[-1])

    async def publish_queue_snapshot(self, *args: Any, **kwargs: Any):
        del kwargs
        self.queue_snapshots.append(args[-1])

    async def publish_compaction(self, *args: Any, **kwargs: Any):
        raise AssertionError((args, kwargs))

    async def publish_terminal_state(self, *args: Any, **kwargs: Any):
        raise AssertionError((args, kwargs))


def test_no_browser_cancels_without_visible_request_and_first_valid_browser_wins() -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        ids = iter(("request-1", "request-2"))
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: next(ids),
            integer_now=lambda: 100,
            timeout_seconds=300,
        )
        response = await broker.request_permission(
            _employee(), _binding(), 1, object(), 4, _request(), permission_declared=True
        )
        assert response.outcome.outcome == "cancelled"
        assert not publisher.requests

        await broker.attach_browser(_employee(), "browser-a")
        await broker.attach_browser(_employee(), "browser-b")
        pending = asyncio.create_task(
            broker.request_permission(
                _employee(), _binding(), 1, object(), 4, _request(), permission_declared=True
            )
        )
        while not publisher.requests:
            await asyncio.sleep(0)
        visible = publisher.requests[0]
        assert [option.option_id for option in visible.request.options] == [
            "once",
            "always",
            "deny",
        ]
        assert visible.deadline_at == 400
        invalid = await broker.respond_to_permission("browser-a", visible.request_id, "missing")
        assert invalid.disposition == "rejected"
        selected = await broker.respond_to_permission("browser-b", visible.request_id, "always")
        duplicate = await broker.respond_to_permission("browser-a", visible.request_id, "once")
        assert selected.disposition == "selected"
        assert duplicate.disposition == "already_settled"
        response = await pending
        assert response.outcome.outcome == "selected"
        assert response.outcome.option_id == "always"
        assert len(publisher.outcomes) == 1
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_worker_guard_marks_exact_pending_inside_immediate_transaction(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "worker-request",
            integer_now=lambda: 100,
            timeout_seconds=300,
        )
        guard_db = sqlite3.connect(tmp_path / "permission-guard.db", isolation_level=None)
        guard_db.execute("CREATE TABLE audit (request_id TEXT PRIMARY KEY)")
        marked_in_transaction: list[bool] = []

        def guard(snapshot: Any, mark_settling: Any) -> bool:
            guard_db.execute("BEGIN IMMEDIATE")
            try:
                assert snapshot.request_id == "worker-request"
                assert snapshot.origin == "worker"
                assert snapshot.settling is False
                mark_settling()
                marked_in_transaction.append(guard_db.in_transaction)
                guard_db.execute("INSERT INTO audit VALUES (?)", (snapshot.request_id,))
                guard_db.execute("COMMIT")
                return True
            except BaseException:
                guard_db.execute("ROLLBACK")
                raise

        broker.set_worker_settlement_guard(guard)
        await broker.attach_browser(_employee(), "browser-a")
        pending = asyncio.create_task(
            broker.request_permission(
                _employee(),
                _binding(),
                1,
                object(),
                4,
                _request(),
                permission_declared=True,
                origin="worker",
            )
        )
        while not publisher.requests:
            await asyncio.sleep(0)
        result = await broker.respond_to_permission(
            "browser-a", "worker-request", "once"
        )
        assert result.disposition == "selected"
        assert marked_in_transaction == [True]
        assert guard_db.execute("SELECT request_id FROM audit").fetchone() == (
            "worker-request",
        )
        assert (await pending).outcome.outcome == "selected"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)
        guard_db.close()

    asyncio.run(exercise())


class _NoopStrategy:
    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError((args, kwargs))

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError((args, kwargs))


def test_real_sdk_permission_callback_is_admitted_by_turn_epoch_and_cancelled_once(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        employee = ConversationEmployee(
            employee_id="employee-sdk-permission",
            entity_kind="ticket",
            entity_id="ticket-sdk-permission",
            workspace_roots=(tmp_path,),
            backend_key="scripted-permission",
        )
        definition = AgentBackendDefinition(
            backend_key=employee.backend_key,
            argv=(sys.executable, str(SCRIPTED_AGENT)),
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
            working_directory_resolver=lambda candidate: candidate.workspace_roots[0],
            turn_strategy=_NoopStrategy(),
        )
        publisher = _Publisher()
        request_ids = iter(("sdk-permission-1", "sdk-permission-2"))
        permission_owner = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: next(request_ids),
            integer_now=lambda: 100,
            timeout_seconds=5,
        )
        repository = InMemoryAcpBindingRepository()
        broker: ConversationTurnBroker | None = None

        async def ingress(_item: Any) -> None:
            return None

        async def fallback_permission(
            _request: RequestPermissionRequest,
        ) -> RequestPermissionResponse:
            return RequestPermissionResponse(
                outcome=DeniedOutcome(outcome="cancelled")
            )

        async def source_permission(
            handle: ConversationRuntimeHandle, request: RequestPermissionRequest
        ) -> RequestPermissionResponse:
            assert broker is not None
            return await broker.request_permission(handle, request)

        async def child_death(
            candidate: ConversationEmployee,
            generation: int,
            error: BaseException | None,
        ) -> None:
            if broker is None:
                return
            binding = await repository.resolve(candidate.employee_id)
            if binding is not None:
                await broker.child_died(
                    candidate.employee_id,
                    binding.binding_generation,
                    generation,
                    error,
                )

        child_factory = SdkAcpEmployeeChildFactory(definition)
        materialized_backend = MaterializedEmployeeBackendRegistration(
            definition=definition,
            child_factory=child_factory,
            is_executable=lambda: True,
        )
        backend_catalog = EmployeeBackendCatalog(
            (
                EmployeeBackendRegistration(
                    backend_key=definition.backend_key,
                    runtime_builder=lambda _context: materialized_backend,
                ),
            )
        )
        registry = AcpEmployeeRegistry(
            backend_catalog=backend_catalog,
            materialized_backends=(materialized_backend,),
            resolve_binding=repository.resolve,
            compare_and_swap_binding=repository.compare_and_swap,
            conversation_ingress=ingress,
            permission_callback=fallback_permission,
            conversation_child_death_callback=child_death,
            source_aware_permission_callback=source_permission,
        )
        broker = ConversationTurnBroker(
            registry,
            publisher,
            integer_now=lambda: 100,
            permission_broker=permission_owner,
        )
        await permission_owner.attach_browser(employee, "browser-sdk")
        record = await registry.get_or_spawn(employee)
        handle = await registry.resolve_runtime_handle(
            employee.employee_id, record.binding.binding_generation
        )
        await broker.deliver(
            handle,
            "permission-turn",
            "normal",
            PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="permission")],
                field_meta={"script": "permission"},
            ),
        )
        async with asyncio.timeout(3):
            while not publisher.requests:
                await asyncio.sleep(0)
        selected = await permission_owner.respond_to_permission(
            "browser-sdk", "sdk-permission-1", "allow-once"
        )
        assert selected.disposition == "selected"
        async with asyncio.timeout(3):
            while not any(
                receipt.client_message_id == "permission-turn"
                and receipt.state == "started"
                for receipt in publisher.receipts
            ) or not publisher.activities or publisher.activities[-1].state != "idle":
                await asyncio.sleep(0)

        await broker.deliver(
            handle,
            "permission-cancel",
            "normal",
            PromptRequest(
                session_id=record.binding.acp_session_id,
                prompt=[TextContentBlock(type="text", text="permission")],
                field_meta={"script": "permission"},
            ),
        )
        async with asyncio.timeout(3):
            while len(publisher.requests) < 2:
                await asyncio.sleep(0)
        await broker.cancel(handle)
        async with asyncio.timeout(3):
            while len(publisher.outcomes) < 2:
                await asyncio.sleep(0)
        assert publisher.outcomes[-1].response.outcome.outcome == "cancelled"
        assert len(
            [
                outcome
                for outcome in publisher.outcomes
                if outcome.request_id == "sdk-permission-2"
            ]
        ) == 1
        late = await broker.request_permission(
            handle,
            _request().model_copy(
                update={"session_id": record.binding.acp_session_id}
            ),
        )
        assert late.outcome.outcome == "cancelled"
        assert len(publisher.requests) == 2
        await broker.shutdown(asyncio.get_running_loop().time() + 2)
        await registry.shutdown(asyncio.get_running_loop().time() + 2)

    asyncio.run(exercise())


def test_active_cancel_outcome_publication_failure_resolves_callback_and_starts_no_queue(
    tmp_path: Path,
) -> None:
    class BlockingChild:
        generation = 1
        alive = True

        def __init__(self) -> None:
            self.prompts: list[PromptRequest] = []
            self.never = asyncio.Event()

        async def prompt(self, request: PromptRequest) -> Any:
            self.prompts.append(request)
            await self.never.wait()
            raise AssertionError("unreachable")

        async def cancel(self, _notification: Any) -> None:
            return None

    class Runtime:
        def __init__(self, handle: ConversationRuntimeHandle) -> None:
            self.handle = handle

        async def acquire_runtime_lease(
            self, handle: ConversationRuntimeHandle
        ) -> ConversationRuntimeLease:
            if handle is not self.handle:
                raise RuntimeError("stale")
            return ConversationRuntimeLease(handle)

        async def retire_runtime_lease(
            self, _lease: ConversationRuntimeLease, _deadline: float
        ) -> None:
            self.handle.child.alive = False

        def strategy_for_lease(self, _lease: ConversationRuntimeLease) -> Any:
            return self.handle.definition.turn_strategy

    async def exercise() -> None:
        employee = ConversationEmployee(
            employee_id="employee-permission-failure",
            entity_kind="ticket",
            entity_id="ticket-permission-failure",
            workspace_roots=(tmp_path,),
            backend_key="fake",
        )
        binding = ConversationSessionBinding(
            employee_id=employee.employee_id,
            acp_session_id="session-permission-failure",
            backend_key=employee.backend_key,
            binding_generation=1,
        )
        definition = AgentBackendDefinition(
            backend_key=employee.backend_key,
            argv=("/fake",),
            inherited_environment_names=(),
            environment_overrides=(),
            expected_agent_name="fake",
            expected_agent_version="1",
            turn_capabilities=BackendTurnCapabilities(
                supports_steer=False, observes_compaction=False
            ),
            reverse_service_capabilities=ReverseServiceCapabilities(
                filesystem=False, terminal=False, permission=True
            ),
            working_directory_resolver=lambda candidate: candidate.workspace_roots[0],
            turn_strategy=_NoopStrategy(),
        )
        child = BlockingChild()
        handle = ConversationRuntimeHandle(
            employee=employee,
            binding=binding,
            child_generation=1,
            child=child,
            definition=definition,
            record_identity=object(),
        )
        runtime = Runtime(handle)
        publisher = _Publisher()
        permission_owner = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "permission-publisher-failure",
            integer_now=lambda: 10,
            timeout_seconds=300,
        )
        broker = ConversationTurnBroker(
            runtime,
            publisher,
            integer_now=lambda: 10,
            permission_broker=permission_owner,
        )
        await permission_owner.attach_browser(employee, "browser-failure")
        prompt = PromptRequest(
            session_id=binding.acp_session_id,
            prompt=[TextContentBlock(type="text", text="active")],
        )
        await broker.deliver(handle, "active", "normal", prompt)
        await broker.deliver(handle, "queued", "queue", prompt)
        request = _request().model_copy(
            update={"session_id": binding.acp_session_id}
        )
        callback = asyncio.create_task(broker.request_permission(handle, request))
        async with asyncio.timeout(1):
            while not publisher.requests:
                await asyncio.sleep(0)
        publisher.fail_permission_outcome = True
        await broker.cancel(handle)
        response = await callback
        assert response.outcome.outcome == "cancelled"
        async with asyncio.timeout(1):
            while not any(
                receipt.client_message_id == "queued" and receipt.state == "rejected"
                for receipt in publisher.receipts
            ):
                await asyncio.sleep(0)
        assert len(child.prompts) == 1
        rejected = await broker.deliver(handle, "later", "normal", prompt)
        assert rejected.state == "rejected"
        assert len(child.prompts) == 1
        assert not permission_owner._pending
        assert not permission_owner._settlement_tasks
        publisher.fail_permission_outcome = False
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_activity_failure_after_visible_selection_cannot_revoke_agent_response() -> None:
    class ActivityFailingPublisher(_Publisher):
        async def publish_activity(
            self, employee: Any, binding: Any, state: Any, detail: str
        ) -> ConversationActivity:
            if self.outcomes:
                raise RuntimeError("activity publisher failed")
            return await super().publish_activity(employee, binding, state, detail)

    async def exercise() -> None:
        publisher = ActivityFailingPublisher()
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "request-activity-failure",
            integer_now=lambda: 10,
            timeout_seconds=300,
        )
        await broker.attach_browser(_employee(), "browser-a")
        pending = asyncio.create_task(
            broker.request_permission(
                _employee(),
                _binding(),
                1,
                object(),
                2,
                _request(),
                permission_declared=True,
            )
        )
        while not publisher.requests:
            await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="activity publisher failed"):
            await broker.respond_to_permission(
                "browser-a", "request-activity-failure", "once"
            )
        response = await pending
        assert response.outcome.outcome == "selected"
        assert response.outcome.option_id == "once"
        assert len(publisher.outcomes) == 1
        assert publisher.outcomes[0].response == response
        late = await broker.respond_to_permission(
            "browser-a", "request-activity-failure", "once"
        )
        assert late.disposition == "already_settled"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_shutdown_deadline_disposes_cancellation_resistant_permission_publication() -> None:
    class HoldingOutcomePublisher(_Publisher):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.cancel_seen = asyncio.Event()
            self.release = asyncio.Event()
            self.exited = asyncio.Event()

        async def publish_permission_outcome(
            self,
            employee: Any,
            binding: Any,
            request_id: str,
            response: Any,
            cancellation_reason: str | None,
        ) -> ConversationPermissionOutcome:
            self.entered.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_seen.set()
            result = await super().publish_permission_outcome(
                employee,
                binding,
                request_id,
                response,
                cancellation_reason,
            )
            self.exited.set()
            return result

    async def exercise() -> None:
        publisher = HoldingOutcomePublisher()
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "request-shutdown-held",
            integer_now=lambda: 10,
            timeout_seconds=300,
        )
        await broker.attach_browser(_employee(), "browser-a")
        pending = asyncio.create_task(
            broker.request_permission(
                _employee(),
                _binding(),
                1,
                object(),
                2,
                _request(),
                permission_declared=True,
            )
        )
        while not publisher.requests:
            await asyncio.sleep(0)
        started = asyncio.get_running_loop().time()
        shutdown = asyncio.create_task(broker.shutdown(started + 0.03))
        await publisher.entered.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(shutdown, timeout=0.2)
        assert asyncio.get_running_loop().time() - started < 0.2
        assert publisher.cancel_seen.is_set()
        response = await pending
        assert response.outcome.outcome == "cancelled"
        assert not broker._pending
        assert not broker._settlement_tasks
        assert not broker._failure_tasks
        publisher.release.set()
        await asyncio.wait_for(publisher.exited.wait(), timeout=0.2)

    asyncio.run(exercise())


def test_one_detach_keeps_request_open_and_last_detach_cancels_exactly_once() -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "request-a",
            integer_now=lambda: 10,
            timeout_seconds=300,
        )
        await broker.attach_browser(_employee(), "browser-a")
        await broker.attach_browser(_employee(), "browser-b")
        pending = asyncio.create_task(
            broker.request_permission(
                _employee(), _binding(), 1, object(), 2, _request(), permission_declared=True
            )
        )
        while not publisher.requests:
            await asyncio.sleep(0)
        await broker.detach_browser("browser-a")
        assert not pending.done()
        await broker.detach_browser("browser-b")
        response = await pending
        assert response.outcome.outcome == "cancelled"
        assert len(publisher.outcomes) == 1
        assert publisher.outcomes[0].cancellation_reason == "No browser is attached"
        await broker.detach_browser("browser-b")
        assert len(publisher.outcomes) == 1
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_epoch_tombstone_rejects_late_open_without_publication() -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "request-a",
            integer_now=lambda: 10,
            timeout_seconds=300,
        )
        employee = _employee()
        binding = _binding()
        await broker.attach_browser(employee, "browser-a")
        identity = object()
        await broker.cancel_prompt_epoch(
            employee.employee_id,
            binding.binding_generation,
            1,
            identity,
            7,
            "Prompt was cancelled",
        )
        response = await broker.request_permission(
            employee, binding, 1, identity, 7, _request(), permission_declared=True
        )
        assert response.outcome.outcome == "cancelled"
        assert not publisher.requests
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_timeout_uses_injected_deadline_and_settles_once_with_activity_restore() -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "request-timeout",
            integer_now=lambda: 40,
            timeout_seconds=0.01,
        )
        await broker.attach_browser(_employee(), "browser-a")
        response = await broker.request_permission(
            _employee(), _binding(), 1, object(), 3, _request(), permission_declared=True
        )
        assert response.outcome.outcome == "cancelled"
        assert publisher.requests[0].deadline_at == 40
        assert len(publisher.outcomes) == 1
        assert publisher.outcomes[0].cancellation_reason == "Permission request timed out"
        assert publisher.activities[-1].state == "thinking"
        late = await broker.respond_to_permission("browser-a", "request-timeout", "once")
        assert late.disposition == "already_settled"
        assert len(publisher.outcomes) == 1
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_cancelling_sdk_callback_task_does_not_cancel_actor_owned_settlement() -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "request-shielded",
            integer_now=lambda: 10,
            timeout_seconds=300,
        )
        await broker.attach_browser(_employee(), "browser-a")
        callback_task = asyncio.create_task(
            broker.request_permission(
                _employee(),
                _binding(),
                1,
                object(),
                2,
                _request(),
                permission_declared=True,
            )
        )
        while not publisher.requests:
            await asyncio.sleep(0)
        callback_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await callback_task
        result = await broker.respond_to_permission("browser-a", "request-shielded", "once")
        assert result.disposition == "selected"
        assert len(publisher.outcomes) == 1
        assert publisher.outcomes[0].response.outcome.outcome == "selected"
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_child_death_cause_is_generation_guarded_and_suppresses_restore() -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "request-death",
            integer_now=lambda: 10,
            timeout_seconds=300,
        )
        employee = _employee()
        binding = _binding()
        await broker.attach_browser(employee, "browser-a")
        pending = asyncio.create_task(
            broker.request_permission(
                employee,
                binding,
                1,
                object(),
                2,
                _request(),
                permission_declared=True,
            )
        )
        while not publisher.requests:
            await asyncio.sleep(0)
        await broker.cancel_child_generation(
            employee.employee_id, binding.binding_generation, 99, "stale death"
        )
        assert not pending.done()
        await broker.cancel_child_generation(
            employee.employee_id, binding.binding_generation, 1, "Child died"
        )
        response = await pending
        assert response.outcome.outcome == "cancelled"
        assert len(publisher.outcomes) == 1
        assert publisher.outcomes[0].cancellation_reason == "Child died"
        assert publisher.activities[-1].state == "waiting_for_permission"
        await broker.cancel_child_generation(
            employee.employee_id, binding.binding_generation, 1, "duplicate"
        )
        assert len(publisher.outcomes) == 1
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_same_session_id_different_employee_cannot_answer_request() -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: "request-b",
            integer_now=lambda: 10,
            timeout_seconds=300,
        )
        employee_b = _employee("employee-b")
        binding_b = _binding("employee-b")
        await broker.attach_browser(_employee(), "browser-a")
        await broker.attach_browser(employee_b, "browser-b")
        pending = asyncio.create_task(
            broker.request_permission(
                employee_b,
                binding_b,
                1,
                object(),
                1,
                _request(),
                permission_declared=True,
            )
        )
        while not publisher.requests:
            await asyncio.sleep(0)
        wrong = await broker.respond_to_permission("browser-a", "request-b", "once")
        assert wrong.disposition == "rejected"
        selected = await broker.respond_to_permission("browser-b", "request-b", "once")
        assert selected.disposition == "selected"
        await pending
        await broker.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


@pytest.mark.parametrize("cause", ["prompt_cancel", "new_conversation", "shutdown"])
def test_visible_terminal_causes_cancel_exactly_once(cause: str) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        identity = object()
        broker = ConversationPermissionBroker(
            publisher,
            request_id_factory=lambda: f"request-{cause}",
            integer_now=lambda: 10,
            timeout_seconds=300,
        )
        employee = _employee()
        binding = _binding()
        await broker.attach_browser(employee, "browser-a")
        pending = asyncio.create_task(
            broker.request_permission(
                employee,
                binding,
                1,
                identity,
                5,
                _request(),
                permission_declared=True,
            )
        )
        while not publisher.requests:
            await asyncio.sleep(0)
        if cause == "prompt_cancel":
            await broker.cancel_prompt_epoch(
                employee.employee_id,
                binding.binding_generation,
                1,
                identity,
                5,
                "Prompt cancelled",
            )
        elif cause == "new_conversation":
            await broker.cancel_binding(
                employee.employee_id,
                binding.binding_generation,
                "New conversation",
            )
        else:
            await broker.shutdown(asyncio.get_running_loop().time() + 1)
        response = await pending
        assert response.outcome.outcome == "cancelled"
        assert len(publisher.outcomes) == 1
        if cause != "shutdown":
            await broker.shutdown(asyncio.get_running_loop().time() + 1)
        assert len(publisher.outcomes) == 1

    asyncio.run(exercise())
