# T11 plan review — codex output + orchestrator dispositions

Reviewer: `codex exec` (non-interactive, repo root), pointed at plan.md, ticket.md, SPEC §6.1–6.3/§7.1–7.6/§13/§16/§18.2–18.3, and the frozen contracts (dispatch data/logic, days boundary/data/effects/tree, adapters, config, clock).

## Codex findings (verbatim)

**1. SPEC/Ticket Semantic Violations**

1. `plan.md:257-260` computes `piso`, then `src/planner/days/boundary.py:42-43` recomputes the planning date inside `run_boundary`. If the clock crosses 05:00 between those reads, the report can describe one date while the job runs another. Violates SPEC 6.1 (SPEC.md:117), 6.2 (SPEC.md:121), and the T11 report seam (ticket.md:22).

**2. Owned Set / Pinned T09 Seam**

1. `plan.md:159-162` and `plan.md:668-670` make scheduler.py depend on module-private helpers from days/boundary.py (`_boundary_ran`, `_error_text`, `_read_*`). Those are not public seam contracts. This exceeds the owned-file boundary (ticket.md:9-14) and is brittle against T03 internals. Pinned T09 entry-point signatures themselves are not broken.

**3. Race / Lock Discipline**

1. Same-process duplicate dispatcher loops are not prevented. `_ensure_dispatcher_lock` returns True whenever the path is already in `_LOCK_FDS` (plan.md:50-57), and `start_background_loops` has no singleton guard (plan.md:393-401). Calling `start_background_loops` twice in one process creates two dispatch loops that both pass the lock. Violates SPEC 7.1 (SPEC.md:145) and T11 lock semantics (ticket.md:11).

2. Boundary idempotency is non-atomic under overlapping ticks. The plan relies on `boundary_runs` and says no flock (plan.md:270-272), but `run_boundary` does a read guard (boundary.py:44) and only inserts at boundary.py:99-103. Two calls can both pass the guard before the primary-key insert, causing duplicate deterministic events and/or an IntegrityError. Violates SPEC 6.2 "never twice" (SPEC.md:121).

3. The replan queue is not actually single-consumer safe. `process_pending_replan` snapshots pending under a lock, then executes outside it (plan.md:197-203), with no consumer/in-flight mutex in `_ReplanQueue`. Two concurrent consumers can call the adapter for the same generation; one result is discarded, but serialization was already violated. Violates SPEC 6.3/R5 and T11 "single worker" (ticket.md:12).

4. `BackgroundLoops.stop()` claims `gather()` waits out an in-flight `to_thread` tick (plan.md:389-391), but cancelling an asyncio task awaiting `to_thread` does not wait for the worker thread to finish. The plan can release the dispatcher lock while `run_tick` is still running. Violates the async `.stop()` seam expectation (ticket.md:22) and the dispatcher lock guarantee (SPEC.md:145).

**4. Safety**

1. Timeout enforcement sends SIGTERM before proving the claim is still active. `_enforce_run_timeouts` kills first, then calls `close_run`, and swallows PlannerError (plan.md:71-78). But `close_run` rejects expired/cleared claims. A race with reclaim/agent close can therefore signal a PID for a run that is no longer valid to close. Violates SPEC 7.3 claim/TTL semantics (SPEC.md:153-155).

No test-mode fake-PID signal path found if the plan's test_mode substitutions are implemented.

**5. Test-Design Flaws**

1. Real spawn smoke does not assert the exact command or all four required env vars (only PLAN_TICKET_ID and pgid). Violates SPEC 7.4 and T11 acceptance (ticket.md:18).

2. No runtime test covers spawn failure (`SpawnResult(ok=False)` or adapter raise). A broken implementation could leave the claim running and still pass. Violates T11 ticket.md:11 and SPEC breaker semantics.

3. No test covers a run that is both claim-expired and past run_max_seconds. A wrong timeout-before-reclaim implementation could pass. Violates T11 tick order and SPEC 7.3.

