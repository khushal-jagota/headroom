from __future__ import annotations

import asyncio
import concurrent.futures
import json
import queue
import threading
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from acp.schema import (
    PermissionOption,
    RequestPermissionRequest,
    ToolCallUpdate,
)

from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.contracts import (
    ConversationEmployee,
    ConversationSessionBinding,
)
from planner.conversation.permission_broker import PendingPermissionSnapshot
from planner.conversation.runtime_ports import ConversationRuntimeHandle
from planner.conversation.sqlite_binding_repository import (
    SqliteConversationBindingRepository,
)
from planner.conversation.turn_broker import (
    ConversationTurnBrokerError,
    TrackedTurnHandle,
    TrackedTurnResult,
)
from planner.core.db import connect, create_schema
from planner.runtime.acp_step_gateway import AcpStepGateway
from planner.runtime.step_gateway import EmployeeStepGatewayBusy
from planner.worker_context.contracts import (
    PreparedWorkerPrompt,
    WorkerContextReceipt,
)
from planner.worker_types.configuration import PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS


class _LoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.started = threading.Event()
        self.owner_thread_id = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.started.wait(1)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.owner_thread_id = threading.get_ident()
        self.started.set()
        self.loop.run_forever()

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=1)
        self.loop.close()


class _Repository:
    def __init__(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding | None,
    ) -> None:
        self.employee = employee
        self.binding = binding
        self.resolve_calls = 0
        self.resolve_entered: threading.Event | None = None
        self.resolve_release: threading.Event | None = None

    async def resolve(self, employee_id: str) -> ConversationSessionBinding | None:
        assert employee_id == self.employee.employee_id
        self.resolve_calls += 1
        if self.resolve_entered is not None and self.resolve_release is not None:
            self.resolve_entered.set()
            released = await asyncio.to_thread(self.resolve_release.wait, 1)
            assert released
        return self.binding

    async def resolve_employee(self, employee_id: str) -> ConversationEmployee:
        assert employee_id == self.employee.employee_id
        return self.employee


class _Child:
    alive = True


class _Strategy:
    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def _runtime(
    tmp_path: Path,
) -> tuple[
    ConversationEmployee,
    ConversationSessionBinding,
    ConversationRuntimeHandle,
]:
    employee = ConversationEmployee(
        employee_id="t_gateway",
        entity_kind="ticket",
        entity_id="t_gateway",
        workspace_roots=(tmp_path,),
        backend_key="hermes",
    )
    binding = ConversationSessionBinding(
        employee_id=employee.employee_id,
        acp_session_id="session-gateway",
        backend_key="hermes",
        binding_generation=1,
    )
    definition = AgentBackendDefinition(
        backend_key="hermes",
        argv=("/tmp/hermes", "acp"),
        inherited_environment_names=(),
        environment_overrides=(),
        expected_agent_name="hermes-agent",
        expected_agent_version="0.18.2",
        turn_capabilities=BackendTurnCapabilities(True, True),
        reverse_service_capabilities=ReverseServiceCapabilities(False, False, True),
        working_directory_resolver=lambda value: value.workspace_roots[0],
        turn_strategy=_Strategy(),
    )
    handle = ConversationRuntimeHandle(
        employee=employee,
        binding=binding,
        child_generation=1,
        child=_Child(),  # type: ignore[arg-type]
        definition=definition,
        record_identity=object(),
    )
    return employee, binding, handle


class _Hub:
    def __init__(
        self,
        repository: Any,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        handle: ConversationRuntimeHandle,
    ) -> None:
        self.repository = repository
        self.employee = employee
        self.binding = binding
        self.handle = handle
        self.accepting = True
        self.ensure_calls = 0
        self.order: list[str] = []

    async def ensure_stream_ready(
        self, employee_id: str, binding: ConversationSessionBinding
    ) -> ConversationRuntimeHandle:
        assert (employee_id, binding) == (self.employee.employee_id, self.binding)
        self.ensure_calls += 1
        self.order.append("stream")
        return self.handle

    async def ensure_employee_stream(self, employee_id: str) -> Any:
        assert employee_id == self.employee.employee_id
        self.ensure_calls += 1
        self.order.append("stream")
        return self.employee, self.binding, self.handle

    def next_worker_client_message_id(self) -> str:
        return "worker-message"


