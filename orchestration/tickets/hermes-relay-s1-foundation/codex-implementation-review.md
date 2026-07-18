## Findings

1. **High — shutdown can overtake startup before the stdout reader starts, leaving an init worker alive beyond the shared deadline.**  
   [employee_child_pool.py:228](/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/employee_child_pool.py:228) publishes the partial transport, then releases `_lock` before `register_child` and `start_reading` at lines 243–246. `pool.shutdown` can snapshot and shut down that transport during this gap ([employee_child_pool.py:364](/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/employee_child_pool.py:364)). `start_reading` then silently returns because shutdown began ([raw_frame_transport.py:105](/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/raw_frame_transport.py:105)), but the initializer still calls `wait_ready`. Because shutdown never opens `_ready_gate` or `dead_event` when stdout was never started ([raw_frame_transport.py:201](/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/raw_frame_transport.py:201)), that worker can remain blocked for `READY_TIMEOUT_DEFAULT`—10 seconds—after the application’s shorter shared shutdown deadline. A late `register_child` can also temporarily recreate a binding after shutdown’s unregister.

   This violates plan §1 startup/shutdown guarantees at lines 129–139, §6 line 417, and contract line 60. It is not one of the deferred S1 limitations.

   **Fix:** coordinate startup admission and shutdown atomically. At minimum, make `start_reading` report/raise when shutdown has begun and have shutdown wake readiness/death waiters even when stdout never started. Also serialize teardown with a shutdown-leader/completion event, since the reserved shutdown path and initializer exception path can currently call `transport.shutdown` concurrently.

2. **Medium — a failed pool session-RPC response can be dropped before relay fan-out and tee observation.**  
   The permanent callback wakes the pool responder before scheduling relay delivery ([employee_child_pool.py:210](/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/employee_child_pool.py:210)). On `session.create`/`resume` error, the init thread can finish cleanup and directly unregister the binding at lines 250–253 before the queued `deliver_child_frame` runs. The relay then drops the already-emitted response because the binding is absent ([employee_child_relay.py:367](/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/employee_child_relay.py:367)).

   This violates plan §5 lines 375–381 and contract lines 71–85: every uncorrelated frame, including pool session responses, must fan out and be tee’d.

   **Fix:** retire the binding only after previously queued frame deliveries—for example, queue unregister on the loop after the reader callback has completed, or use an off-loop delivery acknowledgement. Add a failing session-RPC visibility race test.

3. **Medium — synthesized child-reset frames bypass the tee.**  
   [employee_child_relay.py:413](/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/employee_child_relay.py:413) sends the reset frame to subscribers but never invokes `_notify_tee`. Plan §1’s `deliver_child_death` clause explicitly says “Notify tee,” and contract line 81 requires registered observers to see every frame in both directions.

   **Fix:** tee the reset frame with the employee label and downstream direction; add a death-path tee assertion.

4. **Medium — the saturated-init-executor test never saturates initialization.**  
   [test_hermes_backend_pool.py:326](/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_pool.py:326) uses `_HungReadyChild`, which emits ready and completes `session.create` immediately; only process `wait()` hangs during shutdown. The init jobs can therefore finish before shutdown, so the test would pass even if shutdown shared the init executor. This does not implement plan §8 line 514.

   **Fix:** use pre-ready gated children, wait on barriers proving all eight init workers are blocked with partial transports published, then invoke shutdown on the reserved executor.

5. **Medium — the lifespan shutdown test copy-pastes the intended call instead of exercising production wiring.**  
   [test_hermes_backend_config_gate.py:138](/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_config_gate.py:138) directly runs its own `run_in_executor(...functools.partial...)`. It would still pass if [server.py:262](/Users/khushaljagota/.hermes/planning-v2/src/planner/core/server.py:262) were removed, used the wrong executor, or passed the keyword-only deadline incorrectly. That falls short of plan §8 line 563.

   **Fix:** drive the real application lifespan with composition monkeypatched to a recording pool, or extract a production teardown helper used by both lifespan and test.

6. **Medium — receive-side teardown test has no in-flight forward to cancel or drain.**  
   [test_hermes_backend_relay_routing.py:291](/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_relay_routing.py:291) disconnects immediately. It verifies unregistering and stopping the idle writer, but not plan §8 line 537’s required cancellation/draining of an in-progress `_forward`.

   **Fix:** block `child_for_employee` behind a barrier, deliver a request followed by disconnect, then prove the tracked forward settles and the downstream is unregistered.

