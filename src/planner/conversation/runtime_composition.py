"""Synchronous startup of the actual Signal producer on retained core adapters."""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager, closing
from typing import IO

from planner import database_modules
from planner.conversation.lifecycle_storage import (
    acquire_runtime_ownership,
    validate_runtime_ownership,
)
from planner.conversation.publication import persistence
from planner.conversation.publication.coordinator import (
    SignalGuardLease,
    SignalInitializationAdmission,
    SignalPublicationCoordinator,
)
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
