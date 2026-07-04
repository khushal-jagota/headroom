"""The dispatcher tick (§7.1). One synchronous tick: fail-safe dispatch_enabled
re-read, machine-wide advisory flock, reclaim sweep, run-timeout enforcement,
eligibility recompute, claim-then-spawn up to the concurrency cap. In test mode no
code path ever signals a real pid (fake pids may collide with live processes). The
lock is acquired once and held by the process; ``release_dispatcher_lock`` is the
explicit release for loops.stop() and tests."""

from __future__ import annotations

import fcntl
import os
import signal
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Final

from planner.core.adapters.base import SpawnRequest, SpawnResult
from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock
from planner.core.config import HOST, Config, read_dispatch_enabled
from planner.core.contracts import JsonDict
from planner.core.errors import PlannerError
from planner.dispatch.contracts import RunStatus
from planner.dispatch.data import claim, close_run, load_candidates, sweep_reclaims
from planner.dispatch.logic import is_eligible, ordering_key

ConnFactory = Callable[[], sqlite3.Connection]

# One cached OS fd per lock path. The first successful acquisition caches the fd; the
# process holds the flock from then on (released only on process exit or explicit
# release).
_LOCK_FDS: Final[dict[str, int]] = {}


def _ensure_dispatcher_lock(lock_path: str) -> bool:
    """Acquire (once) the machine-wide advisory lock; True when this process holds it."""
    if lock_path in _LOCK_FDS:
        return True
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return False
    _LOCK_FDS[lock_path] = fd
    return True


def release_dispatcher_lock(lock_path: str) -> None:
    """Explicit release used by loops.stop() and tests. Safe no-op when not held."""
    fd = _LOCK_FDS.pop(lock_path, None)
    if fd is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _pid_alive(pid: int) -> bool:
    """Real-mode liveness probe via signal 0."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_alive_always(_pid: int) -> bool:
    """Test-mode liveness probe: never signals, always reports alive."""
    return True


def _sigterm(pid: int) -> None:
    """Real-mode kill: SIGTERM, guarded against an already-gone pid."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _kill_noop(_pid: int) -> None:
    """Test-mode kill: never signals."""
    return None


def _enforce_run_timeouts(
    conn: sqlite3.Connection,
    now: int,
    run_max_seconds: int,
    failure_limit: int,
    kill: Callable[[int], None],
) -> list[str]:
    """Close every running run that has exceeded ``run_max_seconds`` (fires at exactly
    the boundary). A5: close FIRST (guarded by a PlannerError skip); only the run whose
    close won the status CAS is then signalled and reported."""
    rows = conn.execute(
        "SELECT id, pid FROM runs WHERE status='running' AND started_at <= ?",
        (now - run_max_seconds,),
    ).fetchall()
    closed: list[str] = []
    for row in rows:
        run_id = str(row["id"])
        pid = int(row["pid"]) if row["pid"] is not None else None
        try:
            close_run(
                conn,
                run_id,
                RunStatus.timed_out,
                now,
                failure_limit,
                error=f"exceeded run_max_seconds ({run_max_seconds}s)",
            )
        except PlannerError:
            continue
        if pid is not None:
            kill(pid)
        closed.append(run_id)
    return closed


def run_tick(
    conn_factory: ConnFactory, config: Config, clock: Clock, adapters: Adapters
) -> JsonDict:
    """One §7.1-ordered dispatcher tick. Returns the pinned five-key report."""
    report: JsonDict = {
        "skipped": None,
        "reclaimed": [],
        "timed_out": [],
        "spawned": [],
        "spawn_failed": [],
    }

    # (0) fail-safe dispatch flag — before the lock and before any connection.
    if not read_dispatch_enabled():
        report["skipped"] = "dispatch_disabled"
        return report

    # (0b) machine-wide advisory lock — a skipped tick opens no connection.
    if not _ensure_dispatcher_lock(config.dispatcher_lock_path):
        report["skipped"] = "lock_held"
        return report

    conn = conn_factory()
    try:
        now = clock.now_unix()

        # (1) reclaim expired claims / dead pids.
        pid_alive = _pid_alive_always if config.test_mode else _pid_alive
        report["reclaimed"] = sweep_reclaims(conn, now, pid_alive, config.failure_limit)

        # (2) enforce per-run max runtime.
        kill = _kill_noop if config.test_mode else _sigterm
        report["timed_out"] = _enforce_run_timeouts(
            conn, now, config.run_max_seconds, config.failure_limit, kill
        )

        # (3) recompute eligibility, ordered.
        candidates = load_candidates(conn, now)
        eligible = sorted((c for c in candidates if is_eligible(c)), key=ordering_key)

        # (4) claim-then-spawn up to the concurrency cap.
        active = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM runs WHERE status='running'"
            ).fetchone()["n"]
        )
        budget = max(config.max_runs - active, 0)
        Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
        for cand in eligible:
            if budget == 0:
                break
            budget -= 1  # every claim attempt consumes one unit, win or lose (RD-2).
            claimed = claim(conn, cand.ticket_id, now, config.claim_ttl_seconds)
            if claimed is None:
                continue
            run_id, token = claimed
            request = SpawnRequest(
                ticket_id=cand.ticket_id,
                run_id=run_id,
                claim=token,
                server_url=f"http://{HOST}:{config.port}",
                log_path=str(Path(config.logs_dir) / f"{run_id}.log"),
                hermes_bin=config.hermes_bin,
                profile=config.hermes_profile,
                skill=config.worker_skill,
            )
            try:
                result = adapters.spawn.spawn(request)
            except Exception as exc:  # adapter raise = spawn failure (RD-9).
                result = SpawnResult(ok=False, error=str(exc))
            if result.ok:
                conn.execute(
                    "UPDATE runs SET pid=? WHERE id=?", (result.pid, run_id)
                )
                report["spawned"].append(
                    {"ticket_id": cand.ticket_id, "run_id": run_id, "pid": result.pid}
                )
            else:
                close_run(
                    conn,
                    run_id,
                    RunStatus.spawn_failed,
                    now,
                    config.failure_limit,
                    error=result.error or "spawn failed",
                )
                report["spawn_failed"].append(
                    {
                        "ticket_id": cand.ticket_id,
                        "run_id": run_id,
                        "error": result.error or "spawn failed",
                    }
                )
    finally:
        conn.close()
    return report
