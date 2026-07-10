# t_arch02 implementation report

## Outcome

Implemented the reviewed domain-owned readiness doorbell plan on top of `2e3a6ca`.
Ticket, Day membership, Link, and runner-settlement ownership seams now ring a
best-effort same-process doorbell only after their canonical work has settled. SQLite
and the periodic readiness timer remain canonical.

## RED evidence

The named acceptance tests were added before the production contract existed, then run
with:

```sh
.venv/bin/python -m pytest -q \
  tests/unit/test_readiness_doorbell.py \
  tests/unit/test_readiness_actions.py \
  tests/unit/test_day_api.py \
  tests/unit/test_value_edit_api.py \
  tests/unit/test_chief_external_work.py \
  tests/unit/test_ticket_delete.py \
  tests/unit/test_core_loops.py \
  tests/unit/test_ticket_readiness_loop.py \
  tests/unit/test_employee_step_runner.py \
  tests/unit/test_return_for_revision.py
```

Result: exit 2 during collection. Six suites failed with
`ModuleNotFoundError: No module named 'planner.runtime.readiness_doorbell'`. This was
the expected missing-contract RED for the new doorbell, action, startup, runtime, and
revision assertions.

## Implemented contracts

- Added `ReadinessDoorbell`, `LoopReadinessDoorbell`, and
  `NoOpReadinessDoorbell`. The loop adapter calls one no-argument callback, catches and
  logs `Exception` with a traceback, and has no retry, queue, persistence, Ticket key,
  or IPC.
- Added explicit Ticket action wrappers for create, Chief external create/reconcile,
  delete, accept, approve, settled-value edit, scope, state, drop, takeover, and
  release. Chief reconcile compares the complete returned Ticket while aligning only
  `updated_at`; its atomic writer was not changed.
- Added Day membership actions with action-owned `BEGIN IMMEDIATE` and commit-before-
  ring. `remove_day_ticket` now returns only the boolean needed to distinguish an
  actual removal from its existing no-op.
- Added Link actions with action-owned transactions. Only `blocks` add/remove rings.
- Replaced the runner's temporary idle callback with injected settlement ringing.
  It rings after the active count is decremented for normal, busy, ownership-lost,
  interrupted, errored/crash, and accepted-revision settlements. Stale discovery,
  losing claims, claims in flight, and cancelled reservations do not ring.
- Runtime composition selects one shared doorbell for the runner and app. Dispatch-off
  and polling-lock-loss use no-op. The lock winner binds the real adapter to
  `TicketReadinessLoop.wake`. Partial construction/start failure stops the partial
  loop, drains/discards the candidate runner, releases the lock once, and constructs a
  fresh runner with a fresh no-op doorbell.
- Removed concrete readiness-loop dependencies and direct wake policy from Ticket and
  Day routes. Low-level Ticket, Day, and Link writers remain runtime-free.
- Updated live Markdown and designed HTML documentation.

## GREEN evidence

Final focused acceptance/regression run, using the command above:

```text
........................................................................ [ 73%]
..........................                                               [100%]
```

Result: 98 passed. Warnings were the existing Starlette `httpx` deprecation and
pytest's non-collectable `TestClock` warning.

Focused lint and type checks:

```sh
.venv/bin/ruff check \
  src/planner/runtime/readiness_doorbell.py \
  src/planner/tickets/actions.py src/planner/tickets/api.py \
  src/planner/days/actions.py src/planner/days/api.py src/planner/days/data.py \
  src/planner/core/link_actions.py src/planner/core/loops.py src/planner/core/server.py \
  src/planner/runtime/employee_step_runner.py \
  src/planner/runtime/ticket_readiness_loop.py \
  tests/unit/test_readiness_doorbell.py tests/unit/test_readiness_actions.py \
  tests/unit/test_day_api.py tests/unit/test_value_edit_api.py \
  tests/unit/test_chief_external_work.py tests/unit/test_ticket_delete.py \
  tests/unit/test_core_loops.py tests/unit/test_ticket_readiness_loop.py \
  tests/unit/test_employee_step_runner.py tests/unit/test_return_for_revision.py
```

Result: `All checks passed!`

```sh
.venv/bin/mypy \
  src/planner/runtime/readiness_doorbell.py \
  src/planner/tickets/actions.py src/planner/days/actions.py \
  src/planner/core/link_actions.py src/planner/core/loops.py src/planner/core/server.py \
  src/planner/runtime/employee_step_runner.py \
  src/planner/runtime/ticket_readiness_loop.py \
  src/planner/tickets/api.py src/planner/days/api.py
```

Result: `Success: no issues found in 10 source files`.

Boundary searches proved:

