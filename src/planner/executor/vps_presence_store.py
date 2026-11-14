"""Presence and incarnation SQL, and the incarnation fence.

Thin helpers over mac_executor_presence and executor_incarnations, shared by the lease
handler, the mailbox, child assignment and the item path, so all of them fence by the
same row. The *_in_transaction helpers run inside the caller's transaction and never
commit; the binding fence check runs inside the transaction that writes.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import ValidationError

from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect
from planner.execution_identity import ExecutorIdentity
from planner.executor.contracts import AcceptedLease, PresenceRecord
from planner.executor.logic.presence import continuous_since_after_lease
from planner.executor.protocol import (
    ExecutorRefusal,
    LeaseRequest,
    as_object,
    type_adapter,
)
from planner.machines.contracts import ORIGINAL_MAC_MACHINE_ID


def _json(value: object) -> str:
    return json.dumps(
        as_object(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _parse[T](text: str | None, value_type: type[T]) -> T | None:
    return None if text is None else type_adapter(value_type).validate_json(text)


def _parse_lease(text: str | None) -> LeaseRequest | None:
    """The stored lease is diagnostic only: one that no longer parses (an older shape)
    reads as no lease rather than failing every presence read."""
    try:
        return _parse(text, LeaseRequest)
    except ValidationError:
        return None


def not_current(executor_incarnation: str) -> PlannerError:
    return PlannerError(
        ErrorCode.executor_incarnation_not_current,
        "This Mac executor process is not the current one.",
        {"executor_incarnation": executor_incarnation},
    )


def read_presence(
    conn: sqlite3.Connection, machine_id: str = ORIGINAL_MAC_MACHINE_ID
) -> PresenceRecord | None:
    row = conn.execute(
        "SELECT executor_incarnation,identity_json,last_contact_at_ms,last_lease_at_ms,"
        "continuous_since_ms,going_to_sleep_at_ms,lease_json,refusal_json "
        "FROM mac_executor_presence WHERE machine_id=?",
        (machine_id,),
    ).fetchone()
    if row is None:
        return None
    return PresenceRecord(
        executor_incarnation=row[0],
        identity=_parse(row[1], ExecutorIdentity),
        last_contact_at_ms=row[2],
        last_lease_at_ms=row[3],
        continuous_since_ms=row[4],
        going_to_sleep_at_ms=row[5],
        lease=_parse_lease(row[6]),
        refusal=_parse(row[7], ExecutorRefusal),
    )


def current_incarnation(
    conn: sqlite3.Connection, machine_id: str = ORIGINAL_MAC_MACHINE_ID
) -> str | None:
    row = conn.execute(
        "SELECT executor_incarnation FROM mac_executor_presence WHERE machine_id=?",
        (machine_id,),
    ).fetchone()
    return None if row is None else row[0]


def require_current_incarnation_in_transaction(
    conn: sqlite3.Connection,
    executor_incarnation: str,
    machine_id: str,
) -> None:
    """The binding fence: raise executor_incarnation_not_current unless executor_incarnation
    is the presence row's current one. Call it inside the transaction that writes."""
    if current_incarnation(conn, machine_id) != executor_incarnation:
        raise not_current(executor_incarnation)


def require_current_incarnation(
    db_path: str, identity: ExecutorIdentity, machine_id: str
) -> None:
    """The fence on its own connection, before any write. Blocking: call it on a worker
    thread (core.task_settlement.run_on_worker_thread)."""
    conn = connect(db_path)
    try:
        require_current_incarnation_in_transaction(
            conn, identity.executor_incarnation, machine_id
        )
    finally:
        conn.close()


