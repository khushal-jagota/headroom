"""Synchronous startup of the actual Signal producer on retained core adapters."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import AbstractContextManager, AsyncExitStack, closing
from dataclasses import dataclass
from typing import IO

from planner import database_modules
from planner.conversation.backend_state import BackendStateStore
from planner.conversation.backend_usage import BackendUsageService, production_backend_usage_service
from planner.conversation.backends.codex_app_server.login import CodexLoginRejectionMemory
from planner.conversation.backends.codex_app_server.login_session import CodexSignInService
from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.lifecycle_storage import (
    acquire_runtime_ownership,
    validate_runtime_ownership,
)
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.conversation.model_resolution.service import ModelResolutionService
from planner.conversation.publication import persistence
from planner.conversation.publication.coordinator import (
    SignalGuardLease,
    SignalInitializationAdmission,
    SignalPublicationCoordinator,
)
from planner.conversation.snapshot import BackendSnapshotService
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import (
    ChangeSignallingConnection,
    bind_transaction_admission,
    connect,
    create_schema,
    transaction_control_guard,
    transaction_savepoint_depth,
)
from planner.core.transaction_guards import GuardRegistration, register_transaction_guard


def _managed(connection: sqlite3.Connection) -> ChangeSignallingConnection:
    if not isinstance(connection, ChangeSignallingConnection):
        raise TypeError("Signal runtime requires a retained managed connection")
    return connection


def bind_admission(connection: sqlite3.Connection, token: object) -> AbstractContextManager[None]:
    return bind_transaction_admission(_managed(connection), token)


def transaction_control(connection: sqlite3.Connection) -> AbstractContextManager[None]:
    return transaction_control_guard(_managed(connection))


def savepoint_depth(connection: sqlite3.Connection) -> int:
    return transaction_savepoint_depth(_managed(connection))


class _SignalTransactionGuard:
    """Refresh functions on idle connections from before this producer generation."""

    def __init__(self, source: SignalPublicationCoordinator) -> None:
        self.source = source

    def enter(
        self, connection: sqlite3.Connection, admission: object | None, /
    ) -> SignalGuardLease:
        self.source.attach_connection(connection)
        return self.source.enter(connection, admission)


class SignalRuntimeComposition:
    """Keep bootstrap ownership until the conversation system accepts its exact handle.

    All phase transitions run synchronously in the lifespan thread. No gate context
    survives a call. The system owns shutdown only after successful construction.
    """

    def __init__(self, db_path: str, busy_timeout_ms: int) -> None:
        self.db_path = db_path
        self.busy_timeout_ms = busy_timeout_ms
        self.ownership: IO[bytes] | None = None
        self.coordinator: SignalPublicationCoordinator
        self.initialization: SignalInitializationAdmission
        self._guard: GuardRegistration | None = None
        self._modules: database_modules.SignalRegistration | None = None
        self._transferred = False
        self._closed = False

    def connect(self) -> ChangeSignallingConnection:
        return _managed(connect(self.db_path, self.busy_timeout_ms))

    def bootstrap(self) -> None:
        if self.ownership is not None or self._closed:
            raise RuntimeError("Signal bootstrap is single use")
        self.ownership = acquire_runtime_ownership(self.db_path)
        try:
            self.coordinator = SignalPublicationCoordinator(
                self.db_path,
                self.ownership,
                connect=self.connect,
                authority_check=validate_runtime_ownership,
                savepoint_depth=savepoint_depth,
                transaction_control=transaction_control,
                bind_admission=bind_admission,
            )
            self._modules = database_modules.register_signal_publication(
                self.db_path, self.coordinator
            )
            self._guard = register_transaction_guard(
                self.db_path, _SignalTransactionGuard(self.coordinator)
            )
            with self.coordinator.bootstrap_authority(self.ownership) as bootstrap:
                with closing(self.connect()) as connection:
                    create_schema(connection, migration_admission=bootstrap)
                    with bind_admission(connection, bootstrap):
                        connection.execute("BEGIN IMMEDIATE")
                        try:
                            persistence.create_schema(connection)
                            persistence.install_source_triggers(connection)
                            connection.commit()
                        except BaseException:
                            connection.rollback()
                            raise
                self.initialization = self.coordinator.begin_initialization(bootstrap)
        except BaseException:
            self.close()
            raise

    def transfer_ownership(self) -> None:
        if self.ownership is None or self.ownership.closed or self._transferred:
            raise RuntimeError("Signal runtime ownership cannot transfer")
        self._transferred = True

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            with bind_admission(connection, self.initialization):
                connection.execute("BEGIN IMMEDIATE")
            try:
                self.coordinator.initialize(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def close(self) -> None:
        if self._closed:
            return
        # A refused drain must retain module hooks and ownership. Releasing either
        # could expose an active transaction to a successor without its participant.
        if self._guard is not None:
            self._guard.close()
        if self._modules is not None:
            self._modules.close()
        if self.ownership is not None and not self._transferred:
            self.ownership.close()
        self._closed = True


@dataclass(frozen=True, slots=True)
class ConversationRuntime:
    """The conversation system as one running thing, built once for the process."""

    store: ConversationStore
    system: SqliteProcessConversationSystem
    live_tail: ConversationLiveTail
    backend_snapshots: BackendSnapshotService
    backend_usage: BackendUsageService
    message_files: ConversationMessageFiles
    database_path: str
    sse_heartbeat_ms: int
    model_resolution: ModelResolutionService | None = None
    backend_state: BackendStateStore | None = None
    # Absent when this process has no backend that can start a sign-in. The routes say
    # so rather than pretending the affordance exists.
    codex_sign_in: CodexSignInService | None = None

    async def shutdown(self) -> None:
        """Let the watchers go first, then stop the system's children.

        A tail is idle nearly all the time and only ends when its browser leaves, so a
        server that waited for them to finish on their own would never finish at all.
        """
        self.live_tail.close_all_subscriptions()
        await self.system.shutdown()
        if self.codex_sign_in is not None:
            await self.codex_sign_in.shutdown()


def build_conversation_runtime(
    *,
    db_path: str,
    db_busy_timeout_ms: int,
    sse_heartbeat_ms: int,
    backend_child_factories: Mapping[ConversationBackendKey, BackendChildFactory],
    remembered_codex_login_rejection: CodexLoginRejectionMemory | None = None,
    cleanup: AsyncExitStack | None = None,
    signal_composition: SignalRuntimeComposition | None = None,
) -> ConversationRuntime:
    """Compose the conversation system, its record, the files it keeps, and the tail."""
    store = ConversationStore(
        db_path,
        busy_timeout_ms=db_busy_timeout_ms,
        signal_coordinator=None if signal_composition is None else signal_composition.coordinator,
        bind_signal_admission=None if signal_composition is None else bind_admission,
        runtime_reconciliation_admission=(
            None if signal_composition is None else signal_composition.initialization
        ),
        transaction_control_guard=None if signal_composition is None else transaction_control,
    )
    live_tail = ConversationLiveTail()
    message_files = ConversationMessageFiles(db_path)
    backend_snapshots = BackendSnapshotService(
        remembered_codex_login_rejection=remembered_codex_login_rejection
    )

    backend_state = BackendStateStore(db_path, busy_timeout_ms=db_busy_timeout_ms)
    model_resolution = ModelResolutionService(
        snapshots=backend_snapshots,
        store=ModelResolutionStore(db_path, busy_timeout_ms=db_busy_timeout_ms),
        backend_state=backend_state,
    )

    async def codex_app_server_argv() -> tuple[str, ...] | None:
        """How to run a codex app-server here, read when a sign-in starts, not at boot.

        A machine can gain or lose codex while the server runs, so the path comes from the
        current card rather than being captured once.
        """
        snapshot = await backend_snapshots.snapshot(ConversationBackendKey.codex)
        executable = snapshot.executable_path
        return None if executable is None else (executable, "app-server")

    system = SqliteProcessConversationSystem(
        store=store,
        backend_child_factories=backend_child_factories,
        message_files=message_files,
        live_tail=live_tail,
        model_resolver=model_resolution,
        runtime_ownership=None if signal_composition is None else signal_composition.ownership,
    )
    if signal_composition is not None:
        signal_composition.transfer_ownership()
    runtime: ConversationRuntime | None = None

    async def shutdown_composed_runtime() -> None:
        if runtime is None:
            await system.shutdown()
        else:
            await runtime.shutdown()

    # A later component constructor can fail after ownership transfers to system.
    if cleanup is not None:
        cleanup.push_async_callback(shutdown_composed_runtime)
    runtime = ConversationRuntime(
        store=store,
        system=system,
        live_tail=live_tail,
        backend_snapshots=backend_snapshots,
        backend_usage=production_backend_usage_service(),
        message_files=message_files,
        database_path=db_path,
        sse_heartbeat_ms=sse_heartbeat_ms,
        backend_state=backend_state,
        model_resolution=model_resolution,
        codex_sign_in=CodexSignInService(
            resolve_argv=codex_app_server_argv,
            remembered_login_rejection=remembered_codex_login_rejection,
        ),
    )
    return runtime
