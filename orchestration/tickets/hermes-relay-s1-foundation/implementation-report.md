# hermes-relay-s1-foundation — implementation report

Implemented EXACTLY the reviewed plan (`implementation-plan.md`) in the §9 RED-first order.
All 7 focused test files are green, ruff-clean, and mypy-clean on the changed files. No
`./verify`, no real Hermes/`tui_gateway` process, no long-running server, no commit/push,
nothing under `data/` touched.

## 1. Files created / edited (absolute paths)

### New package + implementation (6 impl + package marker) — `src/planner/hermes_backend/`
- `/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/__init__.py`
- `/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/raw_frame_transport.py`
- `/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/employee_child_pool.py`
- `/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/employee_child_relay.py`
- `/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/relay_route.py`
- `/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/relay_tee.py`
- `/Users/khushaljagota/.hermes/planning-v2/src/planner/hermes_backend/composition.py`

### New tests (7) — `tests/unit/`
- `/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_pool.py`
- `/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_relay_ids.py`
- `/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_relay_routing.py`
- `/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_child_death.py`
- `/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_verbatim_order.py`
- `/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_session_lifecycle.py`
- `/Users/khushaljagota/.hermes/planning-v2/tests/unit/test_hermes_backend_config_gate.py`

### Minimal edits (exactly §7/§2/§10)
- `/Users/khushaljagota/.hermes/planning-v2/src/planner/core/server.py` — `import asyncio`,
  `import functools`; `employee_child_pool_to_shutdown = None` local; the
  `compose_relay_backend_if_enabled(...)` call inside the existing `elif not config.test_mode:`
  branch (after role-gateway/runtime composition); the two `app.state.employee_child_pool = None`
  / `app.state.employee_child_relay = None` inits; the off-loop reserved-executor
  `functools.partial(pool.shutdown, deadline=deadline)` shutdown hook in `finally`; the
  `@app.websocket("/api/relay")` route next to `/api/events`.
- `/Users/khushaljagota/.hermes/planning-v2/src/planner/core/config.py` — the one
  `relay_backend_enabled: bool` field on `Config` and the one `_bool_value(...)` line
  (`PLAN_RELAY_BACKEND_ENABLED`, default `False`).
- `/Users/khushaljagota/.hermes/planning-v2/config.yaml` — the one line
  `relay_backend_enabled: false` (backend OFF by default).

`server.py` does NOT import `EmployeeChildPool`/`EmployeeChildRelay` directly (composition.py
is the sole import site). No product tee observer is registered anywhere in `src/`.

## 2. Focused pytest — FULL command + output

```
cd /Users/khushaljagota/.hermes/planning-v2 && PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/test_hermes_backend_pool.py \
  tests/unit/test_hermes_backend_relay_ids.py \
  tests/unit/test_hermes_backend_relay_routing.py \
  tests/unit/test_hermes_backend_child_death.py \
  tests/unit/test_hermes_backend_verbatim_order.py \
  tests/unit/test_hermes_backend_session_lifecycle.py \
  tests/unit/test_hermes_backend_config_gate.py -q

....................................................                     [100%]
52 passed in 18.05s
```

Per-file counts: pool 16, relay_ids 7, relay_routing 11, child_death 6, verbatim_order 4,
session_lifecycle 5, config_gate 3 = **52 passed**. Every named test function in §8 is
implemented (no stubs; each asserts real behavior). The session-lifecycle completeness test
mechanically scans the installed `tui_gateway/server.py` `@method("...")` decorators (static
file read) and passes against the real checkout (20 `session.*` methods, 3 hatches confirmed).

Existing suites unaffected by the `Config` field + server edits:
`PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_minds.py tests/unit/test_config.py -q`
→ `106 passed`.

## 3. Ruff + mypy — commands + output (clean)

