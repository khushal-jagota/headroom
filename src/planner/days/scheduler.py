"""The boundary scheduler tick (§6.2) and the serialized latest-wins replan queue
(§6.3, R5). One tick = run the idempotent boundary job for the current planning
date, then drain the pending replan slot. The queue holds at most one pending
request; a newer submission overwrites the older and invalidates any in-flight
execution (generation check). Deterministic replan inputs are recomputed from the
DB via boundary.py's reader helpers."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import partial

from planner.core.adapters.base import BoundaryInputs
from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import EventKind, JsonDict
from planner.core.events import append_event
from planner.core.ids import day_id
from planner.days.boundary import (
    _boundary_ran,
    _error_text,
    _read_approval_candidates,
    _read_overdue_candidates,
    _read_yesterday_tickets,
    run_boundary,
)
from planner.days.contracts import NodeStatus, PlanNode, PlanTree
from planner.days.data import load_plan, store_plan
from planner.days.logic.carryover import (
    approvals_digest,
    carryover_candidates,
    overdue_list,
)
from planner.days.logic.dates import planning_date
from planner.days.logic.effects import ReplanChild, ReplanRequest, ReplanRoot
from planner.days.logic.tree import as_proposed, tree_to_dict


@dataclass(frozen=True)
class _PendingReplan:
    day_id: str
    request: ReplanRequest
    generation: int


class _ReplanQueue:            # plain class, not a dataclass (holds a Lock)
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: _PendingReplan | None = None
        self.generation = 0


_QUEUE = _ReplanQueue()        # module singleton

# A3: serializes the boundary read-guard-then-insert and single-flights the replan
# consumer in-process. RLock so run_boundary_tick's nested process_pending_replan
# re-enters. The generation check remains the backstop for supersession mid-flight.
_TICK_MUTEX = threading.RLock()


def submit_replan(day_id: str, request: ReplanRequest) -> None:
    """Latest-wins enqueue: overwrite whatever was pending, bump the generation.
    Never executes anything — T10's invalidate endpoints call this from the request
    path; execution happens only in the consumer."""
    with _QUEUE.lock:
        _QUEUE.generation += 1
        _QUEUE.pending = _PendingReplan(day_id, request, _QUEUE.generation)


def reset_replan_queue() -> None:
    """Test helper: clear the slot and reset the generation."""
    with _TICK_MUTEX:
        with _QUEUE.lock:
            _QUEUE.pending = None
            _QUEUE.generation = 0


def _consume_if_current(generation: int) -> bool:
    """Clear the slot iff it still holds the request with ``generation``."""
    with _QUEUE.lock:
        if _QUEUE.pending is not None and _QUEUE.pending.generation == generation:
            _QUEUE.pending = None
            return True
        return False


def _replan_inputs(conn: sqlite3.Connection, piso: str) -> BoundaryInputs:
    """Recompute the deterministic §6.2 inputs for the day's planning date, reusing
    boundary.py's private readers (RD-5)."""
    yid = day_id(date.fromisoformat(piso) - timedelta(days=1))
    carry = carryover_candidates(_read_yesterday_tickets(conn, yid))
    overdue = overdue_list(*_read_overdue_candidates(conn), piso)
    approvals = approvals_digest(*_read_approval_candidates(conn))
    return BoundaryInputs(
        planning_date=piso, carryover=carry, overdue=overdue, approvals_digest=approvals
    )


