"""Unit tests for the dispatch domain and links infrastructure — acceptance items
9, 11, 14, 15, 16 (§18.3). Per plan A5, exactly one test per item carries the
test_aNN_ anchor and covers that item's full fence; supplementary tests are named
without an anchor. Tickets are inserted via direct SQL only; the sole
planner.tickets import is its contracts. Times are literal ints (D17)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import planner.core.links as links
from planner.core.config import Config
from planner.core.contracts import EventKind, LinkKind, Priority
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.dispatch import data
from planner.dispatch.contracts import DispatchCandidate, RunStatus
from planner.dispatch.logic import is_eligible, next_breaker_state, ordering_key
from planner.tickets.contracts import AtCap, TicketState

T0 = 1_000_000


# --- helpers (plain functions, no fixtures) ---


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    state: str = "needs_success",
    priority: str = "P3",
    deadline: str | None = None,
    ceiling: str = "needs_success",
    at_cap: str = "propose",
    created_at: int = T0,
    fields: dict | None = None,
) -> None:
    columns = ["id", "title", "state", "priority", "deadline", "ceiling", "at_cap"]
    values: list[object] = [
        ticket_id, f"ticket {ticket_id}", state, priority, deadline, ceiling, at_cap,
    ]
    if fields is not None:
        columns.append("fields")
        values.append(json.dumps(fields))
    columns += ["created_at", "updated_at"]
    values += [created_at, created_at]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO tickets ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def _mk_candidate(
    ticket_id: str = "t_x",
    *,
    state: TicketState = TicketState.needs_success,
    priority: Priority = Priority.P3,
    deadline: str | None = None,
    created_at: int = T0,
    ceiling: TicketState = TicketState.needs_success,
    at_cap: AtCap = AtCap.propose,
    auto_blocked: bool = False,
    has_active_claim: bool = False,
    is_blocked: bool = False,
    gating_pending: bool = False,
) -> DispatchCandidate:
    return DispatchCandidate(
        ticket_id=ticket_id,
        state=state,
        priority=priority,
        deadline=deadline,
        created_at=created_at,
        ceiling=ceiling,
        at_cap=at_cap,
        auto_blocked=auto_blocked,
        has_active_claim=has_active_claim,
        is_blocked=is_blocked,
        gating_pending=gating_pending,
    )


def _events(conn: sqlite3.Connection, entity_id: str, kind: EventKind) -> list[dict]:
    rows = conn.execute(
        "SELECT payload FROM events WHERE entity_id=? AND kind=? ORDER BY id",
        (entity_id, kind.value),
    ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def _ticket_row(conn: sqlite3.Connection, ticket_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    assert row is not None
    return row


def _run_row(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row is not None
    return row


def _candidate(conn: sqlite3.Connection, ticket_id: str, now: int) -> DispatchCandidate:
    matches = [c for c in data.load_candidates(conn, now) if c.ticket_id == ticket_id]
    assert len(matches) == 1
    return matches[0]


def PENDING_FIELDS(field: str) -> dict:
    slots = {
        name: {"value": None, "proposal": None, "notes": None}
        for name in ("success", "approach", "plan", "result")
    }
    slots[field] = {
        "value": None,
        "proposal": {"body": "x", "proposed_by": "agent", "created_at": T0},
        "notes": None,
    }
    return slots


def _assert_error(
    exc: PlannerError,
    code: ErrorCode,
    *,
    keys: tuple[str, ...] = (),
    values: dict | None = None,
) -> None:
    """A4: pin the full to_payload() envelope, its inner keys, the code string, a
    non-empty message, and the plan-specified detail keys/values."""
    payload = exc.to_payload()
    assert set(payload) == {"error"}
    inner = payload["error"]
    assert set(inner) == {"code", "message", "detail"}
    assert inner["code"] == code.value
    assert isinstance(inner["message"], str) and inner["message"] != ""
    detail = inner["detail"]
    for key in keys:
        assert key in detail
    for key, val in (values or {}).items():
        assert detail[key] == val


# --- Item 9 (anchored): blocking and cycle rejection (SPEC line 286) ---


def test_a09_blocking_and_cycle(tmp_db: sqlite3.Connection) -> None:
    # Blocked by an open ticket -> dispatch-ineligible; the blocker itself is not blocked.
    _insert_ticket(tmp_db, "t_blk", state="in_progress")
    _insert_ticket(tmp_db, "t_tgt")
    links.add_link(tmp_db, "t_blk", "t_tgt", LinkKind.blocks, T0)
    tgt = _candidate(tmp_db, "t_tgt", T0)
    assert tgt.is_blocked is True
    assert is_eligible(tgt) is False
    blk = _candidate(tmp_db, "t_blk", T0)
    assert blk.is_blocked is False
    # Blocker -> done makes the target eligible again.
    tmp_db.execute("UPDATE tickets SET state='done' WHERE id='t_blk'")
    tgt_after = _candidate(tmp_db, "t_tgt", T0)
    assert tgt_after.is_blocked is False
    assert is_eligible(tgt_after) is True
    # blocks cycle rejected: direct 2-cycle...
    _insert_ticket(tmp_db, "t_a")
    _insert_ticket(tmp_db, "t_b")
    _insert_ticket(tmp_db, "t_c")
    links.add_link(tmp_db, "t_a", "t_b", LinkKind.blocks, T0)
    with pytest.raises(PlannerError) as exc:
        links.add_link(tmp_db, "t_b", "t_a", LinkKind.blocks, T0)
    assert exc.value.code is ErrorCode.link_cycle
    assert exc.value.code.value == "link_cycle"
    _assert_error(
        exc.value,
        ErrorCode.link_cycle,
        keys=("from_id", "to_id", "kind"),
        values={"from_id": "t_b", "to_id": "t_a", "kind": "blocks"},
    )
    # ...and transitive 3-node cycle at depth 2.
    links.add_link(tmp_db, "t_b", "t_c", LinkKind.blocks, T0)
    with pytest.raises(PlannerError) as exc2:
        links.add_link(tmp_db, "t_c", "t_a", LinkKind.blocks, T0)
    assert exc2.value.code is ErrorCode.link_cycle
    _assert_error(
        exc2.value,
        ErrorCode.link_cycle,
        keys=("from_id", "to_id", "kind"),
        values={"from_id": "t_c", "to_id": "t_a", "kind": "blocks"},
    )
    # Rejected inserts left no rows among the cycle triple.
    count = tmp_db.execute(
        "SELECT COUNT(*) FROM links WHERE kind='blocks' AND from_id IN ('t_a','t_b','t_c')"
    ).fetchone()[0]
    assert count == 2


def test_links_blocker_dropped_unblocks(tmp_db: sqlite3.Connection) -> None:
    # A dropped blocker also unblocks — locks the "not done OR dropped" wording (line 57).
    _insert_ticket(tmp_db, "t_blk2", state="in_progress")
    _insert_ticket(tmp_db, "t_tgt2")
    links.add_link(tmp_db, "t_blk2", "t_tgt2", LinkKind.blocks, T0)
    tmp_db.execute("UPDATE tickets SET state='dropped' WHERE id='t_blk2'")
    tgt2 = _candidate(tmp_db, "t_tgt2", T0)
    assert tgt2.is_blocked is False
    assert is_eligible(tgt2) is True


def test_links_parent_child_cycle_rejected(tmp_db: sqlite3.Connection) -> None:
    _insert_ticket(tmp_db, "t_p")
    _insert_ticket(tmp_db, "t_q")
    _insert_ticket(tmp_db, "t_r")
    links.add_link(tmp_db, "t_p", "t_q", LinkKind.parent_child, T0)
    with pytest.raises(PlannerError) as exc:
        links.add_link(tmp_db, "t_q", "t_p", LinkKind.parent_child, T0)
    assert exc.value.code is ErrorCode.link_cycle
    _assert_error(
        exc.value,
        ErrorCode.link_cycle,
        keys=("from_id", "to_id", "kind"),
        values={"from_id": "t_q", "to_id": "t_p", "kind": "parent_child"},
    )
    links.add_link(tmp_db, "t_q", "t_r", LinkKind.parent_child, T0)
    with pytest.raises(PlannerError) as exc2:
        links.add_link(tmp_db, "t_r", "t_p", LinkKind.parent_child, T0)
    assert exc2.value.code is ErrorCode.link_cycle
    _assert_error(
        exc2.value,
        ErrorCode.link_cycle,
        keys=("from_id", "to_id", "kind"),
        values={"from_id": "t_r", "to_id": "t_p", "kind": "parent_child"},
    )
    count = tmp_db.execute(
        "SELECT COUNT(*) FROM links WHERE kind='parent_child'"
    ).fetchone()[0]
    assert count == 2


def test_links_self_link_second_belongs_to_duplicate_rejected(
    tmp_db: sqlite3.Connection,
) -> None:
    _insert_ticket(tmp_db, "t_a")
    # (a) self-link
    with pytest.raises(PlannerError) as exc:
        links.add_link(tmp_db, "t_a", "t_a", LinkKind.relates, T0)
    assert exc.value.code is ErrorCode.link_invalid
    _assert_error(exc.value, ErrorCode.link_invalid, keys=("from_id", "to_id", "kind"))
    # (b) second belongs_to
    links.add_link(tmp_db, "t_a", "si_one", LinkKind.belongs_to, T0)
    with pytest.raises(PlannerError) as exc2:
        links.add_link(tmp_db, "t_a", "si_two", LinkKind.belongs_to, T0)
    assert exc2.value.code is ErrorCode.link_invalid
    _assert_error(exc2.value, ErrorCode.link_invalid, keys=("from_id",))
    count = tmp_db.execute(
        "SELECT COUNT(*) FROM links WHERE from_id='t_a' AND kind='belongs_to'"
    ).fetchone()[0]
    assert count == 1
    surviving = tmp_db.execute(
        "SELECT to_id FROM links WHERE from_id='t_a' AND kind='belongs_to'"
    ).fetchone()[0]
    assert surviving == "si_one"
    # (c) duplicate identical triple
    with pytest.raises(PlannerError) as exc3:
        links.add_link(tmp_db, "t_a", "si_one", LinkKind.belongs_to, T0)
    assert exc3.value.code is ErrorCode.link_invalid
    _assert_error(exc3.value, ErrorCode.link_invalid, keys=("from_id",))


def test_links_events_and_remove(tmp_db: sqlite3.Connection) -> None:
    _insert_ticket(tmp_db, "t_a")
    _insert_ticket(tmp_db, "t_b")
    links.add_link(tmp_db, "t_a", "t_b", LinkKind.relates, T0)
    links.remove_link(tmp_db, "t_a", "t_b", LinkKind.relates, T0 + 1)
    added = _events(tmp_db, "t_a", EventKind.link_added)
    assert added == [{"from_id": "t_a", "to_id": "t_b", "kind": "relates"}]
    removed = _events(tmp_db, "t_a", EventKind.link_removed)
    assert removed == [{"from_id": "t_a", "to_id": "t_b", "kind": "relates"}]
    gone = tmp_db.execute(
        "SELECT COUNT(*) FROM links WHERE from_id='t_a' AND to_id='t_b' AND kind='relates'"
    ).fetchone()[0]
    assert gone == 0
    with pytest.raises(PlannerError) as exc:
        links.remove_link(tmp_db, "t_a", "t_b", LinkKind.relates, T0 + 2)
    assert exc.value.code is ErrorCode.not_found
    _assert_error(exc.value, ErrorCode.not_found, keys=("from_id", "to_id", "kind"))


# --- Item 11 (anchored): ordering over five eligible tickets (SPEC line 288) ---


def test_a11_dispatch_ordering_five_eligible() -> None:
    c_p0_nodate = _mk_candidate("c_p0_nodate", priority=Priority.P0, deadline=None, created_at=200)
    c_p1_early = _mk_candidate(
        "c_p1_early", priority=Priority.P1, deadline="2026-07-08", created_at=300
    )
    c_p1_late = _mk_candidate(
        "c_p1_late", priority=Priority.P1, deadline="2026-07-10", created_at=100
    )
    c_p1_none_a = _mk_candidate("c_p1_none_a", priority=Priority.P1, deadline=None, created_at=50)
    c_p1_none_b = _mk_candidate("c_p1_none_b", priority=Priority.P1, deadline=None, created_at=60)
    all_cands = [c_p0_nodate, c_p1_early, c_p1_late, c_p1_none_a, c_p1_none_b]
    assert all(is_eligible(c) for c in all_cands)
    shuffled = [c_p1_late, c_p1_none_b, c_p0_nodate, c_p1_none_a, c_p1_early]
    ordered = sorted((c for c in shuffled if is_eligible(c)), key=ordering_key)
    assert [c.ticket_id for c in ordered] == [
        "c_p0_nodate",
        "c_p1_early",
        "c_p1_late",
        "c_p1_none_a",
        "c_p1_none_b",
    ]


def test_eligibility_gate_matrix() -> None:
    base = _mk_candidate(
        state=TicketState.needs_success, ceiling=TicketState.needs_plan, at_cap=AtCap.propose
    )
    assert is_eligible(base) is True
    assert (
        is_eligible(
            _mk_candidate(
                state=TicketState.needs_success, ceiling=TicketState.needs_plan, at_cap=AtCap.stop
            )
        )
        is True
    )
    assert is_eligible(_mk_candidate(state=TicketState.needs_review)) is False
    assert is_eligible(_mk_candidate(state=TicketState.done)) is False
    assert is_eligible(_mk_candidate(state=TicketState.dropped)) is False
    assert (
        is_eligible(
            _mk_candidate(
                state=TicketState.needs_success, ceiling=TicketState.needs_plan, gating_pending=True
            )
        )
        is False
    )
    assert (
        is_eligible(
            _mk_candidate(
                state=TicketState.needs_success, ceiling=TicketState.needs_plan, is_blocked=True
            )
        )
        is False
    )
    assert (
        is_eligible(
            _mk_candidate(
                state=TicketState.needs_success, ceiling=TicketState.needs_plan, auto_blocked=True
            )
        )
        is False
    )
    assert (
        is_eligible(
            _mk_candidate(
                state=TicketState.needs_success,
                ceiling=TicketState.needs_plan,
                has_active_claim=True,
            )
        )
        is False
    )
    assert (
        is_eligible(
            _mk_candidate(
                state=TicketState.needs_plan, ceiling=TicketState.needs_plan, at_cap=AtCap.propose
            )
        )
        is True
    )
    assert (
        is_eligible(
            _mk_candidate(
                state=TicketState.needs_plan, ceiling=TicketState.needs_plan, at_cap=AtCap.stop
            )
        )
        is False
    )
    assert (
        is_eligible(
            _mk_candidate(
                state=TicketState.in_progress,
                ceiling=TicketState.needs_success,
                at_cap=AtCap.propose,
            )
        )
        is False
    )
    assert (
        is_eligible(_mk_candidate(state=TicketState.in_progress, ceiling=TicketState.done)) is True
    )


def test_eligibility_gating_pending_assembly(tmp_db: sqlite3.Connection) -> None:
    _insert_ticket(
        tmp_db, "t_gate", state="needs_approach", ceiling="in_progress",
        fields=PENDING_FIELDS("approach"),
    )
    gate = _candidate(tmp_db, "t_gate", T0)
    assert gate.gating_pending is True
    assert is_eligible(gate) is False
    _insert_ticket(
        tmp_db, "t_free", state="needs_approach", ceiling="in_progress",
        fields=PENDING_FIELDS("plan"),
    )
    free = _candidate(tmp_db, "t_free", T0)
    assert free.gating_pending is False
    assert is_eligible(free) is True


# --- Item 14 (anchored): claim CAS (SPEC line 291) ---


def test_a14_claim_cas_exactly_one_winner(tmp_path: Path, cfg: Config) -> None:
    db = tmp_path / "cas.db"
    conn1 = connect(str(db))
    create_schema(conn1)
    conn2 = connect(str(db))
    try:
        _insert_ticket(conn1, "t_race")
        assert cfg.claim_ttl_seconds == 900  # SPEC §13 default
        won = data.claim(conn1, "t_race", T0, cfg.claim_ttl_seconds, pid=111)
        lost = data.claim(conn2, "t_race", T0, cfg.claim_ttl_seconds, pid=222)
        assert won is not None
        assert lost is None
        run_id, token = won
        run_count = conn1.execute(
            "SELECT COUNT(*) FROM runs WHERE ticket_id='t_race'"
        ).fetchone()[0]
        assert run_count == 1
        run = _run_row(conn1, run_id)
        assert run["status"] == "running"
        assert run["started_at"] == T0
        assert run["ended_at"] is None
        assert run["pid"] == 111
        ticket = _ticket_row(conn1, "t_race")
        assert ticket["claim_lock"] == token
        assert ticket["claim_expires"] == T0 + 900
        assert _events(conn1, "t_race", EventKind.run_started) == [
            {"run_id": run_id, "pid": 111}
        ]
    finally:
        conn1.close()
        conn2.close()


# --- Item 15 (anchored): TTL reclaim + heartbeat (SPEC line 292) ---


def test_a15_ttl_reclaim_and_heartbeat(tmp_db: sqlite3.Connection, cfg: Config) -> None:
    # Part 1: expired claim reclaimed — run reclaimed, lock cleared, eligible again.
    _insert_ticket(tmp_db, "t_exp")
    won = data.claim(tmp_db, "t_exp", T0, 900, pid=333)
    assert won is not None
    run_id, _token = won
    pre = _candidate(tmp_db, "t_exp", T0)
    assert pre.has_active_claim is True
    assert is_eligible(pre) is False
    # PID reported alive but the claim is expired -> reclaimed with reason "expired".
    reclaimed = data.sweep_reclaims(
        tmp_db, T0 + 901, pid_alive=lambda p: True, failure_limit=cfg.failure_limit
    )
    assert reclaimed == [run_id]
    run = _run_row(tmp_db, run_id)
    assert run["status"] == "reclaimed"
    assert run["ended_at"] == T0 + 901
    assert run["summary"] is None
    ticket = _ticket_row(tmp_db, "t_exp")
    assert ticket["claim_lock"] is None
    assert ticket["claim_expires"] is None
    assert ticket["consecutive_failures"] == 0
    assert _events(tmp_db, "t_exp", EventKind.claim_reclaimed) == [
        {"run_id": run_id, "reason": "expired"}
    ]
    assert is_eligible(_candidate(tmp_db, "t_exp", T0 + 901)) is True
    # Part 2: heartbeat extends expiry to exactly now + one TTL (second ticket).
    _insert_ticket(tmp_db, "t_hb")
    won_hb = data.claim(tmp_db, "t_hb", T0, cfg.claim_ttl_seconds)
    assert won_hb is not None
    run_hb, _token_hb = won_hb
    assert _ticket_row(tmp_db, "t_hb")["claim_expires"] == T0 + 900
    new = data.heartbeat(tmp_db, run_hb, T0 + 300, cfg.claim_ttl_seconds)
    # Exactly one TTL from *now*, not from the previous expiry.
    assert new == T0 + 1200
    assert new != T0 + 1800
    assert _ticket_row(tmp_db, "t_hb")["claim_expires"] == T0 + 300 + 900
    assert _events(tmp_db, "t_hb", EventKind.claim_heartbeat) == [
        {"run_id": run_hb, "claim_expires": T0 + 1200}
    ]


def test_claims_expiry_boundary_exact(tmp_db: sqlite3.Connection) -> None:
    _insert_ticket(tmp_db, "t_bnd")
    won = data.claim(tmp_db, "t_bnd", T0, 900)
    assert won is not None
    run_id, _token = won
    assert (
        data.sweep_reclaims(tmp_db, T0 + 899, pid_alive=lambda p: True, failure_limit=2) == []
    )
    assert _run_row(tmp_db, run_id)["status"] == "running"
    assert _candidate(tmp_db, "t_bnd", T0 + 899).has_active_claim is True
    assert (
        data.sweep_reclaims(tmp_db, T0 + 900, pid_alive=lambda p: True, failure_limit=2)
        == [run_id]
    )


def test_claims_dead_pid_reclaimed(tmp_db: sqlite3.Connection, cfg: Config) -> None:
    _insert_ticket(tmp_db, "t_dead")
    won = data.claim(tmp_db, "t_dead", T0, 900, pid=444)
    assert won is not None
    run_id, _token = won
    reclaimed = data.sweep_reclaims(
        tmp_db, T0 + 10, pid_alive=lambda p: False, failure_limit=cfg.failure_limit
    )
    assert reclaimed == [run_id]
    assert _run_row(tmp_db, run_id)["status"] == "reclaimed"
    assert _events(tmp_db, "t_dead", EventKind.claim_reclaimed) == [
        {"run_id": run_id, "reason": "dead_pid"}
    ]
    assert _ticket_row(tmp_db, "t_dead")["claim_lock"] is None


def test_claims_heartbeat_on_expired_rejected(tmp_db: sqlite3.Connection) -> None:
    _insert_ticket(tmp_db, "t_late")
    won = data.claim(tmp_db, "t_late", T0, 900)
    assert won is not None
    run_id, _token = won
    with pytest.raises(PlannerError) as exc:
        data.heartbeat(tmp_db, run_id, T0 + 900, 900)
    assert exc.value.code is ErrorCode.stale_claim
    _assert_error(
        exc.value,
        ErrorCode.stale_claim,
        keys=("ticket_id", "claim_expires"),
        values={"claim_expires": T0 + 900},
    )
    assert _ticket_row(tmp_db, "t_late")["claim_expires"] == T0 + 900
    assert _events(tmp_db, "t_late", EventKind.claim_heartbeat) == []


def test_claims_close_after_reclaim_rejected(tmp_db: sqlite3.Connection, cfg: Config) -> None:
    _insert_ticket(tmp_db, "t_gone")
    won = data.claim(tmp_db, "t_gone", T0, 900)
    assert won is not None
    run_id, _token = won
    assert (
        data.sweep_reclaims(
            tmp_db, T0 + 901, pid_alive=lambda p: True, failure_limit=cfg.failure_limit
        )
        == [run_id]
    )
    with pytest.raises(PlannerError) as exc:
        data.close_run(tmp_db, run_id, RunStatus.done, T0 + 950, cfg.failure_limit)
    assert exc.value.code is ErrorCode.stale_claim
    _assert_error(
        exc.value,
        ErrorCode.stale_claim,
        keys=("run_id", "run_status"),
        values={"run_status": "reclaimed"},
    )
    run = _run_row(tmp_db, run_id)
    assert run["status"] == "reclaimed"
    assert run["ended_at"] == T0 + 901
    assert _ticket_row(tmp_db, "t_gone")["consecutive_failures"] == 0
    assert _events(tmp_db, "t_gone", EventKind.run_closed) == []


def test_claims_close_after_expiry_rejected(tmp_db: sqlite3.Connection, cfg: Config) -> None:
    _insert_ticket(tmp_db, "t_lc")
    won = data.claim(tmp_db, "t_lc", T0, 900)
    assert won is not None
    run_id, token = won
    # Close at exactly claim_expires -> rejected (A1 half-open lease), nothing written.
    with pytest.raises(PlannerError) as exc:
        data.close_run(tmp_db, run_id, RunStatus.done, T0 + 900, cfg.failure_limit)
    assert exc.value.code is ErrorCode.stale_claim
    _assert_error(
        exc.value,
        ErrorCode.stale_claim,
        keys=("ticket_id", "claim_expires"),
        values={"claim_expires": T0 + 900},
    )
    run = _run_row(tmp_db, run_id)
    assert run["status"] == "running"
    assert run["ended_at"] is None
    ticket = _ticket_row(tmp_db, "t_lc")
    assert ticket["claim_lock"] == token
    assert ticket["consecutive_failures"] == 0
    assert _events(tmp_db, "t_lc", EventKind.run_closed) == []
    # Boundary: a close at T0+899 on a fresh identical setup succeeds.
    _insert_ticket(tmp_db, "t_lc2")
    won2 = data.claim(tmp_db, "t_lc2", T0, 900)
    assert won2 is not None
    run_id2, _token2 = won2
    data.close_run(tmp_db, run_id2, RunStatus.done, T0 + 899, cfg.failure_limit)
    assert _run_row(tmp_db, run_id2)["status"] == "done"
    # The still-running t_lc run is then reclaimable normally.
    assert (
        data.sweep_reclaims(
            tmp_db, T0 + 901, pid_alive=lambda p: True, failure_limit=cfg.failure_limit
        )
        == [run_id]
    )


# --- Item 16 (anchored): circuit breaker (SPEC line 293) ---


def test_a16_circuit_breaker(tmp_db: sqlite3.Connection, cfg: Config) -> None:
    assert cfg.failure_limit == 2  # SPEC §13/§7.5 default
    # Part 1: two consecutive crashed -> counter 2, sticky auto_blocked, one event,
    # ineligible.
    _insert_ticket(tmp_db, "t_cb")
    won1 = data.claim(tmp_db, "t_cb", T0, 900)
    assert won1 is not None
    run1, _t1 = won1
    data.close_run(tmp_db, run1, RunStatus.crashed, T0 + 10, cfg.failure_limit, error="boom")
    ticket = _ticket_row(tmp_db, "t_cb")
    assert ticket["consecutive_failures"] == 1
    assert ticket["auto_blocked"] == 0
    assert ticket["claim_lock"] is None
    run1row = _run_row(tmp_db, run1)
    assert run1row["status"] == "crashed"
    assert run1row["ended_at"] == T0 + 10
    assert run1row["error"] == "boom"
    assert _events(tmp_db, "t_cb", EventKind.run_closed) == [
        {"run_id": run1, "status": "crashed", "summary": None}
    ]
    assert _events(tmp_db, "t_cb", EventKind.auto_blocked) == []
    won2 = data.claim(tmp_db, "t_cb", T0 + 20, 900)
    assert won2 is not None
    run2, _t2 = won2
    data.close_run(tmp_db, run2, RunStatus.crashed, T0 + 30, cfg.failure_limit)
    ticket2 = _ticket_row(tmp_db, "t_cb")
    assert ticket2["consecutive_failures"] == 2
    assert ticket2["auto_blocked"] == 1
    assert _events(tmp_db, "t_cb", EventKind.auto_blocked) == [{"consecutive_failures": 2}]
    cand = _candidate(tmp_db, "t_cb", T0 + 40)
    assert cand.auto_blocked is True
    assert is_eligible(cand) is False
    # Part 2: the human clear-action resets flag AND counter -> eligible again.
    data.clear_auto_block(tmp_db, "t_cb", T0 + 100)
    cleared = _ticket_row(tmp_db, "t_cb")
    assert cleared["auto_blocked"] == 0
    assert cleared["consecutive_failures"] == 0
    assert _events(tmp_db, "t_cb", EventKind.auto_block_cleared) == [{}]
    assert is_eligible(_candidate(tmp_db, "t_cb", T0 + 100)) is True
    # Part 3: a done close resets the counter (separate ticket); the subsequent crash
    # yields 1, not 2, and no auto_blocked event ever fires — the reset was real.
    _insert_ticket(tmp_db, "t_rst")
    won_r1 = data.claim(tmp_db, "t_rst", T0, 900)
    assert won_r1 is not None
    run_r1, _tr1 = won_r1
    data.close_run(tmp_db, run_r1, RunStatus.crashed, T0 + 10, cfg.failure_limit)
    assert _ticket_row(tmp_db, "t_rst")["consecutive_failures"] == 1
    won_r2 = data.claim(tmp_db, "t_rst", T0 + 20, 900)
    assert won_r2 is not None
    run_r2, _tr2 = won_r2
    data.close_run(tmp_db, run_r2, RunStatus.done, T0 + 40, cfg.failure_limit, summary="ok")
    reset = _ticket_row(tmp_db, "t_rst")
    assert reset["consecutive_failures"] == 0
    assert reset["auto_blocked"] == 0
    assert _events(tmp_db, "t_rst", EventKind.run_closed) == [
        {"run_id": run_r1, "status": "crashed", "summary": None},
        {"run_id": run_r2, "status": "done", "summary": "ok"},
    ]
    won_r3 = data.claim(tmp_db, "t_rst", T0 + 50, 900)
    assert won_r3 is not None
    run_r3, _tr3 = won_r3
    data.close_run(tmp_db, run_r3, RunStatus.crashed, T0 + 60, cfg.failure_limit)
    assert _ticket_row(tmp_db, "t_rst")["consecutive_failures"] == 1
    assert _events(tmp_db, "t_rst", EventKind.auto_blocked) == []


def test_breaker_blocked_outcome_leaves_counter(
    tmp_db: sqlite3.Connection, cfg: Config
) -> None:
    _insert_ticket(tmp_db, "t_neu")
    won1 = data.claim(tmp_db, "t_neu", T0, 900)
    assert won1 is not None
    run1, _t1 = won1
    data.close_run(tmp_db, run1, RunStatus.crashed, T0 + 10, cfg.failure_limit)
    assert _ticket_row(tmp_db, "t_neu")["consecutive_failures"] == 1
    won2 = data.claim(tmp_db, "t_neu", T0 + 20, 900)
    assert won2 is not None
    run2, _t2 = won2
    data.close_run(tmp_db, run2, RunStatus.blocked, T0 + 30, cfg.failure_limit)
    ticket = _ticket_row(tmp_db, "t_neu")
    assert ticket["consecutive_failures"] == 1
    assert ticket["auto_blocked"] == 0
    assert _events(tmp_db, "t_neu", EventKind.auto_blocked) == []


def test_breaker_pure_matrix() -> None:
    assert next_breaker_state(0, RunStatus.crashed, 2) == (1, False)
    assert next_breaker_state(1, RunStatus.crashed, 2) == (2, True)
    assert next_breaker_state(1, RunStatus.timed_out, 2) == (2, True)
    assert next_breaker_state(1, RunStatus.spawn_failed, 2) == (2, True)
    assert next_breaker_state(1, RunStatus.done, 2) == (0, False)
    assert next_breaker_state(1, RunStatus.blocked, 2) == (1, False)
    assert next_breaker_state(1, RunStatus.reclaimed, 2) == (1, False)
    assert next_breaker_state(0, RunStatus.crashed, 1) == (1, True)
