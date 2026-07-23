"""The single production composition for ACP-backed employee conversations."""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from acp.schema import (
    DeniedOutcome,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
)

from planner.conversation.backend_catalog import EmployeeBackendBuildContext
from planner.core.clock import Clock
from planner.core.db import connect
from planner.runtime.acp_step_gateway import AcpStepGateway
from planner.tickets.conversation_projection import TicketConversationProjection
from planner.worker_context.service import SqliteWorkerContextService
from planner.worker_types.configuration import (
    ConfiguredEmployeeRuntimeDefinitions,
    configured_employee_runtime_definitions,
)

from .backend_contracts import AcpConversationIngress, ConversationIngressTransition
from .configuration import ACP_BROWSER_LIVE_QUEUE_MAX_ENVELOPES
from .employee_configuration import EmployeeConfigurationCatalogService
from .employee_registry import (
    AcpEmployeeRegistry,
    ConversationIngressSource,
)
from .hub import ConversationHub
from .permission_broker import ConversationPermissionBroker
from .role_skill_kickoff import RoleSkillKickoffAcpEmployeeChildFactory
from .runtime_ports import ConversationRuntimeHandle
from .sqlite_binding_repository import SqliteConversationBindingRepository
from .turn_broker import ConversationTurnBroker
from .wire_contracts import ProtocolUpdateRejectedPayload


