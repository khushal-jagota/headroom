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
from planner.core.authority.logic import (
    ChainFacts,
    is_self,
    shares_the_chain,
    stands_above,
    stands_above_or_is_self,
)
from planner.core.contracts import (
    ErrorCode,
    PlannerError,
    Principal,
    PrincipalKind,
    principal_legacy_actor,
)

_ABOVE_EVERY_OUTCOME = frozenset({PrincipalKind.owner, PrincipalKind.chief})


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


def _caller_parent_outcome_id(conn: sqlite3.Connection, caller: Principal) -> str | None:
    """The Outcome the caller sits under, if the caller is a Ticket that sits under one.

    The same column and the same ``normal`` restriction as ``_target_parent_outcome_id``,
    asked about the caller. Only the chain question reads it.
    """
    if caller.kind is not PrincipalKind.ticket:
        return None
    row = conn.execute(
        "SELECT item.id AS id FROM tickets AS ticket "
        "JOIN sprint_items AS item ON item.id = ticket.sprint_item_id "
        "WHERE ticket.id = ? AND item.kind = 'normal'",
        (caller.id,),
    ).fetchone()
    return None if row is None else str(row["id"])


def _target_is_itself_a_principal(conn: sqlite3.Connection, target: Target) -> bool:
    """Whether the target is a thing that can act: a real Ticket, or a ``normal`` Outcome.

    An ``other`` Sprint Item is a per-project bucket. Its supervisor columns are held NULL
    by a trigger, so it has no identity and nothing can claim to be it.
    """
    if target.kind is TargetKind.ticket:
        row = conn.execute("SELECT 1 FROM tickets WHERE id = ?", (target.id,)).fetchone()
        return row is not None
    if target.kind is TargetKind.outcome:
        row = conn.execute(
            "SELECT 1 FROM sprint_items WHERE id = ? AND kind = 'normal'", (target.id,)
        ).fetchone()
        return row is not None
    return False


def chain_facts(conn: sqlite3.Connection, caller: Principal, target: Target) -> ChainFacts:
    return ChainFacts(
        target_parent_outcome_id=_target_parent_outcome_id(conn, target),
        caller_declared_targets=_caller_declared_targets(conn, caller),
        target_is_itself_a_principal=_target_is_itself_a_principal(conn, target),
        caller_parent_outcome_id=_caller_parent_outcome_id(conn, caller),
    )


def is_above(conn: sqlite3.Connection, caller: Principal, target: Target) -> bool:
    return stands_above(caller, target, chain_facts(conn, caller, target))


def is_above_or_self(conn: sqlite3.Connection, caller: Principal, target: Target) -> bool:
    facts = chain_facts(conn, caller, target)
    return stands_above_or_is_self(caller, target, facts)


def _refuse(caller: Principal, target: Target) -> None:
    """Refuse, naming both sides.

    There is no hand-written action string. The detail names the principal and the thing
    it asked to act on, and the request itself names the operation, so a label repeated at
    every call site would add nothing and drift.
    """
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "this principal does not stand above what it asked to act on",
        {
            "actor": principal_legacy_actor(caller),
            "principal_kind": caller.kind.value,
            "principal_id": caller.id,
            "target_kind": target.kind.value,
            "target_id": target.id,
            "target_fields": sorted(target.fields),
        },
    )


def require_above(conn: sqlite3.Connection, caller: Principal, target: Target) -> None:
    """Admit a caller that stands strictly above the target. Refuse everything else."""
    if not is_above(conn, caller, target):
        _refuse(caller, target)


def require_self(conn: sqlite3.Connection, caller: Principal, target: Target) -> None:
    """Admit only the target's own principal, for an act that is nobody else's to perform.

    A Ticket proposes its own work, records its own trouble and asks for its own help. That
    is the Ticket speaking, so standing above it does not grant it.
    """
    if not is_self(caller, target, chain_facts(conn, caller, target)):
        _refuse(caller, target)


def require_above_or_self(conn: sqlite3.Connection, caller: Principal, target: Target) -> None:
    """Admit the target's own principal as well, for an operation on its own record."""
    if not is_above_or_self(conn, caller, target):
        _refuse(caller, target)


