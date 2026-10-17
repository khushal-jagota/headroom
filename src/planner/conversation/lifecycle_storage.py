"""SQLite lifecycle transitions and exclusive ownership of ephemeral capabilities."""

from __future__ import annotations

import fcntl
import inspect
import sqlite3
from pathlib import Path
from typing import IO

from planner.conversation.lifecycle import (
    ConversationTransactionParticipant,
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
    LifecycleTransition,
)
from planner.conversation.logic.lifecycle_transitions import validate_fact, validate_transition


class ConversationRuntimeAlreadyOwned(RuntimeError):
    """Another runtime owns this canonical database. No reconciliation occurred."""


def acquire_runtime_ownership(db_path: str) -> IO[bytes]:
    path = Path(db_path).resolve()
    lock = path.with_name(path.name + ".conversation-owner.lock").open("a+b")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        lock.close()
        raise ConversationRuntimeAlreadyOwned(str(path)) from error
    return lock


def read_facts(conn: sqlite3.Connection, conversation_id: str) -> tuple[LifecycleFact, ...]:
    rows = conn.execute(
        "SELECT kind,identity,turn_number,operation,status,provider_identity "
        "FROM conversation_lifecycle_facts WHERE conversation_id=? ORDER BY kind,identity",
        (conversation_id,),
    ).fetchall()
    return tuple(
        LifecycleFact(
            LifecycleKind(row[0]),
            str(row[1]),
            int(row[2]),
            LifecycleOperation(str(row[3])),
            LifecycleStatus(row[4]),
            None if row[5] is None else str(row[5]),
        )
        for row in rows
    )


def apply_facts(
    conn: sqlite3.Connection, conversation_id: str, facts: tuple[LifecycleFact, ...]
) -> None:
    for fact in facts:
        validate_fact(fact)
        previous = next(
            (
                old
                for old in read_facts(conn, conversation_id)
                if old.kind is fact.kind and old.identity == fact.identity
            ),
            None,
        )
        if previous is not None:
            validate_transition(previous, fact)
        conn.execute(
            "INSERT INTO conversation_lifecycle_facts "
            "(conversation_id,kind,identity,turn_number,operation,status,provider_identity) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(conversation_id,kind,identity) "
            "DO UPDATE SET status=excluded.status",
            (
                conversation_id,
                fact.kind,
                fact.identity,
                fact.turn_number,
                fact.operation,
                fact.status,
                fact.provider_identity,
            ),
        )


def participate(
    conn: sqlite3.Connection,
    participant: ConversationTransactionParticipant | None,
    candidate: LifecycleTransition,
) -> None:
    if participant is None:
        return

    # Give the participant the actual connection, but retain sole commit ownership.
    def authorize(
        action: int,
        first: str | None,
        second: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        return (
            sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT)
            else sqlite3.SQLITE_OK
        )

    conn.set_authorizer(authorize)
    try:
        result = participant(conn, candidate)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("conversation transaction participants must be synchronous")
    finally:
        conn.set_authorizer(None)