class _FakeWorkerContextService:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.prepared_worker_prompt: PreparedWorkerPrompt | None = None
        self.prepare_calls: list[tuple[str, str]] = []
        self.prepare_thread_ids: list[int] = []
        self.acknowledge_calls: list[tuple[str, tuple[WorkerContextReceipt, ...]]] = []
        self.acknowledge_thread_ids: list[int] = []
        self.acknowledge_error: Exception | None = None

    def prepare(self, worker_entity_id: str, prompt_text: str) -> PreparedWorkerPrompt:
        self.order.append("prepare")
        self.prepare_calls.append((worker_entity_id, prompt_text))
        self.prepare_thread_ids.append(threading.get_ident())
        return self.prepared_worker_prompt or PreparedWorkerPrompt(prompt_text, ())

    def acknowledge(
        self,
        worker_entity_id: str,
        receipts: tuple[WorkerContextReceipt, ...],
    ) -> None:
        self.order.append("acknowledge")
        self.acknowledge_calls.append((worker_entity_id, receipts))
        self.acknowledge_thread_ids.append(threading.get_ident())
        if self.acknowledge_error is not None:
            raise self.acknowledge_error


class _Broker:
    def __init__(self, handle: ConversationRuntimeHandle, hub: _Hub) -> None:
        self.handle = handle
        self.hub = hub
        self.started = threading.Event()
        self.tracked: TrackedTurnHandle | None = None
        self.after: Any = None
        self.auto_complete = True
        self.busy = False
        self.rejection: str | None = None
        self.cancelled = 0
        self.cancel_started = threading.Event()
        self.cancel_never_completes = False
        self.prompt_epoch = 0
        self.prompts: list[Any] = []

    async def deliver_tracked_normal(
        self,
        handle: ConversationRuntimeHandle,
        client_message_id: str,
        prompt: Any,
        *,
        before_prompt_started: Any,
        after_prompt_settled: Any,
    ) -> TrackedTurnHandle:
        assert handle is self.handle
        if self.busy:
            raise ConversationTurnBrokerError("a prompt is already active")
        if self.rejection is not None:
            raise ConversationTurnBrokerError(self.rejection)
        completion: asyncio.Future[TrackedTurnResult] = asyncio.get_running_loop().create_future()
        self.prompt_epoch += 1
        tracked = TrackedTurnHandle(
            employee_id=handle.employee.employee_id,
            acp_session_id=handle.binding.acp_session_id,
            binding_generation=handle.binding.binding_generation,
            child_generation=handle.child_generation,
            record_identity=handle.record_identity,
            client_message_id=client_message_id,
            prompt_epoch=self.prompt_epoch,
            completion=completion,
        )
        before_prompt_started(tracked)
        self.hub.order.append("prompt")
        self.prompts.append(prompt)
        self.tracked = tracked
        self.after = after_prompt_settled
        self.started.set()
        if self.auto_complete:
            completion.set_result(TrackedTurnResult("complete"))
            after_prompt_settled(tracked)
        self.hub.order.append("delivery-return")
        return tracked

    async def cancel(self, handle: ConversationRuntimeHandle) -> None:
        assert handle is self.handle
        self.cancelled += 1
        self.cancel_started.set()
        if self.cancel_never_completes:
            await asyncio.Future()

    def complete(self, status: str = "complete", error: str | None = None) -> None:
        assert self.tracked is not None
        result = TrackedTurnResult(status, error=error)  # type: ignore[arg-type]
        self.tracked.completion.set_result(result)
        self.after(self.tracked)