# --- creation: the three things a new Ticket arrives with -----------------------
#
# Creating a Ticket acts on nothing that exists, so nothing refuses it. Each canonical
# value creation carries does act on something, and takes the ordinary question about it.


def require_in_chain(conn: sqlite3.Connection, caller: Principal, target: Target) -> None:
    """Admit a caller that is in the target's chain: above it, it, or below it.

    The parent question at creation. A Ticket may put work under the Outcome it already
    answers to, and under no other.
    """
    if not shares_the_chain(caller, target, chain_facts(conn, caller, target)):
        _refuse(caller, target)


def _facts_for_a_ticket_being_created(
    conn: sqlite3.Connection, principal: Principal, *, parent_outcome_id: str | None
) -> ChainFacts:
    """The rule's facts about a Ticket that does not exist yet.

    Nothing here is guessed. The Ticket's id is already allocated, its Outcome is the
    parent the request named, and a Ticket being written is a principal by definition.
    """
    return ChainFacts(
        target_parent_outcome_id=parent_outcome_id,
        caller_declared_targets=_caller_declared_targets(conn, principal),
        target_is_itself_a_principal=True,
    )


def require_above_a_ticket_being_created(
    conn: sqlite3.Connection,
    caller: Principal,
    *,
    ticket_id: str,
    parent_outcome_id: str | None,
) -> None:
    """Admit a caller that will stand above the Ticket it is creating.

    The ceiling and the holder are the two canonical values ``PATCH`` reserves for a
    caller above the Ticket. Stating one at creation is that same act a moment earlier,
    so it gets the same answer and not a softer one.
    """
    target = Target(TargetKind.ticket, ticket_id)
    facts = _facts_for_a_ticket_being_created(conn, caller, parent_outcome_id=parent_outcome_id)
    if not stands_above(caller, target, facts):
        _refuse(caller, target)


def require_holder_can_be_asked(
    conn: sqlite3.Connection,
    caller: Principal,
    holder: Principal,
    *,
    ticket_id: str,
    parent_outcome_id: str | None,
) -> None:
    """A stated holder is an address for approval, so it must be one that can answer.

    The caller itself is always admitted. Unstated, the creator holds the Ticket, so
    naming yourself is the existing default said out loud. Anybody else has to stand
    above the Ticket, because that is what the approval door will ask of them. Without
    this, a caller can park its proposals on a principal that will never be able to
    resolve them.

    Asked only where a holder is stated. The stored holder of an existing Ticket is not
    re-judged, because a row written under an older answer must not lose its next write.
    """
    if holder == caller:
        return
    facts = _facts_for_a_ticket_being_created(conn, holder, parent_outcome_id=parent_outcome_id)
    if stands_above(holder, Target(TargetKind.ticket, ticket_id), facts):
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "a stated ceiling holder must be the caller or stand above the Ticket",
        {
            "actor": principal_legacy_actor(caller),
            "ticket_id": ticket_id,
            "holder": {"kind": holder.kind.value, "id": holder.id},
        },
    )


def refuse_outcome_re_parenting(caller: Principal, ticket_id: str) -> None:
    """The one stated exception to the rule, asked wherever a Ticket's Outcome is set.

    An Outcome stands above its Tickets because of ``tickets.sprint_item_id``. Setting that
    column is reassigning authority rather than exercising it, and "strictly below" cannot
    refuse it: at the moment of the call the Ticket really is below the Outcome that is
    moving it away. Only Khushal and the Chief, who stand above every Outcome, may do it.

    Asked of the caller alone, because no answer about the Ticket can decide it. That
    includes the Ticket itself: a Ticket that could set its own Outcome could leave one,
    or leave every Outcome, and choose who is allowed to act on it. Being a thing does not
    include choosing who stands above you.
    """
    if caller.kind in _ABOVE_EVERY_OUTCOME:
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "only Khushal or the Chief can move a Ticket out of its own chain",
        {"field": "sprint_item_id", "ticket_id": ticket_id},
    )


__all__ = [
    "chain_facts",
    "is_above",
    "is_above_or_self",
    "refuse_outcome_re_parenting",
    "require_above",
    "require_above_a_ticket_being_created",
    "require_above_or_self",
    "require_holder_can_be_asked",
    "require_in_chain",
    "require_self",
]
