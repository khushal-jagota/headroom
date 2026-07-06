"""The boundary job (§6.2). Runs once per planning date, guarded by the
boundary_runs table. Deterministic pass (materialize the day, carryover, overdue,
approvals, close yesterday) then one adapter judgment call wrapped in a
caller-owned timeout. On failure/timeout the day survives with an empty overview.
Judgment is skipped entirely when the human already planned the day."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import timedelta
from typing import Any

from planner.core.adapters.base import BoundaryAdapter, BoundaryInputs, BoundaryJudgment
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import EventKind
from planner.core.events import append_event
from planner.core.ids import day_id
from planner.days.contracts import NodeStatus
from planner.days.data import load_plan, materialize_day, store_judgment
from planner.days.logic.carryover import (
    approvals_digest,
    carryover_candidates,
    day_ticket_counts,
    overdue_list,
)
from planner.days.logic.dates import planning_date


def run_boundary(
    conn: sqlite3.Connection, clock: Clock, config: Config, adapter: BoundaryAdapter
) -> None:
    """One boundary tick. Idempotent per planning date via the boundary_runs
    guard, so the stage-4 scheduler may call it every tick — only the first tick
    that advances the planning date does work (planning_date only becomes the new
    date at/after boundary_hour, satisfying §6.2 'first tick at/after the boundary
    hour')."""
    pd = planning_date(clock.now(), config.boundary_hour)
    piso = pd.isoformat()
    if _boundary_ran(conn, piso):
        return

    now = clock.now_unix()
    ndid = day_id(pd)
    yid = day_id(pd - timedelta(days=1))

    # Deterministic pass — always runs on a first tick.
    materialize_day(conn, ndid, now)
    yts = _read_yesterday_tickets(conn, yid)
    carry = carryover_candidates(yts)
    overdue_tickets, overdue_items = _read_overdue_candidates(conn)
    overdue = overdue_list(overdue_tickets, overdue_items, piso)
    approval_tickets, approval_items = _read_approval_candidates(conn)
    approvals = approvals_digest(approval_tickets, approval_items)
    done, not_done = day_ticket_counts(yts)
    append_event(
        conn,
        yid,
        EventKind.day_closed,
        {"done_count": done, "not_done_count": not_done},
        now,
    )

    # Skip check (§6.2 last paragraph): a human already planned this day.
    if _human_planned(conn, ndid):
        _record_boundary(conn, piso, now, "skipped")
        return

    # Judgment pass — one adapter call under a caller-owned timeout.
    inputs = BoundaryInputs(
        planning_date=piso, carryover=carry, overdue=overdue, approvals_digest=approvals
    )
    timeout_s = config.boundary_timeout_seconds
    try:
        judgment = _judgment_with_timeout(adapter, inputs, timeout_s)
    except Exception as exc:  # timeout OR any adapter raise; day survives empty
        append_event(
            conn, ndid, EventKind.boundary_failed, {"error": _error_text(exc, timeout_s)}, now
        )
        _record_boundary(conn, piso, now, "failed")
        return

    store_judgment(
        conn, ndid, judgment.focus, judgment.brief_take, judgment.watchout,
        judgment.if_today_lands, now,
    )
    # The overview was authored — one day_updated so an open Day page refetches.
    append_event(
        conn, ndid, EventKind.day_updated, {"field": "overview", "cause": "boundary"}, now
    )
    _record_boundary(conn, piso, now, "ok")


def _boundary_ran(conn: sqlite3.Connection, pd_iso: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM boundary_runs WHERE planning_date = ?", (pd_iso,)
    ).fetchone() is not None


def _record_boundary(conn: sqlite3.Connection, pd_iso: str, now_unix: int, judgment: str) -> None:
    conn.execute(
        "INSERT INTO boundary_runs (planning_date, ran_at, judgment) VALUES (?, ?, ?)",
        (pd_iso, now_unix, judgment),
    )


def _read_yesterday_tickets(conn: sqlite3.Connection, yesterday_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT t.id AS id, t.title AS title, t.state AS state, t.priority AS priority "
        "FROM day_tickets dt JOIN tickets t ON t.id = dt.ticket_id "
        "WHERE dt.day_id = ? ORDER BY dt.position",
        (yesterday_id,),
    ).fetchall()
    return [
        {"id": r["id"], "title": r["title"], "state": r["state"], "priority": r["priority"]}
        for r in rows
    ]


def _read_overdue_candidates(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tickets: list[dict[str, Any]] = [
        {
            "id": r["id"],
            "title": r["title"],
            "state": r["state"],
            "priority": r["priority"],
            "deadline": r["deadline"],
        }
        for r in conn.execute(
            "SELECT id, title, state, priority, deadline FROM tickets"
        ).fetchall()
    ]
    items: list[dict[str, Any]] = [
        {
            "id": r["id"],
            "title": r["title"],
            "status": r["status"],
            "priority": r["priority"],
            "deadline": r["deadline"],
        }
        for r in conn.execute(
            "SELECT id, title, status, priority, deadline FROM sprint_items"
        ).fetchall()
    ]
    return tickets, items


def _read_approval_candidates(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tickets: list[dict[str, Any]] = [
        {
            "id": r["id"],
            "state": r["state"],
            "fields": json.loads(r["fields"]),
            "updated_at": r["updated_at"],
        }
        for r in conn.execute("SELECT id, state, fields, updated_at FROM tickets").fetchall()
    ]
    items: list[dict[str, Any]] = []
    for r in conn.execute("SELECT id, status_proposal FROM sprint_items").fetchall():
        raw = r["status_proposal"]
        items.append(
            {"id": r["id"], "status_proposal": json.loads(raw) if raw is not None else None}
        )
    return tickets, items


def _human_planned(conn: sqlite3.Connection, new_day_id: str) -> bool:
    """True iff a day-ticket exists on the new day OR the stored plan has the root
    accepted or any child accepted (§6.2 'any day-ticket or accepted plan')."""
    if conn.execute(
        "SELECT 1 FROM day_tickets WHERE day_id = ? LIMIT 1", (new_day_id,)
    ).fetchone() is not None:
        return True
    tree = load_plan(conn, new_day_id)
    if tree is None:
        return False
    if tree.root.status == NodeStatus.accepted:
        return True
    return any(child.status == NodeStatus.accepted for child in tree.children)


def _judgment_with_timeout(
    adapter: BoundaryAdapter, inputs: BoundaryInputs, timeout_s: int
) -> BoundaryJudgment:
    """Run adapter.judgment in a worker thread; the CALLER owns the timeout
    (base.py). result(timeout=...) raises FuturesTimeout on timeout and re-raises
    any adapter exception — both are caught by run_boundary. shutdown(wait=False)
    so a hung adapter cannot block run_boundary past the configured timeout; the
    abandoned worker thread is left to finish on its own."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(adapter.judgment, inputs).result(timeout=timeout_s)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _error_text(exc: Exception, timeout_s: int) -> str:
    if isinstance(exc, FuturesTimeout):
        return f"boundary judgment timed out after {timeout_s}s"
    return str(exc) or exc.__class__.__name__