def _gateway(
    tmp_path: Path,
    loop_thread: _LoopThread,
    *,
    binding: ConversationSessionBinding | None,
    callback_timeout_seconds: float = 30.0,
    backend_available: Any = None,
) -> tuple[
    AcpStepGateway,
    _Hub,
    _Broker,
    _Repository,
    _FakeWorkerContextService,
]:
    employee, actual_binding, handle = _runtime(tmp_path)
    repository = _Repository(employee, binding)
    hub = _Hub(repository, employee, actual_binding, handle)
    broker = _Broker(handle, hub)
    worker_context_service = _FakeWorkerContextService(hub.order)
    gateway = AcpStepGateway(
        hub=hub,  # type: ignore[arg-type]
        broker=broker,  # type: ignore[arg-type]
        loop=loop_thread.loop,
        owner_thread_id=loop_thread.owner_thread_id,
        db_path=str(tmp_path / "unused.db"),
        worker_context_service=worker_context_service,
        callback_timeout_seconds=callback_timeout_seconds,
        backend_available=backend_available,
    )
    return gateway, hub, broker, repository, worker_context_service


async def _call_step_gateway_on_owner_loop(
    gateway: AcpStepGateway,
    employee_id: str,
) -> Any:
    return gateway.run_ticket_step(None, employee_id, "owner-loop")


def test_require_existing_session_rejects_before_spawn_load_or_reset(
    tmp_path: Path,
) -> None:
    loop_thread = _LoopThread()
    try:
        gateway, hub, broker, repository, _worker_context_service = _gateway(
            tmp_path, loop_thread, binding=None
        )
        with pytest.raises(RuntimeError, match="missing or mismatched"):
            gateway.run_ticket_step(
                "stored-session",
                "t_gateway",
                "continue",
                require_existing_session=True,
            )
        assert repository.resolve_calls == 1
        assert hub.ensure_calls == 0
        assert broker.started.is_set() is False
    finally:
        loop_thread.close()


def test_worker_context_prepares_once_on_caller_thread_and_acknowledges_after_tracked_start(
    tmp_path: Path,
) -> None:
    loop_thread = _LoopThread()
    try:
        employee, binding, _handle = _runtime(tmp_path)
        gateway, hub, broker, _repository, worker_context_service = _gateway(
            tmp_path, loop_thread, binding=binding
        )
        caller_thread = threading.get_ident()
        callback_threads: list[int] = []
        receipts = (WorkerContextReceipt("ticket_changed", 7),)
        prepared_text = "work\n\n[Pending worker context]\n- Reread the ticket."
        worker_context_service.prepared_worker_prompt = PreparedWorkerPrompt(
            prepared_text, receipts
        )

        def callback(session_id: str) -> None:
            assert session_id == binding.acp_session_id
            callback_threads.append(threading.get_ident())
            hub.order.append("callback")

        result = gateway.run_ticket_step(
            binding.acp_session_id,
            employee.employee_id,
            "work",
            on_employee_session_id=callback,
            require_existing_session=True,
        )
        assert (
            result.status,
            result.employee_session_id,
            result.error,
        ) == ("complete", binding.acp_session_id, None)
        assert callback_threads == [caller_thread]
        assert worker_context_service.prepare_calls == [(employee.employee_id, "work")]
        assert worker_context_service.prepare_thread_ids == [caller_thread]
        assert len(broker.prompts) == 1
        assert broker.prompts[0].prompt[0].text == prepared_text
        assert worker_context_service.acknowledge_calls == [(employee.employee_id, receipts)]
        assert len(worker_context_service.acknowledge_thread_ids) == 1
        assert worker_context_service.acknowledge_thread_ids[0] != loop_thread.owner_thread_id
        assert hub.order == [
            "prepare",
            "stream",
            "callback",
            "prompt",
            "delivery-return",
            "acknowledge",
        ]

        owner_call = asyncio.run_coroutine_threadsafe(
            _call_step_gateway_on_owner_loop(gateway, employee.employee_id),
            loop_thread.loop,
        )
        with pytest.raises(RuntimeError, match="owner event-loop thread"):
            owner_call.result(timeout=1)
        assert worker_context_service.prepare_calls == [(employee.employee_id, "work")]

        loop_thread.close()
        with pytest.raises(RuntimeError, match="event loop is not running"):
            gateway.run_ticket_step(None, employee.employee_id, "stopped")
        assert worker_context_service.prepare_calls == [(employee.employee_id, "work")]
    finally:
        if not loop_thread.loop.is_closed():
            loop_thread.close()


