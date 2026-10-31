"""VPS run, connection and answer persistence, with shared transaction mechanics."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import asdict
from typing import Any, cast

from planner.conversation.backends.contracts import RetainedBackendRun, TurnToken
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    ChildCommand,
    ChildOperation,
    CommandOutcome,
    ConnectionState,
    EnvelopeKind,
    QueuedEnvelope,
    type_adapter,
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record(value: Any) -> str:
    return _json(asdict(value))


def _parse[T](model: type[T], value: str) -> T:
    return type_adapter(model).validate_json(value)


def _invalid(message: str) -> PlannerError:
    return PlannerError(ErrorCode.validation, message)


class VpsDatabase:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @contextmanager
    def _transaction(
        self,
        transaction_context: Callable[[sqlite3.Connection], AbstractContextManager[None]]
        | None = None,
        before_commit: Callable[[sqlite3.Connection], None] | None = None,
    ) -> Iterator[sqlite3.Connection]:
        connection = connect(self.db_path)
        try:
            with nullcontext() if transaction_context is None else transaction_context(connection):
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                if before_commit is not None:
                    before_commit(connection)
                connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class ExecutorStore(VpsDatabase):
    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        connection = connect(self.db_path)
        try:
            return tuple(
                RetainedBackendRun(
                    ChildReference(
                        row["executor_incarnation"], row["child_id"], row["conversation_id"]
                    ),
                    TurnToken(row["conversation_id"], row["turn_number"]),
                    bool(row["delivery_uncertain"]),
                    row["stop_state"] == "closed",
                )
                for row in connection.execute("SELECT * FROM mac_runs")
            )
        finally:
            connection.close()

    def prepare_command(self, command: ChildCommand) -> None:
        """Commit uncertainty before transport. An old callback cannot reopen custody."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mac_runs WHERE conversation_id=? "
                "AND executor_incarnation=? AND child_id=?",
                (
                    command.child.conversation_id,
                    command.child.executor_incarnation,
                    command.child.child_id,
                ),
            ).fetchone()
            if row is None or row["stop_state"] == "closed":
                raise ValueError("Mac run no longer owns this conversation")
            if command.operation in (ChildOperation.start, ChildOperation.write_prompt):
                if row["stop_state"] != "open" or (
                    command.turn is not None and command.turn.turn_number < row["turn_number"]
                ):
                    raise ValueError("Mac run cannot accept a stale or stopped delivery")
                connection.execute(
                    "UPDATE mac_runs SET delivery_uncertain=1,turn_number=? "
                    "WHERE conversation_id=?",
                    (
                        row["turn_number"] if command.turn is None else command.turn.turn_number,
                        command.child.conversation_id,
                    ),
                )
            elif command.operation is ChildOperation.stop:
                connection.execute(
                    "UPDATE mac_runs SET stop_state='stopping' WHERE conversation_id=? "
                    "AND stop_state='open'",
                    (command.child.conversation_id,),
                )

    def record_prompt_outcome(self, command: ChildCommand, outcome: CommandOutcome) -> None:
        if (
            command.operation is not ChildOperation.write_prompt
            or outcome.disposition.value == "uncertain"
        ):
            return
        with self._transaction() as connection:
            connection.execute(
                "UPDATE mac_runs SET delivery_uncertain=0 WHERE conversation_id=? "
                "AND executor_incarnation=? AND child_id=? AND turn_number=? AND stop_state='open'",
                (
                    command.child.conversation_id,
                    command.child.executor_incarnation,
                    command.child.child_id,
                    command.turn.turn_number if command.turn is not None else 0,
                ),
            )

    def has_accepted_command_outcome(self, outcome: CommandOutcome) -> bool:
        """A retained result alone does not prove its original FIFO publication."""
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT outcome_json,output_confirmed FROM executor_command_outcomes "
                "WHERE executor_incarnation=? AND command_id=?",
                (outcome.child.executor_incarnation, outcome.command_id),
            ).fetchone()
            if row is None or row[0] != _record(outcome):
                return False
            return bool(row[1])
        finally:
            connection.close()

    def begin_connection(self, identity: ExecutorIdentity, now_ms: int) -> None:
        if (
            identity.protocol_version != PROTOCOL_VERSION
            or identity.spool_format_version != SPOOL_FORMAT_VERSION
            or identity.connection_generation < 1
        ):
            raise _invalid("Executor protocol or spool format is incompatible")
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT * FROM executor_connections WHERE active=1"
            ).fetchone()
            seen = connection.execute(
                "SELECT * FROM executor_connections WHERE executor_incarnation=?",
                (identity.executor_incarnation,),
            ).fetchone()
            if seen is not None and (
                not seen["active"]
                or identity.connection_generation <= seen["connection_generation"]
            ):
                raise _invalid("Executor connection generation is stale")
            if current is not None:
                connection.execute("UPDATE executor_connections SET active=0 WHERE active=1")
            connection.execute(
                "INSERT INTO executor_connections VALUES (?,?,?,?,?,?,1) "
                "ON CONFLICT(executor_incarnation) DO UPDATE SET "
                "connection_generation=excluded.connection_generation, "
                "identity_json=excluded.identity_json,connection_state=excluded.connection_state, "
                "observed_at_ms=excluded.observed_at_ms,lost_at_ms=NULL,active=1",
                (
                    identity.executor_incarnation,
                    identity.connection_generation,
                    _record(identity),
                    ConnectionState.reconciling.value,
                    now_ms,
                    None,
                ),
            )

    @staticmethod
    def _current(connection: sqlite3.Connection, identity: ExecutorIdentity) -> sqlite3.Row:
        current = connection.execute("SELECT * FROM executor_connections WHERE active=1").fetchone()
        if current is None or current["identity_json"] != _record(identity):
            raise _invalid("Executor connection generation is stale")
        return cast(sqlite3.Row, current)

    @classmethod
    def _connected(cls, connection: sqlite3.Connection, identity: ExecutorIdentity) -> sqlite3.Row:
        current = cls._current(connection, identity)
        if current["connection_state"] not in (
            ConnectionState.reconciling.value,
            ConnectionState.ready.value,
        ):
            raise _invalid("Executor connection is unavailable")
        return current

    def mark_ready(self, identity: ExecutorIdentity, now_ms: int) -> None:
        with self._transaction() as connection:
            self._connected(connection, identity)
            connection.execute(
                "UPDATE executor_connections SET connection_state=?,observed_at_ms=? "
                "WHERE active=1",
                (ConnectionState.ready.value, now_ms),
            )

    def mark_lost(self, identity: ExecutorIdentity, now_ms: int) -> None:
        with self._transaction() as connection:
            # A late close from an old socket cannot invalidate the replacement.
            row = connection.execute(
                "SELECT identity_json FROM executor_connections WHERE active=1"
            ).fetchone()
            if row is None or row[0] != _record(identity):
                return
            connection.execute(
                "UPDATE executor_connections SET connection_state=?,"
                "lost_at_ms=COALESCE(lost_at_ms,?),observed_at_ms=? "
                "WHERE active=1",
                (ConnectionState.disconnected.value, now_ms, now_ms),
            )

    def register_child(
        self, child: ChildReference, principal_kind: str, principal_id: str, now_ms: int
    ) -> None:
        with self._transaction() as connection:
            self.register_child_in_transaction(
                connection, child, principal_kind, principal_id, now_ms
            )

    @staticmethod
    def register_child_in_transaction(
        connection: sqlite3.Connection,
        child: ChildReference,
        principal_kind: str,
        principal_id: str,
        now_ms: int,
    ) -> None:
        existing = connection.execute(
            "SELECT * FROM executor_child_assignments WHERE executor_incarnation=? AND child_id=?",
            (child.executor_incarnation, child.child_id),
        ).fetchone()
        if existing:
            if (
                existing["conversation_id"],
                existing["principal_kind"],
                existing["principal_id"],
            ) != (child.conversation_id, principal_kind, principal_id):
                raise _invalid("Child assignment identity conflicts")
            return
        retained = connection.execute(
            "SELECT * FROM mac_runs WHERE conversation_id=?", (child.conversation_id,)
        ).fetchone()
        if retained is not None and retained["stop_state"] != "closed":
            raise _invalid("An unresolved Mac run already owns this conversation")
        turn = connection.execute(
            "SELECT COALESCE(MAX(turn_number),0) FROM conversation_turn_occurrences "
            "WHERE conversation_id=?",
            (child.conversation_id,),
        ).fetchone()
        connection.execute(
            "INSERT INTO mac_runs VALUES(?,?,?,?,?,?,0,'open') "
            "ON CONFLICT(conversation_id) DO UPDATE SET "
            "executor_incarnation=excluded.executor_incarnation,child_id=excluded.child_id,"
            "principal_kind=excluded.principal_kind,principal_id=excluded.principal_id,"
            "turn_number=excluded.turn_number,delivery_uncertain=0,stop_state='open'",
            (
                child.conversation_id,
                child.executor_incarnation,
                child.child_id,
                principal_kind,
                principal_id,
                turn[0],
            ),
        )
        connection.execute(
            "INSERT INTO executor_child_assignments VALUES (?,?,?,?,?,?,NULL)",
            (
                child.executor_incarnation,
                child.child_id,
                child.conversation_id,
                principal_kind,
                principal_id,
                now_ms,
            ),
        )

    def assigned_ticket_children(
        self, ticket_id: str, incarnation: str
    ) -> tuple[ChildReference, ...]:
        """Include absent, closed and delayed-start identities; retirement fences them all."""
        connection = connect(self.db_path)
        try:
            rows = connection.execute(
                "SELECT child_id,conversation_id FROM executor_child_assignments "
                "WHERE executor_incarnation=? AND principal_kind='ticket' AND principal_id=? "
                "ORDER BY child_id",
                (incarnation, ticket_id),
            ).fetchall()
            return tuple(ChildReference(incarnation, row[0], row[1]) for row in rows)
        finally:
            connection.close()

    def require_ready_connection(self, identity: ExecutorIdentity) -> None:
        """Read only: also safe while the move writer owns its short write transaction."""
        connection = connect(self.db_path)
        try:
            row = self._current(connection, identity)
            if row["connection_state"] != ConnectionState.ready.value:
                raise _invalid("Executor source is not ready")
        finally:
            connection.close()

    def has_closed_child_assignment(self, child: ChildReference) -> bool:
        """Historical identity evidence only; this grants no child/domain authority."""
        connection = connect(self.db_path)
        try:
            return (
                connection.execute(
                    "SELECT 1 FROM executor_child_assignments WHERE executor_incarnation=? "
                    "AND child_id=? AND conversation_id=? AND closed_at_ms IS NOT NULL",
                    (child.executor_incarnation, child.child_id, child.conversation_id),
                ).fetchone()
                is not None
            )
        finally:
            connection.close()

    def child_assignment(self, child: ChildReference) -> tuple[str, str] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT principal_kind,principal_id FROM executor_child_assignments "
                "WHERE executor_incarnation=? AND child_id=? AND conversation_id=? "
                "AND closed_at_ms IS NULL",
                (child.executor_incarnation, child.child_id, child.conversation_id),
            ).fetchone()
            return (row[0], row[1]) if row else None
        finally:
            connection.close()

    def close_child(self, child: ChildReference, now_ms: int) -> None:
        with self._transaction() as connection:
            self.close_child_in_transaction(connection, child, now_ms)

    @staticmethod
    def close_child_in_transaction(
        connection: sqlite3.Connection,
        child: ChildReference,
        now_ms: int,
    ) -> None:
        """Retire assignment and asks inside the caller's existing transaction."""
        row = connection.execute(
            "SELECT stop_state FROM mac_runs WHERE conversation_id=? "
            "AND executor_incarnation=? AND child_id=?",
            (child.conversation_id, child.executor_incarnation, child.child_id),
        ).fetchone()
        current = connection.execute(
            "SELECT executor_incarnation FROM executor_connections WHERE active=1"
        ).fetchone()
        if row is None or (
            row[0] == "unproved" and (current is None or current[0] == child.executor_incarnation)
        ):
            raise ValueError("Mac run termination remains unproved")
        connection.execute(
            "UPDATE mac_runs SET stop_state='closed' WHERE conversation_id=? "
            "AND executor_incarnation=? AND child_id=?",
            (child.conversation_id, child.executor_incarnation, child.child_id),
        )
        connection.execute(
            "UPDATE executor_child_assignments SET closed_at_ms=? "
            "WHERE executor_incarnation=? AND child_id=? AND conversation_id=?",
            (now_ms, child.executor_incarnation, child.child_id, child.conversation_id),
        )

    @staticmethod
    def record_command_outcome_in_transaction(
        connection: sqlite3.Connection, outcome: CommandOutcome, *, output_confirmed: bool = False
    ) -> None:
        existing = connection.execute(
            "SELECT outcome_json FROM executor_command_outcomes "
            "WHERE executor_incarnation=? AND command_id=?",
            (outcome.child.executor_incarnation, outcome.command_id),
        ).fetchone()
        if existing is not None:
            if existing[0] != _record(outcome):
                raise _invalid("Command outcome conflicts with first result")
            if output_confirmed:
                connection.execute(
                    "UPDATE executor_command_outcomes SET output_confirmed=1 "
                    "WHERE executor_incarnation=? AND command_id=?",
                    (outcome.child.executor_incarnation, outcome.command_id),
                )
            return
        connection.execute(
            "INSERT INTO executor_command_outcomes VALUES (?,?,?,?)",
            (
                outcome.child.executor_incarnation,
                outcome.command_id,
                _record(outcome),
                int(output_confirmed),
            ),
        )

    def verify_connection(self, identity: ExecutorIdentity) -> None:
        with self._transaction() as connection:
            self._connected(connection, identity)

    @staticmethod
    def _mark_refused_cleanup_evidence(
        connection: sqlite3.Connection, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> None:
        # A refused domain command is an ordinary answer. Only a required native
        # publication can invalidate evidence that cleanup waits for.
        if envelope.kind in (EnvelopeKind.backend_event, EnvelopeKind.child_stopped):
            child_payload = envelope.payload.get("child")
        elif envelope.kind is EnvelopeKind.answer_application:
            ask = envelope.payload.get("ask")
            child_payload = ask.get("child") if isinstance(ask, dict) else None
        else:
            return
        # A malformed publication still needs a refusal receipt. Its child alone
        # is enough to fence cleanup, even when the rest cannot be decoded.
        try:
            child = type_adapter(ChildReference).validate_python(child_payload)
        except ValueError:
            return
        if (
            child.executor_incarnation != identity.executor_incarnation
            or child.conversation_id != envelope.conversation_id
        ):
            return
        connection.execute(
            "UPDATE mac_runs SET stop_state='unproved' WHERE conversation_id=? "
            "AND executor_incarnation=? AND child_id=? AND stop_state!='closed'",
            (child.conversation_id, child.executor_incarnation, child.child_id),
        )