class _BindOnceAsyncCallback[**CallbackParams, CallbackResult]:
    """A construction-cycle seam which rejects use before its sole binding."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._callback: Callable[CallbackParams, Awaitable[CallbackResult]] | None = None

    def bind(
        self,
        callback: Callable[CallbackParams, Awaitable[CallbackResult]],
    ) -> None:
        if self._callback is not None:
            raise RuntimeError(f"{self._name} callback is already bound")
        self._callback = callback

    async def __call__(
        self,
        *args: CallbackParams.args,
        **kwargs: CallbackParams.kwargs,
    ) -> CallbackResult:
        callback = self._callback
        if callback is None:
            raise RuntimeError(f"{self._name} callback is not bound")
        return await callback(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class ConversationTestOptions:
    """Explicit test-only runtime substitution; production never imports test subjects."""

    employee_runtime_definitions: ConfiguredEmployeeRuntimeDefinitions
    ingress_capacity: int = 256
    browser_capacity: int | None = None
    # Temporary live ceiling while the imported durable replay is measured.
    reset_buffer_byte_limit: int = 6 * 1024 * 1024
    connection_id_factory: Callable[[], str] | None = None
    worker_client_message_id_factory: Callable[[], str] | None = None
    permission_request_id_factory: Callable[[], str] | None = None
    backend_available: Callable[[], bool] | None = None


@dataclass(slots=True)
class ConversationComposition:
    """Own the one hub, registry, broker, permission service, and step bridge."""

    hub: ConversationHub
    registry: AcpEmployeeRegistry
    broker: ConversationTurnBroker
    permission_broker: ConversationPermissionBroker
    step_gateway: AcpStepGateway
    employee_configuration_catalog: EmployeeConfigurationCatalogService
    ticket_conversation_projection: TicketConversationProjection
    employee_backend_startup_preflights: tuple[Callable[[], Awaitable[None]], ...]
    _employee_backend_startup_preflights_ran: bool = False

    @classmethod
    def build(
        cls,
        *,
        db_path: str,
        busy_timeout_ms: int,
        clock: Clock,
        repository_root: Path,
        loop: asyncio.AbstractEventLoop,
        test_options: ConversationTestOptions | None = None,
        planner_home_default: Path | None = None,
    ) -> ConversationComposition:
        repository_root = repository_root.resolve(strict=False)
        if not repository_root.is_absolute():
            raise ValueError("conversation repository root must be absolute")

        if test_options is None:
            employee_runtime_definitions = configured_employee_runtime_definitions()
            ingress_capacity = 2_048
            browser_capacity = ACP_BROWSER_LIVE_QUEUE_MAX_ENVELOPES
            # Temporary live ceiling while the imported durable replay is measured.
            reset_buffer_byte_limit = 6 * 1024 * 1024
            connection_id_factory = None
            worker_client_message_id_factory = None
            permission_request_id_factory: Callable[[], str] = cls._new_identifier
        else:
            employee_runtime_definitions = test_options.employee_runtime_definitions
            ingress_capacity = test_options.ingress_capacity
            browser_capacity = (
                test_options.browser_capacity
                if test_options.browser_capacity is not None
                else ACP_BROWSER_LIVE_QUEUE_MAX_ENVELOPES
            )
            reset_buffer_byte_limit = test_options.reset_buffer_byte_limit
            connection_id_factory = test_options.connection_id_factory
            worker_client_message_id_factory = test_options.worker_client_message_id_factory
            permission_request_id_factory = (
                test_options.permission_request_id_factory or cls._new_identifier
            )
        catalog = employee_runtime_definitions.employee_backend_catalog
        materialized_backends = tuple(
            replace(
                backend,
                child_factory=RoleSkillKickoffAcpEmployeeChildFactory(
                    backend.child_factory
                ),
            )
            for backend in catalog.materialize(
                EmployeeBackendBuildContext(
                    data_directory=Path(db_path).expanduser().parent.resolve(
                        strict=False
                    ),
                    planner_home_default=planner_home_default,
                    repository_root=repository_root,
                )
            )
        )

        def backend_available() -> bool:
            if test_options is not None and test_options.backend_available is not None:
                return test_options.backend_available()
            return any(backend.is_executable() for backend in materialized_backends)

        repository = SqliteConversationBindingRepository(
            db_path,
            workspace_root=repository_root,
            integer_now=clock.now_unix,
            busy_timeout_ms=busy_timeout_ms,
            employee_backend_catalog=catalog,
            chief_backend_key="hermes",
            worker_type_registry=employee_runtime_definitions.worker_type_registry,
        )
        ticket_conversation_projection = TicketConversationProjection(
            db_path,
            now=clock.now_unix,
            busy_timeout_ms=busy_timeout_ms,
        )
        hub = ConversationHub(
            repository,
            ingress_capacity=ingress_capacity,
            browser_capacity=browser_capacity,
            reset_buffer_byte_limit=reset_buffer_byte_limit,
            connection_id_factory=connection_id_factory,
            worker_client_message_id_factory=worker_client_message_id_factory,
            ticket_conversation_projection=ticket_conversation_projection,
        )
        source_ingress = _BindOnceAsyncCallback[
            [ConversationIngressSource, ConversationIngressTransition], None
        ]("conversation ingress")
        source_permission = _BindOnceAsyncCallback[
            [ConversationRuntimeHandle, RequestPermissionRequest],
            RequestPermissionResponse,
        ]("conversation permission")
        source_death = _BindOnceAsyncCallback[
            [ConversationIngressSource, BaseException | None], None
        ]("conversation child death")
        permission_broker = ConversationPermissionBroker(
            hub,
            request_id_factory=permission_request_id_factory,
            integer_now=clock.now_unix,
        )

        async def reject_unscoped_ingress(
            _payload: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            raise RuntimeError("unscoped ACP ingress is not composed")

        async def reject_unscoped_permission(
            _request: RequestPermissionRequest,
        ) -> RequestPermissionResponse:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

        registry = AcpEmployeeRegistry(
            backend_catalog=catalog,
            materialized_backends=materialized_backends,
            resolve_binding=repository.resolve,
            compare_and_swap_binding=repository.compare_and_swap,
            compare_and_swap_initial_binding=repository.compare_and_swap_initial,
            resolve_employee=repository.resolve_employee,
            resolve_employee_for_new_conversation=(
                repository.resolve_employee_for_new_conversation
            ),
            resolve_compaction_boundaries=repository.resolve_compaction_boundaries,
            compare_and_swap_compaction=repository.compare_and_swap_compaction,
            ensure_conversation=repository.ensure_conversation,
            conversation_ingress=cast(AcpConversationIngress, reject_unscoped_ingress),
            permission_callback=reject_unscoped_permission,
            source_aware_permission_callback=source_permission,
            source_aware_conversation_ingress=source_ingress,
            source_aware_child_death_callback=source_death,
        )
        broker = ConversationTurnBroker(
            registry,
            hub,
            integer_now=clock.now_unix,
            permission_broker=permission_broker,
        )
        broker.set_compaction_transition_port(hub)
        broker.set_requested_cancel_recovery_transition_port(hub)
        hub.bind_owners(
            registry=registry,
            broker=broker,
            permission_broker=permission_broker,
        )
        source_ingress.bind(hub.registry_conversation_ingress)
        source_permission.bind(broker.request_permission)
        source_death.bind(hub.registry_child_died)
        worker_context_service = SqliteWorkerContextService(
            lambda: connect(db_path, busy_timeout_ms)
        )
        step_gateway = AcpStepGateway(
            hub=hub,
            broker=broker,
            loop=loop,
            owner_thread_id=threading.get_ident(),
            db_path=db_path,
            worker_context_service=worker_context_service,
            busy_timeout_ms=busy_timeout_ms,
            backend_available=backend_available,
        )
        permission_broker.set_worker_settlement_guard(
            step_gateway.guard_worker_permission_settlement
        )
        employee_configuration_catalog = EmployeeConfigurationCatalogService(
            {
                backend.definition.backend_key: (
                    backend.resolved_employee_configuration_adapter()
                )
                for backend in materialized_backends
            },
            database_path=db_path,
            busy_timeout_ms=busy_timeout_ms,
            now_unix=clock.now_unix,
        )
        return cls(
            hub=hub,
            registry=registry,
            broker=broker,
            permission_broker=permission_broker,
            step_gateway=step_gateway,
            employee_configuration_catalog=employee_configuration_catalog,
            ticket_conversation_projection=ticket_conversation_projection,
            employee_backend_startup_preflights=tuple(
                backend.startup_preflight
                for backend in materialized_backends
                if backend.startup_preflight is not None
            ),
        )

    async def run_employee_backend_startup_preflights(self) -> None:
        if self._employee_backend_startup_preflights_ran:
            return
        self._employee_backend_startup_preflights_ran = True
        for startup_preflight in self.employee_backend_startup_preflights:
            await startup_preflight()

    async def close_admission(self) -> None:
        await self.hub.close_admission()

    async def shutdown(self, deadline: float) -> None:
        """Close service owners while the hub publisher remains available until last."""

        failures: list[BaseException] = []
        for shutdown in (
            self.broker.shutdown,
            self.registry.shutdown,
            self.permission_broker.shutdown,
        ):
            try:
                await shutdown(deadline)
            except BaseException as error:
                failures.append(error)
        try:
            await self.hub.shutdown(deadline)
        except BaseException as error:
            failures.append(error)
        if failures:
            raise failures[0]

    @staticmethod
    def _new_identifier() -> str:
        return uuid.uuid4().hex
