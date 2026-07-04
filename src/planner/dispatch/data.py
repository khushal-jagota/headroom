"""The dispatch data layer over sqlite3 — claims (§7.3), heartbeat, reclaim, run
close + circuit breaker (§7.5), and candidate assembly (§7.2 inputs). Config values
(ttl_seconds, failure_limit) are always parameters; this module never reads config or
the clock — every function takes now: int.

Single-winner guarantees come from two CAS statements: the claim CAS on
tickets.claim_lock and the finalize CAS on runs.status, each one atomic UPDATE.
Follow-up statements run only on the CAS winner's path, so they cannot double-run."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Final, NamedTuple

from planner.core.contracts import EventKind, JsonDict, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.core.ids import new_claim, new_id
from planner.core.links import blocked_target_ids
from planner.dispatch.contracts import (
    AGENT_CLOSE_OUTCOMES,
    FAILURE_STATUSES,
    DispatchCandidate,
    RunStatus,
)
from planner.dispatch.logic import (
    expiry_at,
    gating_field_pending,
    has_active_claim,
    is_expired,
    next_breaker_state,
)
from planner.tickets.contracts import AtCap, TicketState

# The statuses close_run accepts: agent outcomes plus failures (D20). running and
# reclaimed are rejected — reclaimed is reachable only through sweep_reclaims.
_CLOSABLE: Final[frozenset[RunStatus]] = AGENT_CLOSE_OUTCOMES | FAILURE_STATUSES


def claim(
    conn: sqlite3.Connection,
    ticket_id: str,
    now: int,
    ttl_seconds: int,
    pid: int | None = None,
) -> tuple[str, str] | None:
    """§7.3 claim: the CAS is the sole arbiter against double-claim. Returns
    (run_id, claim_token) on the win, or None when lost (rowcount 0 — indistinguishable
    from an unknown ticket id, D13). No eligibility re-check, no updated_at bump (D7)."""
    token = new_claim()
    expires = expiry_at(now, ttl_seconds)
    cursor = conn.execute(
        "UPDATE tickets SET claim_lock=?, claim_expires=? WHERE id=? AND claim_lock IS NULL",
        (token, expires, ticket_id),
    )
    if cursor.rowcount == 0:
        return None
    run_id = new_id("run")
    conn.execute(
        "INSERT INTO runs (id, ticket_id, status, started_at, ended_at, summary, error, pid) "
        "VALUES (?, ?, 'running', ?, NULL, NULL, NULL, ?)",
        (run_id, ticket_id, now, pid),
    )
    append_event(conn, ticket_id, EventKind.run_started, {"run_id": run_id, "pid": pid}, now)
    return run_id, token


def heartbeat(conn: sqlite3.Connection, run_id: str, now: int, ttl_seconds: int) -> int:
    """Extend the lease by exactly one TTL from now (D2), returning the new expiry.
    A missing run is not_found; a non-running run or an expired/cleared claim is
    stale_claim (D3) — an expired lease is never revived by a late heartbeat."""
    row = conn.execute("SELECT ticket_id, status FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "run not found", {"run_id": run_id})
    if str(row["status"]) != RunStatus.running.value:
        raise PlannerError(
            ErrorCode.stale_claim,
            "run is not running",
            {"run_id": run_id, "run_status": str(row["status"])},
        )
    ticket_id = str(row["ticket_id"])
    ticket = conn.execute(
        "SELECT claim_lock, claim_expires FROM tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    claim_lock = str(ticket["claim_lock"]) if ticket["claim_lock"] is not None else None
    claim_expires = int(ticket["claim_expires"]) if ticket["claim_expires"] is not None else None
    if not has_active_claim(claim_lock, claim_expires, now):
        raise PlannerError(
            ErrorCode.stale_claim,
            "claim expired or cleared",
            {"ticket_id": ticket_id, "claim_expires": claim_expires},
        )
    new_expires = expiry_at(now, ttl_seconds)
    conn.execute("UPDATE tickets SET claim_expires=? WHERE id=?", (new_expires, ticket_id))
    append_event(
        conn,
        ticket_id,
        EventKind.claim_heartbeat,
        {"run_id": run_id, "claim_expires": new_expires},
        now,
    )
    return new_expires


class _Finalized(NamedTuple):
    ticket_id: str
    consecutive_failures: int
    newly_auto_blocked: bool


def _finalize_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: RunStatus,
    now: int,
    summary: str | None,
    error: str | None,
    failure_limit: int,
) -> _Finalized | None:
    """The single door for runs.status + breaker writes (D5). The runs-UPDATE is a
    CAS guarded by status='running', so a close racing a reclaim has exactly one
    winner; the loser gets rowcount 0 and None back. Returns None on a missing run or
    a lost CAS — the caller decides what that means."""
    row = conn.execute("SELECT ticket_id FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        return None
    ticket_id = str(row["ticket_id"])
    cursor = conn.execute(
        "UPDATE runs SET status=?, ended_at=?, summary=?, error=? WHERE id=? AND status='running'",
        (status.value, now, summary, error, run_id),
    )
    if cursor.rowcount == 0:
        return None
    trow = conn.execute(
        "SELECT consecutive_failures, auto_blocked FROM tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    current = int(trow["consecutive_failures"])
    was_blocked = bool(trow["auto_blocked"])
    new_failures, tripped = next_breaker_state(current, status, failure_limit)
    newly = tripped and not was_blocked
    # auto_blocked is sticky: once set it is never written back to 0 here (only
    # clear_auto_block does that).
    flag = 1 if (was_blocked or tripped) else 0
    # Clear the lease and apply the breaker in one statement (no updated_at, D7). The
    # unconditional lock clear is safe: a newer claim needs claim_lock IS NULL, which
    # needs this run finalized first — the CAS above established we are that finalizer.
    conn.execute(
        "UPDATE tickets SET claim_lock=NULL, claim_expires=NULL, "
        "consecutive_failures=?, auto_blocked=? WHERE id=?",
        (new_failures, flag, ticket_id),
    )
    return _Finalized(ticket_id, new_failures, newly)


def close_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: RunStatus,
    now: int,
    failure_limit: int,
    summary: str | None = None,
    error: str | None = None,
) -> None:
    """Close a run with an agent outcome or failure status (§7.5). A1: an expired
    claim can never write — the close is rejected (stale_claim) before finalizing,
    leaving the run for sweep_reclaims. Emits run_closed, then auto_blocked only on the
    breaker's 0->1 trip."""
    if status not in _CLOSABLE:
        raise PlannerError(
            ErrorCode.validation, "not a closeable run status", {"status": status.value}
        )
    row = conn.execute("SELECT status, ticket_id FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "run not found", {"run_id": run_id})
    current_status = str(row["status"])
    if current_status != RunStatus.running.value:
        raise PlannerError(
            ErrorCode.stale_claim,
            "run is not running",
            {"run_id": run_id, "run_status": current_status},
        )
    ticket_id = str(row["ticket_id"])
    ticket = conn.execute(
        "SELECT claim_lock, claim_expires FROM tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    claim_lock = str(ticket["claim_lock"]) if ticket["claim_lock"] is not None else None
    claim_expires = int(ticket["claim_expires"]) if ticket["claim_expires"] is not None else None
    if not has_active_claim(claim_lock, claim_expires, now):
        raise PlannerError(
            ErrorCode.stale_claim,
            "claim expired or cleared",
            {"ticket_id": ticket_id, "claim_expires": claim_expires},
        )
    fin = _finalize_run(conn, run_id, status, now, summary, error, failure_limit)
    if fin is None:
        raise PlannerError(
            ErrorCode.stale_claim,
            "run is not running",
            {"run_id": run_id, "run_status": current_status},
        )
    append_event(
        conn,
        fin.ticket_id,
        EventKind.run_closed,
        {"run_id": run_id, "status": status.value, "summary": summary},
        now,
    )
    if fin.newly_auto_blocked:
        append_event(
            conn,
            fin.ticket_id,
            EventKind.auto_blocked,
            {"consecutive_failures": fin.consecutive_failures},
            now,
        )


def sweep_reclaims(
    conn: sqlite3.Connection,
    now: int,
    pid_alive: Callable[[int], bool],
    failure_limit: int,
) -> list[str]:
    """Stage-4 tick primitive: reclaim every running run whose claim expired (reason
    'expired', checked first — D14) or whose pid is dead (reason 'dead_pid'). Returns
    the reclaimed run ids. reclaimed leaves the breaker unchanged (D6); a run whose
    finalize CAS was lost to a legitimate close is skipped silently."""
    rows = conn.execute(
        "SELECT r.id AS run_id, r.pid, r.ticket_id, t.claim_expires "
        "FROM runs r JOIN tickets t ON t.id = r.ticket_id "
        "WHERE r.status = 'running' AND t.claim_lock IS NOT NULL"
    ).fetchall()
    reclaimed: list[str] = []
    for row in rows:
        run_id = str(row["run_id"])
        ticket_id = str(row["ticket_id"])
        claim_expires = int(row["claim_expires"]) if row["claim_expires"] is not None else None
        pid = int(row["pid"]) if row["pid"] is not None else None
        if is_expired(claim_expires, now):
            reason = "expired"
        elif pid is not None and not pid_alive(pid):
            reason = "dead_pid"
        else:
            continue
        fin = _finalize_run(conn, run_id, RunStatus.reclaimed, now, None, None, failure_limit)
        if fin is None:
            continue
        append_event(
            conn,
            ticket_id,
            EventKind.claim_reclaimed,
            {"run_id": run_id, "reason": reason},
            now,
        )
        reclaimed.append(run_id)
    return reclaimed


def clear_auto_block(conn: sqlite3.Connection, ticket_id: str, now: int) -> None:
    """§7.5 human unblock: resets both the flag and the counter and bumps updated_at
    (a human action on the ticket, D7). Idempotent — no guard on the current flag."""
    row = conn.execute("SELECT 1 FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})
    conn.execute(
        "UPDATE tickets SET auto_blocked=0, consecutive_failures=0, updated_at=? WHERE id=?",
        (now, ticket_id),
    )
    append_event(conn, ticket_id, EventKind.auto_block_cleared, {}, now)


def load_candidates(conn: sqlite3.Connection, now: int) -> list[DispatchCandidate]:
    """Assemble the pure §7.2 input rows for every ticket. Filtering is exclusively
    logic.is_eligible's job — no eligibility rule is duplicated in SQL. Every row value
    is coerced immediately so no Any leaks into the dataclass."""
    blocked = blocked_target_ids(conn)
    rows = conn.execute(
        "SELECT id, state, priority, deadline, created_at, ceiling, at_cap, "
        "auto_blocked, claim_lock, claim_expires, fields FROM tickets"
    ).fetchall()
    candidates: list[DispatchCandidate] = []
    for row in rows:
        state = TicketState(str(row["state"]))
        raw_deadline = row["deadline"]
        fields_json: JsonDict = json.loads(str(row["fields"]))
        claim_lock = str(row["claim_lock"]) if row["claim_lock"] is not None else None
        claim_expires = int(row["claim_expires"]) if row["claim_expires"] is not None else None
        ticket_id = str(row["id"])
        candidates.append(
            DispatchCandidate(
                ticket_id=ticket_id,
                state=state,
                priority=Priority(str(row["priority"])),
                deadline=str(raw_deadline) if raw_deadline is not None else None,
                created_at=int(row["created_at"]),
                ceiling=TicketState(str(row["ceiling"])),
                at_cap=AtCap(str(row["at_cap"])),
                auto_blocked=bool(row["auto_blocked"]),
                has_active_claim=has_active_claim(claim_lock, claim_expires, now),
                is_blocked=ticket_id in blocked,
                gating_pending=gating_field_pending(state, fields_json),
            )
        )
    return candidates
