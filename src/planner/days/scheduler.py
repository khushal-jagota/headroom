"""The boundary scheduler tick (§6.2). One tick runs the idempotent boundary job
for the current planning date; a simple module lock serializes ticks so the
materialize-guard read-then-work stays TOCTOU-safe (the prod boundary loop is its
one live caller)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable

from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.days.boundary import run_boundary
from planner.days.logic.dates import planning_date

# Serializes run_boundary_tick so the materialize-guard read-then-work stays
# TOCTOU-safe. The prod boundary loop is the one live caller.
_TICK_MUTEX = threading.Lock()


def run_boundary_tick(
    conn_factory: Callable[[], sqlite3.Connection],
    config: Config,
    clock: Clock,
    adapters: Adapters,
) -> JsonDict:
    """One boundary tick. Runs the idempotent boundary job for the snapshot planning
    date (A6). Returns {planning_date, ran, judgment}: `ran` is True iff the job did
    work (the next day was not already materialized), and `judgment` is the boundary
    outcome ("ok" / "skipped" / "failed") or None on a guarded skip."""
    with _TICK_MUTEX:
        piso = planning_date(clock.now(), config.boundary_hour).isoformat()
        conn = conn_factory()
        try:
            outcome = run_boundary(conn, clock, config, adapters.boundary)
        finally:
            conn.close()
        return {"planning_date": piso, "ran": outcome is not None, "judgment": outcome}
