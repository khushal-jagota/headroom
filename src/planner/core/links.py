"""Link create/remove over the `links` table plus the blocked-derivation (§3.6,
SPEC lines 54–57). Core infrastructure over the DB (like events.py) — sqlite3 lives
here, not in dispatch/logic. Config-free: `now` is always a parameter."""

from __future__ import annotations

import sqlite3
from collections import deque
from typing import Final

from planner.core.contracts import EventKind, LinkKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event

# blocks/parent_child are two independently-checked transitive relations (§3.6).
_CYCLE_CHECKED: Final[frozenset[LinkKind]] = frozenset(
    {LinkKind.blocks, LinkKind.parent_child}
)
# Endpoint prefix rules (D8): (allowed from-prefixes, allowed to-prefixes); None = any.
_ENDPOINT_RULES: Final[dict[LinkKind, tuple[frozenset[str] | None, frozenset[str] | None]]] = {
    LinkKind.belongs_to: (frozenset({"t"}), frozenset({"si"})),
    LinkKind.parent_child: (frozenset({"t"}), frozenset({"t"})),
    LinkKind.blocks: (frozenset({"t"}), frozenset({"t", "si"})),
    LinkKind.relates: (None, None),
}


def _prefix(entity_id: str) -> str:
    """The endpoint kind is the id prefix before the first '_' (t_x -> 't')."""
    return entity_id.split("_", 1)[0]


def add_link(
    conn: sqlite3.Connection, from_id: str, to_id: str, kind: LinkKind, now: int
) -> None:
    """Create a link, enforcing §3.6: no self-links, fixed endpoint kinds per link,
    at most one belongs_to per ticket, and no blocks/parent_child transitive cycle."""
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
    # Steps 3–6 serialize under the write lock (A2): BEGIN IMMEDIATE takes the lock
    # before the reads, so two connections cannot interleave BFS-then-insert to admit
    # a cycle or a second belongs_to. Contention resolves via db.connect's busy_timeout.
    own_txn = not conn.in_transaction
    if own_txn:
        conn.execute("BEGIN IMMEDIATE")
    try:
        # 3. belongs_to uniqueness (§3.6): at most one per ticket.
        if kind is LinkKind.belongs_to:
            existing = conn.execute(
                "SELECT 1 FROM links WHERE from_id=? AND kind='belongs_to' LIMIT 1",
                (from_id,),
            ).fetchone()
            if existing is not None:
                raise PlannerError(
                    ErrorCode.link_invalid,
                    "ticket already has a belongs_to link",
                    {"from_id": from_id},
                )
        # 4. Transitive cycle check (§3.6, D10): from->to makes a cycle iff `from` is
        # reachable from `to` along same-kind edges. BFS from to_id, visited-guarded.
        if kind in _CYCLE_CHECKED:
            queue: deque[str] = deque([to_id])
            visited: set[str] = {to_id}
            while queue:
                node = queue.popleft()
                if node == from_id:
                    raise PlannerError(
                        ErrorCode.link_cycle,
                        f"link would create a {kind.value} cycle",
                        detail,
                    )
                rows = conn.execute(
                    "SELECT to_id FROM links WHERE from_id=? AND kind=?",
                    (node, kind.value),
                ).fetchall()
                for succ in rows:
                    nxt = str(succ["to_id"])
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append(nxt)
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
    """Whether one entity is blocked. The join to tickets makes the rule literal:
    only a ticket source in an open state blocks; a missing/non-ticket source never
    does (D8)."""
    row = conn.execute(
        "SELECT 1 FROM links l "
        "JOIN tickets src ON src.id = l.from_id "
        "WHERE l.kind = 'blocks' AND src.state NOT IN ('done', 'dropped') "
        "AND l.to_id = ? LIMIT 1",
        (entity_id,),
    ).fetchone()
    return row is not None