def accept_lease_in_transaction(
    conn: sqlite3.Connection,
    request: LeaseRequest,
    now_ms: int,
    machine_id: str = ORIGINAL_MAC_MACHINE_ID,
) -> AcceptedLease:
    """Write one accepted lease. The caller has already admitted its versions.

    A replaced incarnation is refused (executor_incarnation_not_current): its process must
    exit. An incarnation not seen before becomes current, and the previous current one is
    marked replaced; the caller settles the replaced incarnation's commands in this same
    transaction. The lease clears any refusal.
    """
    incarnation = request.identity.executor_incarnation
    seen = conn.execute(
        "SELECT replaced_at_ms,machine_id FROM executor_incarnations WHERE executor_incarnation=?",
        (incarnation,),
    ).fetchone()
    if seen is not None and (seen[0] is not None or seen[1] != machine_id):
        raise not_current(incarnation)
    previous = read_presence(conn, machine_id)
    replaced = None if previous is None else previous.executor_incarnation
    new_incarnation = replaced != incarnation
    if new_incarnation:
        conn.execute(
            "UPDATE executor_incarnations SET replaced_at_ms=? "
            "WHERE machine_id=? AND replaced_at_ms IS NULL AND executor_incarnation<>?",
            (now_ms, machine_id, incarnation),
        )
        conn.execute(
            "INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,"
            "replaced_at_ms,machine_id) VALUES (?,?,NULL,?) ON "
            "CONFLICT(executor_incarnation) DO NOTHING",
            (incarnation, now_ms, machine_id),
        )
    # Set by the first going-to-sleep lease of this process; cleared by a lease without it.
    going_to_sleep_at_ms = None
    if request.going_to_sleep:
        kept = (
            None
            if previous is None or new_incarnation
            else previous.going_to_sleep_at_ms
        )
        going_to_sleep_at_ms = now_ms if kept is None else kept
    previous_contact = None if previous is None else previous.last_contact_at_ms
    current = PresenceRecord(
        executor_incarnation=incarnation,
        identity=request.identity,
        last_contact_at_ms=(
            now_ms if previous_contact is None else max(now_ms, previous_contact)
        ),
        last_lease_at_ms=now_ms,
        continuous_since_ms=continuous_since_after_lease(
            previous, new_incarnation=new_incarnation, now_ms=now_ms
        ),
        going_to_sleep_at_ms=going_to_sleep_at_ms,
        lease=request,
        refusal=None,
    )
    _write_presence(conn, current, machine_id)
    return AcceptedLease(
        previous=previous,
        current=current,
        new_incarnation=new_incarnation,
        replaced_incarnation=replaced if new_incarnation else None,
    )


def _write_presence(
    conn: sqlite3.Connection, record: PresenceRecord, machine_id: str
) -> None:
    values: tuple[Any, ...] = (
        machine_id,
        record.executor_incarnation,
        None if record.identity is None else _json(record.identity),
        record.last_contact_at_ms,
        record.last_lease_at_ms,
        record.continuous_since_ms,
        record.going_to_sleep_at_ms,
        None if record.lease is None else _json(record.lease),
        None if record.refusal is None else _json(record.refusal),
    )
    conn.execute(
        "INSERT INTO mac_executor_presence(machine_id,executor_incarnation,identity_json,"
        "last_contact_at_ms,last_lease_at_ms,continuous_since_ms,going_to_sleep_at_ms,"
        "lease_json,refusal_json) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(machine_id) DO UPDATE SET executor_incarnation=excluded.executor_incarnation,"
        "identity_json=excluded.identity_json,last_contact_at_ms=excluded.last_contact_at_ms,"
        "last_lease_at_ms=excluded.last_lease_at_ms,"
        "continuous_since_ms=excluded.continuous_since_ms,"
        "going_to_sleep_at_ms=excluded.going_to_sleep_at_ms,lease_json=excluded.lease_json,"
        "refusal_json=excluded.refusal_json",
        values,
    )


def record_refusal(
    conn: sqlite3.Connection,
    refusal: ExecutorRefusal,
    machine_id: str = ORIGINAL_MAC_MACHINE_ID,
) -> bool:
    """Store a version refusal and its contact time, only when its reason or peer
    identity differ from the stored one. True if it wrote, which is the one case its
    commit should announce."""
    previous = read_presence(conn, machine_id)
    stored = None if previous is None else previous.refusal
    if (
        stored is not None
        and stored.reason == refusal.reason
        and stored.peer_identity == refusal.peer_identity
    ):
        return False
    conn.execute(
        "INSERT INTO mac_executor_presence(machine_id,last_contact_at_ms,refusal_json) "
        "VALUES (?,?,?) ON CONFLICT(machine_id) DO UPDATE SET "
        "last_contact_at_ms=MAX(COALESCE(last_contact_at_ms,0),excluded.last_contact_at_ms),"
        "refusal_json=excluded.refusal_json",
        (machine_id, refusal.refused_at_ms, _json(refusal)),
    )
    return True


def record_contact(
    conn: sqlite3.Connection, at_ms: int, machine_id: str = ORIGINAL_MAC_MACHINE_ID
) -> None:
    """Flush contact without a lease (refused leases, the WebSocket stub, update routes).
    Commit it with core.db.commit_without_change_signal."""
    conn.execute(
        "INSERT INTO mac_executor_presence(machine_id,last_contact_at_ms) VALUES (?,?) "
        "ON CONFLICT(machine_id) DO UPDATE SET "
        "last_contact_at_ms=MAX(COALESCE(last_contact_at_ms,0),excluded.last_contact_at_ms)",
        (machine_id, at_ms),
    )


def require_incarnation_owner(
    conn: sqlite3.Connection, incarnation: str, machine_id: str
) -> None:
    """An old incarnation can replay only within its original machine."""
    row = conn.execute(
        "SELECT machine_id FROM executor_incarnations WHERE executor_incarnation=?",
        (incarnation,),
    ).fetchone()
    if row is None or row[0] != machine_id:
        raise PlannerError(
            ErrorCode.agent_forbidden, "executor reference belongs to another machine"
        )
