"""Transient, attached-browser-owned ACP permission settlement."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    RequestPermissionRequest,
    RequestPermissionResponse,
)

from .configuration import CONVERSATION_PERMISSION_RESPONSE_TIMEOUT_SECONDS
from .contracts import (
    ConversationActivityState,
    ConversationEmployee,
    ConversationSessionBinding,
)
from .runtime_event_publisher import ConversationRuntimeEventPublisher

type PermissionResponseDisposition = Literal["selected", "already_settled", "rejected"]
type PermissionPublicationFailureCallback = Callable[
    [
        ConversationEmployee,
        ConversationSessionBinding,
        int,
        object,
        int,
        Exception,
    ],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class PermissionResponseResult:
    disposition: PermissionResponseDisposition


@dataclass(frozen=True, slots=True)
class PendingPermissionSnapshot:
    request_id: str
    employee: ConversationEmployee
    binding: ConversationSessionBinding
    child_generation: int
    record_identity: object
    prompt_epoch: int
    origin: Literal["browser", "worker"]
    request: RequestPermissionRequest
    settling: bool


type WorkerPermissionSettlementGuard = Callable[
    [PendingPermissionSnapshot, Callable[[], None]], bool
]


@dataclass(slots=True)
class _PendingPermission:
    request_id: str
    employee: ConversationEmployee
    binding: ConversationSessionBinding
    child_generation: int
    record_identity: object
    prompt_epoch: int
    request: RequestPermissionRequest
    future: asyncio.Future[RequestPermissionResponse]
    opened: asyncio.Event
    origin: Literal["browser", "worker"]
    settling: bool = False
    visible: bool = False
    timeout_task: asyncio.Task[None] | None = None


class ConversationPermissionBroker:
    def __init__(
        self,
        publisher: ConversationRuntimeEventPublisher,
        *,
        request_id_factory: Callable[[], str],
        integer_now: Callable[[], int],
        timeout_seconds: float = CONVERSATION_PERMISSION_RESPONSE_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._publisher = publisher
        self._request_id_factory = request_id_factory
        self._integer_now = integer_now
        self._timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()
        self._connection_employee: dict[str, str] = {}
        self._employee_connections: dict[str, set[str]] = {}
        self._pending: dict[str, _PendingPermission] = {}
        self._settled_request_ids: set[str] = set()
        self._closed_sources: set[tuple[str, int, int, int, int]] = set()
        self._settlement_tasks: set[asyncio.Task[None]] = set()
        self._failure_tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        self._shutdown_task: asyncio.Task[None] | None = None
        self._publication_failure_callback: (
            PermissionPublicationFailureCallback | None
        ) = None
        self._worker_settlement_guard: WorkerPermissionSettlementGuard | None = None

    def set_publication_failure_callback(
        self, callback: PermissionPublicationFailureCallback
    ) -> None:
        current = self._publication_failure_callback
        if current is not None and current != callback:
            raise RuntimeError("permission publication failure callback is already set")
        self._publication_failure_callback = callback

    def set_worker_settlement_guard(
        self, guard: WorkerPermissionSettlementGuard
    ) -> None:
        current = self._worker_settlement_guard
        if current is not None and current != guard:
            raise RuntimeError("worker permission settlement guard is already set")
        self._worker_settlement_guard = guard

    async def attach_browser(
        self, employee: ConversationEmployee, browser_connection_id: str
    ) -> None:
        if not browser_connection_id:
            raise ValueError("browser_connection_id must not be empty")
        async with self._lock:
            if self._closing:
                raise RuntimeError("permission broker is closing")
            current = self._connection_employee.get(browser_connection_id)
            if current is not None and current != employee.employee_id:
                raise ValueError("browser connection is already attached to another employee")
            self._connection_employee[browser_connection_id] = employee.employee_id
            self._employee_connections.setdefault(employee.employee_id, set()).add(
                browser_connection_id
            )

    async def detach_browser(self, browser_connection_id: str) -> None:
        to_cancel: list[_PendingPermission] = []
        async with self._lock:
            employee_id = self._connection_employee.pop(browser_connection_id, None)
            if employee_id is None:
                return
            connections = self._employee_connections.get(employee_id)
            if connections is not None:
                connections.discard(browser_connection_id)
                if not connections:
                    self._employee_connections.pop(employee_id, None)
                    to_cancel = [
                        pending
                        for pending in self._pending.values()
                        if pending.employee.employee_id == employee_id and not pending.settling
                    ]
                    for pending in to_cancel:
                        pending.settling = True
        for pending in to_cancel:
            self._schedule_settlement(
                pending,
                RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled")),
                "No browser is attached",
            )

    async def request_permission(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        child_generation: int,
        record_identity: object,
        prompt_epoch: int,
        request: RequestPermissionRequest,
        *,
        permission_declared: bool,
        origin: Literal["browser", "worker"] = "browser",
    ) -> RequestPermissionResponse:
        cancelled = RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        source = self._source_key(
            employee.employee_id,
            binding.binding_generation,
            child_generation,
            record_identity,
            prompt_epoch,
        )
        async with self._lock:
            if (
                self._closing
                or not permission_declared
                or request.session_id != binding.acp_session_id
                or binding.employee_id != employee.employee_id
                or source in self._closed_sources
                or not self._employee_connections.get(employee.employee_id)
            ):
                return cancelled
            request_id = self._request_id_factory()
            if (
                not request_id
                or request_id in self._pending
                or request_id in self._settled_request_ids
            ):
                raise RuntimeError("permission request ID is not unique")
            pending = _PendingPermission(
                request_id=request_id,
                employee=employee,
                binding=binding,
                child_generation=child_generation,
                record_identity=record_identity,
                prompt_epoch=prompt_epoch,
                request=request,
                future=asyncio.get_running_loop().create_future(),
                opened=asyncio.Event(),
                origin=origin,
            )
            self._pending[request_id] = pending

        deadline_at = self._integer_now() + int(self._timeout_seconds)
        try:
            await self._publisher.publish_permission_request(
                employee,
                binding,
                request_id,
                employee.backend_key,
                request,
                deadline_at,
            )
            pending.visible = True
            await self._publisher.publish_activity(
                employee,
                binding,
                "waiting_for_permission",
                "Employee is waiting for permission",
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with self._lock:
                if self._pending.get(request_id) is pending:
                    self._pending.pop(request_id, None)
                    self._settled_request_ids.add(request_id)
            pending.opened.set()
            if not pending.future.done():
                pending.future.set_result(cancelled)
            self._schedule_publication_failure(pending, error)
            raise
        pending.opened.set()
        async with self._lock:
            if not pending.settling:
                pending.timeout_task = asyncio.create_task(
                    self._timeout(pending),
                    name=f"panels.acp.permission-timeout.{request_id}",
                )
        return await asyncio.shield(pending.future)

    async def respond_to_permission(
        self,
        browser_connection_id: str,
        request_id: str,
        option_id: str,
    ) -> PermissionResponseResult:
        async with self._lock:
            employee_id = self._connection_employee.get(browser_connection_id)
            if employee_id is None:
                return PermissionResponseResult("rejected")
            pending = self._pending.get(request_id)
            if pending is None:
                if request_id in self._settled_request_ids:
                    return PermissionResponseResult("already_settled")
                return PermissionResponseResult("rejected")
            if pending.employee.employee_id != employee_id:
                return PermissionResponseResult("rejected")
            if pending.settling:
                return PermissionResponseResult("already_settled")
            if not any(option.option_id == option_id for option in pending.request.options):
                return PermissionResponseResult("rejected")
            if pending.origin == "worker":
                guard = self._worker_settlement_guard
                if guard is None:
                    return PermissionResponseResult("rejected")
                marked = False

                def mark_settling() -> None:
                    nonlocal marked
                    if pending.settling:
                        raise RuntimeError("permission request is already settling")
                    pending.settling = True
                    marked = True

                if not guard(self._snapshot(pending), mark_settling):
                    if marked:
                        raise RuntimeError(
                            "worker permission guard marked a rejected request"
                        )
                    return PermissionResponseResult("rejected")
                if not marked:
                    raise RuntimeError(
                        "worker permission guard returned success without marking settlement"
                    )
            else:
                pending.settling = True
        await self._settle(
            pending,
            RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=option_id)
            ),
            None,
        )
        return PermissionResponseResult("selected")

    async def pending_snapshot(
        self, employee_id: str | None = None
    ) -> tuple[PendingPermissionSnapshot, ...]:
        async with self._lock:
            return tuple(
                self._snapshot(pending)
                for pending in self._pending.values()
                if employee_id is None or pending.employee.employee_id == employee_id
            )

    async def cancel_prompt_epoch(
        self,
        employee_id: str,
        binding_generation: int,
        child_generation: int,
        record_identity: object,
        prompt_epoch: int,
        reason: str,
    ) -> None:
        source = self._source_key(
            employee_id,
            binding_generation,
            child_generation,
            record_identity,
            prompt_epoch,
        )
        async with self._lock:
            self._closed_sources.add(source)
            pending = [
                item
                for item in self._pending.values()
                if self._pending_source(item) == source and not item.settling
            ]
            for item in pending:
                item.settling = True
        await self._settle_many(pending, reason, restore_activity=False)

    async def cancel_child_generation(
        self,
        employee_id: str,
        binding_generation: int,
        child_generation: int,
        reason: str,
        deadline: float | None = None,
    ) -> None:
        async with self._lock:
            pending = [
                item
                for item in self._pending.values()
                if item.employee.employee_id == employee_id
                and item.binding.binding_generation == binding_generation
                and item.child_generation == child_generation
                and not item.settling
            ]
            for item in pending:
                item.settling = True
                self._closed_sources.add(self._pending_source(item))
        await self._settle_many(
            pending,
            reason,
            restore_activity=False,
            deadline=deadline,
        )

    async def cancel_binding(
        self,
        employee_id: str,
        binding_generation: int,
        reason: str,
        deadline: float | None = None,
    ) -> None:
        async with self._lock:
            pending = [
                item
                for item in self._pending.values()
                if item.employee.employee_id == employee_id
                and item.binding.binding_generation == binding_generation
                and not item.settling
            ]
            for item in pending:
                item.settling = True
                self._closed_sources.add(self._pending_source(item))
        await self._settle_many(
            pending,
            reason,
            restore_activity=False,
            deadline=deadline,
        )

    async def shutdown(self, deadline: float) -> None:
        async with self._lock:
            task = self._shutdown_task
            if task is None:
                self._closing = True
                pending = [item for item in self._pending.values() if not item.settling]
                for item in pending:
                    item.settling = True
                    self._closed_sources.add(self._pending_source(item))
                task = asyncio.create_task(
                    self._shutdown_owned(pending, deadline),
                    name="panels.acp.permissions.shutdown",
                )
                self._shutdown_task = task
        await asyncio.shield(task)

    async def _shutdown_owned(
        self, pending: list[_PendingPermission], deadline: float
    ) -> None:
        tasks = [
            asyncio.create_task(
                self._settle(
                    item,
                    RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled")),
                    "Conversation service is shutting down",
                    restore_activity=False,
                )
            )
            for item in pending
        ]
        tasks.extend(self._settlement_tasks)
        tasks.extend(self._failure_tasks)
        if not tasks:
            self._force_dispose_pending()
            return
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, still_pending = await asyncio.wait(tasks, timeout=remaining)
        for task in done:
            if task.cancelled():
                continue
            try:
                task.result()
            except Exception:
                continue
        for task in still_pending:
            task.cancel()
        self._force_dispose_pending()
        if still_pending:
            raise TimeoutError("permission settlement exceeded its shutdown deadline")

    async def _timeout(self, pending: _PendingPermission) -> None:
        await asyncio.sleep(self._timeout_seconds)
        async with self._lock:
            if self._pending.get(pending.request_id) is not pending or pending.settling:
                return
            pending.settling = True
        await self._settle(
            pending,
            RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled")),
            "Permission request timed out",
        )

    async def _settle_many(
        self,
        pending_items: list[_PendingPermission],
        reason: str,
        *,
        restore_activity: bool,
        deadline: float | None = None,
    ) -> None:
        tasks = {
            asyncio.create_task(
                self._settle(
                    pending,
                    RequestPermissionResponse(
                        outcome=DeniedOutcome(outcome="cancelled")
                    ),
                    reason,
                    restore_activity=restore_activity,
                ),
                name=f"panels.acp.permission-settle.{pending.request_id}",
            ): pending
            for pending in pending_items
        }
        if not tasks:
            return
        self._settlement_tasks.update(tasks)
        for task in tasks:
            task.add_done_callback(self._settlement_finished)
        if deadline is None:
            await asyncio.gather(*tasks)
            return
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, still_pending = await asyncio.wait(tasks, timeout=remaining)
        for completed in done:
            completed.result()
        if not still_pending:
            return
        for task in still_pending:
            task.cancel()
            self._settlement_tasks.discard(task)
        self._force_dispose_records(
            tuple(tasks[task] for task in still_pending)
        )
        raise TimeoutError("permission settlement exceeded its deadline")

    def _schedule_settlement(
        self,
        pending: _PendingPermission,
        response: RequestPermissionResponse,
        reason: str | None,
    ) -> None:
        task = asyncio.create_task(
            self._settle(pending, response, reason),
            name=f"panels.acp.permission-settle.{pending.request_id}",
        )
        self._settlement_tasks.add(task)
        task.add_done_callback(self._settlement_finished)

    def _settlement_finished(self, task: asyncio.Task[None]) -> None:
        self._settlement_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            return

    async def _settle(
        self,
        pending: _PendingPermission,
        response: RequestPermissionResponse,
        cancellation_reason: str | None,
        *,
        restore_activity: bool = True,
    ) -> None:
        await pending.opened.wait()
        if not pending.visible:
            return
        if pending.timeout_task is not None and pending.timeout_task is not asyncio.current_task():
            pending.timeout_task.cancel()
        outcome_error: Exception | None = None
        try:
            await self._publisher.publish_permission_outcome(
                pending.employee,
                pending.binding,
                pending.request_id,
                response,
                cancellation_reason,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            outcome_error = error
            response = RequestPermissionResponse(
                outcome=DeniedOutcome(outcome="cancelled")
            )
        async with self._lock:
            if self._pending.get(pending.request_id) is not pending:
                if outcome_error is not None:
                    raise outcome_error
                return
            self._pending.pop(pending.request_id, None)
            self._settled_request_ids.add(pending.request_id)
        if not pending.future.done():
            pending.future.set_result(response)
        if outcome_error is not None:
            self._schedule_publication_failure(pending, outcome_error)
            raise outcome_error

        if not restore_activity:
            return
        activity_state: ConversationActivityState = (
            "thinking" if pending.prompt_epoch > 0 else "idle"
        )
        detail = (
            "Employee is responding"
            if activity_state == "thinking"
            else "Employee is ready"
        )
        try:
            await self._publisher.publish_activity(
                pending.employee, pending.binding, activity_state, detail
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._schedule_publication_failure(pending, error)
            raise

    def _schedule_publication_failure(
        self, pending: _PendingPermission, error: Exception
    ) -> None:
        callback = self._publication_failure_callback
        if callback is None:
            return

        async def notify() -> None:
            await callback(
                pending.employee,
                pending.binding,
                pending.child_generation,
                pending.record_identity,
                pending.prompt_epoch,
                error,
            )

        task = asyncio.create_task(
            notify(),
            name=f"panels.acp.permission-publication-failure.{pending.request_id}",
        )
        self._failure_tasks.add(task)
        task.add_done_callback(self._failure_notification_finished)

    def _failure_notification_finished(self, task: asyncio.Task[None]) -> None:
        self._failure_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            return

    def _force_dispose_pending(self) -> None:
        self._force_dispose_records(tuple(self._pending.values()))
        for task in tuple(self._settlement_tasks) + tuple(self._failure_tasks):
            if not task.done():
                task.cancel()
        self._settlement_tasks.clear()
        self._failure_tasks.clear()

    def _force_dispose_records(
        self, pending_items: tuple[_PendingPermission, ...]
    ) -> None:
        cancelled = RequestPermissionResponse(
            outcome=DeniedOutcome(outcome="cancelled")
        )
        for pending in pending_items:
            if self._pending.get(pending.request_id) is pending:
                self._pending.pop(pending.request_id, None)
            self._settled_request_ids.add(pending.request_id)
            pending.opened.set()
            if pending.timeout_task is not None:
                pending.timeout_task.cancel()
                pending.timeout_task = None
            if not pending.future.done():
                pending.future.set_result(cancelled)

    @staticmethod
    def _source_key(
        employee_id: str,
        binding_generation: int,
        child_generation: int,
        record_identity: object,
        prompt_epoch: int,
    ) -> tuple[str, int, int, int, int]:
        return (
            employee_id,
            binding_generation,
            child_generation,
            id(record_identity),
            prompt_epoch,
        )

    @staticmethod
    def _pending_source(
        pending: _PendingPermission,
    ) -> tuple[str, int, int, int, int]:
        return (
            pending.employee.employee_id,
            pending.binding.binding_generation,
            pending.child_generation,
            id(pending.record_identity),
            pending.prompt_epoch,
        )

    @staticmethod
    def _snapshot(pending: _PendingPermission) -> PendingPermissionSnapshot:
        return PendingPermissionSnapshot(
            request_id=pending.request_id,
            employee=pending.employee,
            binding=pending.binding,
            child_generation=pending.child_generation,
            record_identity=pending.record_identity,
            prompt_epoch=pending.prompt_epoch,
            origin=pending.origin,
            request=pending.request,
            settling=pending.settling,
        )
