"""Serialized domain publication. Core transaction admission remains an injected adapter.

The coordinator borrows the real runtime ownership handle. It never acquires or closes
that handle. Every lease must be entered before BEGIN by the retained core adapter.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import IO, Literal, Protocol, cast

from planner.conversation.contracts import (
    ConversationAttention,
    ConversationSignalPublicationV1,
    SignalAssociationEffect,
    SignalAvailability,
    SignalBatch,
    SignalCursor,
    SignalDeletion,
    SignalInstaller,
    SignalInvalidation,
    SignalKnowledge,
    SignalParticipant,
    SignalReadPermit,
    SignalSnapshot,
    SignalUnavailable,
)
from planner.conversation.publication import persistence
from planner.conversation.publication.mutations import (
    PlainFieldMutation,
    SignalRuntimeMutation,
    apply_fields,
    undo_fields,
)
from planner.conversation.publication.reader import read_durable_facts

_GATES_LOCK = threading.Lock()
_GATES: dict[str, threading.RLock] = {}

type InvalidationReason = Literal[
    "initializing",
    "authority_lost",
    "publication_failed",
    "commit_uncertain",
    "install_failed",
    "detached",
    "shutdown",
]


class _PendingTurn(Protocol):
    @property
    def pending_permission_ask_ids(self) -> Collection[str]: ...

    @property
    def pending_user_input_questions(self) -> Mapping[str, object]: ...


def _identity() -> str:
    return uuid.uuid4().hex


def _sync(function: Callable[..., object], *args: object) -> object:
    result = function(*args)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError("Signal publication callbacks must be synchronous")
    return result


@dataclass(eq=False, slots=True)
class SignalAdmission:
    owner: SignalPublicationCoordinator
    connection: sqlite3.Connection
    thread: int
    operation_id: str
    mutation: SignalRuntimeMutation | None
    retain_failure: bool = False
    used: bool = False


@dataclass(eq=False, slots=True)
class SignalBootstrapAdmission:
    owner: SignalPublicationCoordinator
    thread: int
    active: bool = True


@dataclass(eq=False, slots=True)
class SignalInitializationAdmission:
    owner: SignalPublicationCoordinator
    thread: int
    active: bool = True


@dataclass(eq=False, slots=True)
class SignalInstallationAdmission:
    owner: SignalPublicationCoordinator
    connection: sqlite3.Connection
    thread: int
    used: bool = False


@dataclass(frozen=True, slots=True)
class SignalReconciliationHandle:
    producer_id: str
    operation_id: str
    batch_id: str


@dataclass(eq=False, slots=True)
class SignalGuardLease:
    owner: SignalPublicationCoordinator
    connection: sqlite3.Connection
    admission: (
        SignalAdmission
        | SignalBootstrapAdmission
        | SignalInitializationAdmission
        | SignalInstallationAdmission
        | None
    )
    batch_id: str = field(default_factory=_identity)
    operation_id: str = field(default_factory=_identity)
    undo: list[PlainFieldMutation] = field(default_factory=list)
    finalized: bool = False
    settled: bool = False
    released: bool = False
    applied: bool = False
    result: object = None
    initialization: bool = False
    post_turn: object = None
    published: bool = False
    pruned: tuple[str, ...] = ()
    _handle: SignalReconciliationHandle | None = None
    _outcome: str | None = None

    @property
    def reconciliation_handle(self) -> SignalReconciliationHandle:
        if self._handle is None:
            raise RuntimeError("Signal admission did not create its reconciliation handle")
        return self._handle

    def check_savepoint(self, action: Literal["create", "release", "rollback_to"], /) -> None:
        if self.applied and action == "create":
            raise RuntimeError("runtime candidates cannot enter nested savepoints")

    def settle(self, outcome: str, error: BaseException | None, /) -> None:
        if self.settled:
            raise RuntimeError("Signal transaction already settled")
        if outcome not in ("committed", "rolled_back", "uncertain"):
            raise ValueError("unknown transaction outcome")
        self.settled = True
        self._outcome = outcome
        if outcome == "rolled_back":
            undo_fields(self.undo)
            if (
                error is not None
                and isinstance(self.admission, SignalAdmission)
                and self.admission.retain_failure
            ):
                self.owner._failed_operations.add(self.operation_id)
                self.owner._invalidate("publication_failed")
            if self.initialization:
                self.owner._epoch = ""
                self.owner._scope = ""
        elif outcome == "uncertain":
            self.owner._failed_operations.add(self.operation_id)
            self.owner._unresolved[self.operation_id] = self
            self.owner._invalidate("commit_uncertain")
        elif not self.finalized and not isinstance(
            self.admission, SignalBootstrapAdmission | SignalInitializationAdmission
        ):
            self.owner._invalidate("publication_failed")
            raise RuntimeError("committed source transaction skipped Signal finalization")
        elif self.initialization:
            self.owner._initialized = True
            self.owner._phase = "installation"
            self.owner._prunable.add(self.operation_id)
            if isinstance(self.admission, SignalInitializationAdmission):
                self.admission.active = False
        if outcome == "committed":
            for operation_id in self.pruned:
                self.owner._expire_handles(operation_id)
            if isinstance(self.admission, SignalAdmission) and self.published:
                self.owner._committed_operations.add(self.operation_id)
            self.owner._prunable.difference_update(self.pruned)
            if self.admission is None and self.published:
                self.owner._prunable.add(self.operation_id)

    def invalidate(self, error: BaseException, /) -> None:
        self.owner._invalidate("publication_failed")

    def release(self) -> None:
        if self.released:
            return
        if not self.settled:
            self.owner._invalidate("commit_uncertain")
        self.released = True
        if (
            self._outcome in ("committed", "rolled_back")
            and (not self.published or self._outcome == "rolled_back")
            and not self.initialization
            and self.operation_id not in self.owner._failed_operations
        ):
            self.owner._handles.pop(self.batch_id, None)
        self.owner._leases.pop(id(self.connection), None)
        self.owner._gate.release()


class _Attachment:
    def __init__(self, owner: SignalPublicationCoordinator, installation_id: str) -> None:
        self.owner = owner
        self.installation_id = installation_id
        self.closed = False

    def capture_permit(self) -> SignalReadPermit:
        with self.owner.current_read():
            if self.closed or self.owner._attachment is not self:
                raise SignalUnavailable("Signal attachment is not active")
            return SignalReadPermit(
                self.owner._scope,
                self.owner.runtime_epoch,
                self.installation_id,
                self.owner._generation,
            )

    def validate(self, permit: SignalReadPermit, /) -> None:
        if permit != self.capture_permit():
            raise SignalUnavailable("Signal generation changed")

    def close(self) -> None:
        with self.owner._gate:
            self.owner._reject_reentrant()
            if self.closed:
                return
            self.closed = True
            if self.owner._attachment is self:
                self.owner._invalidate("detached")
                self.owner._attachment = None
                self.owner._participant = None
                self.owner._unavailable = None


class SignalPublicationCoordinator:
    def __init__(
        self,
        database_path: str,
        ownership: IO[bytes],
        *,
        connect: Callable[[], sqlite3.Connection] | None = None,
        authority_check: Callable[[str, IO[bytes]], None] | None = None,
        current_turn_reader: Callable[[str], object | None] | None = None,
        attention_reader: Callable[[sqlite3.Connection, str], ConversationAttention | None]
        | None = None,
        savepoint_depth: Callable[[sqlite3.Connection], int] | None = None,
        transaction_control: Callable[[sqlite3.Connection], AbstractContextManager[None]]
        | None = None,
        bind_admission: Callable[[sqlite3.Connection, object], AbstractContextManager[None]]
        | None = None,
    ) -> None:
        self.database_path = str(Path(database_path).resolve())
        self._producer_id = _identity()
        self._handles: dict[str, SignalReconciliationHandle] = {}
        self.ownership = ownership
        self._connect = connect
        self._authority_check = authority_check
        self._turn = current_turn_reader
        self._attention = attention_reader
        self._savepoint_depth = savepoint_depth
        self._transaction_control = transaction_control
        self._bind_admission = bind_admission
        self._internal_connection: sqlite3.Connection | None = None
        self._prunable: set[str] = set()
        with _GATES_LOCK:
            self._gate = _GATES.setdefault(self.database_path, threading.RLock())
        self._leases: dict[int, SignalGuardLease] = {}
        self._epoch = ""
        self._initialized = False
        self._scope = ""
        self._coherent = True
        self._unresolved: dict[str, SignalGuardLease] = {}
        self._failed_operations: set[str] = set()
        self._committed_operations: set[str] = set()
        self._phase = "bootstrap"
        self._stopping = False
        self._generation = 0
        self._state: Literal["initializing", "current", "unavailable"] = "initializing"
        self._reason: str | None = "initializing"
        self._attachment: _Attachment | None = None
        self._participant: SignalParticipant | None = None
        self._unavailable: Callable[[SignalInvalidation], None] | None = None

    @property
    def runtime_epoch(self) -> str:
        return self._epoch

    def bind_runtime(
        self,
        current_turn_reader: Callable[[str], object | None],
        attention_reader: Callable[[sqlite3.Connection, str], ConversationAttention | None]
        | None = None,
    ) -> None:
        with self._gate:
            if self._phase == "runtime":
                raise RuntimeError("cannot replace live Signal runtime bindings")
            self._turn = current_turn_reader
            self._attention = attention_reader or self._canonical_attention

    def _canonical_attention(
        self,
        connection: sqlite3.Connection,
        identity: str,
    ) -> ConversationAttention | None:
        durable = read_durable_facts(connection, identity)
        if durable is None:
            return None
        if self._turn is None:
            raise RuntimeError("Signal runtime is not bound")
        turn = cast(_PendingTurn | None, self._turn(identity))
        awaiting = turn is not None and bool(
            turn.pending_permission_ask_ids or turn.pending_user_input_questions
        )
        return ConversationAttention(
            {"working": turn is not None, "message_for_owner": durable.message_for_owner},
            awaiting,
            durable.last_turn_failed,
        )

    def _require_authority(self) -> None:
        if self.ownership.closed:
            self._invalidate("authority_lost")
            raise SignalUnavailable("runtime ownership is closed")
        try:
            if self._authority_check is not None:
                self._authority_check(self.database_path, self.ownership)
            else:
                raise SignalUnavailable("borrowed ownership requires the runtime lease validator")
        except BaseException:
            self._invalidate("authority_lost")
            raise

    def _reject_reentrant(self) -> None:
        if any(not lease.released for lease in self._leases.values()):
            raise SignalUnavailable("public Signal access during a candidate transaction")

    @contextmanager
    def current_read(self) -> Iterator[None]:
        with self._gate:
            self._reject_reentrant()
            self._require_authority()
            if self._state != "current":
                raise SignalUnavailable(self._reason or "Signal is unavailable")
            yield

    @contextmanager
    def runtime_registry(self) -> Iterator[None]:
        with self._gate:
            self._reject_reentrant()
            self._require_authority()
            yield

    @contextmanager
    def bootstrap_authority(self, ownership: IO[bytes], /) -> Iterator[SignalBootstrapAdmission]:
        with self._gate:
            if ownership is not self.ownership or self._phase != "bootstrap":
                raise SignalUnavailable("invalid bootstrap authority")
            self._require_authority()
            token = SignalBootstrapAdmission(self, threading.get_ident())
            try:
                yield token
            finally:
                token.active = False

    def prepare_mutation(
        self,
        connection: sqlite3.Connection,
        mutation: SignalRuntimeMutation | None,
        *,
        operation_id: str,
        retain_failure: bool = False,
    ) -> SignalAdmission:
        if self._stopping:
            raise SignalUnavailable("Signal source admission stopped for shutdown")
        self._validate_connection(connection)
        if connection.in_transaction:
            raise RuntimeError("prepare Signal admission before BEGIN")
        if not operation_id:
            raise ValueError("operation identity must not be empty")
        return SignalAdmission(
            self,
            connection,
            threading.get_ident(),
            operation_id,
            mutation,
            retain_failure,
        )

    def _validate_connection(self, connection: sqlite3.Connection) -> None:
        main = next(
            (row for row in connection.execute("PRAGMA database_list") if row[1] == "main"), None
        )
        if main is None or not main[2] or str(Path(main[2]).resolve()) != self.database_path:
            raise SignalUnavailable("connection belongs to another Signal database")

    def begin_initialization(
        self,
        bootstrap: SignalBootstrapAdmission,
        /,
    ) -> SignalInitializationAdmission:
        with self._gate:
            self._reject_reentrant()
            self._require_authority()
            if (
                bootstrap.owner is not self
                or not bootstrap.active
                or bootstrap.thread != threading.get_ident()
                or self._phase != "bootstrap"
            ):
                raise SignalUnavailable("invalid bootstrap transition")
            bootstrap.active = False
            self._phase = "initialization"
            return SignalInitializationAdmission(self, threading.get_ident())

    def enter(
        self, connection: sqlite3.Connection, admission: object | None, /
    ) -> SignalGuardLease:
        self._gate.acquire()
        try:
            self._validate_connection(connection)
            self._require_authority()
            self._reject_reentrant()
            if self._stopping and not isinstance(admission, SignalAdmission):
                raise SignalUnavailable("Signal source admission stopped for shutdown")
            if connection.in_transaction:
                raise RuntimeError("Signal gate must precede SQLite transaction admission")
            if isinstance(admission, SignalBootstrapAdmission):
                if (
                    admission.owner is not self
                    or not admission.active
                    or admission.thread != threading.get_ident()
                    or self._phase != "bootstrap"
                ):
                    raise SignalUnavailable("expired bootstrap authority")
            elif isinstance(admission, SignalAdmission):
                if self._phase != "runtime":
                    raise SignalUnavailable("runtime operation precedes complete installation")
                if (
                    admission.owner is not self
                    or admission.connection is not connection
                    or admission.thread != threading.get_ident()
                    or admission.used
                ):
                    raise SignalUnavailable("invalid Signal mutation admission")
                admission.used = True
                if admission.mutation is not None:
                    self._validate_mutation(admission.mutation)
            elif isinstance(admission, SignalInitializationAdmission):
                if (
                    admission.owner is not self
                    or not admission.active
                    or admission.thread != threading.get_ident()
                    or self._phase != "initialization"
                ):
                    raise SignalUnavailable("expired initialization authority")
            elif isinstance(admission, SignalInstallationAdmission):
                if (
                    admission.owner is not self
                    or admission.connection is not connection
                    or admission.thread != threading.get_ident()
                    or admission.used
                    or self._phase not in ("installation", "runtime")
                ):
                    raise SignalUnavailable("invalid installation authority")
                admission.used = True
            elif admission is not None:
                raise SignalUnavailable("unknown Signal admission")
            elif self._phase != "runtime":
                raise SignalUnavailable("runtime admission is not open")
            lease = SignalGuardLease(self, connection, admission)
            if isinstance(admission, SignalAdmission):
                lease.operation_id = admission.operation_id
            lease._handle = SignalReconciliationHandle(
                self._producer_id,
                lease.operation_id,
                lease.batch_id,
            )
            self._handles[lease.batch_id] = lease._handle
            self._leases[id(connection)] = lease
            return lease
        except BaseException:
            self._gate.release()
            raise

    def _lease(self, connection: sqlite3.Connection) -> SignalGuardLease:
        lease = self._leases.get(id(connection))
        if lease is None or lease.released:
            raise SignalUnavailable("source connection lacks Signal admission")
        return lease

    def _validate_mutation(self, mutation: SignalRuntimeMutation) -> None:
        if mutation.runtime_epoch != self._epoch or self._turn is None:
            raise SignalUnavailable("obsolete Signal runtime epoch")
        if self._turn(mutation.conversation_id) is not mutation.expected_turn:
            raise SignalUnavailable("obsolete Signal turn")

    def apply_mutation(self, connection: sqlite3.Connection, admission: SignalAdmission, /) -> None:
        lease = self._lease(connection)
        if (
            lease.admission is not admission
            or lease.applied
            or lease.finalized
            or not connection.in_transaction
        ):
            raise RuntimeError("invalid Signal mutation phase")
        if self._savepoint_depth is None:
            raise RuntimeError("runtime mutation requires retained savepoint-depth adapter")
        if self._savepoint_depth(connection):
            raise RuntimeError("runtime mutation requires an outer transaction")
        mutation = admission.mutation
        if mutation is not None:
            exists = connection.execute(
                "SELECT 1 FROM conversations WHERE conversation_id=?",
                (mutation.conversation_id,),
            ).fetchone()
            tombstone = connection.execute(
                "SELECT value IS NULL FROM signal_publication_values WHERE conversation_id=?",
                (mutation.conversation_id,),
            ).fetchone()
            if exists is None or (tombstone is not None and tombstone[0]):
                raise SignalUnavailable("runtime mutation names a deleted conversation")
            if any(
                isinstance(item.admission, SignalAdmission)
                and item.admission.mutation is not None
                and item.admission.mutation.conversation_id == mutation.conversation_id
                for item in self._unresolved.values()
            ):
                raise SignalUnavailable("conversation has an unresolved Signal operation")
            self._validate_mutation(mutation)
            lease.undo = apply_fields(mutation)
            lease.post_turn = self._turn(mutation.conversation_id) if self._turn else None
            persistence.mark_changed(connection, mutation.conversation_id)
        lease.applied = True

    def record_result(self, connection: sqlite3.Connection, result: object) -> None:
        lease = self._lease(connection)
        if lease.finalized:
            raise RuntimeError("Signal source phase is closed")
        persistence.encode(result)
        lease.result = result

    def attach_connection(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA recursive_triggers=ON")

        def admitted() -> int:
            lease = self._leases.get(id(connection))
            return int(lease is not None and not lease.released and not lease.finalized)

        connection.create_function("signal_publication_admitted", 0, admitted)
        connection.create_function(
            "signal_publication_internal",
            0,
            lambda: int(self._internal_connection is connection),
        )

    @contextmanager
    def _private_scope(self, connection: sqlite3.Connection) -> Iterator[None]:
        previous = self._internal_connection
        self._internal_connection = connection
        try:
            yield
        finally:
            self._internal_connection = previous

    def initialize(self, connection: sqlite3.Connection) -> None:
        with self._private_scope(connection):
            self._initialize(connection)

    def _initialize(self, connection: sqlite3.Connection) -> None:
        lease = self._lease(connection)
        if not isinstance(lease.admission, SignalInitializationAdmission):
            raise SignalUnavailable("initialization requires reconciliation authority")
        if self._initialized or self._phase != "initialization":
            raise SignalUnavailable("Signal runtime is already initialized")
        if self._attention is None:
            raise RuntimeError("bind reconciled runtime before Signal initialization")
        old = connection.execute("SELECT scope FROM signal_publication_meta").fetchone()
        self._scope = str(old[0]) if old else _identity()
        self._epoch = _identity()
        lease.initialization = True
        # Old process handles cannot authorize repair in a new producer epoch.
        # Recovery never retries their external effects; exclusive bootstrap retires
        # the private settlement evidence with the old runtime capabilities.
        connection.execute("DELETE FROM signal_publication_operations")
        connection.execute("DELETE FROM signal_publication_batches")
        connection.execute("DELETE FROM signal_publication_meta")
        connection.execute(
            "INSERT INTO signal_publication_meta VALUES(1,?,?,0)", (self._scope, self._epoch)
        )
        connection.execute("DELETE FROM signal_publication_values")
        connection.execute("DELETE FROM signal_publication_dirty")
        connection.execute("DELETE FROM signal_publication_associations")
        for row in connection.execute("SELECT conversation_id FROM conversations"):
            identity = str(row[0])
            attention = self._attention(connection, identity)
            if attention is None:
                raise RuntimeError("complete Signal initialization omitted a conversation")
            value = ConversationSignalPublicationV1(identity, self._epoch, 0, attention)
            connection.execute(
                "INSERT INTO signal_publication_values VALUES(?,0,?)",
                (identity, persistence.encode(asdict(value))),
            )
        lease.finalized = True
        connection.execute(
            "INSERT INTO signal_publication_operations VALUES(?,?,?)",
            (
                lease.operation_id,
                lease.batch_id,
                persistence.encode({"runtime_epoch": self._epoch, "scope": self._scope}),
            ),
        )

    def admit_runtime(self) -> None:
        with self._gate:
            self._reject_reentrant()
            self._require_authority()
            if (
                not self._initialized
                or self._attachment is None
                or self._state != "current"
                or self._phase != "runtime"
            ):
                raise SignalUnavailable("runtime admission requires complete Signal installation")

    def finalize_signal(self, connection: sqlite3.Connection, /) -> None:
        with self._private_scope(connection):
            self._finalize_signal(connection)

    def _finalize_signal(self, connection: sqlite3.Connection) -> None:
        lease = self._lease(connection)
        if lease.finalized:
            return
        if isinstance(lease.admission, SignalBootstrapAdmission | SignalInitializationAdmission):
            return
        if not connection.in_transaction or self._attention is None:
            raise RuntimeError("Signal finalization requires source transaction and runtime")
        self._require_authority()
        relevant = connection.execute("SELECT 1 FROM signal_publication_dirty LIMIT 1").fetchone()
        associations_exist = connection.execute(
            "SELECT 1 FROM signal_publication_associations LIMIT 1"
        ).fetchone()
        if not relevant and not associations_exist and lease.admission is None:
            lease.finalized = True
            return
        if isinstance(lease.admission, SignalAdmission) and lease.applied:
            mutation = lease.admission.mutation
            if mutation is not None and (
                mutation.runtime_epoch != self._epoch
                or self._turn is None
                or self._turn(mutation.conversation_id) is not lease.post_turn
            ):
                raise SignalUnavailable("runtime changed outside the Signal transaction")
        base = persistence.cursor(connection)
        if base.runtime_epoch != self._epoch:
            raise SignalUnavailable("obsolete producer epoch")
        replacements = []
        deletions = []
        for row in connection.execute(
            "SELECT conversation_id FROM signal_publication_dirty ORDER BY conversation_id"
        ).fetchall():
            identity = str(row[0])
            previous = connection.execute(
                "SELECT revision,value FROM signal_publication_values WHERE conversation_id=?",
                (identity,),
            ).fetchone()
            attention = self._attention(connection, identity)
            if previous is not None and previous[1] is None and attention is not None:
                raise SignalUnavailable("deleted conversation identity cannot be recreated")
            if attention is None:
                if previous is None or previous[1] is None:
                    continue
                revision = int(previous[0]) + 1
                deletions.append(SignalDeletion(identity, self._epoch, revision))
                body = None
            else:
                if previous is not None:
                    prior = persistence.decode_publication(str(previous[1]))
                    if prior.attention == attention:
                        continue
                revision = 0 if previous is None else int(previous[0]) + 1
                value = ConversationSignalPublicationV1(identity, self._epoch, revision, attention)
                replacements.append(value)
                body = persistence.encode(asdict(value))
            connection.execute(
                "INSERT INTO signal_publication_values VALUES(?,?,?) ON CONFLICT(conversation_id) "
                "DO UPDATE SET revision=excluded.revision,value=excluded.value",
                (identity, revision, body),
            )
        associations = tuple(
            SignalAssociationEffect(**json.loads(row[0]))
            for row in connection.execute(
                "SELECT value FROM signal_publication_associations ORDER BY sequence"
            )
        )
        cursor = SignalCursor(base.scope, base.runtime_epoch, base.sequence + 1)
        payload = dict(
            base_cursor=asdict(base),
            cursor=asdict(cursor),
            batch_id=lease.batch_id,
            operation_ids=(lease.operation_id,),
            replacements=[asdict(value) for value in replacements],
            deletions=[asdict(value) for value in deletions],
            associations=[asdict(value) for value in associations],
        )
        fingerprint = hashlib.sha256(persistence.encode(payload).encode()).hexdigest()
        batch = SignalBatch(
            base,
            cursor,
            lease.batch_id,
            (lease.operation_id,),
            fingerprint,
            tuple(replacements),
            tuple(deletions),
            associations,
        )
        # No protected source mutation is legal after this point, including callbacks.
        lease.finalized = True
        lease.published = True
        try:
            connection.execute("UPDATE signal_publication_meta SET sequence=?", (cursor.sequence,))
            connection.execute(
                "INSERT INTO signal_publication_batches VALUES(?,?,?,?)",
                (lease.batch_id, cursor.sequence, fingerprint, persistence.encode(asdict(batch))),
            )
            connection.execute(
                "INSERT INTO signal_publication_operations VALUES(?,?,?)",
                (lease.operation_id, lease.batch_id, persistence.encode(lease.result)),
            )
            connection.execute("DELETE FROM signal_publication_dirty")
            connection.execute("DELETE FROM signal_publication_associations")
            lease.pruned = tuple(set(lease.pruned) | self._prunable)
            for operation_id in lease.pruned:
                row = connection.execute(
                    "SELECT batch_id FROM signal_publication_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if row is not None:
                    connection.execute(
                        "DELETE FROM signal_publication_operations WHERE operation_id=?",
                        (operation_id,),
                    )
                    connection.execute(
                        "DELETE FROM signal_publication_batches WHERE batch_id=?", (str(row[0]),)
                    )
            if self._participant is not None:
                if self._transaction_control is None:
                    raise RuntimeError(
                        "Signal callbacks require retained transaction-control guard"
                    )
                self._internal_connection = None
                with self._transaction_control(connection):
                    _sync(self._participant, connection, batch)
        except BaseException:
            self._invalidate("publication_failed")
            raise

    def signal_availability(self) -> SignalAvailability:
        with self._gate:
            if self.ownership.closed and self._reason != "authority_lost":
                self._invalidate("authority_lost")
            return SignalAvailability(self._generation, self._state, self._reason)

    def _invalidate(self, reason: InvalidationReason) -> None:
        if reason == "shutdown":
            self._stopping = True
        self._generation += 1
        self._state = "unavailable"
        self._reason = reason
        if reason in ("commit_uncertain", "publication_failed", "authority_lost"):
            self._coherent = False
        if self._unavailable is not None:
            try:
                _sync(self._unavailable, SignalInvalidation(self._generation, reason))
            except BaseException:
                # The authoritative generation changes even if notification fails.
                pass

    def invalidate(self, reason: InvalidationReason = "publication_failed") -> None:
        with self._gate:
            self._invalidate(reason)

    def resolve_operation(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        *,
        rollback_established: bool = False,
    ) -> tuple[str, object] | None:
        """Read private evidence only after the failed connection is resolved.

        Absence is not rollback proof. The retained adapter supplies that proof before
        runtime undo or a storage retry. A committed result never repeats an append.
        """
        with self._gate:
            self._reject_reentrant()
            self._validate_connection(connection)
            self._require_authority()
            evidence = persistence.operation_result(connection, operation_id)
            if evidence is not None:
                self._committed_operations.add(operation_id)
            pending = self._unresolved.get(operation_id)
            if pending is not None:
                if evidence is None:
                    if not rollback_established:
                        raise SignalUnavailable("operation outcome remains uncertain")
                    undo_fields(pending.undo)
                elif pending.initialization:
                    self._initialized = True
                    self._phase = "installation"
                    if isinstance(pending.admission, SignalInitializationAdmission):
                        pending.admission.active = False
                self._unresolved.pop(operation_id)
            return evidence

    def resolve_reconciliation(
        self,
        connection: sqlite3.Connection,
        handle: SignalReconciliationHandle,
        *,
        rollback_established: bool = False,
    ) -> tuple[str, object] | None:
        with self._gate:
            if (
                handle.producer_id != self._producer_id
                or self._handles.get(handle.batch_id) is not handle
            ):
                raise SignalUnavailable("reconciliation handle is foreign, expired or unknown")
            self._validate_connection(connection)
            evidence = persistence.operation_result(connection, handle.operation_id)
            pending = self._unresolved.get(handle.operation_id)
            if (evidence is not None and evidence[0] != handle.batch_id) or (
                pending is not None and pending.batch_id != handle.batch_id
            ):
                raise SignalUnavailable("reconciliation handle names another operation attempt")
            if evidence is None and pending is None:
                raise SignalUnavailable("reconciliation handle has no unresolved outcome")
            return self.resolve_operation(
                connection,
                handle.operation_id,
                rollback_established=rollback_established,
            )

    def confirm_repaired(self, connection: sqlite3.Connection) -> None:
        with self._gate:
            self._reject_reentrant()
            self._validate_connection(connection)
            self._require_authority()
            if (
                self._unresolved
                or self._failed_operations
                or self._attention is None
                or not connection.in_transaction
            ):
                raise SignalUnavailable("Signal repair requires resolved evidence and snapshot")
            actual_ids = {
                str(row[0])
                for row in connection.execute("SELECT conversation_id FROM conversations")
            }
            published = persistence.values(connection)
            if actual_ids != {value.conversation_id for value in published} or any(
                self._attention(connection, value.conversation_id) != value.attention
                for value in published
            ):
                raise SignalUnavailable("runtime and durable publication still disagree")
            self._coherent = True

    def acknowledge_operation(self, connection: sqlite3.Connection, operation_id: str) -> None:
        """Prune private evidence after the operation owner accepts its known outcome."""
        lease = self._lease(connection)
        if lease.finalized or operation_id in self._unresolved:
            raise SignalUnavailable("cannot prune unresolved operation evidence")
        row = connection.execute(
            "SELECT batch_id FROM signal_publication_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if row is not None:
            lease.pruned = (*lease.pruned, operation_id)
            with self._private_scope(connection):
                connection.execute(
                    "DELETE FROM signal_publication_operations WHERE operation_id=?",
                    (operation_id,),
                )
                connection.execute(
                    "DELETE FROM signal_publication_batches WHERE batch_id=?", (str(row[0]),)
                )

    def accept_operation_result(self, operation_id: str) -> None:
        """Queue bounded private evidence cleanup after the caller accepts its result."""
        with self._gate:
            if operation_id in self._unresolved:
                raise SignalUnavailable("cannot accept an uncertain operation result")
            if operation_id not in self._committed_operations:
                raise SignalUnavailable("operation result lacks known committed evidence")
            self._failed_operations.discard(operation_id)
            self._committed_operations.discard(operation_id)
            self._prunable.add(operation_id)
            self._expire_handles(operation_id)

    def _expire_handles(self, operation_id: str) -> None:
        for handle in list(self._handles.values()):
            if handle.operation_id == operation_id:
                self._handles.pop(handle.batch_id, None)

    def retain_operation_failure(self, operation_id: str) -> None:
        with self._gate:
            self._failed_operations.add(operation_id)
            self._invalidate("publication_failed")

    def read_durable_signal_knowledge(
        self,
        connection: sqlite3.Connection,
        conversation_ids: tuple[str, ...],
        expected_generation: int,
        /,
    ) -> tuple[SignalKnowledge, ...]:
        with self._gate:
            self._reject_reentrant()
            self._validate_connection(connection)
            if not connection.in_transaction:
                raise RuntimeError("durable knowledge requires a coherent read transaction")
            availability = self.signal_availability()
            if availability.generation != expected_generation:
                raise SignalUnavailable("Signal generation changed")
            values = tuple(
                SignalKnowledge(
                    identity, availability, None, read_durable_facts(connection, identity)
                )
                for identity in conversation_ids
            )
            if self.signal_availability().generation != expected_generation:
                raise SignalUnavailable("Signal generation changed")
            return values

    def attach_signal_publication(
        self,
        *,
        install: SignalInstaller,
        participate: SignalParticipant,
        unavailable: Callable[[SignalInvalidation], None],
    ) -> _Attachment:
        with self._gate:
            self._reject_reentrant()
            self._require_authority()
            if self._phase not in ("installation", "runtime") or self._connect is None:
                raise SignalUnavailable("Signal producer is not initialized")
            if self._transaction_control is None:
                raise RuntimeError(
                    "Signal installation requires retained transaction-control guard"
                )
            if not self._coherent or self._unresolved:
                raise SignalUnavailable("Signal publication requires exact outcome repair")
            if self._bind_admission is None:
                raise RuntimeError("Signal installation requires retained admission binder")
            if self._attachment is not None:
                raise RuntimeError("Signal publication already attached")
            self._generation += 1
            self._state, self._reason = "initializing", "initializing"
            connection = self._connect()
            token = SignalInstallationAdmission(self, connection, threading.get_ident())
            try:
                # Injected connection adapter owns BEGIN admission, finalization and settlement.
                with self._bind_admission(connection, token):
                    connection.execute("BEGIN IMMEDIATE")
                # An installation is a read of the already accepted producer cursor.
                # Its navigation writes cannot mint an unobserved producer batch.
                self._lease(connection).finalized = True
                snapshot = SignalSnapshot(
                    _identity(), persistence.cursor(connection), persistence.values(connection)
                )
                with self._transaction_control(connection):
                    _sync(install, connection, snapshot)
                connection.commit()
                # This overridable hook permits proof of postcommit registration failure.
                self._register_callbacks(participate, unavailable)
                attachment = _Attachment(self, snapshot.installation_id)
                self._attachment = attachment
                self._phase = "runtime"
                self._state, self._reason = "current", None
                return attachment
            except BaseException:
                connection.rollback()
                self._participant = None
                self._unavailable = None
                self._invalidate("install_failed")
                raise
            finally:
                connection.close()

    def _register_callbacks(
        self,
        participate: SignalParticipant,
        unavailable: Callable[[SignalInvalidation], None],
    ) -> None:
        self._participant, self._unavailable = participate, unavailable
