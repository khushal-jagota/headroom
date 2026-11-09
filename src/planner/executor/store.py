"""VPS run and child-assignment persistence, with shared transaction mechanics."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

from planner.conversation.backends.contracts import (
    BackendSpawnFailed,
    RetainedBackendRun,
    TurnToken,
)
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect
from planner.execution_identity import ChildReference, TicketLocale
from planner.executor.protocol import (
    ChildCommand,
    ChildOperation,
    CommandOutcome,
)
from planner.executor.vps_presence_store import current_incarnation


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record(value: Any) -> str:
    return _json(asdict(value))


def _invalid(message: str) -> PlannerError:
    return PlannerError(ErrorCode.validation, message)


class VpsDatabase:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class ExecutorStore(VpsDatabase):
    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        """Every run, with whether a stop is in the mailbox and whether its process is the
        current one. Only an open run of the current incarnation is adopted live."""
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
                    stop_requested=row["stop_state"] == "stopping",
                    incarnation_current=row["executor_incarnation"] == row["current_incarnation"],
                )
                for row in connection.execute(
                    "SELECT r.*,p.executor_incarnation AS current_incarnation FROM mac_runs AS r "
                    "LEFT JOIN mac_executor_presence AS p ON p.singleton=1"
                )
            )
        finally:
            connection.close()

    def unresolved_runs_of_replaced_incarnations(self) -> tuple[ChildReference, ...]:
        """Runs not closed whose process is no longer the current one: each waits for its
        conversation's items to drain before it is closed."""
        connection = connect(self.db_path)
        try:
            return tuple(
                ChildReference(row[0], row[1], row[2])
                for row in connection.execute(
                    "SELECT r.executor_incarnation,r.child_id,r.conversation_id FROM mac_runs AS r "
                    "LEFT JOIN mac_executor_presence AS p ON p.singleton=1 "
                    "WHERE r.stop_state<>'closed' "
                    "AND r.executor_incarnation IS NOT p.executor_incarnation"
                )
            )
        finally:
            connection.close()

    def prepare_command(self, command: ChildCommand) -> None:
        """Commit uncertainty before transport. An old callback cannot reopen custody."""
        with self._transaction() as connection:
            self.prepare_command_in_transaction(connection, command)

    @staticmethod
    def prepare_command_in_transaction(
        connection: sqlite3.Connection, command: ChildCommand
    ) -> None:
        """The mac_runs half of a mailbox put, in the put's own transaction."""
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
                "UPDATE mac_runs SET delivery_uncertain=1,turn_number=? WHERE conversation_id=?",
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
        with self._transaction() as connection:
            self.record_prompt_outcome_in_transaction(connection, command, outcome)

    @staticmethod
    def record_prompt_outcome_in_transaction(
        connection: sqlite3.Connection, command: ChildCommand, outcome: CommandOutcome
    ) -> None:
        """A prompt the Mac answered for: its delivery is no longer uncertain."""
        if (
            command.operation is not ChildOperation.write_prompt
            or outcome.disposition.value == "uncertain"
        ):
            return
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

    def register_child(
        self, child: ChildReference, principal_kind: str, principal_id: str, now_ms: int
    ) -> None:
        with self._transaction() as connection:
            # The binding fence: a child is assigned only under the current process, read
            # in the same transaction that writes the assignment.
            current = current_incarnation(connection)
            if current != child.executor_incarnation:
                from planner.tickets.locale_configuration import execution_unavailable_reason

                raise BackendSpawnFailed(
                    "The Mac executor process changed before this child was assigned"
                    if current is not None
                    else execution_unavailable_reason(connection, TicketLocale.macos)
                )
            existing = connection.execute(
                "SELECT * FROM executor_child_assignments "
                "WHERE executor_incarnation=? AND child_id=?",
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
            # The last turn whose prompt committed, not the one being delivered now: its
            # write_prompt raises turn_number when its row commits. A VPS crash between
            # this assignment and that commit must not leave a turn that seems sent.
            turn = connection.execute(
                "SELECT COALESCE(MAX(turn_number),0) FROM conversation_lifecycle_facts "
                "WHERE conversation_id=? AND kind='turn' AND status<>'intent'",
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
            return self.open_child_assignment(connection, child)
        finally:
            connection.close()

    @staticmethod
    def open_child_assignment(
        connection: sqlite3.Connection, child: ChildReference
    ) -> tuple[str, str] | None:
        row = connection.execute(
            "SELECT principal_kind,principal_id FROM executor_child_assignments "
            "WHERE executor_incarnation=? AND child_id=? AND conversation_id=? "
            "AND closed_at_ms IS NULL",
            (child.executor_incarnation, child.child_id, child.conversation_id),
        ).fetchone()
        return (row[0], row[1]) if row else None

    def close_child(self, child: ChildReference, now_ms: int) -> None:
        with self._transaction() as connection:
            self.close_child_in_transaction(connection, child, now_ms)

    @staticmethod
    def close_child_in_transaction(
        connection: sqlite3.Connection, child: ChildReference, now_ms: int
    ) -> None:
        """Close the run and its assignment in the caller's transaction.

        A child already closed is left as it is, so closing twice keeps the first close
        and never touches a later run of the same conversation."""
        if connection.execute(
            "SELECT 1 FROM executor_child_assignments WHERE executor_incarnation=? "
            "AND child_id=? AND conversation_id=? AND closed_at_ms IS NOT NULL",
            (child.executor_incarnation, child.child_id, child.conversation_id),
        ).fetchone():
            return
        row = connection.execute(
            "SELECT stop_state FROM mac_runs WHERE conversation_id=? "
            "AND executor_incarnation=? AND child_id=?",
            (child.conversation_id, child.executor_incarnation, child.child_id),
        ).fetchone()
        if row is None:
            raise ValueError("Mac run does not match this child")
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