Ruff (changed files only):
```
.venv/bin/ruff check src/planner/hermes_backend/ \
  tests/unit/test_hermes_backend_pool.py tests/unit/test_hermes_backend_relay_ids.py \
  tests/unit/test_hermes_backend_relay_routing.py tests/unit/test_hermes_backend_child_death.py \
  tests/unit/test_hermes_backend_verbatim_order.py tests/unit/test_hermes_backend_session_lifecycle.py \
  tests/unit/test_hermes_backend_config_gate.py src/planner/core/server.py src/planner/core/config.py

All checks passed!
```

Mypy (new src modules; repo `[tool.mypy] strict = true`):
```
PYTHONPATH=src .venv/bin/mypy src/planner/hermes_backend/raw_frame_transport.py \
  src/planner/hermes_backend/employee_child_pool.py src/planner/hermes_backend/employee_child_relay.py \
  src/planner/hermes_backend/relay_route.py src/planner/hermes_backend/relay_tee.py \
  src/planner/hermes_backend/composition.py

Success: no issues found in 6 source files
```

Mypy on the two edited core files also clean:
```
PYTHONPATH=src .venv/bin/mypy src/planner/core/server.py src/planner/core/config.py
Success: no issues found in 2 source files
```

## 4. Places the plan under-specified something — decisions taken (with justification)

1. **The repo has NO `pytest-asyncio`.** The existing suite (`test_core_loops`, `test_chat_images`)
   drives coroutines via `asyncio.run(...)`, not the `@pytest.mark.asyncio` marker. The plan's
   §8 describes `async` bodies and `asyncio.gather` but does not name the runner. I wrote every
   async test body as an inner `async def body(): ...` executed with `asyncio.run(body())` — the
   repo-native style. No new dependency added. This is a test-harness mechanic, not a design change.

2. **Blocking `threading.Event.wait` must not run on the loop thread while a scheduled spawn is
   pending.** A subtle real bug surfaced during RED: a helper that wrapped `run_in_executor` in an
   extra `async def` and was scheduled via `ensure_future` had NOT started executing when a test then
   called a blocking `event.wait(2.0)` on the loop thread — the blocking wait starved the loop so the
   spawn's executor job never ran. Fix (test-only): the `_spawn` helper now submits the executor job
   synchronously and returns the future (submission is immediate, so a later blocking wait cannot
   starve it), and blocking event waits in async bodies go through an off-loop `_blocking_wait`
   helper (`run_in_executor(None, event.wait, timeout)`). This is a correct test-mechanics fix; the
   production `_forward`/`child_for_employee` path already runs `child_for_employee` off-loop on
   `pool.init_executor` exactly as the plan specifies. Noted here per instruction.

3. **`deliver_child_frame` correlation is int-keyed for mypy strictness AND correctness.** The plan
   says a correlated response is "id present AND (result or error) AND pending has frame['id']". Since
   the relay allocates child-facing ids from an `int` space (§4), I guard correlation with
   `isinstance(frame_id, int)`. This is behavior-preserving: `id:null` parse-error responses and any
   non-int id fall through to the uncorrelated fan-out+tee branch (exactly R3-D), and it satisfies
   `mypy --strict` (the dict key is `int`). No design change.

4. **`_transport_request` death/timeout wait loop.** The plan says "block on
   `(responder.done OR transport.dead_event)` up to the request timeout." Python's `threading.Event`
   has no compound wait, so I implemented a bounded poll: check `responder.done` / `transport.dead_event`,
   then `responder.done.wait(min(remaining, 0.05))`, until done, death, or deadline. Semantics match
   the plan (a child death wakes it near-immediately; timeout is bounded); the 0.05s cap is a small
   local poll granularity, not a new tuning knob.

5. **Test assertions on graceful teardown check `closed`/`dead`, not always `kill()`.** For fakes that
   die gracefully on stdin-close (`close_stdin` → child exit), `transport.shutdown` reaches the
   graceful path and never calls `kill()`. Where the plan text says a shutdown "sees `kill()`", that
   holds only for fakes whose process stays alive until killed (modeled with a `wait()` that blocks
   until `kill()` — `_SlowReadyChild`, `_HungReadyChild`, `NonExitingChild`-style). Tests assert the
   appropriate teardown signal per fake (`killed` for wedged/alive children; `closed`/stdin-closed for
   gracefully-dying ones). This is faithful to `transport.shutdown`'s two-path discipline.

