"""Publish current conversation status within ordinary SQLite transactions.

The coordinator borrows the runtime ownership handle. It never acquires or closes it.
"""

from __future__ import annotations

import inspect
import sqlite3
import threading
import uuid
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import IO, Literal, Protocol, cast

from planner.conversation.contracts import (
    ConversationAttention,
    ConversationSignalPublicationV1,
    SignalAvailability,
    SignalBatch,
    SignalCursor,
    SignalDeletion,
    SignalInstaller,
    SignalInvalidation,
    SignalParticipant,
    SignalReadPermit,
    SignalSnapshot,
    SignalUnavailable,
)
from planner.conversation.publication import persistence
from planner.conversation.publication.reader import read_durable_facts

type InvalidationReason = Literal["authority_lost", "detached", "shutdown"]


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
        transaction_control: Callable[[sqlite3.Connection], AbstractContextManager[None]]
        | None = None,
    ) -> None:
        self.database_path = str(Path(database_path).resolve())
        self.ownership = ownership
        self._connect = connect
        self._authority_check = authority_check
        self._turn = current_turn_reader
        self._attention = attention_reader
        self._transaction_control = transaction_control
        self._gate = threading.RLock()
        self._epoch = ""
        self._scope = ""
        self._initialized = False
        self._phase = "bootstrap"
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
            {
                "working": turn is not None or durable.has_live_background_task,
                "message_for_owner": durable.message_for_owner,
            },
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

    @contextmanager
    def current_read(self) -> Iterator[None]:
        with self._gate:
            self._require_authority()
            if self._state != "current":
                raise SignalUnavailable(self._reason or "Signal is not active")
            yield

    @contextmanager
    def runtime_registry(self) -> Iterator[None]:
        with self._gate:
            self._require_authority()
            yield

    def attach_connection(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA recursive_triggers=ON")

    def initialize(self, connection: sqlite3.Connection) -> None:
        if self._attention is None:
            raise RuntimeError("bind reconciled runtime before Signal initialization")
        old = connection.execute("SELECT scope FROM signal_publication_meta").fetchone()
        self._scope = str(old[0]) if old else _identity()
        self._epoch = _identity()
        # Recalculate every conversation for the new runtime epoch.
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
        self._initialized = True
        self._phase = "installation"

    def finalize_signal(self, connection: sqlite3.Connection, /) -> None:
        if not self._initialized or self._attention is None:
            return
        relevant = connection.execute("SELECT 1 FROM signal_publication_dirty LIMIT 1").fetchone()
        associations_exist = connection.execute(
            "SELECT 1 FROM signal_publication_associations LIMIT 1"
        ).fetchone()
        if not relevant and not associations_exist:
            return
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
            if attention is None:
                if previous is None or previous[1] is None:
                    continue
                revision = int(previous[0]) + 1
                deletions.append(SignalDeletion(identity, self._epoch, revision))
                body = None
            else:
                if previous is not None and previous[1] is not None:
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
            persistence.decode_association(str(row[0]))
            for row in connection.execute(
                "SELECT value FROM signal_publication_associations ORDER BY sequence"
            )
        )
        cursor = SignalCursor(base.scope, base.runtime_epoch, base.sequence + 1)
        batch = SignalBatch(base, cursor, tuple(replacements), tuple(deletions), associations)
        connection.execute("UPDATE signal_publication_meta SET sequence=?", (cursor.sequence,))
        connection.execute("DELETE FROM signal_publication_dirty")
        connection.execute("DELETE FROM signal_publication_associations")
        if self._participant is not None:
            assert self._transaction_control is not None
            with self._transaction_control(connection):
                _sync(self._participant, connection, batch)

    def signal_availability(self) -> SignalAvailability:
        with self._gate:
            if self.ownership.closed and self._reason != "authority_lost":
                self._invalidate("authority_lost")
            return SignalAvailability(self._generation, self._state, self._reason)

    def _invalidate(self, reason: InvalidationReason) -> None:
        self._generation += 1
        self._state = "unavailable"
        self._reason = reason
        if self._unavailable is not None:
            try:
                _sync(self._unavailable, SignalInvalidation(self._generation, reason))
            except BaseException:
                # The authoritative generation changes even if notification fails.
                pass

    def invalidate(self, reason: InvalidationReason) -> None:
        with self._gate:
            self._invalidate(reason)

    def attach_signal_publication(
        self,
        *,
        install: SignalInstaller,
        participate: SignalParticipant,
        unavailable: Callable[[SignalInvalidation], None],
    ) -> _Attachment:
        with self._gate:
            self._require_authority()
            if self._connect is None or not self._initialized:
                raise RuntimeError("Signal producer is not initialized")
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                snapshot = SignalSnapshot(
                    _identity(), persistence.cursor(connection), persistence.values(connection)
                )
                assert self._transaction_control is not None
                with self._transaction_control(connection):
                    _sync(install, connection, snapshot)
                connection.commit()
                self._participant, self._unavailable = participate, unavailable
                self._generation += 1
                attachment = _Attachment(self, snapshot.installation_id)
                self._attachment = attachment
                self._phase = "runtime"
                self._state, self._reason = "current", None
                return attachment
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