@pytest.mark.parametrize(
    ("terminal_status", "terminal_error", "expected_status", "expected_error"),
    [
        ("complete", None, "complete", None),
        ("interrupted", None, "interrupted", None),
        ("errored", "ACP failed", "errored", "ACP failed"),
    ],
)
def test_terminal_result_maps_exactly_to_employee_step_result(
    tmp_path: Path,
    terminal_status: str,
    terminal_error: str | None,
    expected_status: str,
    expected_error: str | None,
) -> None:
    loop_thread = _LoopThread()
    try:
        employee, binding, _handle = _runtime(tmp_path)
        gateway, _hub, broker, _repository, _worker_context_service = _gateway(
            tmp_path, loop_thread, binding=binding
        )
        broker.auto_complete = False
        result_holder: list[Any] = []
        caller = threading.Thread(
            target=lambda: result_holder.append(
                gateway.run_ticket_step(None, employee.employee_id, "work")
            )
        )
        caller.start()
        assert broker.started.wait(1)
        loop_thread.loop.call_soon_threadsafe(broker.complete, terminal_status, terminal_error)
        caller.join(timeout=1)
        assert not caller.is_alive()
        result = result_holder[0]
        assert (
            result.status,
            result.employee_session_id,
            result.error,
        ) == (
            expected_status,
            binding.acp_session_id,
            expected_error,
        )
    finally:
        loop_thread.close()


def test_callback_failure_and_timeout_happen_before_prompt(tmp_path: Path) -> None:
    loop_thread = _LoopThread()
    try:
        employee, binding, _handle = _runtime(tmp_path)
        gateway, _hub, broker, _repository, _worker_context_service = _gateway(
            tmp_path,
            loop_thread,
            binding=binding,
            callback_timeout_seconds=0.01,
        )

        def fail_callback(_session_id: str) -> None:
            raise ValueError("callback failed")

        with pytest.raises(ValueError, match="callback failed"):
            gateway.run_ticket_step(
                binding.acp_session_id,
                employee.employee_id,
                "work",
                on_employee_session_id=fail_callback,
                require_existing_session=True,
            )
        assert not broker.started.is_set()

        callback_requests: queue.Queue[Any] = queue.Queue(maxsize=1)
        future = asyncio.run_coroutine_threadsafe(
            gateway._run_ticket_step(  # noqa: SLF001 - contract handshake timeout
                binding.acp_session_id,
                employee.employee_id,
                PreparedWorkerPrompt("work", ()),
                callback_requests,
                True,
                require_existing_session=True,
            ),
            loop_thread.loop,
        )
        with pytest.raises(TimeoutError, match="callback timed out"):
            future.result(timeout=1)
        assert not broker.started.is_set()
    finally:
        loop_thread.close()


