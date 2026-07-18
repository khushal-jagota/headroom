No—the plan is not yet safe to implement. A, D, E, F, and H are resolved. B, C, and G retain genuine correctness defects introduced or exposed by the latest edits.

## A–H confirmation

- A — RESOLVED. [Plan:35](</Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:35>), [plan:425](</Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:425>), and [plan:452](</Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:452>) import `functools` and pass `functools.partial(pool.shutdown, deadline=deadline)` as the executor callable. `deadline` is genuinely bound by keyword. The lifespan test at line 557 exercises a keyword-only fake.

- B — NOT RESOLVED. The intended fields, immediate partial publication, shutdown teardown, and final `_closing` check exist at [plan:103](</Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:103>)–139. A finished record cannot be inserted into `_records` after closing. However, shutdown can snapshot an existing slot while `slot.transport is None`; the owner can then publish the transport at line 129 without checking `_closing`, register/start it, and enter readiness/RPC after shutdown’s one-time transport check. Shutdown can consequently reach its deadline with that child and binding still alive, even though the record is eventually rejected.

  There is a second pre-start race: shutdown can receive the partial transport between lines 129 and 131, before `start_reading()`. Transport shutdown at [line 74](</Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:74>) unconditionally joins the stdout thread; Python raises `RuntimeError` when joining a thread that has never started. `start_reading()` and shutdown also lack a shared lifecycle guard.

- C — NOT RESOLVED. The exactly-once ownership argument itself is sound at [plan:180](</Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:180>) and lines 185–187/350–354: either no pending is registered and `_forward` completes directly, or death completes the registered pending. But the written implementation self-deadlocks: line 175 specifies a non-reentrant `threading.Lock`; line 177 says `binding_alive()` acquires it; line 180 calls that helper while `_forward` is already under the same lock.

- D — RESOLVED. Lines 181–183 and 370–376 fan out and tee every uncorrelated frame. The responder observes without consuming. The full-pool test at [line 524](</Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:524>) asserts the complete ordered sequence `[gateway.ready, session.create response, event1, event2]`. The pool response has no downstream `PendingForward`, so it is delivered once through fan-out and does not also complete a downstream pending.

- E — RESOLVED. Lines 322–346 and [line 552](</Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:552>) statically scan the actual `@method("...")` decorators and compare the derived `session.*` set against the snapshot. A 21st decorator in that checkout fails equality. The current source contains exactly 20 `session.*` methods and all three generic escape hatches. No live process is involved.

- F — RESOLVED. Lines 41–50, 479, 577–591, and 625 consistently state six implementation files, seven test files, and one package marker: 14 new files, 13 substantive.

- G — NOT RESOLVED. The starvation fix is genuine: lines 85–90 define separate bounded init and reserved shutdown executors; lines 452–460 schedule shutdown on the reserved executor; line 509 tests saturation. There is no init-versus-shutdown queue starvation or specified executor-lock deadlock.

  But the reserved executor is never shut down. [Line 90](</Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:90>) merely calls it “daemon-equivalent” and leaves it to process exit. A `ThreadPoolExecutor` worker does not self-terminate after completing one task; it waits for more work until the executor is shut down. This leaks one worker per composed pool. B can additionally leave init work alive past the deadline.

- H — RESOLVED. [Lines 627–634](</Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:627>) explicitly document final-frame-versus-write-death ordering and unbounded queues/overload, including rationale and deferred mitigation. They match [D-relay-s1-plan-scope](</Users/khushaljagota/.hermes/planning-v2/decisions.md:1746>) and are not blockers.

## Regression confirmation

- Pool lock discipline — RESOLVED as an ordinary locking rule: blocking spawn/readiness/RPC/teardown remains outside the pool lock. B is a lifecycle coordination defect, not a return to holding blocking work under the lock.
- Atomic `_mark_dead` — RESOLVED at lines 71–72 and tested at line 538.
- JSON-RPC rejection shape — RESOLVED at lines 287–318 and tested at lines 548–550.
- Single-owner failure completion — RESOLVED conceptually at lines 185–187 and 616, but C currently prevents that path from executing.
- Single-writer FIFO — RESOLVED at lines 15, 67, and 350–356, with actual wire-order coverage at line 513.
- Structural-verbatim plus `Queue[str]` — RESOLVED at lines 149 and 390–392, with deep-equality coverage.
- Nine-method denylist plus 20-method registry — RESOLVED at lines 287–346.

