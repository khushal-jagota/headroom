"""Link create/remove over the `links` table plus the blocked-derivation (§3.6,
SPEC lines 54–57). Core infrastructure over the DB (like events.py) — sqlite3 lives
here, not in dispatch/logic. Config-free: `now` is always a parameter."""

from __future__ import annotations

import sqlite3
from collections import deque
from typing import Final, Literal

from planner.core.contracts import (
    BlockedBySummaryRow,
    BlockerSummary,
    BlocksTargetSummaryRow,
    EventKind,
    LinkKind,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event

# Endpoint prefix rules: (allowed from-prefixes, allowed to-prefixes).
_ENDPOINT_RULES: Final[dict[LinkKind, tuple[frozenset[str] | None, frozenset[str] | None]]] = {
    LinkKind.blocks: (frozenset({"t"}), frozenset({"t", "si"})),
}


def _prefix(entity_id: str) -> str:
    """The endpoint kind is the id prefix before the first '_' (t_x -> 't')."""
    return entity_id.split("_", 1)[0]


def _ticket_is_active(conn: sqlite3.Connection, ticket_id: str) -> bool:
    row = conn.execute("SELECT state FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return row is not None and str(row["state"]) not in {"done", "dropped"}


def _require_ticket(conn: sqlite3.Connection, entity_id: str, field: str) -> None:
    if conn.execute("SELECT 1 FROM tickets WHERE id = ?", (entity_id,)).fetchone() is None:
        raise PlannerError(
            ErrorCode.link_invalid,
            f"{field} must be an existing ticket",
            {field: entity_id},
        )


def _require_target(conn: sqlite3.Connection, entity_id: str) -> None:
    if _prefix(entity_id) == "t":
        _require_ticket(conn, entity_id, "to_id")
        return
    if (
        conn.execute("SELECT 1 FROM sprint_items WHERE id = ?", (entity_id,)).fetchone()
        is None
    ):
        raise PlannerError(
            ErrorCode.link_invalid,
            "to_id must be an existing ticket or sprint item",
            {"to_id": entity_id},
        )


def _active_successor_ids(conn: sqlite3.Connection, ticket_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT links.to_id
        FROM links
        JOIN tickets source ON source.id = links.from_id
        WHERE links.from_id = ?
          AND links.kind = 'blocks'
          AND source.state NOT IN ('done', 'dropped')
        ORDER BY links.to_id
        """,
        (ticket_id,),
    ).fetchall()
    return [str(row["to_id"]) for row in rows]


def would_create_active_blocks_cycle(
    conn: sqlite3.Connection, from_id: str, to_id: str
) -> bool:
    """True when an active edge from ``from_id`` to ``to_id`` closes an active cycle."""
    queue: deque[str] = deque([to_id])
    visited: set[str] = {to_id}
    while queue:
        node = queue.popleft()
        if node == from_id:
            return True
        if _prefix(node) != "t":
            continue
        for nxt in _active_successor_ids(conn, node):
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)
    return False


def add_link(
    conn: sqlite3.Connection, from_id: str, to_id: str, kind: LinkKind, now: int
) -> None:
    """Create a blocks link, enforcing no self-links, real endpoints, and no active cycle."""
    detail = {"from_id": from_id, "to_id": to_id, "kind": kind.value}
    # 1. Self-link (§3.6): the two endpoints must differ. Pure check, outside the txn.
    if from_id == to_id:
        raise PlannerError(ErrorCode.link_invalid, "self-links are not allowed", detail)
    # 2. Endpoint kinds (D8): the prefix fixes each side. Pure check, outside the txn.
    from_rule, to_rule = _ENDPOINT_RULES[kind]
    if from_rule is not None and _prefix(from_id) not in from_rule:
        raise PlannerError(
            ErrorCode.link_invalid,
            f"{kind.value} from-endpoint must be one of {sorted(from_rule)}",
            detail,
        )
    if to_rule is not None and _prefix(to_id) not in to_rule:
        raise PlannerError(
            ErrorCode.link_invalid,
            f"{kind.value} to-endpoint must be one of {sorted(to_rule)}",
            detail,
        )
    # Steps 3–6 serialize under the write lock: BEGIN IMMEDIATE takes the lock
    # before the reads, so two connections cannot interleave BFS-then-insert to admit
    # a cycle or duplicate edge. Contention resolves via db.connect's busy_timeout.
    own_txn = not conn.in_transaction
    if own_txn:
        conn.execute("BEGIN IMMEDIATE")
    try:
        _require_ticket(conn, from_id, "from_id")
        _require_target(conn, to_id)
        if _ticket_is_active(conn, from_id) and would_create_active_blocks_cycle(
            conn, from_id, to_id
        ):
            raise PlannerError(
                ErrorCode.link_cycle,
                f"link would create a {kind.value} cycle",
                detail,
            )
        # 5. Insert; any IntegrityError (PK dup, partial unique index race, CHECK) is
        # re-raised as link_invalid (D9) — a raw IntegrityError never escapes.
        try:
            conn.execute(
                "INSERT INTO links (from_id, to_id, kind) VALUES (?, ?, ?)",
                (from_id, to_id, kind.value),
            )
        except sqlite3.IntegrityError as exc:
            raise PlannerError(
                ErrorCode.link_invalid,
                "link already exists or violates a link constraint",
                detail,
            ) from exc
        # 6. Event.
        append_event(conn, from_id, EventKind.link_added, detail, now)
    except BaseException:
        if own_txn:
            conn.execute("ROLLBACK")
        raise
    if own_txn:
        conn.execute("COMMIT")


def remove_link(
    conn: sqlite3.Connection, from_id: str, to_id: str, kind: LinkKind, now: int
) -> None:
    """Delete a link; a missing link is not_found (D12)."""
    detail = {"from_id": from_id, "to_id": to_id, "kind": kind.value}
    cursor = conn.execute(
        "DELETE FROM links WHERE from_id=? AND to_id=? AND kind=?",
        (from_id, to_id, kind.value),
    )
    if cursor.rowcount == 0:
        raise PlannerError(ErrorCode.not_found, "link not found", detail)
    append_event(conn, from_id, EventKind.link_removed, detail, now)


def blocked_target_ids(conn: sqlite3.Connection) -> set[str]:
    """Ids (tickets or sprint items) currently blocked: targets of a blocks link
    whose source ticket is not done/dropped (§3.6 line 57)."""
    rows = conn.execute(
        "SELECT DISTINCT l.to_id FROM links l "
        "JOIN tickets src ON src.id = l.from_id "
        "WHERE l.kind = 'blocks' AND src.state NOT IN ('done', 'dropped')"
    ).fetchall()
    return {str(row["to_id"]) for row in rows}


def is_blocked(conn: sqlite3.Connection, entity_id: str) -> bool:
    """Whether one entity is blocked by the canonical resolved summary."""
    return blocker_summary(conn, entity_id).blocked


def blocker_summary(conn: sqlite3.Connection, entity_id: str) -> BlockerSummary:
    """Resolved active/read summary for the one Ticket relationship."""
    incoming_rows = conn.execute(
        """
        SELECT source.id, source.title, source.state
        FROM links
        JOIN tickets source ON source.id = links.from_id
        WHERE links.kind = 'blocks' AND links.to_id = ?
        ORDER BY source.state NOT IN ('done', 'dropped') DESC,
          source.title COLLATE NOCASE,
          source.id
        """,
        (entity_id,),
    ).fetchall()
    blocked_by = tuple(
        BlockedBySummaryRow(
            ticket_id=str(row["id"]),
            title=str(row["title"]),
            state=str(row["state"]),
            active=str(row["state"]) not in {"done", "dropped"},
            href=f"#/ticket/{row['id']}",
        )
        for row in incoming_rows
    )

    source_active = _ticket_is_active(conn, entity_id)
    outgoing_rows = conn.execute(
        """
        SELECT links.to_id,
          tickets.title AS ticket_title,
          sprint_items.title AS item_title
        FROM links
        LEFT JOIN tickets ON tickets.id = links.to_id
        LEFT JOIN sprint_items ON sprint_items.id = links.to_id
        WHERE links.kind = 'blocks' AND links.from_id = ?
        ORDER BY links.to_id
        """,
        (entity_id,),
    ).fetchall()
    blocks: list[BlocksTargetSummaryRow] = []
    for row in outgoing_rows:
        target_id = str(row["to_id"])
        target_kind: Literal["ticket", "sprint_item"]
        if _prefix(target_id) == "si":
            target_kind = "sprint_item"
            title = str(row["item_title"])
            href = f"#/sprint?item={target_id}"
        else:
            target_kind = "ticket"
            title = str(row["ticket_title"])
            href = f"#/ticket/{target_id}"
        blocks.append(
            BlocksTargetSummaryRow(
                target_id=target_id,
                target_kind=target_kind,
                title=title,
                active=source_active,
                href=href,
            )
        )

    return BlockerSummary(
        blocked=any(row.active for row in blocked_by),
        blocked_by=blocked_by,
        blocks=tuple(blocks),
    )
