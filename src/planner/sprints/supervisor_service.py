"""The item-scoped application service for Sprint Item supervisors."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

from planner.core import authority
from planner.core.authctx import RequestContext
from planner.core.authority import require_above, require_above_or_self
from planner.core.errors import ErrorCode, PlannerError
from planner.files.logic.paths import sprint_item_files_root
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views
from planner.tickets.contracts import Ticket

MAXIMUM_HISTORY_EVENTS = 100
SUPERVISOR_ARTIFACTS_DIRECTORY = "artifacts"
# A worker step gets this long to prove it is alive before anyone may restart it.
WORKER_STEP_RESTART_FLOOR_SECONDS = 300


def _require_child_of(conn: sqlite3.Connection, sprint_item_id: str, ticket_id: str) -> None:
    """Refuse a request whose path names one Outcome and whose Ticket sits under another.

    The rule answers whether the caller may act on the Ticket. This answers whether the
    address is coherent, which is a separate question these routes ask because they carry
    the Outcome in the path. The addresses go in this Ticket's route step and this goes
    with them.
    """
    row = conn.execute(
        "SELECT 1 FROM tickets WHERE id = ? AND sprint_item_id = ?",
        (ticket_id, sprint_item_id),
    ).fetchone()
    if row is None:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "that Ticket is not a current child of that Outcome",
            {"sprint_item_id": sprint_item_id, "ticket_id": ticket_id},
        )


def require_current_child(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
) -> Ticket:
    """Return the child after the shared server-side authority check."""
    require_above(conn, ctx.principal, authority.ticket(ticket_id))
    _require_child_of(conn, sprint_item_id, ticket_id)
    return tickets_data.read_ticket(conn, ticket_id)


def ticket_context(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
    *,
    now: int,
    triggering_message_sequence: int | None,
) -> dict[str, object]:
    with _coherent_read(conn):
        ticket = require_current_child(conn, ctx, sprint_item_id, ticket_id)
        triggering_message = None
        if triggering_message_sequence is not None and ticket.conversation_id is not None:
            row = conn.execute(
                "SELECT sequence, kind, payload, created_at FROM conversation_events "
                "WHERE conversation_id = ? AND kind = 'agent_message' AND sequence = ?",
                (ticket.conversation_id, triggering_message_sequence),
            ).fetchone()
            if row is not None:
                triggering_message = _event_json(row)
        if triggering_message_sequence is not None and triggering_message is None:
            raise PlannerError(
                ErrorCode.not_found,
                "the triggering Worker message is not in the current conversation",
                {
                    "ticket_id": ticket_id,
                    "conversation_id": ticket.conversation_id,
                    "sequence": triggering_message_sequence,
                },
            )
        day_ids = [
            str(row["day_id"])
            for row in conn.execute(
                "SELECT day_id FROM day_tickets WHERE ticket_id = ? ORDER BY day_id",
                (ticket_id,),
            ).fetchall()
        ]
        return {
            "sprint_item_id": sprint_item_id,
            "ticket": tickets_views.ticket_detail(conn, ticket_id, now),
            "day_ids": day_ids,
            "conversation_id": ticket.conversation_id,
            "triggering_worker_message": triggering_message,
        }


def conversation_history(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
    *,
    limit: int,
    before_sequence: int | None,
) -> dict[str, object]:
    if limit < 1 or limit > MAXIMUM_HISTORY_EVENTS:
        raise PlannerError(
            ErrorCode.validation,
            f"limit must be between 1 and {MAXIMUM_HISTORY_EVENTS}",
            {"limit": limit},
        )
    with _coherent_read(conn):
        ticket = require_current_child(conn, ctx, sprint_item_id, ticket_id)
        if ticket.conversation_id is None:
            raise PlannerError(
                ErrorCode.not_found,
                "the ticket has no current Worker conversation",
                {"ticket_id": ticket_id},
            )
        params: list[object] = [ticket.conversation_id]
        before_clause = ""
        if before_sequence is not None:
            before_clause = " AND sequence < ?"
            params.append(before_sequence)
        params.append(limit)
        rows = conn.execute(
            "SELECT * FROM ("
            "SELECT sequence, kind, payload, created_at FROM conversation_events "
            "WHERE conversation_id = ?"
            + before_clause
            + " ORDER BY sequence DESC LIMIT ?) ORDER BY sequence",
            tuple(params),
        ).fetchall()
        events = [_event_json(row) for row in rows]
        return {
            "sprint_item_id": sprint_item_id,
            "ticket_id": ticket_id,
            "conversation_id": ticket.conversation_id,
            "events": events,
            "has_more": bool(events and int(str(events[0]["sequence"])) > 1),
        }


@contextmanager
def _coherent_read(conn: sqlite3.Connection) -> Iterator[None]:
    """Hold one read result against child moves and conversation resets.

    A deferred ``BEGIN`` fixes one WAL snapshot at the scope check and does not reserve
    the writer. ``ROLLBACK`` closes an owned read on every exit without emitting the
    process change signal. Existing transaction owners keep ownership and supply their
    own coherent boundary.
    """
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        yield
    finally:
        if owns_transaction and conn.in_transaction:
            conn.execute("ROLLBACK")


def list_artifacts(
    conn: sqlite3.Connection, ctx: RequestContext, sprint_item_id: str, db_path: str
) -> dict[str, object]:
    _require_item(conn, ctx, sprint_item_id)
    root = _artifact_root(db_path, sprint_item_id, create=False)
    paths = (
        []
        if root is None
        else sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    )
    return {"sprint_item_id": sprint_item_id, "artifacts": paths}


def list_artifact_details(
    conn: sqlite3.Connection, ctx: RequestContext, sprint_item_id: str, db_path: str
) -> list[dict[str, object]]:
    """Return read-only file facts for the Sprint Item workspace."""
    _require_item(conn, ctx, sprint_item_id)
    root = _artifact_root(db_path, sprint_item_id, create=False)
    if root is None:
        return []
    return [
        {
            "path": f"{SUPERVISOR_ARTIFACTS_DIRECTORY}/{path.relative_to(root).as_posix()}",
            "modified_at": path.stat().st_mtime,
        }
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]


def write_artifact(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    db_path: str,
    relative_path: str,
    content: str,
) -> dict[str, object]:
    _require_item(conn, ctx, sprint_item_id)
    root = _artifact_root(db_path, sprint_item_id, create=True)
    assert root is not None
    target = _safe_artifact_target(root, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(root, target)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "sprint_item_id": sprint_item_id,
        "path": f"{SUPERVISOR_ARTIFACTS_DIRECTORY}/{relative_path}",
        "url": (
            f"/files/sprint-items/{sprint_item_id}/{SUPERVISOR_ARTIFACTS_DIRECTORY}/{relative_path}"
        ),
    }


def delete_artifact(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    db_path: str,
    relative_path: str,
) -> dict[str, object]:
    _require_item(conn, ctx, sprint_item_id)
    root = _artifact_root(db_path, sprint_item_id, create=False)
    if root is None:
        raise PlannerError(ErrorCode.not_found, "Sprint Item artifact not found", {})
    target = _safe_artifact_target(root, relative_path)
    _reject_symlink_ancestors(root, target)
    if not target.is_file() or target.is_symlink():
        raise PlannerError(ErrorCode.not_found, "Sprint Item artifact not found", {})
    target.unlink()
    return {"ok": True, "sprint_item_id": sprint_item_id, "path": relative_path}


def _event_json(row: sqlite3.Row) -> dict[str, object]:
    return {
        "sequence": int(row["sequence"]),
        "kind": str(row["kind"]),
        "payload": json.loads(str(row["payload"])),
        "created_at": int(row["created_at"]),
    }


def _require_item(conn: sqlite3.Connection, ctx: RequestContext, sprint_item_id: str) -> None:
    require_above_or_self(conn, ctx.principal, authority.outcome(sprint_item_id))


def _artifact_root(db_path: str, sprint_item_id: str, *, create: bool) -> Path | None:
    files_root = sprint_item_files_root(db_path)
    if files_root.is_symlink():
        raise PlannerError(ErrorCode.validation, "unsafe Sprint Item artifact path", {})
    item_root = files_root / sprint_item_id
    if create:
        item_root.mkdir(parents=True, exist_ok=True)
    elif not item_root.is_dir() or item_root.is_symlink():
        return None
    root = item_root / SUPERVISOR_ARTIFACTS_DIRECTORY
    if create:
        if item_root.is_symlink() or root.is_symlink():
            raise PlannerError(ErrorCode.validation, "unsafe Sprint Item artifact path", {})
        root.mkdir(exist_ok=True)
    elif not root.is_dir() or root.is_symlink():
        return None
    return root.resolve()


def _safe_artifact_target(root: Path, relative_path: str) -> Path:
    parsed = PurePosixPath(relative_path)
    if (
        not relative_path
        or parsed.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise PlannerError(ErrorCode.validation, "unsafe Sprint Item artifact path", {})
    target = root.joinpath(*parsed.parts)
    try:
        target.parent.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise PlannerError(ErrorCode.validation, "unsafe Sprint Item artifact path", {}) from exc
    return target


def _reject_symlink_ancestors(root: Path, target: Path) -> None:
    current = target.parent
    while current != root:
        if current.is_symlink():
            raise PlannerError(ErrorCode.validation, "unsafe Sprint Item artifact path", {})
        current = current.parent
