"""Bounded conversation history pages and locale-correct keyboard seeks."""

from __future__ import annotations

import json
import sqlite3

import anyio.to_thread
import icu  # type: ignore[import-untyped]
from fastapi import APIRouter, Request

from planner.conversation.history_contracts import (
    ConversationHistoryEntry,
    ConversationHistoryPage,
    HistoryOwnerKind,
)
from planner.core import authority
from planner.core.authctx import request_context
from planner.core.authority import require_above_or_self
from planner.core.errors import ErrorCode, PlannerError
from planner.files.catalog.config import PAGE_MAX_BYTES, PAGE_SIZE
from planner.managers.data import manager_agent_key
from planner.resource_versions.data import source_revision

router = APIRouter()


def _read(
    request: Request,
    kind: HistoryOwnerKind,
    owner_id: str,
    offset: int,
    limit: int,
    selected: str | None,
    prefix: str | None,
    after: int,
    locale: str,
    timezone: str,
) -> ConversationHistoryPage:
    if (
        offset < 0
        or not 1 <= limit <= PAGE_SIZE
        or len(locale) > 80
        or len(timezone) > 100
        or (prefix and len(prefix) > 200)
    ):
        raise PlannerError(ErrorCode.validation, "invalid conversation history page", {})
    conn = request.app.state.conn_factory()
    try:
        conn.execute("BEGIN")
        if kind == "manager":
            require_above_or_self(
                conn, request_context(request).principal, authority.manager(owner_id)
            )
        elif kind == "goal":
            require_above_or_self(
                conn, request_context(request).principal, authority.goal(owner_id)
            )
        elif conn.execute("SELECT 1 FROM tickets WHERE id=?", (owner_id,)).fetchone() is None:
            raise PlannerError(ErrorCode.not_found, "ticket not found", {})
        current_row = conn.execute(
            "SELECT conversation_id FROM tickets WHERE id=?"
            if kind == "ticket"
            else "SELECT conversation_id FROM agents WHERE agent_key=?",
            (
                owner_id
                if kind == "ticket"
                else "goal_" + owner_id
                if kind == "goal"
                else manager_agent_key(owner_id, conn),
            ),
        ).fetchone()
        current_id = current_row[0] if current_row else None
        args = (kind, owner_id, current_id)
        base = (
            " FROM conversation_history_catalog WHERE owner_kind=? AND owner_id=? "
            "AND conversation_id IS NOT ?"
        )
        order = " ORDER BY created_at,conversation_id"
        total = int(conn.execute("SELECT count(*)" + base, args).fetchone()[0])
        formatter = icu.DateFormat.createDateTimeInstance(
            icu.DateFormat.kMedium, icu.DateFormat.kShort, icu.Locale(locale)
        )
        formatter.setTimeZone(icu.TimeZone.createTimeZone(timezone))

        def entry(row: sqlite3.Row) -> ConversationHistoryEntry:
            return {
                "conversation_id": str(row[0]),
                "created_at": int(row[1]),
                "label": formatter.format(float(row[1])) + " · " + str(row[0]),
            }

        active: int | None = None
        if selected:
            row = conn.execute(
                "SELECT created_at,conversation_id" + base + " AND conversation_id=?",
                (*args, selected),
            ).fetchone()
            if row:
                active = int(
                    conn.execute(
                        "SELECT count(*)" + base + " AND (created_at,conversation_id)<(?,?)",
                        (*args, row[0], row[1]),
                    ).fetchone()[0]
                )
        if prefix:
            active = None
            wanted = prefix.casefold()
            # Format bounded cursor batches. No locale-specific index is written by a GET.
            start = min(max(after + 1, 0), total)
            for begin, end in ((start, total), (0, start)):
                cursor = conn.execute(
                    "SELECT conversation_id,created_at" + base + order + " LIMIT ? OFFSET ?",
                    (*args, end - begin, begin),
                )
                index = begin
                while batch := cursor.fetchmany(PAGE_SIZE):
                    for row in batch:
                        if entry(row)["label"].casefold().startswith(wanted):
                            active = index
                            break
                        index += 1
                    if active is not None:
                        break
                if active is not None:
                    break
        if active is not None:
            offset = (active // limit) * limit
        rows = conn.execute(
            "SELECT conversation_id,created_at" + base + order + " LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )
        entries: list[ConversationHistoryEntry] = []
        bytes_used = 0
        for row in rows:
            value = entry(row)
            size = len(json.dumps(value, ensure_ascii=False).encode())
            if bytes_used + size > PAGE_MAX_BYTES:
                if not entries:
                    raise PlannerError(
                        ErrorCode.validation, "conversation metadata exceeds page budget", {}
                    )
                break
            entries.append(value)
            bytes_used += size
        return {
            "revision": source_revision(conn, "conversation-history", kind + ":" + owner_id)
            + ":"
            + str(current_id),
            "offset": offset,
            "total": total,
            "active_index": active,
            "entries": entries,
        }
    finally:
        conn.close()


@router.get("/conversation-history/{kind}/{owner_id}")
async def history_page(
    request: Request,
    kind: str,
    owner_id: str,
    offset: int = 0,
    limit: int = PAGE_SIZE,
    selected: str | None = None,
    prefix: str | None = None,
    after: int = -1,
    locale: str = "en-US",
    timezone: str = "UTC",
) -> ConversationHistoryPage:
    canonical: HistoryOwnerKind
    if kind == "ticket":
        canonical = "ticket"
    elif kind == "goal":
        canonical = "goal"
    elif kind in {"manager", "sprint_item", "orchestrator"}:
        canonical = "manager"
    else:
        raise PlannerError(ErrorCode.validation, "unknown conversation history owner kind", {})
    return await anyio.to_thread.run_sync(
        _read,
        request,
        canonical,
        owner_id,
        offset,
        limit,
        selected,
        prefix,
        after,
        locale,
        timezone,
    )
