"""Durable ownership of the canonical workspace and Ticket launch sources.

There is no expiry. A bound uncertain command retains only its own source keys.
Writers refuse with those keys until the existing command outcome settles it.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from planner.core.errors import ErrorCode, PlannerError


@contextmanager
def _write(conn: sqlite3.Connection) -> Iterator[None]:
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if own:
            conn.rollback()
        raise
    else:
        if own:
            conn.commit()


def _process_identity() -> str:
    # Linux process start ticks distinguish PID reuse without any elapsed-time lease.
    stat = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()
    return f"{os.getpid()}:{stat[19]}"


def _owner_gone(identity: str) -> bool:
    if identity.startswith("recovered:"):
        return True
    pid, _, start = identity.partition(":")
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return True
    except (OSError, ValueError, IndexError):
        return False
    return stat[19] != start


def authority_keys(conn: sqlite3.Connection, ticket_id: str) -> tuple[str, ...]:
    row = conn.execute(
        "SELECT CASE WHEN t.manager_kind='manager' THEN m.project_id ELSE t.project_id END "
        "FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id WHERE t.id=?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "Ticket not found", {"ticket_id": ticket_id})
    return tuple(sorted((f"ticket:{ticket_id}",) + ((f"project:{row[0]}",) if row[0] else ())))


@dataclass(slots=True)
class LaunchAuthorityPermit:
    lease_id: str
    keys: tuple[str, ...]
    mode: str

    def extend_read_keys(
        self, conn: sqlite3.Connection, keys: tuple[str, ...]
    ) -> LaunchAuthorityPermit:
        """Extend this same lease without an upgrade or a blocking lock wait."""
        with _write(conn):
            rows = conn.execute(
                "SELECT authority_key,owner_process,command_id,executor_incarnation,"
                "release_requested,policy_context_json "
                "FROM ticket_launch_authority WHERE lease_id=?",
                (self.lease_id,),
            ).fetchall()
            if not rows:
                raise PlannerError(ErrorCode.already_running, "launch authority no longer exists")
            held = {str(row[0]) for row in rows}
            missing = tuple(sorted(set(keys) - held))
            if missing:
                marks = ",".join("?" for _ in missing)
                conflicts = conn.execute(
                    f"SELECT authority_key,command_id FROM ticket_launch_authority "
                    f"WHERE authority_key IN ({marks}) AND mode='mutation' AND lease_id<>?",
                    (*missing, self.lease_id),
                ).fetchall()
                if conflicts:
                    raise PlannerError(
                        ErrorCode.already_running,
                        "launch policy has an exclusive writer",
                        {
                            "authority_keys": sorted({str(row[0]) for row in conflicts}),
                            "command_ids": sorted({str(row[1]) for row in conflicts if row[1]}),
                        },
                    )
                base = rows[0]
                conn.executemany(
                    "INSERT INTO ticket_launch_authority(lease_id,authority_key,mode,owner_process,"
                    "command_id,executor_incarnation,release_requested,policy_context_json) "
                    "VALUES(?,?,'read',?,?,?,?,?)",
                    (
                        (self.lease_id, key, base[1], base[2], base[3], base[4], base[5])
                        for key in missing
                    ),
                )
            self.keys = tuple(sorted(held | set(keys)))
        return self

    def release(self, conn: sqlite3.Connection) -> None:
        with _write(conn):
            conn.execute(
                "DELETE FROM ticket_launch_authority WHERE lease_id=? AND command_id IS NULL",
                (self.lease_id,),
            )
            conn.execute(
                "UPDATE ticket_launch_authority SET release_requested=1 WHERE lease_id=?",
                (self.lease_id,),
            )

    def bind_command(
        self, conn: sqlite3.Connection, command_id: str, executor_incarnation: str
    ) -> None:
        """Bind before dispatch, in the same transaction as command recording."""
        with _write(conn):
            rows = conn.execute(
                "SELECT command_id,executor_incarnation,policy_context_json "
                "FROM ticket_launch_authority "
                "WHERE lease_id=?",
                (self.lease_id,),
            ).fetchall()
            if not rows or any(
                r[0] is not None and (r[0], r[1]) != (command_id, executor_incarnation)
                for r in rows
            ):
                raise PlannerError(
                    ErrorCode.already_running,
                    "launch authority is absent or held by another command",
                    {"authority_keys": list(self.keys)},
                )
            from planner.conversation.admission.policy import (
                POLICY_CONTEXT_ADAPTER,
                CompletePolicyContext,
            )

            provenance = {r[2] for r in rows}
            if provenance != {None}:
                try:
                    if len(provenance) != 1:
                        raise ValueError("mixed launch policy provenance")
                    original = POLICY_CONTEXT_ADAPTER.validate_json(next(iter(provenance)))
                    if not isinstance(original, CompletePolicyContext):
                        raise ValueError("model policy preparation has not completed")
                except (ValueError, TypeError) as exc:
                    raise PlannerError(
                        ErrorCode.validation,
                        "complete launch policy proof is required before command recording",
                    ) from exc
            conn.execute(
                "UPDATE ticket_launch_authority SET command_id=?,executor_incarnation=? "
                "WHERE lease_id=?",
                (command_id, executor_incarnation, self.lease_id),
            )


_current: ContextVar[LaunchAuthorityPermit | None] = ContextVar(
    "ticket_launch_authority", default=None
)


@contextmanager
def launch_authority_scope(permit: LaunchAuthorityPermit) -> Iterator[None]:
    token = _current.set(permit)
    try:
        yield
    finally:
        _current.reset(token)


def current_launch_authority_permit() -> LaunchAuthorityPermit | None:
    return _current.get()


def _acquire(conn: sqlite3.Connection, keys: tuple[str, ...], mode: str) -> LaunchAuthorityPermit:
    keys = tuple(sorted(set(keys)))
    if not keys:
        raise ValueError("launch authority requires a canonical source")
    lease_id = uuid4().hex
    with _write(conn):
        marks = ",".join("?" for _ in keys)
        conflicts = conn.execute(
            "SELECT authority_key,command_id,owner_process FROM ticket_launch_authority "
            f"WHERE authority_key IN ({marks}) AND (?='mutation' OR mode='mutation')",
            (*keys, mode),
        ).fetchall()
        if conflicts:
            raise PlannerError(
                ErrorCode.already_running,
                "launch authority is held by an unsettled operation; "
                "retry after its outcome is known",
                {
                    "authority_keys": sorted({str(r[0]) for r in conflicts}),
                    "command_ids": sorted({str(r[1]) for r in conflicts if r[1] is not None}),
                },
            )
        conn.executemany(
            "INSERT INTO ticket_launch_authority(lease_id,authority_key,mode,owner_process) "
            "VALUES(?,?,?,?)",
            ((lease_id, key, mode, _process_identity()) for key in keys),
        )
    return LaunchAuthorityPermit(lease_id, keys, mode)


def acquire_read(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    backend_key: object = None,
    submitted_token: str | None = None,
) -> LaunchAuthorityPermit:
    with _write(conn):
        from planner.conversation.admission.policy import policy_key, record_policy_preparation
        from planner.conversation.contracts import require_conversation_backend_key

        row = conn.execute(
            "SELECT employee_backend,employee_launch_model,conversation_id FROM tickets WHERE id=?",
            (ticket_id,),
        ).fetchone()
        keys = set(authority_keys(conn, ticket_id))
        backend = require_conversation_backend_key(row[0] if backend_key is None else backend_key)
        token = row[1] if submitted_token is None else submitted_token
        if token is not None and backend.value in ("codex", "claude"):
            keys.add(policy_key(backend, str(token)))
        permit = _acquire(conn, tuple(sorted(keys)), "read")
        if backend.value in ("codex", "claude"):
            record_policy_preparation(
                conn, permit, backend, str(token) if token is not None else None, row[2]
            )
        return permit


def acquire_mutation(conn: sqlite3.Connection, keys: tuple[str, ...]) -> LaunchAuthorityPermit:
    return _acquire(conn, keys, "mutation")


def acquire_ticket_mutation(conn: sqlite3.Connection, ticket_id: str) -> LaunchAuthorityPermit:
    """Change this Ticket while sharing read ownership of its workspace source."""
    with _write(conn):
        keys = authority_keys(conn, ticket_id)
        workspace_keys = tuple(key for key in keys if key.startswith("project:"))
        owned = _acquire(conn, (f"ticket:{ticket_id}",), "mutation")
        try:
            if workspace_keys:
                shared = _acquire(conn, workspace_keys, "read")
                conn.execute(
                    "UPDATE ticket_launch_authority SET lease_id=? WHERE lease_id=?",
                    (owned.lease_id, shared.lease_id),
                )
        except BaseException:
            owned.release(conn)
            raise
        return LaunchAuthorityPermit(owned.lease_id, keys, "mutation")


@contextmanager
def mutation_scope(
    conn: sqlite3.Connection, keys: tuple[str, ...], permit: LaunchAuthorityPermit | None = None
) -> Iterator[None]:
    """Canonical writers hold exclusion through their existing write transaction."""
    inherited = permit or current_launch_authority_permit()
    with _write(conn):
        if inherited is not None:
            actual = {
                str(r[0])
                for r in conn.execute(
                    "SELECT authority_key FROM ticket_launch_authority "
                    "WHERE lease_id=? AND mode='mutation' AND command_id IS NULL",
                    (inherited.lease_id,),
                )
            }
            if not set(keys).issubset(actual):
                raise PlannerError(
                    ErrorCode.already_running,
                    "matching launch mutation authority is required",
                    {"authority_keys": list(keys)},
                )
            with launch_authority_scope(inherited):
                yield
        else:
            owned = acquire_mutation(conn, keys)
            try:
                with launch_authority_scope(owned):
                    yield
            finally:
                owned.release(conn)


def settle_command(conn: sqlite3.Connection, command_id: str, executor_incarnation: str) -> None:
    """Only the definitive outcome owner may call this; uncertainty is not settlement."""
    with _write(conn):
        args = (command_id, executor_incarnation)
        conn.execute(
            "DELETE FROM ticket_launch_authority WHERE command_id=? "
            "AND executor_incarnation=? AND release_requested=1",
            args,
        )
        conn.execute(
            "UPDATE ticket_launch_authority SET command_id=NULL,executor_incarnation=NULL "
            "WHERE command_id=? AND executor_incarnation=?",
            args,
        )


def reconcile_abandoned_permits(
    conn: sqlite3.Connection,
    *,
    unbound_command_absent: Callable[[str], bool],
    command_settled: Callable[[str, str], bool],
) -> int:
    """Startup proof hooks inspect the journal; absence or age is never an outcome.

    Unbound rows need both a dead process and proof no journal command references
    the lease. Bound rows require a definitive recorded outcome/retirement receipt.
    Otherwise they continue to block exactly their source keys.
    """
    removed = 0
    with _write(conn):
        rows = conn.execute(
            "SELECT DISTINCT lease_id,owner_process,command_id,executor_incarnation "
            "FROM ticket_launch_authority"
        ).fetchall()
        for lease_id, owner, command_id, incarnation in rows:
            if not _owner_gone(str(owner)):
                continue
            proved = (
                unbound_command_absent(str(lease_id))
                if command_id is None
                else command_settled(str(command_id), str(incarnation))
            )
            if proved:
                conn.execute("DELETE FROM ticket_launch_authority WHERE lease_id=?", (lease_id,))
                removed += 1
    return removed


@dataclass(frozen=True, slots=True)
class LaunchAuthorityRecovery:
    reconstructed: int
    settled: int
    blocked_authority_keys: tuple[str, ...]


def reconcile_persisted_launch_authority(conn: sqlite3.Connection) -> LaunchAuthorityRecovery:
    """Before writers resume, reconstruct unresolved retained remote commands.

    This reads existing durable command/outcome and source-retirement facts. It
    never sends a command, changes its fate, renews evidence or expires ownership.
    An orphaned local admission with an existing conversation stays blocked: the
    journal alone cannot prove that process emitted nothing before the crash.
    """
    from pydantic import TypeAdapter, ValidationError

    from planner.executor.contracts import (
        ChildCommand,
        ChildOperation,
        ChildReference,
        CommandDisposition,
        CommandOutcome,
    )

    command_type = TypeAdapter(ChildCommand)
    outcome_type = TypeAdapter(CommandOutcome)
    child_type = TypeAdapter(ChildReference)
    retired: set[tuple[str, str]] = set()

    def settled(command_id: str, incarnation: str) -> bool:
        row = conn.execute(
            "SELECT outcome_json FROM executor_command_outcomes "
            "WHERE executor_incarnation=? AND command_id=?",
            (incarnation, command_id),
        ).fetchone()
        if row is not None:
            try:
                outcome = outcome_type.validate_json(row[0])
            except (ValidationError, ValueError, TypeError):
                return False
            if (
                outcome.command_id == command_id
                and outcome.child.executor_incarnation == incarnation
            ):
                if outcome.disposition in (
                    CommandDisposition.succeeded,
                    CommandDisposition.refused,
                ):
                    return True
        return (incarnation, command_id) in retired

    reconstructed = 0
    with _write(conn):
        retained = conn.execute(
            "SELECT e.child_json,e.runtime_json,t.id FROM executor_core_turns e "
            "JOIN tickets t ON t.conversation_id=e.conversation_id"
        ).fetchall()
        for child_json, runtime_json, ticket_id in retained:
            try:
                child = child_type.validate_json(child_json)
                runtime = json.loads(runtime_json)
                commands = tuple(
                    command_type.validate_python(raw) for raw in runtime.get("initial_commands", ())
                )
            except (ValidationError, ValueError, TypeError, AttributeError):
                # Preserve malformed legacy uncertainty without blocking startup.
                lease_id = f"recovered:malformed:{ticket_id}"
                conn.executemany(
                    "INSERT OR IGNORE INTO ticket_launch_authority(lease_id,authority_key,mode,"
                    "owner_process,release_requested) VALUES(?,?,'read',?,1)",
                    ((lease_id, key, lease_id) for key in authority_keys(conn, str(ticket_id))),
                )
                continue
            source_retired = (
                runtime.get("source_retired") is True
                and runtime.get("cleanup_evidence_uncertain") is not True
            ) or conn.execute(
                "SELECT 1 FROM executor_child_assignments WHERE executor_incarnation=? "
                "AND child_id=? AND conversation_id=? AND closed_at_ms IS NOT NULL",
                (child.executor_incarnation, child.child_id, child.conversation_id),
            ).fetchone() is not None
            for command in commands:
                if command.child != child or command.operation not in (
                    ChildOperation.start,
                    ChildOperation.write_prompt,
                ):
                    continue
                identity = (command.child.executor_incarnation, command.command_id)
                if source_retired:
                    retired.add(identity)
                if settled(command.command_id, command.child.executor_incarnation):
                    continue
                if (
                    conn.execute(
                        "SELECT 1 FROM ticket_launch_authority "
                        "WHERE command_id=? AND executor_incarnation=?",
                        (command.command_id, child.executor_incarnation),
                    ).fetchone()
                    is not None
                ):
                    continue
                lease_id = f"recovered:{child.executor_incarnation}:{command.command_id}"
                keys = authority_keys(conn, str(ticket_id))
                conn.executemany(
                    "INSERT OR IGNORE INTO ticket_launch_authority(lease_id,authority_key,mode,"
                    "owner_process,command_id,executor_incarnation,release_requested) "
                    "VALUES(?,?,'read',?,?,?,1)",
                    (
                        (lease_id, key, lease_id, command.command_id, child.executor_incarnation)
                        for key in keys
                    ),
                )
                reconstructed += 1

        def unbound_absent(lease_id: str) -> bool:
            from planner.conversation.admission.policy import (
                CompletePolicyContext,
                PolicyContext,
                PreparationPolicyContext,
            )

            provenance = {
                row[0]
                for row in conn.execute(
                    "SELECT policy_context_json FROM ticket_launch_authority WHERE lease_id=?",
                    (lease_id,),
                )
            }
            original: PolicyContext | None = None
            if len(provenance) == 1 and None not in provenance:
                try:
                    original = TypeAdapter(PolicyContext).validate_json(next(iter(provenance)))
                except (ValidationError, ValueError, TypeError):
                    return False
            if isinstance(original, CompletePolicyContext):
                # Local emission has no remote command; complete proof must
                # remain until the real provider/source settlement owner acts.
                return False
            keys = tuple(
                str(row[0])
                for row in conn.execute(
                    "SELECT authority_key FROM ticket_launch_authority WHERE lease_id=?",
                    (lease_id,),
                )
            )
            ticket_ids = [key.removeprefix("ticket:") for key in keys if key.startswith("ticket:")]
            if isinstance(original, PreparationPolicyContext):
                # Provider admission is unreachable until completion. Binding is
                # atomic with recording and rejects preparation provenance, so
                # this exact unbound lease has no retained provider command.
                # Another lease's older uncertain command is not this operation.
                return True
            # A project-only mutation contains no asynchronous provider operation.
            # Ticket-local unbound ownership is released only before conversation
            # linking; any linked local source remains an explicit uncertain scope.
            return all(
                conn.execute(
                    "SELECT 1 FROM tickets WHERE id=? AND conversation_id IS NOT NULL", (ticket_id,)
                ).fetchone()
                is None
                for ticket_id in ticket_ids
            )

        removed = reconcile_abandoned_permits(
            conn, unbound_command_absent=unbound_absent, command_settled=settled
        )
        from planner.conversation.admission.policy import recover_policy_ownership

        recover_policy_ownership(conn)
        blocked = tuple(
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT authority_key FROM ticket_launch_authority ORDER BY authority_key"
            )
        )
    return LaunchAuthorityRecovery(reconstructed, removed, blocked)
