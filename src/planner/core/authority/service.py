"""Ask the one rule about a real caller and a real target.

This is the only place that reads the database on authority's behalf. It gathers the two
facts the rule needs and hands them to :mod:`planner.core.authority.logic`, which stays
dependency-free.

The chain is already in the data and this module stores nothing new. A Ticket's Outcome
is ``tickets.sprint_item_id``. A planning Ticket's reach is declared in
:mod:`planner.core.authority.declarations`, which is code, not a row.

A position is a truthful local claim, exactly as it was before: the caller's
``X-Plan-Actor`` and its one id header, resolved once in
:mod:`planner.core.authctx`. Nothing here authenticates a claim, and nothing here
compares a claimed Outcome manager against ``sprint_items.supervisor_agent_key``. What
the rule does add is that a claim must name the thing being acted on.
"""

from __future__ import annotations

import sqlite3

from planner.core.authority.contracts import Target, TargetKind
from planner.core.authority.declarations import stands_above_for_worker_type
from planner.core.authority.logic import ChainFacts, is_self, stands_above
from planner.core.contracts import (
    ErrorCode,
    PlannerError,
    Principal,
    PrincipalKind,
    principal_legacy_actor,
)


def _target_parent_outcome_id(conn: sqlite3.Connection, target: Target) -> str | None:
    """The Outcome a Ticket target sits under right now, if it sits under one.

    Only a ``normal`` Sprint Item counts, which is the rule the guards this replaces
    already applied: an ``other`` Item has no supervisor and so is nobody's position.
    """
    if target.kind is not TargetKind.ticket:
        return None
    row = conn.execute(
        "SELECT item.id AS id FROM tickets AS ticket "
        "JOIN sprint_items AS item ON item.id = ticket.sprint_item_id "
        "WHERE ticket.id = ? AND item.kind = 'normal'",
        (target.id,),
    ).fetchone()
    return None if row is None else str(row["id"])


def _caller_declared_targets(conn: sqlite3.Connection, caller: Principal) -> tuple[Target, ...]:
    """What the caller's Worker type declares it stands above.

    Empty for every caller that is not a Ticket, and for a claimed Ticket id with no row:
    a claim that names nothing stands above nothing.
    """
    if caller.kind is not PrincipalKind.ticket:
        return ()
    row = conn.execute("SELECT worker_type FROM tickets WHERE id = ?", (caller.id,)).fetchone()
    if row is None:
        return ()
    return stands_above_for_worker_type(str(row["worker_type"]))


def chain_facts(conn: sqlite3.Connection, caller: Principal, target: Target) -> ChainFacts:
    return ChainFacts(
        target_parent_outcome_id=_target_parent_outcome_id(conn, target),
        caller_declared_targets=_caller_declared_targets(conn, caller),
    )


def is_above(conn: sqlite3.Connection, caller: Principal, target: Target) -> bool:
    return stands_above(caller, target, chain_facts(conn, caller, target))


def is_above_or_self(conn: sqlite3.Connection, caller: Principal, target: Target) -> bool:
    return is_self(caller, target) or is_above(conn, caller, target)


def _refuse(caller: Principal, target: Target, action: str) -> None:
    raise PlannerError(
        ErrorCode.agent_forbidden,
        f"{action} is not available to this principal",
        {
            "actor": principal_legacy_actor(caller),
            "principal_kind": caller.kind.value,
            "principal_id": caller.id,
            "target_kind": target.kind.value,
            "target_id": target.id,
            "action": action,
        },
    )


def require_above(conn: sqlite3.Connection, caller: Principal, target: Target, action: str) -> None:
    """Admit a caller that stands strictly above the target. Refuse everything else."""
    if not is_above(conn, caller, target):
        _refuse(caller, target, action)


def require_above_or_self(
    conn: sqlite3.Connection, caller: Principal, target: Target, action: str
) -> None:
    """Admit the target's own principal as well, for an operation on its own record."""
    if not is_above_or_self(conn, caller, target):
        _refuse(caller, target, action)


__all__ = [
    "chain_facts",
    "is_above",
    "is_above_or_self",
    "require_above",
    "require_above_or_self",
]
