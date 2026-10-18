"""Process-local transaction admission without domain policy."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

TransactionOutcome = Literal["committed", "rolled_back", "uncertain"]
SavepointAction = Literal["create", "release", "rollback_to"]


class TransactionGuardLease(Protocol):
    @property
    def reconciliation_handle(self) -> object: ...

    def check_savepoint(self, action: SavepointAction, /) -> None: ...
    def settle(self, outcome: TransactionOutcome, error: BaseException | None, /) -> None: ...
    def invalidate(self, error: BaseException, /) -> None: ...
    def release(self) -> None: ...


class UncertainTransactionError(RuntimeError):
    """An unresolved outcome with the producer's unchanged opaque repair handle."""

    def __init__(self, handle: object, error: BaseException | None) -> None:
        super().__init__(f"transaction outcome is uncertain: {error}")
        self._reconciliation_handle = handle

    @property
    def reconciliation_handle(self) -> object:
        return self._reconciliation_handle


class TransactionGuardFactory(Protocol):
    def enter(
        self, connection: sqlite3.Connection, admission: object | None, /
    ) -> TransactionGuardLease: ...


class GuardRegistration(Protocol):
    def close(self) -> None: ...


@dataclass
class _Entry:
    factory: TransactionGuardFactory | None = None
    unavailable: bool = False
    active: int = 0
    owners: dict[int, int] | None = None
    registration: _Registration | None = None


_condition = threading.Condition()
_entries: dict[str, _Entry] = {}


def _database_identity(connection: sqlite3.Connection) -> str:
    for row in connection.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return str(Path(row[2]).resolve())
    # In-memory databases never share registration by a filesystem identity.
    return f":connection:{id(connection)}"


class _Registration:
    def __init__(self, entry: _Entry, factory: TransactionGuardFactory) -> None:
        self.entry = entry
        self.factory = factory
        self.closed = False

    def close(self) -> None:
        with _condition:
            entry = self.entry
            if self.closed and entry.registration is not self:
                return
            if (entry.owners or {}).get(threading.get_ident(), 0):
                raise RuntimeError(
                    "cannot close transaction registration from an active transaction"
                )
            self.closed = True
            entry.unavailable = True
            while entry.registration is self and entry.active:
                _condition.wait()
            # Keep the unavailable sentinel until explicit fresh registration.
            if entry.registration is self:
                entry.factory = None
                entry.registration = None


def register_transaction_guard(
    database_path: str, factory: TransactionGuardFactory, /
) -> GuardRegistration:
    identity = str(Path(database_path).resolve())
    with _condition:
        entry = _entries.setdefault(identity, _Entry())
        if entry.active:
            raise RuntimeError("cannot register a guard while a transaction is active")
        if entry.factory is not None:
            raise RuntimeError("a transaction guard is already registered")
        entry.factory = factory
        entry.unavailable = False
        registration = _Registration(entry, factory)
        entry.registration = registration
        return registration


class TransactionReservation:
    """One outer transaction, including transactions without an installed factory."""

    def __init__(self, entry: _Entry, owner: int) -> None:
        self.entry = entry
        self.owner = owner
        self.lease: TransactionGuardLease | None = None
        self.finished = False
        self.reconciliation_handle: object | None = None

    def check_savepoint(self, action: SavepointAction) -> None:
        if self.lease is not None:
            self.lease.check_savepoint(action)

    def finish(self, outcome: TransactionOutcome, error: BaseException | None) -> None:
        if self.finished:
            return
        self.finished = True
        failure: BaseException | None = None
        try:
            if self.lease is not None:
                try:
                    self.lease.settle(outcome, error)
                except BaseException as caught:
                    failure = caught
                    self._invalidate(caught)
        finally:
            try:
                if self.lease is not None:
                    try:
                        self.lease.release()
                    except BaseException as caught:
                        self._invalidate(caught)
                        if failure is None:
                            failure = caught
                        else:
                            failure.add_note(f"guard release also failed: {caught!r}")
            finally:
                with _condition:
                    self.entry.active -= 1
                    assert self.entry.owners is not None
                    remaining = self.entry.owners[self.owner] - 1
                    if remaining:
                        self.entry.owners[self.owner] = remaining
                    else:
                        del self.entry.owners[self.owner]
                    if (
                        not self.entry.active
                        and self.entry.factory is None
                        and not self.entry.unavailable
                    ):
                        for identity, entry in tuple(_entries.items()):
                            if entry is self.entry:
                                del _entries[identity]
                    _condition.notify_all()
        if outcome == "uncertain" and self.reconciliation_handle is not None:
            uncertain = UncertainTransactionError(self.reconciliation_handle, error)
            if failure is not None:
                uncertain.add_note(f"guard cleanup also failed: {failure!r}")
            raise uncertain from error
        if failure is not None:
            raise failure

    def _invalidate(self, error: BaseException) -> None:
        with _condition:
            self.entry.unavailable = True
        if self.lease is not None:
            try:
                self.lease.invalidate(error)
            except BaseException as failure:
                # Preserve the initiating error, retain the poison sentinel and
                # still attempt release. Invalidators promise not to throw.
                error.add_note(f"guard invalidation also failed: {failure!r}")


def reserve_transaction(
    connection: sqlite3.Connection, admission: object | None
) -> TransactionReservation:
    identity = _database_identity(connection)
    owner = threading.get_ident()
    with _condition:
        entry = _entries.setdefault(identity, _Entry())
        if entry.unavailable:
            raise RuntimeError("transaction admission is unavailable")
        entry.active += 1
        if entry.owners is None:
            entry.owners = {}
        entry.owners[owner] = entry.owners.get(owner, 0) + 1
        factory = entry.factory
        reservation = TransactionReservation(entry, owner)
    # Never wait for the domain gate while holding the registry mutex.
    try:
        if factory is not None:
            reservation.lease = factory.enter(connection, admission)
            try:
                handle = reservation.lease.reconciliation_handle
                if handle is None:
                    raise TypeError("transaction guard requires a non-null reconciliation handle")
                reservation.reconciliation_handle = handle
            except BaseException as error:
                reservation._invalidate(error)
                raise
        with _condition:
            if entry.unavailable:
                raise RuntimeError("transaction admission became unavailable")
        return reservation
    except BaseException as error:
        reservation.finish("rolled_back", error)
        raise
