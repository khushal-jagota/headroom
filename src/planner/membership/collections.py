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
    refuse_manager_re_parenting,
    require_above,
    require_above_or_self,
)
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import Principal
from planner.membership.contracts import Collection
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import ReviewTransferNotice


@dataclass(frozen=True)
class MembershipWrite:
    """One request to put a member in a container, or take it out."""

    conn: sqlite3.Connection
    principal: Principal
    container_id: str
    member_id: str
    now: int
    admit: Callable[[], None]
    review_transfer_notices: list[ReviewTransferNotice] = field(default_factory=list)


Write = Callable[[MembershipWrite], None]
Rule = Callable[[sqlite3.Connection, RequestContext, str, str], None]


def _container_as_given(container: str, clock: Clock, config: Config) -> str:
    return container


# --- the writes ---------------------------------------------------------------


def _add_manager_ticket(write: MembershipWrite) -> None:
    tickets_data.classify_ticket(
        write.conn,
        write.member_id,
        manager_id=write.container_id,
        principal=write.principal,
        now=write.now,
        admit=write.admit,
        review_transfer_notices=write.review_transfer_notices,
    )


def _remove_manager_ticket(write: MembershipWrite) -> None:
    tickets_data.unclassify_ticket(
        write.conn,
        write.member_id,
        manager_id=write.container_id,
        principal=write.principal,
        now=write.now,
        admit=write.admit,
        review_transfer_notices=write.review_transfer_notices,
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


def _manager_ticket_rule(
    conn: sqlite3.Connection, ctx: RequestContext, container_id: str, member_id: str
) -> None:
    """The Ticket is acted on, and this write is the one that sets its Manager.

    So it is re-parenting under another name, and the stated exception decides it. Not
    ``or_self``: a Ticket setting its own Manager is the case the exception exists for.
    """
    require_above(conn, ctx.principal, authority.ticket(member_id))
    refuse_manager_re_parenting(ctx.principal, member_id)


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
    Collection.manager_tickets: CollectionEntry(
        add=_add_manager_ticket,
        remove=_remove_manager_ticket,
        rule=_manager_ticket_rule,
    ),
    Collection.blockers: CollectionEntry(
        add=_add_blocker,
        remove=_remove_blocker,
        rule=_blocker_rule,
    ),
}
