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

from planner.core import authority
from planner.core.authctx import RequestContext
from planner.core.authority import (
    is_above,
    refuse_outcome_re_parenting,
    require_above,
    require_above_or_self,
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
    admit: Callable[[], None]


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
        admit=write.admit,
    )


def _uncommit_outcome(write: MembershipWrite) -> None:
    commitments.set_commitment(
        write.conn,
        write.container_id,
        write.member_id,
        committed=False,
        admit=write.admit,
    )


def _add_blocker(write: MembershipWrite) -> None:
    tickets_actions.add_ticket_block(
        write.conn, write.member_id, write.container_id, now=write.now, admit=write.admit
    )


def _remove_blocker(write: MembershipWrite) -> None:
    tickets_actions.remove_ticket_block(
        write.conn, write.member_id, write.container_id, now=write.now, admit=write.admit
    )


# --- the authority rules ------------------------------------------------------


def _day_ticket_rule(
    conn: sqlite3.Connection, ctx: RequestContext, container_id: str, member_id: str
) -> None:
    """Two callers can put work on a Day, and they are above two different things.

    Anyone above the Ticket is moving their own work around, so the Ticket is the thing
    acted on. A planning Worker is doing something else: it is composing the Day itself,
    which is the plan it was created to write, and it stands above no Ticket. Without the
    second question the Day Workers cannot build a Day, which is their whole job.
    """
    if is_above(conn, ctx.principal, authority.plan("day_tickets")):
        return
    require_above_or_self(conn, ctx.principal, authority.ticket(member_id))


def _outcome_ticket_rule(
    conn: sqlite3.Connection, ctx: RequestContext, container_id: str, member_id: str
) -> None:
    """The Ticket is acted on, and this write is the one that sets its Outcome.

    So it is re-parenting under another name, and the stated exception decides it. Not
    ``or_self``: a Ticket setting its own Outcome is the case the exception exists for.
    """
    require_above(conn, ctx.principal, authority.ticket(member_id))
    refuse_outcome_re_parenting(ctx.principal, member_id)


def _sprint_planning_rule(
    conn: sqlite3.Connection, ctx: RequestContext, container_id: str, member_id: str
) -> None:
    require_above(conn, ctx.principal, authority.plan("sprint_outcomes"))


def _blocker_rule(
    conn: sqlite3.Connection, ctx: RequestContext, container_id: str, member_id: str
) -> None:
    """The blocked Ticket is the one acted on. The blocking Ticket is named, not changed."""
    require_above_or_self(conn, ctx.principal, authority.ticket(container_id))


@dataclass(frozen=True)
class CollectionEntry:
    """One collection, and everything the two routes need to know about it."""

    add: Write
    remove: Write
    rule: Rule
    resolve_container: Callable[[str, Clock, Config], str] = field(default=_container_as_given)


ENTRIES: dict[Collection, CollectionEntry] = {
    # Every entry asks the one rule about the thing the write acts on. There is no longer a
    # second rule chosen from the caller's kind: that split was the two-doors shape in one
    # file, and a collection now answers the same question whoever is asking.
    Collection.day_tickets: CollectionEntry(
        add=_add_day_ticket,
        remove=_remove_day_ticket,
        rule=_day_ticket_rule,
        resolve_container=_day_container,
    ),
    Collection.outcome_tickets: CollectionEntry(
        add=_add_outcome_ticket,
        remove=_remove_outcome_ticket,
        rule=_outcome_ticket_rule,
    ),
    Collection.sprint_outcomes: CollectionEntry(
        add=_commit_outcome,
        remove=_uncommit_outcome,
        rule=_sprint_planning_rule,
    ),
    Collection.blockers: CollectionEntry(
        add=_add_blocker,
        remove=_remove_blocker,
        rule=_blocker_rule,
    ),
}