@pytest.mark.parametrize(
    "failure_mode",
    [
        "missing_binding",
        "stale_binding",
        "callback_error",
        "callback_timeout",
        "busy",
        "rejected_admission",
    ],
)
def test_worker_context_remains_pending_before_tracked_admission(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    loop_thread = _LoopThread()
    try:
        employee, binding, _handle = _runtime(tmp_path)
        repository_binding: ConversationSessionBinding | None = binding
        if failure_mode == "missing_binding":
            repository_binding = None
        elif failure_mode == "stale_binding":
            repository_binding = ConversationSessionBinding(
                employee_id=employee.employee_id,
                acp_session_id="stale-session",
                backend_key=binding.backend_key,
                binding_generation=binding.binding_generation,
            )
        gateway, _hub, broker, _repository, worker_context_service = _gateway(
            tmp_path,
            loop_thread,
            binding=repository_binding,
            callback_timeout_seconds=0.01,
        )
        receipts = (WorkerContextReceipt("ticket_changed", 4),)
        worker_context_service.prepared_worker_prompt = PreparedWorkerPrompt(
            "prepared work", receipts
        )

        session_callback: Any = None
        require_existing_session = failure_mode in {
            "missing_binding",
            "stale_binding",
            "callback_error",
            "callback_timeout",
        }
        if failure_mode == "callback_error":

            def fail_callback(_session_id: str) -> None:
                raise ValueError("callback failed")

            session_callback = fail_callback
        elif failure_mode == "callback_timeout":

            def slow_callback(_session_id: str) -> None:
                threading.Event().wait(0.03)

            session_callback = slow_callback
        elif failure_mode == "busy":
            broker.busy = True
        elif failure_mode == "rejected_admission":
            broker.rejection = "rejected admission"

        expected_error = {
            "missing_binding": "missing or mismatched",
            "stale_binding": "missing or mismatched",
            "callback_error": "callback failed",
            "callback_timeout": "callback timed out",
            "busy": "session is busy",
            "rejected_admission": "rejected admission",
        }[failure_mode]
        with pytest.raises(Exception, match=expected_error):
            gateway.run_ticket_step(
                binding.acp_session_id,
                employee.employee_id,
                "work",
                on_employee_session_id=session_callback,
                require_existing_session=require_existing_session,
            )

        assert worker_context_service.prepare_calls == [(employee.employee_id, "work")]
        assert worker_context_service.acknowledge_calls == []
        assert broker.prompts == []
        assert broker.started.is_set() is False
    finally:
        loop_thread.close()


def test_worker_context_ack_failure_keeps_context_pending_without_cancelling_or_duplicating_turn(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    loop_thread = _LoopThread()
    try:
        employee, binding, _handle = _runtime(tmp_path)
        gateway, _hub, broker, _repository, worker_context_service = _gateway(
            tmp_path,
            loop_thread,
            binding=binding,
        )
        receipts = (WorkerContextReceipt("ticket_changed", 9),)
        prepared_text = "work\n\n[Pending worker context]\n- Still pending."
        worker_context_service.prepared_worker_prompt = PreparedWorkerPrompt(
            prepared_text, receipts
        )
        worker_context_service.acknowledge_error = RuntimeError("database unavailable")

        with caplog.at_level("ERROR", logger="planner.runtime.acp_step_gateway"):
            first = gateway.run_ticket_step(None, employee.employee_id, "work")

        assert first.status == "complete"
        assert len(broker.prompts) == 1
        assert broker.cancelled == 0
        assert "worker context acknowledgement failed after ACP admission" in caplog.text

        second = gateway.run_ticket_step(None, employee.employee_id, "work")

        assert second.status == "complete"
        assert worker_context_service.prepare_calls == [
            (employee.employee_id, "work"),
            (employee.employee_id, "work"),
        ]
        assert worker_context_service.acknowledge_calls == [
            (employee.employee_id, receipts),
            (employee.employee_id, receipts),
        ]
        assert [prompt.prompt[0].text for prompt in broker.prompts] == [
            prepared_text,
            prepared_text,
        ]
        assert broker.cancelled == 0
    finally:
        loop_thread.close()


def test_busy_maps_to_existing_shared_gateway_busy(tmp_path: Path) -> None:
    loop_thread = _LoopThread()
    try:
        employee, binding, _handle = _runtime(tmp_path)
        gateway, _hub, broker, _repository, _worker_context_service = _gateway(
            tmp_path, loop_thread, binding=binding
        )
        broker.busy = True
        with pytest.raises(EmployeeStepGatewayBusy) as raised:
            gateway.run_ticket_step(None, employee.employee_id, "work")
        assert raised.value.employee_session_id == binding.acp_session_id
    finally:
        loop_thread.close()


def test_interrupt_targets_only_current_binding_and_honours_deadline(
    tmp_path: Path,
) -> None:
    loop_thread = _LoopThread()
    try:
        employee, binding, _handle = _runtime(tmp_path)
        gateway, _hub, broker, _repository, _worker_context_service = _gateway(
            tmp_path, loop_thread, binding=binding
        )
        broker.auto_complete = False
        result_holder: list[Any] = []
        caller = threading.Thread(
            target=lambda: result_holder.append(
                gateway.run_ticket_step(None, employee.employee_id, "work")
            )
        )
        caller.start()
        assert broker.started.wait(1)

        gateway.interrupt("wrong-session", employee.employee_id)
        gateway.interrupt(binding.acp_session_id, "wrong-employee")
        assert broker.cancelled == 0
        gateway.interrupt(binding.acp_session_id, employee.employee_id)
        assert broker.cancelled == 1

        broker.cancel_never_completes = True
        with pytest.raises(concurrent.futures.TimeoutError):
            gateway.interrupt(
                binding.acp_session_id,
                employee.employee_id,
                deadline=monotonic() + 0.05,
            )
        assert broker.cancelled == 2
        loop_thread.loop.call_soon_threadsafe(broker.complete, "interrupted")
        caller.join(timeout=1)
        assert not caller.is_alive()
        assert result_holder[0].status == "interrupted"
    finally:
        loop_thread.close()


def test_late_interrupt_does_not_cancel_successor_turn(tmp_path: Path) -> None:
    loop_thread = _LoopThread()
    try:
        employee, binding, _handle = _runtime(tmp_path)
        gateway, _hub, broker, repository, _worker_context_service = _gateway(
            tmp_path, loop_thread, binding=binding
        )
        broker.auto_complete = False
        first_result: list[Any] = []
        first = threading.Thread(
            target=lambda: first_result.append(
                gateway.run_ticket_step(None, employee.employee_id, "first")
            )
        )
        first.start()
        assert broker.started.wait(1)

        repository.resolve_entered = threading.Event()
        repository.resolve_release = threading.Event()
        interrupt = threading.Thread(
            target=lambda: gateway.interrupt(binding.acp_session_id, employee.employee_id)
        )
        interrupt.start()
        assert repository.resolve_entered.wait(1)

        loop_thread.loop.call_soon_threadsafe(broker.complete)
        first.join(timeout=1)
        assert not first.is_alive()
        broker.started.clear()
        second_result: list[Any] = []
        second = threading.Thread(
            target=lambda: second_result.append(
                gateway.run_ticket_step(None, employee.employee_id, "second")
            )
        )
        second.start()
        assert broker.started.wait(1)
        repository.resolve_release.set()
        interrupt.join(timeout=1)
        assert not interrupt.is_alive()
        assert broker.cancelled == 0

        loop_thread.loop.call_soon_threadsafe(broker.complete)
        second.join(timeout=1)
        assert not second.is_alive()
        assert second_result[0].status == "complete"
    finally:
        loop_thread.close()


def test_status_is_observational_and_never_spawns(tmp_path: Path) -> None:
    loop_thread = _LoopThread()
    try:
        _employee, binding, _handle = _runtime(tmp_path)
        gateway, hub, broker, _repository, _worker_context_service = _gateway(
            tmp_path,
            loop_thread,
            binding=binding,
            backend_available=lambda: False,
        )
        assert gateway.status().available is False
        hub.accepting = False
        assert gateway.status().available is False
        assert hub.ensure_calls == 0
        assert not broker.started.is_set()
    finally:
        loop_thread.close()


def _seed_guard_database(
    tmp_path: Path,
) -> tuple[str, SqliteConversationBindingRepository, ConversationSessionBinding]:
    db_path = str(tmp_path / "guard.db")
    conn = connect(db_path)
    create_schema(conn)
    fields = {
        field: {"value": None, "proposal": None, "user_note": None}
        for field in ("kickoff", "success", "approach", "plan", "implementation", "closeout")
    }
    conn.execute(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, stage, ceiling, fields, "
        "ticket_status, created_at, updated_at) "
        "VALUES ('t_gateway', 'Gateway', 'coding', 'hermes', 'needs_kickoff', 'needs_kickoff', ?, "
        "'agent_running_step', 1, 1)",
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
        employee_id="t_gateway",
        acp_session_id="session-gateway",
        backend_key="hermes",
        binding_generation=1,
    )
    asyncio.run(repository.compare_and_swap(None, binding))
    return db_path, repository, binding


def test_worker_permission_guard_proves_binding_ticket_turn_and_active_epoch(
    tmp_path: Path,
) -> None:
    db_path, repository, binding = _seed_guard_database(tmp_path)
    loop_thread = _LoopThread()
    try:
        employee, _binding, handle = _runtime(tmp_path)
        hub = _Hub(repository, employee, binding, handle)
        broker = _Broker(handle, hub)
        broker.auto_complete = False
        gateway = AcpStepGateway(
            hub=hub,  # type: ignore[arg-type]
            broker=broker,  # type: ignore[arg-type]
            loop=loop_thread.loop,
            owner_thread_id=loop_thread.owner_thread_id,
            db_path=db_path,
            worker_context_service=_FakeWorkerContextService(hub.order),
        )

        def callback(session_id: str) -> None:
            conn = connect(db_path)
            conn.execute(
                "INSERT INTO employee_step_runs "
                "(employee_step_id, ticket_id, status, employee_session_id, "
                "started_at, updated_at) VALUES "
                "('step-worker', 't_gateway', 'running', ?, 3, 3)",
                (session_id,),
            )
            conn.close()

        result_holder: list[Any] = []
        caller = threading.Thread(
            target=lambda: result_holder.append(
                gateway.run_ticket_step(
                    binding.acp_session_id,
                    "t_gateway",
                    "work",
                    on_employee_session_id=callback,
                    require_existing_session=True,
                )
            )
        )
        caller.start()
        assert broker.started.wait(1)
        assert broker.tracked is not None
        marked: list[bool] = []
        request = RequestPermissionRequest(
            session_id=binding.acp_session_id,
            tool_call=ToolCallUpdate(
                session_update="tool_call",
                tool_call_id="tool",
                title="Tool",
                status="pending",
            ),
            options=[PermissionOption(option_id="once", name="Allow", kind="allow_once")],
        )
        snapshot = PendingPermissionSnapshot(
            request_id="permission",
            employee=employee,
            binding=binding,
            child_generation=handle.child_generation,
            record_identity=handle.record_identity,
            prompt_epoch=broker.tracked.prompt_epoch,
            origin="worker",
            request=request,
            settling=False,
        )
        corrupt = connect(db_path)
        corrupt.execute(
            "UPDATE tickets SET employee_backend = 'other-backend' WHERE id = 't_gateway'"
        )
        corrupt.close()
        assert (
            gateway.guard_worker_permission_settlement(snapshot, lambda: marked.append(True))
            is False
        )
        assert marked == []
        restored = connect(db_path)
        restored.execute("UPDATE tickets SET employee_backend = 'hermes' WHERE id = 't_gateway'")
        restored.close()
        assert gateway.guard_worker_permission_settlement(snapshot, lambda: marked.append(True))
        assert marked == [True]
        loop_thread.loop.call_soon_threadsafe(broker.complete)
        caller.join(timeout=1)
        assert not caller.is_alive()
        assert result_holder[0].status == "complete"
    finally:
        loop_thread.close()
