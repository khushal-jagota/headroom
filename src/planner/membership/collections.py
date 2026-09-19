"""One entry per collection: how to name its container, who may write it, and the
two writes themselves.

Each entry delegates to the code that already owns the collection, so every membership
rule — what may go in, ordering, and whether a thing may be in two at once — stays
exactly where it is. Nothing here decides membership; it only routes to the decider.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from planner.core.authctx import (
    RequestContext,
    require_planning_write,
    require_sprint_item_supervisor_ticket_write,
    require_ticket_worker_write,
)
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import Principal
from planner.days import actions as days_actions
from planner.days.logic import dates
from planner.membership.contracts import Collection
from planner.sprints import commitments
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data


@dataclass(frozen=True)
class MembershipWrite:
    """One request to put a member in a container, or take it out."""

    conn: sqlite3.Connection
    principal: Principal
    container_id: str
    member_id: str
    now: int
    admit: Callable[[], None] | None


Write = Callable[[MembershipWrite], None]
Rule = Callable[[sqlite3.Connection, RequestContext, str, str], None]


def _container_as_given(container: str, clock: Clock, config: Config) -> str:
    return container


def _day_container(container: str, clock: Clock, config: Config) -> str:
    """`today` or an ISO date names the Day; the id comes from the planning date."""
    return dates.resolve_day_id(container, clock.now(), config.boundary_hour)


# --- the writes ---------------------------------------------------------------


def _add_day_ticket(write: MembershipWrite) -> None:
    days_actions.add_ticket_to_day(
        write.conn, write.container_id, write.member_id, now=write.now, admit=write.admit
    )


def _remove_day_ticket(write: MembershipWrite) -> None:
    days_actions.remove_ticket_from_day(
        write.conn, write.container_id, write.member_id, now=write.now, admit=write.admit
    )


def _add_outcome_ticket(write: MembershipWrite) -> None:
    tickets_data.classify_ticket(
        write.conn,
        write.member_id,
        sprint_item_id=write.container_id,
        principal=write.principal,
        now=write.now,
        admit=write.admit,
    )


def _remove_outcome_ticket(write: MembershipWrite) -> None:
    tickets_data.unclassify_ticket(
        write.conn,
        write.member_id,
        sprint_item_id=write.container_id,
        principal=write.principal,
        now=write.now,
        admit=write.admit,
    )


def _commit_outcome(write: MembershipWrite) -> None:
    commitments.set_commitment(
        write.conn,
        write.container_id,
        write.member_id,
        committed=True,
        admit=_required(write.admit),
    )


def _uncommit_outcome(write: MembershipWrite) -> None:
    commitments.set_commitment(
        write.conn,
        write.container_id,
        write.member_id,
        committed=False,
        admit=_required(write.admit),
    )


def _add_blocker(write: MembershipWrite) -> None:
    tickets_actions.add_ticket_block(
        write.conn, write.member_id, write.container_id, now=write.now, admit=write.admit
    )


def _remove_blocker(write: MembershipWrite) -> None:
    tickets_actions.remove_ticket_block(
        write.conn, write.member_id, write.container_id, now=write.now, admit=write.admit
    )


def _required(admit: Callable[[], None] | None) -> Callable[[], None]:
    """Commitment writes always have a rule, so an absent one is a wiring mistake."""
    if admit is None:
        raise ValueError("this collection always has an authority rule")
    return admit


# --- the authority rules ------------------------------------------------------


def _ticket_worker_rule(
    conn: sqlite3.Connection, ctx: RequestContext, container_id: str, member_id: str
) -> None:
    require_ticket_worker_write(conn, ctx)


def _sprint_planning_rule(
    conn: sqlite3.Connection, ctx: RequestContext, container_id: str, member_id: str
) -> None:
    require_planning_write(conn, ctx, "planning-sprint")


def _supervisor_day_rule(
    conn: sqlite3.Connection, ctx: RequestContext, container_id: str, member_id: str
) -> None:
    require_sprint_item_supervisor_ticket_write(conn, ctx, ctx.principal.id, member_id)


def _supervisor_blocker_rule(
    conn: sqlite3.Connection, ctx: RequestContext, container_id: str, member_id: str
) -> None:
    """A supervisor may block one of its own children with another of its own children."""
    require_sprint_item_supervisor_ticket_write(conn, ctx, ctx.principal.id, member_id)
    require_sprint_item_supervisor_ticket_write(conn, ctx, ctx.principal.id, container_id)


@dataclass(frozen=True)
class CollectionEntry:
    """One collection, and everything the two routes need to know about it."""

    add: Write
    remove: Write
    rule: Rule | None
    supervisor_rule: Rule | None = None
    resolve_container: Callable[[str, Clock, Config], str] = field(default=_container_as_given)


ENTRIES: dict[Collection, CollectionEntry] = {
    # A Ticket on a Day has never had an authority rule of its own, and it keeps none.
    # A Sprint Item supervisor gets the rule its own route used to apply.
    Collection.day_tickets: CollectionEntry(
        add=_add_day_ticket,
        remove=_remove_day_ticket,
        rule=None,
        supervisor_rule=_supervisor_day_rule,
        resolve_container=_day_container,
    ),
    Collection.outcome_tickets: CollectionEntry(
        add=_add_outcome_ticket,
        remove=_remove_outcome_ticket,
        rule=_ticket_worker_rule,
    ),
    Collection.sprint_outcomes: CollectionEntry(
        add=_commit_outcome,
        remove=_uncommit_outcome,
        rule=_sprint_planning_rule,
    ),
    Collection.blockers: CollectionEntry(
        add=_add_blocker,
        remove=_remove_blocker,
        rule=_ticket_worker_rule,
        supervisor_rule=_supervisor_blocker_rule,
    ),
}