7. **Low — the stored-session map is accessed outside its declared lock.**  
   [employee_child_pool.py:255](/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/employee_child_pool.py:255) writes and line 279 reads `_stored_session_id_by_employee` without `_lock`, contrary to plan §1 line 116.

   **Fix:** snapshot and update the map in short `_lock` sections; keep RPC and teardown outside it.

8. **Low — session creation accepts an uncontracted identity fallback.**  
   [employee_child_pool.py:296](/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/employee_child_pool.py:296) falls back from missing `stored_session_id` to live `session_id`. The plan requires recording the returned durable `stored_session_id`; silently accepting a different identity weakens the own-session proof.

   **Fix:** require a non-empty string `stored_session_id` and fail/clean up otherwise.

9. **Low — two fidelity details silently diverge.**

   - [employee_child_relay.py:38](/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/employee_child_relay.py:38) names the constant `RELAY_SESSION_LIFECYCLE_REJECTED_CODE`; the binding name is `RELAY_LIFECYCLE_DENIED_CODE` at plan lines 317–323.
   - [test_hermes_backend_session_lifecycle.py:169](/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_session_lifecycle.py:169) scans only double-quoted decorators. A future single-quoted `@method('session.foo')` could evade the promised drift trip-wire. AST parsing would be robust.

## Confirmed correct

- No changes under `src/planner/minds/`, `src/planner/chat/`, or `src/planner/runtime/`; no forbidden typed gateway machinery is imported.
- Existing product edits match §2/§7 and are minimal. [config.yaml:10](/Users/khushaljagota/.hermes/planning-v2/config.yaml:10) keeps the backend off by default.
- Composition is inside `not config.test_mode`; `/api/relay` matches `/api/events` middleware-only auth and accepts-then-closes 1013 when unavailable.
- No product tee observer is registered.
- Two-phase transport startup, permanent `gateway.ready` forwarding, FIFO single-writer egress, stderr draining, atomic death, and conditional stdout-thread join are present.
- Product spawn/readiness/session RPC, raw sends, and pool shutdown are off-loop. The reported synchronous submission/off-loop waiting adjustment is confined to test helpers and does not conceal a loop-blocking product path.
- Relay ID allocation/registration/enqueue is one critical section using `_binding_alive_locked`; correlation and unmatched-frame fan-out are otherwise faithful.
- The denylist is exactly the required nine methods. The live upstream registry currently has exactly 20 `session.*` methods.
- There are seven focused test files and 52 substantive tests; all use injected fake `ChildProcess` implementations and no real Hermes process.
- Focused result: **52 passed** with `pytest -q -s -p no:cacheprovider tests/unit/test_hermes_backend_*.py`.

The repository-wide diff also contains `PROGRESS.md`, `decisions.md`, and an unrelated dirty submodule marker; within the requested implementation scope, no forbidden source area was modified.

## Bottom line

The architecture is largely faithful, but it is **not yet correct against the reviewed plan and contract**. The High startup-vs-shutdown race must be fixed. The two frame-observability defects and three false-assurance test gaps should also be corrected before the ticket is considered complete.

---

## Orchestrator disposition (round 1 of implementation review)

All 9 findings accepted as valid (verified the High against the code myself — the publish→release-lock→start_reading gap is real; a shutdown in that gap leaves wait_ready blocked for READY_TIMEOUT_DEFAULT past the shared deadline). Confirmed-correct list matches my own spot-checks (two-phase init, atomic death, off-loop blocking, non-recursive _binding_alive_locked, fan-out-all + isolation, 9-method denylist, 20-count, gated composition, 52 tests green). Drove all 9 back to the implementer (agent adc3c270237c99b18, resumed with context):
- High #1: transport.shutdown unconditionally opens dead_event/_ready_gate even if the reader never started + start_reading signals shutdown-began so the initializer aborts before wait_ready; shutdown-once guard; new bounded teardown-during-init test.
- Med #2: retire binding on the loop (after queued deliver_child_frame) so a failed session-RPC response still fans out + tees.
- Med #3: tee the synthesized child-reset frame.
- Med #4/#5/#6: three false-assurance tests made real (gated pre-ready children to actually saturate the init executor; lifespan test drives real wiring / recording pool; receive-side teardown test has a real in-flight forward to drain).
- Low #7: guard _stored_session_id_by_employee under _lock.
- Low #8: require a non-empty stored_session_id (no live-id fallback).
- Low #9: constant name matches the plan; registry scan via ast (both quote styles).
A fresh Codex impl review follows; iterate until no violations.

