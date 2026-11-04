"""One entry per collection: how to name its container and the two writes themselves.

Each entry delegates to the code that already owns the collection, so every membership
rule — what may go in, ordering, and whether a thing may be in two at once — stays
exactly where it is. Nothing here decides membership; it only routes to the decider.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

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
    review_transfer_notices: list[ReviewTransferNotice] = field(default_factory=list)


Write = Callable[[MembershipWrite], None]


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
        review_transfer_notices=write.review_transfer_notices,
    )


def _remove_manager_ticket(write: MembershipWrite) -> None:
    tickets_data.unclassify_ticket(
        write.conn,
        write.member_id,
        manager_id=write.container_id,
        principal=write.principal,
        now=write.now,
        review_transfer_notices=write.review_transfer_notices,
    )


def _add_blocker(write: MembershipWrite) -> None:
    tickets_actions.add_ticket_block(
        write.conn, write.member_id, write.container_id, now=write.now
    )


def _remove_blocker(write: MembershipWrite) -> None:
    tickets_actions.remove_ticket_block(
        write.conn, write.member_id, write.container_id, now=write.now
    )


@dataclass(frozen=True)
class CollectionEntry:
    """One collection, and everything the two routes need to know about it."""

    add: Write
    remove: Write
    resolve_container: Callable[[str, Clock, Config], str] = field(default=_container_as_given)


ENTRIES: dict[Collection, CollectionEntry] = {
    Collection.manager_tickets: CollectionEntry(
        add=_add_manager_ticket,
        remove=_remove_manager_ticket,
    ),
    Collection.blockers: CollectionEntry(
        add=_add_blocker,
        remove=_remove_blocker,
    ),
}
