"""VPS run, connection and answer persistence, with shared transaction mechanics."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import asdict
from typing import Any, cast
from uuid import uuid4

from pydantic import TypeAdapter

from planner.conversation.backends.contracts import RetainedBackendRun, TurnToken
from planner.conversation.events import UserInputAnswer, UserInputQuestion
from planner.conversation.logic.user_input import ordered_user_input_answers
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity
from planner.executor.logic.reconciliation import (
    answer_target_is_open,
    callback_reachability,
    preserved_reachability,
)
from planner.executor.protocol import (
    AnswerAdmission,
    AnswerApplication,
    AskEvidence,
    AskReachability,
    ChildCommand,
    ChildInventory,
    ChildOperation,
    CommandOutcome,
    ConnectionState,
    DurableAnswer,
    EnvelopeKind,
    ExecutorTurnContext,
    QueuedEnvelope,
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record(value: Any) -> str:
    return _json(asdict(value))


def _parse[T](model: type[T], value: str) -> T:
    return TypeAdapter(model).validate_json(value)


def _ask_key(ask: AskReference) -> str:
    return hashlib.sha256(_record(ask).encode()).hexdigest()


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
            connection.execute(
                "UPDATE executor_asks SET reachability=?,observed_at_ms=? "
                "WHERE reachability='reachable'",
                (AskReachability.uncertain.value, now_ms),
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
            connection.execute(
                "UPDATE executor_asks SET reachability=?,observed_at_ms=? "
                "WHERE reachability='reachable'",
                (AskReachability.uncertain.value, now_ms),
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
        connection.execute(
            "UPDATE executor_asks SET reachability=?,observed_at_ms=? "
            "WHERE executor_incarnation=? AND child_id=?",
            (
                AskReachability.child_gone.value,
                now_ms,
                child.executor_incarnation,
                child.child_id,
            ),
        )

    def publish_ask(self, ask: AskReference, option_ids: tuple[str, ...], now_ms: int) -> None:
        with self._transaction() as connection:
            self.publish_ask_in_transaction(connection, ask, option_ids, now_ms)

    @staticmethod
    def publish_ask_in_transaction(
        connection: sqlite3.Connection, ask: AskReference, option_ids: tuple[str, ...], now_ms: int
    ) -> None:
        if (
            ask.kind is not AskKind.permission
            or ask.turn.conversation_id != ask.child.conversation_id
            or not option_ids
        ):
            raise _invalid("Ask identity or options are invalid")
        previous = connection.execute(
            "SELECT option_ids_json FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
        ).fetchone()
        if previous is not None:
            if previous[0] != _json(option_ids):
                raise _invalid("Ask options conflict with original ask")
            return
        connection.execute(
            "INSERT INTO executor_asks "
            "(ask_key,reference_json,option_ids_json,executor_incarnation,"
            "child_id,conversation_id,connection_generation,reachability,observed_at_ms) "
            "VALUES (?,?,?,?,?,?,0,?,?)",
            (
                _ask_key(ask),
                _record(ask),
                _json(option_ids),
                ask.child.executor_incarnation,
                ask.child.child_id,
                ask.child.conversation_id,
                AskReachability.uncertain.value,
                now_ms,
            ),
        )

    def observe_children(
        self, identity: ExecutorIdentity, children: tuple[ChildInventory, ...], now_ms: int
    ) -> None:
        """Update callback reachability once, from this connection's exact live children."""
        with self._transaction() as connection:
            self._connected(connection, identity)
            for row in connection.execute("SELECT * FROM executor_asks").fetchall():
                ask = _parse(AskReference, row["reference_json"])
                reachability = preserved_reachability(
                    AskReachability(row["reachability"]), callback_reachability(ask, children)
                )
                connection.execute(
                    "UPDATE executor_asks SET connection_generation=?,reachability=?,"
                    "observed_at_ms=? WHERE ask_key=?",
                    (identity.connection_generation, reachability.value, now_ms, row["ask_key"]),
                )

    @staticmethod
    def _answer(row: sqlite3.Row) -> DurableAnswer:
        return DurableAnswer(
            row["answer_id"],
            _parse(AskReference, row["reference_json"]),
            row["option_id"],
            row["accepted_at_vps_ms"],
            TypeAdapter(tuple[UserInputAnswer, ...]).validate_json(row["user_input_answers_json"])
            if row["user_input_answers_json"] is not None
            else None,
        )

    @staticmethod
    def _turn_context(
        connection: sqlite3.Connection, conversation_id: str
    ) -> ExecutorTurnContext | None:
        row = connection.execute(
            "SELECT * FROM mac_runs WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        if row is None:
            return None
        facts = connection.execute(
            "SELECT operation,provider_identity,status FROM conversation_lifecycle_facts "
            "WHERE conversation_id=? AND turn_number=? AND kind='request'",
            (conversation_id, row["turn_number"]),
        ).fetchall()
        turn = connection.execute(
            "SELECT status FROM conversation_lifecycle_facts WHERE conversation_id=? "
            "AND turn_number=? AND kind='turn'",
            (conversation_id, row["turn_number"]),
        ).fetchone()
        return ExecutorTurnContext(
            ChildReference(row["executor_incarnation"], row["child_id"], conversation_id),
            TurnToken(conversation_id, row["turn_number"]),
            tuple(
                f[1]
                for f in facts
                if f[0] == "permission" and f[2] in ("pending", "answering") and f[1] is not None
            ),
            tuple(
                f[1]
                for f in facts
                if f[0] == "user_input" and f[2] in ("pending", "answering") and f[1] is not None
            ),
            turn is None or turn[0] != "running",
            row["stop_state"] == "closed",
            bool(row["delivery_uncertain"]),
            row["stop_state"] != "open",
        )

    def public_request_id(self, ask: AskReference) -> str | None:
        """Resolve one persisted original public identity for an exact native ask."""
        connection = connect(self.db_path)
        try:
            evidence = connection.execute(
                "SELECT reference_json FROM executor_asks WHERE ask_key=?",
                (_ask_key(ask),),
            ).fetchone()
            if evidence is None or _parse(AskReference, str(evidence[0])) != ask:
                return None
            matches = connection.execute(
                "SELECT identity FROM conversation_lifecycle_facts WHERE conversation_id=? "
                "AND kind='request' AND turn_number=? AND operation=? AND provider_identity=?",
                (ask.child.conversation_id, ask.turn.turn_number, ask.kind.value, ask.ask_id),
            ).fetchall()
            return str(matches[0][0]) if len(matches) == 1 else None
        finally:
            connection.close()

    def turn_context(self, conversation_id: str) -> ExecutorTurnContext | None:
        connection = connect(self.db_path)
        try:
            return self._turn_context(connection, conversation_id)
        finally:
            connection.close()

    @classmethod
    def _answer_target_is_open(
        cls, connection: sqlite3.Connection, ask: AskReference, row: sqlite3.Row
    ) -> bool:
        assignment = connection.execute(
            "SELECT conversation_id,closed_at_ms FROM executor_child_assignments "
            "WHERE executor_incarnation=? AND child_id=?",
            (ask.child.executor_incarnation, ask.child.child_id),
        ).fetchone()
        return answer_target_is_open(
            AnswerAdmission(
                ask,
                cls._turn_context(connection, ask.child.conversation_id),
                assignment is not None
                and assignment[0] == ask.child.conversation_id
                and assignment[1] is None,
                AskReachability(row["reachability"]),
            )
        )

    def answer_target_is_open(self, ask: AskReference) -> bool:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
            ).fetchone()
            return row is not None and self._answer_target_is_open(connection, ask, row)
        finally:
            connection.close()

    def answer_is_deliverable(self, answer: DurableAnswer, identity: ExecutorIdentity) -> bool:
        connection = connect(self.db_path)
        try:
            current = connection.execute(
                "SELECT * FROM executor_connections WHERE active=1"
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(answer.ask),)
            ).fetchone()
            return (
                current is not None
                and current["identity_json"] == _record(identity)
                and current["connection_state"] == ConnectionState.ready.value
                and identity.executor_incarnation == answer.ask.child.executor_incarnation
                and row is not None
                and self._answer(row) == answer
                and row["applied_at_mac_ms"] is None
                and row["connection_generation"] == identity.connection_generation
                and row["reachability"] == AskReachability.reachable.value
                and self._answer_target_is_open(connection, answer.ask, row)
            )
        finally:
            connection.close()

    def unapplied_answers(self) -> tuple[DurableAnswer, ...]:
        connection = connect(self.db_path)
        try:
            return tuple(
                self._answer(row)
                for row in connection.execute(
                    "SELECT * FROM executor_asks WHERE answer_id IS NOT NULL "
                    "AND applied_at_mac_ms IS NULL"
                )
            )
        finally:
            connection.close()

    def choose_answer(
        self, ask: AskReference, option_id: str, generation: int, now_ms: int
    ) -> DurableAnswer:
        if ask.kind is not AskKind.permission:
            raise _invalid("Permission selection requires a permission ask")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
            ).fetchone()
            if row is None:
                raise _invalid("Unknown permission ask")
            if row["answer_id"] is not None:
                if row["option_id"] != option_id:
                    raise _invalid("Permission choice is already recorded")
                return self._answer(row)
            if not self._answer_target_is_open(connection, ask, row):
                raise _invalid("Original callback target is closed or unknown")
            if option_id not in json.loads(row["option_ids_json"]):
                raise _invalid("Unknown permission option")
            answer = DurableAnswer(str(uuid4()), ask, option_id, now_ms)
            connection.execute(
                "UPDATE executor_asks SET answer_id=?,option_id=?,accepted_at_vps_ms=? "
                "WHERE ask_key=?",
                (answer.answer_id, answer.option_id, now_ms, _ask_key(ask)),
            )
            return answer

    def record_application(
        self, application: AnswerApplication, *, identity: ExecutorIdentity
    ) -> None:
        with self._transaction() as connection:
            self._connected(connection, identity)
            self.record_application_in_transaction(connection, application)

    @staticmethod
    def record_application_in_transaction(
        connection: sqlite3.Connection, application: AnswerApplication
    ) -> None:
        row = connection.execute(
            "SELECT answer_id,applied_at_mac_ms FROM executor_asks WHERE ask_key=?",
            (_ask_key(application.ask),),
        ).fetchone()
        if row is None or row[0] != application.answer_id:
            raise _invalid("Application does not match the recorded answer")
        if row[1] is not None and row[1] != application.applied_at_mac_ms:
            raise _invalid("Application conflicts with the first acknowledgment")
        connection.execute(
            "UPDATE executor_asks SET applied_at_mac_ms=? WHERE ask_key=?",
            (application.applied_at_mac_ms, _ask_key(application.ask)),
        )

    def ask_evidence(self, ask: AskReference) -> AskEvidence | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
            ).fetchone()
            if row is None:
                return None
            return AskEvidence(
                ask,
                row["connection_generation"],
                AskReachability(row["reachability"]),
                row["observed_at_ms"],
                row["answer_id"],
                row["applied_at_mac_ms"],
            )
        finally:
            connection.close()

    def evidence_for_conversation(self, conversation_id: str) -> tuple[AskEvidence, ...]:
        connection = connect(self.db_path)
        try:
            rows = connection.execute(
                "SELECT * FROM executor_asks WHERE conversation_id=?", (conversation_id,)
            ).fetchall()
            return tuple(
                AskEvidence(
                    _parse(AskReference, row["reference_json"]),
                    row["connection_generation"],
                    AskReachability(row["reachability"]),
                    row["observed_at_ms"],
                    row["answer_id"],
                    row["applied_at_mac_ms"],
                )
                for row in rows
            )
        finally:
            connection.close()

    @classmethod
    def observe_callback_in_transaction(
        cls,
        connection: sqlite3.Connection,
        ask: AskReference,
        identity: ExecutorIdentity,
        now_ms: int,
    ) -> None:
        cls._connected(connection, identity)
        if ask.child.executor_incarnation != identity.executor_incarnation:
            raise _invalid("Callback belongs to a different executor incarnation")
        row = connection.execute(
            "SELECT reachability FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
        ).fetchone()
        if row is not None and AskReachability(row[0]) in (
            AskReachability.child_gone,
            AskReachability.callback_gone,
        ):
            return
        changed = connection.execute(
            "UPDATE executor_asks SET connection_generation=?,reachability=?,observed_at_ms=? "
            "WHERE ask_key=?",
            (
                identity.connection_generation,
                AskReachability.reachable.value,
                now_ms,
                _ask_key(ask),
            ),
        ).rowcount
        if changed != 1:
            raise _invalid("Callback has no accepted ask row")

    def answer_by_id(self, answer_id: str) -> DurableAnswer | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE answer_id=?", (answer_id,)
            ).fetchone()
            return self._answer(row) if row is not None else None
        finally:
            connection.close()

    def recorded_answer(self, ask: AskReference) -> DurableAnswer | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
            ).fetchone()
            return self._answer(row) if row is not None and row["answer_id"] is not None else None
        finally:
            connection.close()

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

    @classmethod
    def revoke_turn_callbacks_in_transaction(
        cls,
        connection: sqlite3.Connection,
        child: ChildReference,
        turn: TurnToken,
        identity: ExecutorIdentity,
        now_ms: int,
    ) -> None:
        cls._connected(connection, identity)
        rows = connection.execute(
            "SELECT ask_key,reference_json FROM executor_asks "
            "WHERE executor_incarnation=? AND child_id=? AND conversation_id=?",
            (child.executor_incarnation, child.child_id, child.conversation_id),
        ).fetchall()
        for row in rows:
            ask = _parse(AskReference, row["reference_json"])
            if ask.turn == turn:
                connection.execute(
                    "UPDATE executor_asks SET reachability=?,observed_at_ms=? WHERE ask_key=?",
                    (AskReachability.callback_gone.value, now_ms, row["ask_key"]),
                )

    def publish_user_input_request(
        self, ask: AskReference, questions: tuple[UserInputQuestion, ...], now_ms: int
    ) -> None:
        with self._transaction() as connection:
            self.publish_user_input_request_in_transaction(connection, ask, questions, now_ms)

    @staticmethod
    def publish_user_input_request_in_transaction(
        connection: sqlite3.Connection,
        ask: AskReference,
        questions: tuple[UserInputQuestion, ...],
        now_ms: int,
    ) -> None:
        question_ids = [question.question_id for question in questions]
        if (
            ask.kind is not AskKind.user_input
            or ask.turn.conversation_id != ask.child.conversation_id
            or not question_ids
            or any(not question_id.strip() for question_id in question_ids)
            or len(set(question_ids)) != len(question_ids)
        ):
            raise _invalid("User input identity or original questions are invalid")
        original = _json([asdict(question) for question in questions])
        row = connection.execute(
            "SELECT questions_json FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
        ).fetchone()
        if row is not None:
            if row[0] != original:
                raise _invalid("User input questions conflict with original request")
            return
        connection.execute(
            "INSERT INTO executor_asks "
            "(ask_key,reference_json,option_ids_json,ask_kind,questions_json,executor_incarnation,"
            "child_id,conversation_id,connection_generation,reachability,observed_at_ms) "
            "VALUES (?,?,?,?,?,?,?, ?,0,?,?)",
            (
                _ask_key(ask),
                _record(ask),
                "[]",
                AskKind.user_input.value,
                original,
                ask.child.executor_incarnation,
                ask.child.child_id,
                ask.child.conversation_id,
                AskReachability.uncertain.value,
                now_ms,
            ),
        )

    def choose_user_input_answer(
        self, ask: AskReference, answers: tuple[UserInputAnswer, ...], generation: int, now_ms: int
    ) -> DurableAnswer:
        if ask.kind is not AskKind.user_input:
            raise _invalid("User input selection requires a question request")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
            ).fetchone()
            if row is None:
                raise _invalid("Unknown user input request")
            questions = TypeAdapter(tuple[UserInputQuestion, ...]).validate_json(
                row["questions_json"]
            )
            normalized = ordered_user_input_answers(questions, answers)
            if normalized is None:
                raise _invalid("User input requires a valid complete answer map")
            answer_json = _json([asdict(answer) for answer in normalized])
            if row["answer_id"] is not None:
                if row["user_input_answers_json"] != answer_json:
                    raise _invalid("User input choice is already recorded")
                return self._answer(row)
            if not self._answer_target_is_open(connection, ask, row):
                raise _invalid("Original callback target is closed or unknown")
            answer = DurableAnswer(str(uuid4()), ask, None, now_ms, normalized)
            connection.execute(
                "UPDATE executor_asks SET answer_id=?,user_input_answers_json=?,"
                "accepted_at_vps_ms=? "
                "WHERE ask_key=?",
                (answer.answer_id, answer_json, now_ms, _ask_key(ask)),
            )
            return answer

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
            child = TypeAdapter(ChildReference).validate_python(child_payload)
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