---

## Round 2 of implementation review (after the 9-fix pass)

Fresh Codex re-review: 7 of 9 FIXED (verified: #1 startup/shutdown race, #3 reset tee, #4 real
saturation test, #5 production teardown helper + real test, #7 stored-session lock, #8 no-fallback
stored id, #9 constant rename + ast registry scan). THREE open items, all valid, all driven back:

- **#2 STILL a race (Medium).** The loop-scheduled unregister isn't enough: in `on_frame` the
  responder `observe()` (sets `responder.done`) runs BEFORE `deliver_child_frame` is queued, so the
  woken initializer can queue `unregister_child` ahead of the delivery, dropping the failed-session-RPC
  error response. Fix: queue `deliver_child_frame` BEFORE notifying the responder in `on_frame`; add a
  barrier-based ordering test (no sleeps).
- **NEW Medium — shutdown-once ignores competing deadlines.** The shutdown-once guard makes a follower
  return immediately; if the initializer except-path became leader with its longer CHILD_CLEANUP budget,
  a later `pool.shutdown` with an EARLIER shared deadline returns while the child is still alive past
  that deadline — violating the single-shared-deadline discipline (contract line 60). Fix: a follower
  must force `kill()` at its earlier deadline (breaking the leader's in-flight teardown) without
  re-running the full teardown; coordinate with a completion event + earliest-deadline.
- **#6 test false-assurance (test-only).** Production is correct (route cancels+gathers forwards before
  unregister), but the test's blocker times out naturally (3s) under the route's 5s budget, so it'd pass
  even without cancellation, and never observes CancelledError. Fix: non-expiring blocker, observe
  cancellation/settlement explicitly, release/shutdown the fake executor in finally.

Driven to the implementer (agent adc3c270237c99b18). A fresh review follows; iterate to zero.

---

## Round 3 of implementation review (after the 3-fix pass)

Fresh Codex review: ALL PRODUCTION CODE now correct, NO new production defect. Codex explicitly
verified: #2 on_frame ordering fixed (deliver queued before responder wake; normal/gateway.ready
ordering intact); the competing-deadline follower logic fixed (leader always reaches
`_teardown_complete.set()` at raw_frame_transport.py:255, no deadlock, no double-join, no
unstarted-thread join — matches my own independent check); #6 receive-side teardown test fully
fixed (non-expiring blocker, observes CancelledError, asserts unregister, releases executor in
finally). Two items remain, BOTH test-determinism only (Medium, test-only) — the production
behavior they guard is correct, but the tests use sleep/timing guesses instead of hard barriers so
they could pass under a reversed/buggy impl:
- The #2 ordering test needs an initializer-side cleanup-acknowledgement barrier so reversed order
  cannot reach the assertion until the woken initializer has taken its turn.
- The follower-deadline test needs the fake to SIGNAL when the long-deadline leader has entered its
  blocking wait(), then start the follower, and assert death against the short ABSOLUTE deadline
  with a tight tolerance (not a 5s allowance for a 0.5s deadline; not a sleep-guess of leader
  election).
Driven to the implementer as the (expected final) round. Production is done; this makes the two
regression tests actually able to fail on their bug.

---

## Round 4 (final) — test-determinism confirmed; ticket complete

The two test-determinism gaps are closed. Codex final confirmation: BOTH regression tests are now
rigorous fail-on-revert proofs.
- #2 ordering: the test records a delivery-enqueue token and an observe token synchronously within
  the same on_frame call and asserts `error_deliver_token < observe_token` — necessarily fails if
  on_frame is reverted to observe-then-deliver. Race-free (no sleep in the ordering assertion).
- Follower-deadline: `leader_wait_entered` guarantees the background thread is the leader (so the
  main-thread call is the follower); asserts the child is killed by the follower's SHORT absolute
  deadline and `kill_at < long_deadline - 10.0` — fails under a bare-return revert.
Production code byte-intact (only the two test files changed this round; the touched-mtime on the
two production files is a no-op re-save — I verified the follower logic and on_frame ordering are
unchanged). I ran the two hardened tests 5x with zero flakes and the full focused suite (56 tests)
green; ruff + mypy clean; allowlist clean.

**Codex verdict: "the ticket is complete."** All production code correct, no violations, all
acceptance areas covered, all regression tests rigorous. Handing to the orchestrator/lead for the
single canonical `./verify` run.