None of these altered the plan's names, structures, module layout, constants, denylist, id-space
model, two-phase init, executor model, or shutdown discipline.

## 5. Scope / safety confirmation

- Touched ONLY files in the §10 allowlist: the 7 new `hermes_backend/*` files, the 7 new
  `tests/unit/test_hermes_backend_*.py` files, and the minimal `server.py` / `config.py` /
  `config.yaml` edits. `git status` shows no changes under `src/planner/minds/`,
  `src/planner/chat/`, or `src/planner/runtime/`. (`PROGRESS.md`/`decisions.md` were modified by
  the orchestrator before this implementation began — not by this run.)
- No import/use of `GatewayChild`, `GatewayRequestHandle`, or `ChildSessionEventIngress` (only
  `ChildProcess`/`SpawnFn`/`spawn_popen` + timeout constants + `JsonDict` from
  `planner.minds.gateway`, plus `resolve_hermes_python`/`hermes_src_root` from
  `planner.minds.config` and `CHIEF_OF_STAFF_ENTITY_ID` from `planner.chat.service`).
- Tee ships with TEST observers only (`RecordingRelayTeeObserver` in tests); no product
  observer registered in `src/`.
- Backend OFF by default (`config.yaml: relay_backend_enabled: false`); route accepts-then-closes
  1013 when off/unavailable.
- Did NOT run `./verify`, did NOT spawn a real Hermes/`tui_gateway`/`hermes serve` process (every
  test uses the injectable `SpawnFn` with fake `ChildProcess`es), did NOT start any long-running
  server, did NOT commit or push, did NOT touch `data/`.

---

## Addendum — Codex implementation-diff review fix pass (9 defects, all accepted)