def _call_with_timeout[T](fn: Callable[[], T], timeout_s: int) -> T:
    """Run ``fn`` in a worker thread; the CALLER owns the timeout. FuturesTimeout and
    adapter raises both surface to the caller as exceptions."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(fn).result(timeout=timeout_s)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _execute_pending(
    conn: sqlite3.Connection,
    config: Config,
    clock: Clock,
    adapters: Adapters,
    snapshot: _PendingReplan,
) -> JsonDict:
    """Execute one snapshot outside the queue lock, producing one attempt entry."""
    now = clock.now_unix()
    day = snapshot.day_id
    piso = day.removeprefix("day_")
    request = snapshot.request
    scope: str
    node: str | int
    if isinstance(request, ReplanRoot):
        scope, node = "root", "root"
    else:
        scope, node = "child", request.position

    old_tree = load_plan(conn, day)

    old_child: PlanNode | None = None
    if isinstance(request, ReplanChild):
        # Stale target (RD-7): no stored plan, or no child at the position by
        # execution time. No adapter call, no event.
        if old_tree is None:
            _consume_if_current(snapshot.generation)
            return {"day_id": day, "scope": scope, "node": node, "outcome": "stale_target"}
        for child in old_tree.children:
            if child.position == request.position:
                old_child = child
                break
        if old_child is None:
            _consume_if_current(snapshot.generation)
            return {"day_id": day, "scope": scope, "node": node, "outcome": "stale_target"}

    inputs = _replan_inputs(conn, piso)

    # partial (not a lambda — ruff B023/E731); called per-branch so the generic
    # _call_with_timeout binds a concrete return type, never a widened union.
    adapter_result: PlanTree | PlanNode
    try:
        if isinstance(request, ReplanRoot):
            adapter_result = _call_with_timeout(
                partial(adapters.boundary.replan_root, day, inputs),
                config.boundary_timeout_seconds,
            )
        else:
            assert old_child is not None
            adapter_result = _call_with_timeout(
                partial(adapters.boundary.replan_child, day, old_child, inputs),
                config.boundary_timeout_seconds,
            )
    except Exception as exc:
        current = _consume_if_current(snapshot.generation)
        if current:
            append_event(
                conn,
                day,
                EventKind.boundary_failed,
                {"error": _error_text(exc, config.boundary_timeout_seconds)},
                now,
            )
            outcome = "failed"
        else:
            # Superseded mid-flight: the generation check governs ALL writes (RD-6).
            outcome = "discarded"
        return {"day_id": day, "scope": scope, "node": node, "outcome": outcome}

    current = _consume_if_current(snapshot.generation)
    if not current:
        return {"day_id": day, "scope": scope, "node": node, "outcome": "discarded"}

    if isinstance(adapter_result, PlanTree):
        new_tree = as_proposed(adapter_result)
        store_plan(conn, day, new_tree, now)
        append_event(
            conn,
            day,
            EventKind.plan_replanned,
            {
                "old_tree": tree_to_dict(old_tree) if old_tree is not None else None,
                "scope": "root",
                "node": "root",
            },
            now,
        )
    else:
        assert old_tree is not None
        assert old_child is not None
        spliced = [replace(child) for child in old_tree.children]
        for index, child in enumerate(spliced):
            if child.position == old_child.position:
                spliced[index] = replace(
                    adapter_result,
                    status=NodeStatus.proposed,
                    position=old_child.position,
                )
                break
        store_plan(
            conn, day, PlanTree(root=replace(old_tree.root), children=spliced), now
        )
        append_event(
            conn,
            day,
            EventKind.plan_replanned,
            {"old_tree": tree_to_dict(old_tree), "scope": "child", "node": old_child.position},
            now,
        )
    return {"day_id": day, "scope": scope, "node": node, "outcome": "stored"}


def process_pending_replan(
    conn: sqlite3.Connection, config: Config, clock: Clock, adapters: Adapters
) -> JsonDict | None:
    """Single consumer. Drain the slot until empty (RD-20); a submission that arrives
    during execution is consumed by the next iteration. Returns None when nothing was
    pending."""
    with _TICK_MUTEX:
        attempts: list[JsonDict] = []
        while True:
            with _QUEUE.lock:
                snapshot = _QUEUE.pending
            if snapshot is None:
                break
            attempts.append(_execute_pending(conn, config, clock, adapters, snapshot))
        if not attempts:
            return None
        return {"attempts": attempts}


def run_boundary_tick(
    conn_factory: Callable[[], sqlite3.Connection],
    config: Config,
    clock: Clock,
    adapters: Adapters,
) -> JsonDict:
    """One boundary tick. Runs the idempotent boundary job for the snapshot planning
    date (A6), then drains the replan queue. Returns the pinned four-key report."""
    with _TICK_MUTEX:
        piso = planning_date(clock.now(), config.boundary_hour).isoformat()
        conn = conn_factory()
        try:
            existed = _boundary_ran(conn, piso)
            run_boundary(conn, clock, config, adapters.boundary)
            row = conn.execute(
                "SELECT judgment FROM boundary_runs WHERE planning_date=?", (piso,)
            ).fetchone()
            judgment = str(row["judgment"]) if row is not None else None
            ran = (not existed) and row is not None
            replan = process_pending_replan(conn, config, clock, adapters)
        finally:
            conn.close()
        return {
            "planning_date": piso,
            "ran": ran,
            "judgment": judgment,
            "replan": replan,
        }