## Required corrections

1. High — B, partial-init shutdown race. Re-check `_closing` in the same locked section that publishes `slot.transport/generation`; if closing, never register/start/RPC and tear down off-lock. Shutdown must also observe transports published after its initial snapshot, using coordinated slot state or a publication event.

2. High — B, pre-start transport shutdown. Coordinate `start_reading()` and `shutdown()` with one transport lifecycle lock/state, prevent starting after shutdown begins, and join stdout only if it was started. Add a barrier test between partial publication and `start_reading()`.

3. High — C, recursive relay-lock deadlock. Inside `_forward`’s existing critical section, directly fetch `_children[generation]` and check `transport.alive`, or use a non-locking `_binding_alive_locked()` helper.

4. Medium — G, shutdown-executor leak. As the last action inside `pool.shutdown`, call `_shutdown_executor.shutdown(wait=False, cancel_futures=True)`. `wait=False` is safe from its own worker: it marks the executor shut down and wakes the worker, which exits after the current task returns.

Two isolated confirmation passes independently converged on B, C, and G. Once these mechanical corrections and their focused tests are added, the settled architecture and approved S1 limitations do not present another blocker.

---

## Orchestrator resolution of the round-3 confirming review

Codex confirmed A, D, E, F, H RESOLVED and no regressions; it flagged B, C, G with four precise
mechanical corrections. All four verified real and applied directly to implementation-plan.md
(tagged R3-round3-1..4):

1. **C — recursive relay-lock deadlock (High).** `_forward` ran under the non-reentrant `_lock`
   yet called the lock-acquiring `binding_alive`. Fix: added a NON-locking `_binding_alive_locked`
   helper used inside `_forward`'s critical section; `binding_alive` kept as the lock-acquiring
   wrapper for callers that don't hold the lock. (§1 helper, §4 step 0, `_forward`, §9.)
2. **B — publish-time _closing race (High).** `_spawn_and_bind` now re-checks `_closing` in the
   SAME locked section that publishes `slot.transport`/`generation`; if closing, it aborts before
   start_reading/RPC and tears the transport down. Closes the window where shutdown snapshots a
   slot while `slot.transport is None`. Pool `shutdown` now also handles a `slot.transport is None`
   snapshot by waiting bounded on `slot.done` (owner will abort at the re-check). (§1 `_spawn_and_bind`
   step 1, pool `shutdown`.)
3. **B — pre-start transport shutdown RuntimeError (High).** Added a transport lifecycle guard
   (`_lifecycle_lock` + `_stdout_started` + `_shutdown_begun`): `start_reading` refuses to start if
   shutdown began; `shutdown` joins the stdout thread ONLY if it started (joining a never-started
   thread raises RuntimeError). Added `test_shutdown_before_start_reading_does_not_join_unstarted_thread`.
   (§1 transport __init__/start_reading/shutdown, §8.)
4. **G — reserved shutdown-executor leak (Medium).** `pool.shutdown`'s LAST action is now
   `self._shutdown_executor.shutdown(wait=False, cancel_futures=True)` — safe from its own worker
   (`wait=False` marks it shut, the single worker exits after this task returns), so the reserved
   worker is reclaimed, not leaked. (§1 pool executors + `shutdown`, §6.)

Also declared the previously-referenced `RELAY_CHILD_RESET_CODE` (and the lifecycle-rejection code)
as named relay module constants for consistency.

These are mechanical corrections to a settled architecture (Codex: "once these mechanical
corrections and their focused tests are added, the settled architecture and approved S1 limitations
do not present another blocker"). Per D-relay-s1-plan-scope and the lead's approval, the plan
advances to implementation; the implementation-diff Codex review (pipeline STEP 4) is the gate that
confirms the fixes landed correctly in code. A fourth full plan-review round for four verified
one-line fixes would be looping, not diligence.