Applied all 9 accepted Codex-found defects RED-first (test adjusted/added to FAIL on the
current bug, then fixed). Same allowlist/constraints (no minds/chat/runtime edits, no
`./verify`, no real Hermes, no commit). Focused suite grew from 52 → **55 passed**
(3 new tests for #1/#2/#3; #4/#5/#6 rewritten in place). Ruff clean, focused mypy clean.

### Focused re-run — full commands + output
```
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/test_hermes_backend_pool.py tests/unit/test_hermes_backend_relay_ids.py \
  tests/unit/test_hermes_backend_relay_routing.py tests/unit/test_hermes_backend_child_death.py \
  tests/unit/test_hermes_backend_verbatim_order.py tests/unit/test_hermes_backend_session_lifecycle.py \
  tests/unit/test_hermes_backend_config_gate.py
→ 55 passed in 20.14s

.venv/bin/ruff check src/planner/hermes_backend/ tests/unit/test_hermes_backend_*.py \
  src/planner/core/server.py src/planner/core/config.py
→ All checks passed!

PYTHONPATH=src .venv/bin/mypy src/planner/hermes_backend/raw_frame_transport.py \
  src/planner/hermes_backend/employee_child_pool.py src/planner/hermes_backend/employee_child_relay.py \
  src/planner/hermes_backend/relay_route.py src/planner/hermes_backend/relay_tee.py \
  src/planner/hermes_backend/composition.py src/planner/core/server.py src/planner/core/config.py
→ Success: no issues found in 8 source files
```
Existing suites unaffected: `test_minds.py` (106) + `test_config.py` pass.

### The 9 defects — one-line resolutions
1. **HIGH — startup-vs-shutdown race (init worker blocked ~10s past deadline).**
   `RawFrameChildTransport.shutdown` now UNCONDITIONALLY sets `dead_event`+`_ready_gate`
   (even when the stdout reader never started) so any in-flight `wait_ready`/session-RPC
   wakes at once and raises child-exited-before-ready; `start_reading` now RAISES
   `RawFrameTransportError` when shutdown began so `_spawn_and_bind` aborts BEFORE
   `wait_ready`; added a shutdown-once guard (a second concurrent `shutdown` returns
   immediately). New test `test_pool_shutdown_before_start_reading_unblocks_init_within_deadline`
   parks the init thread at `register_child` (the exact gap) and asserts teardown
   completes well under `READY_TIMEOUT_DEFAULT` (RED-verified: without the fix it times out).
2. **MED — failed pool session-RPC response dropped before fan-out/tee.** The failed-spawn
   except-path now retires the binding on the LOOP via `call_soon_threadsafe(unregister_child)`
   so it lands AFTER the already-queued `deliver_child_frame` (FIFO), keeping the error
   response visible + tee'd. New test asserts (deterministically) `unregister_child` runs
   on the loop thread, plus the error frame reaches the subscriber and the tee
   (RED-verified: buggy synchronous init-thread unregister fails the thread-identity assert).
3. **MED — child-reset frames bypassed the tee.** `deliver_child_death` now calls
   `_notify_tee(employee, FROM_CHILD_TO_DOWNSTREAM, reset_frame)`. New test
   `test_child_reset_frame_is_teed` (RED-verified).
4. **MED — saturated-init-executor test didn't saturate.** Rewrote it to use pre-ready
   `_BarrierBlockedReadyChild`s that block IN `wait_ready` (partial transports published,
   process alive until killed) with a `threading.Barrier` proving all
   `INIT_EXECUTOR_MAX_WORKERS` workers are blocked, THEN `pool.shutdown` on the reserved
   executor; asserts it starts+completes within the deadline and kills each blocked child.
5. **MED — lifespan-shutdown test copy-pasted the call.** Extracted the production teardown
   into `server.py::_shutdown_relay_pool_with_deadline(pool, loop, deadline)` used by BOTH
   `_lifespan`'s finally AND the test; the test now drives that real function with a
   recording pool and asserts the keyword `deadline` + that it ran on the pool's reserved
   shutdown executor thread (a positional/wrong-executor regression fails it).
6. **MED — receive-side teardown test had no in-flight forward.** Rewrote it: a barrier
   blocks `child_for_employee` so a real `_forward` task is in flight; after the request,
   the receive side disconnects; asserts the route unregisters the downstream AND the
   forward is cancelled/drained WITHOUT the barrier ever being released.
7. **LOW — `_stored_session_id_by_employee` accessed outside `_lock`.** Both the read (in
   `_create_or_resume_session`) and the write (after a successful spawn) now happen in
   short `_lock` sections; the RPC/teardown stay outside the lock.
8. **LOW — uncontracted identity fallback on missing `stored_session_id`.** `session.create`
   now REQUIRES a non-empty string `stored_session_id`; the live-`session_id` fallback is
   removed and a missing/empty stored id raises (cleaned up by the partial-init teardown).
9. **LOW — two fidelity nits.** (a) Renamed `RELAY_SESSION_LIFECYCLE_REJECTED_CODE` →
   `RELAY_LIFECYCLE_DENIED_CODE` (the plan's name; value `-32011` unchanged, distinct from
   the reset code). (b) The registry-scan completeness test now parses `@method("...")`
   decorators with `ast` (robust to single-quoted `@method('...')`), replacing the
   double-quote-only regex.

### Scope / safety (unchanged)
Only the same allowlisted files changed (the `hermes_backend/*` modules, the 7
`test_hermes_backend_*.py` files, and the minimal `server.py`/`config.py`/`config.yaml`
edits). No edits under `minds/`/`chat/`/`runtime/`; no forbidden imports; no `./verify`;
no real Hermes/`tui_gateway` spawned (all tests use the injectable `SpawnFn` + fakes); no
commit/push; nothing under `data/`. `server.py` gained one small module-level teardown
helper (`_shutdown_relay_pool_with_deadline`) — within the server.py shutdown-hook allowance.

---

## Addendum 2 — Codex re-review fix pass (3 items: 2 product bugs + 1 weak test)

The fresh Codex re-review confirmed 7 of the 9 prior fixes; three items remained. Applied
all three RED-first, same allowlist/constraints. Focused suite grew from 55 → **56 passed**
(+1 new #2 follower-deadline test; #1 and #6 rewritten in place). Ruff clean, focused mypy
clean on all 8 changed src files. No minds/chat/runtime edits, no ./verify, no real Hermes,
no commit. (Note: `skills/panels-chief-of-staff/SKILL.md` shows modified in git status — that
is another session's edit, not this work; my changes stayed within the allowlist.)

### Focused re-run — commands + output
```
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/test_hermes_backend_pool.py tests/unit/test_hermes_backend_relay_ids.py \
  tests/unit/test_hermes_backend_relay_routing.py tests/unit/test_hermes_backend_child_death.py \
  tests/unit/test_hermes_backend_verbatim_order.py tests/unit/test_hermes_backend_session_lifecycle.py \
  tests/unit/test_hermes_backend_config_gate.py
→ 56 passed in 20.83s

.venv/bin/ruff check src/planner/hermes_backend/ tests/unit/test_hermes_backend_*.py \
  src/planner/core/server.py src/planner/core/config.py
→ All checks passed!

PYTHONPATH=src .venv/bin/mypy <the 6 hermes_backend modules> src/planner/core/server.py src/planner/core/config.py
→ Success: no issues found in 8 source files
```
Existing suites unaffected: `test_minds.py` + `test_config.py` pass; the 3 affected files
are stable across 3 repeated runs.

### The 3 items — resolution + RED-first proof
1. **MED — #2 on_frame ordering not fully fixed.** `_spawn_and_bind`'s `on_frame` now
   QUEUES `deliver_child_frame` on the loop FIRST, THEN wakes the pool responder
   (`observe`). This guarantees (on one thread, happens-before) the error-response
   delivery is enqueued before the woken initializer can enqueue its `unregister_child`.
   RED-first: `test_failed_session_rpc_response_is_visible_and_teed_before_binding_retire`
   rewritten to a deterministic ordering proof — a loop wrapper BLOCKS the reader at the
   error-delivery enqueue point and the test asserts `fake.closed is False` at that gate
   (delivery reached BEFORE observe wakes the initializer, whose except-path would have
   closed stdin). Verified: passes 3/3 with the fix, fails 3/3 with the reversed order.
   (Note surfaced to the reviewer: with the CURRENT code the reversed order does not
   actually DROP the frame in practice, because `_spawn_and_bind`'s except-path runs the
   blocking `_shutdown_transport_bounded` before scheduling `unregister`, so the delivery
   is always enqueued first regardless — measured 0/40 drops. The reorder is nonetheless
   kept as correct defense-in-depth removing any reliance on that teardown-delay accident,
   and the new test proves the ordering invariant deterministically.)
2. **MED — competing shutdown deadlines ignored (regression from the shutdown-once guard).**
   `RawFrameChildTransport.shutdown` now runs the FULL teardown once (the leader), but a
   FOLLOWER enforces ITS OWN (possibly earlier) deadline: it waits for the leader's
   `_teardown_complete` up to its remaining budget, and if the child is still alive by the
   follower's deadline it directly `kill()`s + bounded-`wait()`s — so the earliest deadline
   forces death, without re-running the teardown or double-joining threads. RED-first:
   `test_follower_shutdown_kills_child_by_its_own_earlier_deadline` runs a leader shutdown
   with a 30s deadline in flight (child alive), then a follower with a 0.5s deadline; asserts
   the child is killed within ~5s (its short deadline), not the leader's 30s. Verified:
   passes with the fix; without it the follower returns and `child.killed` is False (FAIL).
3. **MED (test-only) — #6 receive-side teardown test gave false assurance.** Rewrote it with
   a NON-EXPIRING blocker (an Event set only in `finally`) so the in-flight `_forward` can
   ONLY end by cancellation, and a relay wrapper that records the forward coroutine ending
   in `CancelledError`. Asserts: the forward WAS cancelled, the downstream is unregistered,
   and the executor is released in `finally` (no thread leak). RED-first: with the route's
   forward-cancellation removed, the never-completing forward hangs the teardown `gather`
   and `wait_for` times out (FAIL); with cancellation, it passes.

### Scope / safety (unchanged)
Only the allowlisted files changed (the `hermes_backend/*` modules, the 7
`test_hermes_backend_*.py` files, and the minimal `server.py`/`config.py`/`config.yaml`
edits). No edits under `minds/`/`chat/`/`runtime/`; no forbidden imports; no `./verify`; no
real Hermes/`tui_gateway` spawned; no commit/push; nothing under `data/`.

---

## Addendum 3 — Test-determinism final round (2 regression tests hardened; NO production change)

The fresh Codex review confirmed ALL production code is correct and no new production
defect exists (on_frame ordering, follower-deadline leader-always-sets-_teardown_complete,
and the #6 receive-side test all correct). Two TEST-ONLY items remained: two regression
tests used sleep/timing guesses and could pass even under a reverted/buggy implementation.
Hardened both to deterministic reversed-order failures. NO production code changed this
round — only the two test files. Focused suite still **56 passed**; ruff clean; focused
mypy clean on all 8 src files; the two production fixes verified byte-intact.

### Focused re-run — commands + output
```
PYTHONPATH=src .venv/bin/python -m pytest <the 7 focused files>
→ 56 passed in 20.74s
.venv/bin/ruff check src/planner/hermes_backend/ tests/unit/test_hermes_backend_*.py \
  src/planner/core/server.py src/planner/core/config.py
→ All checks passed!
PYTHONPATH=src .venv/bin/mypy <6 hermes_backend modules> src/planner/core/server.py src/planner/core/config.py
→ Success: no issues found in 8 source files
```

### The 2 items — resolution + RED-first proof
1. **#2 on_frame ordering test — was not a deterministic reversed-order failure.** The prior
   `fake.closed`-after-a-gate check could pass under reversed code (the reader could signal
   the gate before the woken initializer's cleanup actually ran). Rewrote it to capture the
   on_frame INTERNAL order directly: both `call_soon_threadsafe(deliver_child_frame)` and
   the responder's `observe()` run SEQUENTIALLY on the ONE reader thread inside on_frame. A
   loop wrapper records an incrementing order-token when the error delivery is enqueued; a
   monkeypatched `_PoolSessionResponder.observe` records an order-token when it observes the
   same error response. The test asserts `deliver_token < observe_token` — a direct, race-free
   reflection of the on_frame code order (no sleep, no cross-thread race, no teardown in the
   path). RED-first: passes 3/3 with the fix (deliver before observe); FAILS 3/3 with on_frame
   reverted to observe-then-deliver.
2. **Follower-deadline test — guessed leader election with a sleep and allowed 5s for a 0.5s
   deadline.** Rewrote for (a) DETERMINISTIC leader election: the fake child's `wait()` fires
   `leader_wait_entered` on first entry — necessarily the LEADER's teardown wait (a follower
   reaches `_child.wait` only after its own `_teardown_complete.wait`, which the leader hasn't
   set while parked) — and the test starts the follower only after that signal, so the main
   thread is guaranteed the FOLLOWER; and (b) TIGHT tolerance: it asserts the child's kill time
   lands around the follower's SHORT ABSOLUTE deadline — at least half the short budget after
   start (not instant / not return-and-ignore) and no later than the deadline + a small epsilon
   (death BY the short deadline), and nowhere near the leader's 30s. RED-first: passes 3/3 with
   the follower own-deadline kill; FAILS 3/3 with it reverted to a bare `return` (child never
   killed by the short deadline).

### Scope / safety (unchanged)
Only the two test files changed this round (`test_hermes_backend_relay_routing.py`,
`test_hermes_backend_verbatim_order.py`); the monkeypatch of `_PoolSessionResponder.observe`
is test-local and restored in a `finally`. NO production code changed (the on_frame ordering
and follower-deadline fixes are byte-intact). No edits under `minds/`/`chat/`/`runtime/`; no
`./verify`; no real Hermes/`tui_gateway`; no commit/push; nothing under `data/`.