- no old route `_poke`, `poke`, concrete loop, `system_a`, or server-state loop seam;
- no runtime/doorbell import in `tickets/data.py`, `days/data.py`, or `core/links.py`;
- no doorbell dependency in Project, Sprint, Chat, or File routes;
- production `.ring()` calls occur only in Ticket actions, Day actions, Link actions,
  and Employee runner settlement.

`git diff --check` passed for the complete in-scope implementation and test paths.

## Acceptance and exclusions audit

- Positive matrix covers every approved Ticket action, actual Day add/remove on today
  and another day, every Link kind, runner settlement branches, startup composition,
  partial-start fallback, and the real long-timer HTTP Day-add fast path.
- No-op/failure matrix covers invalid/auth/missing Ticket actions, exact Chief replay,
  duplicate Day add, absent Day removal, invalid Day Ticket, duplicate/cyclic/missing
  links, stale/losing runner discoveries, and cancelled revision reservation.
- Best-effort ordering tests use a second SQLite connection and an explicit observation
  marker before deliberately throwing. Ticket and Day responses still succeed and the
  committed rows remain visible.
- Successful non-ringing matrix covers ordinary Ticket PATCH, field notes, recap,
  worker proposal, sprint-item parent/unparent, Project create/update, Sprint
  create/update, Chat persistence, Day text, every non-block Link kind, and direct
  revision handoff before runner settlement.
- Ticket deletion with both Day and block-link cleanup rings exactly once.
- Normalization-only `errored -> empty` Chief reconciliation rings once; its following
  exact replay rings zero.
- Item 1 strict revision/session/race/drain behavior remains covered by the focused
  revision and runner suites.

Not implemented: atomic ordinary Ticket PATCH (`t_arch03`), candidates 4/6, gateway
completion correlation, frontend work, schema/events, generic mutation service, event
bus, durable queue, retry, or IPC.

## Integrator gate repairs

The root integrator's full gate found one import-order issue and two test-completeness
checks. They were repaired without changing the production behavior:

- reordered `runtime/__init__.py` imports by hand so the full repository Ruff gate is
  clean;
- replaced the remaining fixed `time.sleep(0.3)` readiness-loop setup with an explicit
  first-empty-scan event before calling `wake()`;
- added recording-doorbell auth-failure assertions for accept, approve, settled-value
  edit, scope, direct state, drop, and release, plus the missing-takeover failure. Each
  proves both the rejected response and an unchanged ring count.

Targeted repair verification:

```sh
.venv/bin/python -m pytest -q \
  tests/unit/test_readiness_actions.py \
  tests/unit/test_ticket_readiness_loop.py
```

Result: 32 passed, with only the existing Starlette and `TestClock` warnings.

```sh
.venv/bin/ruff check src tests
```

Result: `All checks passed!`

## Implementation review dispositions

Both independent implementation reviews accepted the production design and requested
stronger negative-path evidence. All seven findings were accepted and closed with
focused recording-doorbell tests; production code was not changed:

1. Malformed Chief create and reconcile requests, plus reconcile while Ticket control
   is active, now prove zero rings. Reconcile failures also snapshot the canonical
   Ticket row and its events before and after the rejection.
2. Deleting a missing Ticket now explicitly proves a 404 and zero rings.
3. A same-state direct state request now explicitly proves rejection and zero rings.
4. A real SQLite `BEFORE INSERT` trigger aborts Day membership insertion inside the
   action transaction. The test proves a 500, zero rings, and rollback of the newly
   materialized Day, membership row, and Day events.
5. A real SQLite unique-index violation rejects an otherwise valid block Link. The
   test distinguishes this from duplicate, cycle, and missing-Link cases and proves
   zero rings with the Link rows and events unchanged.
6. Both exact Chief reconciliation replays now compare every canonical Ticket column
   except the intentionally refreshed `updated_at`, plus the complete event sequence,
   and prove zero additional rings. The test advances a `TestClock` before each replay
   and separately proves `updated_at` changed to that new time, so the semantic
   comparison cannot pass accidentally within one clock second.
7. The excluded worker `POST /api/tickets/{id}/propose` route now proves its proposal
   and recap both persist, emits its expected canonical events, and rings zero times.

Post-review focused verification:

```sh
.venv/bin/python -m pytest -q tests/unit/test_readiness_actions.py
```

Result: 18 passed.

The complete focused acceptance/regression command from the GREEN evidence section
was then rerun. Result: 101 passed, with only the same existing Starlette and
`TestClock` warnings.

```sh
.venv/bin/ruff check src tests
```

Result: `All checks passed!`. Mypy was not rerun because this review follow-up changed
tests and this report only; production code was unchanged.

## Integrator handoff

No unresolved implementation issue. I did not stage, commit, or run `./verify`; the
root integrator owns independent implementation review, serial integration, and the
one authoritative full verification run.