4. The second-tick tests can pass if the tick is incorrectly skipped — they never assert `report["skipped"] is None`.

5. The background loop test does not exercise stop during an in-flight tick, so it will not catch the to_thread shutdown race above.

## Orchestrator dispositions

- **1.1 — ACCEPTED (bounded).** Real but vanishingly narrow (a RealClock crossing 05:00 between two adjacent reads; impossible under TestClock). We cannot make run_boundary return its date (T03 file, frozen). Amendment A6: snapshot `piso` once before the call; `ran` is defined by before/after existence of the `boundary_runs` row **for that snapshot date**; a mid-call date flip yields `ran=False` for the snapshot date and the next tick reports the new date — self-healing, and the event log stays the source of truth. Noted in the plan as accepted residual imprecision.
- **2.1 — REFUTED as a boundary violation, accepted as a note.** The owned-file boundary restricts what we *write*, not what we import; the ticket dispatch explicitly sanctioned reusing boundary.py's readers to keep one copy of the SQL (RD-5). T03 is landed and frozen — brittleness against "future T03 internals" is hypothetical, and duplicating four SQL readers is the larger drift risk. No change.
- **3.1 — ACCEPTED.** Amendment A2: loops.py gains a module-level active-instance guard — `start_background_loops` raises `RuntimeError("background loops already running")` if a prior instance from this process has not been stopped; `stop()` clears the guard. Test 15 extended to assert the second start raises while running and succeeds after stop.
- **3.2 — ACCEPTED (in-process scope).** run_boundary is frozen, so atomicity is added at the only call sites: Amendment A3 serializes every scheduler entry point behind a module-level `threading.RLock`. In-process overlap (prod loop + test endpoint threadpool) is thereby impossible. Cross-process overlap is not a deployment topology of this system (one server process owns the DB; SPEC's machine-wide flock covers the dispatcher specifically). Residual risk noted, not engineered around.
- **3.3 — ACCEPTED.** Same RLock (A3) makes the replan consumer single-flight in-process; `process_pending_replan` acquires it too (RLock so run_boundary_tick → process_pending_replan re-entry is fine). The generation check remains the cross-window correctness backstop.
- **3.4 — ACCEPTED** (independently found during the orchestrator sense-check before codex returned). Amendment A4: `_loop_forever` shields the `to_thread` future and, on cancellation, awaits the in-flight thread to completion (suppressing its outcome) before re-raising — so `stop()` returns only after in-flight ticks finish, and only then releases the dispatcher lock.
- **4.1 — ACCEPTED.** Amendment A5: close first, kill second. `close_run(timed_out)`'s status-CAS is the single arbiter; only a successful close (we are the finalizer) earns the SIGTERM. A lost CAS/stale claim → no signal, no report entry. Strictly safer and §7.3-faithful (no ordering is specified there).
- **5.1 — ACCEPTED.** Amendment A7: the echo script prints `argv=$@`, all four PLAN_* values, and the pgid; the test asserts the exact §7.4 argv tail (`-p default --skills planning-worker chat -q work planning ticket t_smoke`), all four env values, and pgid == pid.
- **5.2 — ACCEPTED.** Amendment A8: new test — two ticks with scripted `SpawnResult(ok=False, error="boom")`: run closed `spawn_failed`, claim cleared, `consecutive_failures` 1 then 2, `auto_blocked` after the second (breaker), third tick spawns nothing (ineligible).
- **5.3 — ACCEPTED.** Amendment A8: new test — run both claim-expired and past run_max at tick time → status `reclaimed` (step 1 wins), `report["timed_out"] == []`.
- **5.4 — ACCEPTED.** Amendment A8: tests 2 and 3 additionally assert `report["skipped"] is None`.
- **5.5 — ACCEPTED.** Amendment A9: new test monkeypatches `planner.core.loops.run_tick` with a slow recorder (event + sleep + completion marker); `stop()` issued mid-tick must return only after the marker is set — proving A4.

All amendments are appended to plan.md as section 8 (binding).
