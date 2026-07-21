"""Synchronous EmployeeStepRunner bridge onto the event-loop-owned ACP hub."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic

from acp.schema import PromptRequest, TextContentBlock

from planner.conversation.contracts import ConversationSessionBinding
from planner.conversation.hub import ConversationHub
from planner.conversation.permission_broker import PendingPermissionSnapshot
from planner.conversation.runtime_ports import ConversationRuntimeHandle
from planner.conversation.turn_broker import (
    ConversationTurnBroker,
    ConversationTurnBrokerError,
    TrackedTurnHandle,
)
from planner.core.db import connect
from planner.runtime.employee_step_repository import SqliteEmployeeStepRepository
from planner.runtime.step_gateway import EmployeeStepGatewayBusy, EmployeeStepRunResult
from planner.worker_context.contracts import PreparedWorkerPrompt, WorkerContextService

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AcpStepGatewayStatus:
    available: bool


@dataclass(slots=True)
class _CallbackHandshake:
    session_id: str
    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class _ActiveWorkerStep:
    entity_id: str
    binding: ConversationSessionBinding
    runtime_handle: ConversationRuntimeHandle
    tracked_handle: TrackedTurnHandle


class AcpStepGateway:
    """A caller-thread callback handshake around one hub-owned tracked ACP turn."""

    def __init__(
        self,
        *,
        hub: ConversationHub,
        broker: ConversationTurnBroker,
        loop: asyncio.AbstractEventLoop,
        owner_thread_id: int,
        db_path: str,
        worker_context_service: WorkerContextService,
        busy_timeout_ms: int = 5000,
        callback_timeout_seconds: float = 30.0,
        backend_available: Callable[[], bool] | None = None,
    ) -> None:
        if callback_timeout_seconds <= 0:
            raise ValueError("callback_timeout_seconds must be positive")
        self._hub = hub
        self._broker = broker
        self._loop = loop
        self._owner_thread_id = owner_thread_id
        self._db_path = db_path
        self._worker_context_service = worker_context_service
        self._busy_timeout_ms = busy_timeout_ms
        self._callback_timeout_seconds = callback_timeout_seconds
        self._backend_available = backend_available or (lambda: True)
        self._active_lock = threading.Lock()
        self._active: dict[str, _ActiveWorkerStep] = {}

    def run_ticket_step(
        self,
        employee_session_id: str | None,
        entity_id: str,
        prompt_text: str,
        on_employee_session_id: Callable[[str], None] | None = None,
        *,
        require_existing_session: bool = False,
    ) -> EmployeeStepRunResult:
        self._require_caller_thread()
        prepared_worker_prompt = self._worker_context_service.prepare(entity_id, prompt_text)
        callback_requests: queue.Queue[_CallbackHandshake] = queue.Queue(maxsize=1)
        future = asyncio.run_coroutine_threadsafe(
            self._run_ticket_step(
                employee_session_id,
                entity_id,
                prepared_worker_prompt,
                callback_requests,
                on_employee_session_id is not None,
                require_existing_session=require_existing_session,
            ),
            self._loop,
        )
        while not future.done():
            try:
                handshake = callback_requests.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                assert on_employee_session_id is not None
                on_employee_session_id(handshake.session_id)
            except BaseException as error:
                handshake.error = error
            finally:
                handshake.done.set()
        return future.result()

    def interrupt(
        self,
        employee_session_id: str,
        entity_id: str,
        *,
        deadline: float | None = None,
    ) -> None:
        self._require_caller_thread()
        future = asyncio.run_coroutine_threadsafe(
            self._interrupt(employee_session_id, entity_id), self._loop
        )
        timeout = None if deadline is None else max(0.0, deadline - monotonic())
        try:
            future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def status(self) -> AcpStepGatewayStatus:
        available = self._loop.is_running() and self._hub.accepting and self._backend_available()
        return AcpStepGatewayStatus(available)

    def guard_worker_permission_settlement(
        self,
        snapshot: PendingPermissionSnapshot,
        mark_settling: Callable[[], None],
    ) -> bool:
        """Validate and mark one worker permission while BEGIN IMMEDIATE is held."""

        if snapshot.origin != "worker" or snapshot.employee.entity_kind != "ticket":
            return False
        with self._active_lock:
            active = self._active.get(snapshot.employee.employee_id)
        if active is None or not self._snapshot_matches_active(snapshot, active):
            return False
        conn = connect(self._db_path, self._busy_timeout_ms)
        conn.execute("BEGIN IMMEDIATE")
        try:
            binding_row = conn.execute(
                "SELECT acp_session_id, backend_key, binding_generation "
                "FROM conversation_session_bindings WHERE employee_id = ? "
                "AND entity_kind = 'ticket' AND entity_id = ?",
                (snapshot.employee.employee_id, snapshot.employee.employee_id),
            ).fetchone()
            ticket_row = conn.execute(
                "SELECT employee_session_id, employee_backend, ticket_status "
                "FROM tickets WHERE id = ?",
                (snapshot.employee.employee_id,),
            ).fetchone()
            running_step = SqliteEmployeeStepRepository().read_running(
                conn, snapshot.employee.employee_id
            )
            valid = (
                binding_row is not None
                and ticket_row is not None
                and running_step is not None
                and str(binding_row["acp_session_id"]) == snapshot.binding.acp_session_id
                and str(binding_row["backend_key"]) == snapshot.binding.backend_key
                and int(binding_row["binding_generation"]) == snapshot.binding.binding_generation
                and ticket_row["employee_session_id"] == snapshot.binding.acp_session_id
                and ticket_row["employee_backend"] == snapshot.binding.backend_key
                and ticket_row["ticket_status"] == "agent_running_step"
                and running_step.employee_session_id == snapshot.binding.acp_session_id
            )
            with self._active_lock:
                still_active = self._active.get(snapshot.employee.employee_id)
            if not valid or still_active is not active:
                conn.execute("ROLLBACK")
                return False
            mark_settling()
            conn.execute("COMMIT")
            return True
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    async def _run_ticket_step(
        self,
        employee_session_id: str | None,
        entity_id: str,
        prepared_worker_prompt: PreparedWorkerPrompt,
        callback_requests: queue.Queue[_CallbackHandshake],
        callback_required: bool,
        *,
        require_existing_session: bool,
    ) -> EmployeeStepRunResult:
        if require_existing_session:
            binding = await self._hub.repository.resolve(entity_id)
            if (
                binding is None
                or employee_session_id is None
                or binding.acp_session_id != employee_session_id
            ):
                raise RuntimeError("existing ACP session binding is missing or mismatched")
            employee = await self._hub.repository.resolve_employee(entity_id)
            runtime_handle = await self._hub.ensure_stream_ready(entity_id, binding)
        else:
            employee, binding, runtime_handle = await self._hub.ensure_employee_stream(entity_id)
        del employee
        if callback_required:
            handshake = _CallbackHandshake(binding.acp_session_id)
            callback_requests.put_nowait(handshake)
            acknowledged = await asyncio.to_thread(
                handshake.done.wait, self._callback_timeout_seconds
            )
            if not acknowledged:
                raise TimeoutError("Employee session callback timed out")
            if handshake.error is not None:
                raise handshake.error

        client_message_id = self._hub.next_worker_client_message_id()

        def before_prompt_started(tracked: TrackedTurnHandle) -> None:
            active = _ActiveWorkerStep(
                entity_id=entity_id,
                binding=binding,
                runtime_handle=runtime_handle,
                tracked_handle=tracked,
            )
            with self._active_lock:
                if entity_id in self._active:
                    raise ConversationTurnBrokerError(
                        "a worker turn is already active for this employee"
                    )
                self._active[entity_id] = active

        def after_prompt_settled(tracked: TrackedTurnHandle) -> None:
            with self._active_lock:
                active = self._active.get(entity_id)
                if active is not None and active.tracked_handle is tracked:
                    self._active.pop(entity_id, None)

        try:
            tracked = await self._broker.deliver_tracked_normal(
                runtime_handle,
                client_message_id,
                PromptRequest(
                    session_id=binding.acp_session_id,
                    prompt=[
                        TextContentBlock(
                            type="text",
                            text=prepared_worker_prompt.model_text,
                        )
                    ],
                ),
                before_prompt_started=before_prompt_started,
                after_prompt_settled=after_prompt_settled,
            )
        except ConversationTurnBrokerError as error:
            if "already active" in str(error):
                raise EmployeeStepGatewayBusy(binding.acp_session_id) from error
            raise
        if prepared_worker_prompt.receipts:
            try:
                await asyncio.to_thread(
                    self._worker_context_service.acknowledge,
                    entity_id,
                    prepared_worker_prompt.receipts,
                )
            except Exception:
                _log.exception(
                    "worker context acknowledgement failed after ACP admission",
                    extra={"ticket_id": entity_id},
                )
        terminal = await tracked.completion
        if terminal.status == "complete":
            return EmployeeStepRunResult("complete", binding.acp_session_id, None)
        if terminal.status == "interrupted":
            return EmployeeStepRunResult("interrupted", binding.acp_session_id, None)
        return EmployeeStepRunResult(
            "errored",
            binding.acp_session_id,
            terminal.error or "ACP worker turn failed",
        )

    async def _interrupt(self, employee_session_id: str, entity_id: str) -> None:
        with self._active_lock:
            active = self._active.get(entity_id)
        if active is None or active.binding.acp_session_id != employee_session_id:
            return
        durable = await self._hub.repository.resolve(entity_id)
        if durable != active.binding:
            return
        with self._active_lock:
            if self._active.get(entity_id) is not active:
                return
        await self._broker.cancel(active.runtime_handle)

    def _require_caller_thread(self) -> None:
        if threading.get_ident() == self._owner_thread_id:
            raise RuntimeError("ACP step gateway cannot block its owner event-loop thread")
        if not self._loop.is_running():
            raise RuntimeError("ACP conversation event loop is not running")

    @staticmethod
    def _snapshot_matches_active(
        snapshot: PendingPermissionSnapshot, active: _ActiveWorkerStep
    ) -> bool:
        tracked = active.tracked_handle
        return (
            snapshot.employee.employee_id == active.entity_id
            and snapshot.binding == active.binding
            and snapshot.child_generation == tracked.child_generation
            and snapshot.record_identity is tracked.record_identity
            and snapshot.prompt_epoch == tracked.prompt_epoch
            and snapshot.request.session_id == active.binding.acp_session_id
        )
