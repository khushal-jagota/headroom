# Implementation report — t_s0q8d2ln

## Outcome

Implemented the reviewed shutdown repair within the ticket boundary. The runner closes admission,
snapshots only running worker turns whose bound session id matches the Ticket's durable Employee
session id, interrupts those owned sessions once, and drains only for the remaining shared deadline.
If that drain expires, it first-wins-settles the still-active matched snapshot turns before returning,
preserving partial output without changing the Ticket or Employee session id.
Routed cleanup attempts every unique gateway and re-raises the first ordinary failure afterward.
An already-expired deadline now makes the router observation non-blocking and inconclusive rather
than a false stuck-router failure.

## RED → GREEN evidence

### Real process seam

RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_server_shutdown_process.py::test_serve_exits_cleanly_when_employee_uses_the_entire_shutdown_deadline
```

Meaningful RED output after correcting one test-fixture date type:

```text
F                                                                        [100%]
AssertionError: assert 'Application shutdown complete' in output
server output contained:
Hermes session ingress router did not terminate within 0.0s
ERROR:    Application shutdown failed. Exiting.
1 failed
```

The test stayed in place while the focused seams were implemented. Final GREEN output:

```text
.                                                                        [100%]
1 passed
```

### Employee runner and concrete deadline-aware interrupt

RED commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py -k 'service_stop_interruption_leaves_ticket_recoverable or stop_does_not_invent_missing_employee_session_id or stop_does_not_interrupt_parked_revision_reservation or stop_attempts_each_bound_session_when_one_interrupt_fails or concurrent_stop_interrupts_each_bound_session_once or stop_interrupt_wait_uses_shared_deadline'
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds.py -k 'deadline_interrupt_does_not_spawn_missing_child or deadline_interrupt_does_not_resume_missing_live_session'
```

RED output:

```text
F..FFF                                                                   [100%]
FAILED test_service_stop_interruption_leaves_ticket_recoverable
FAILED test_stop_attempts_each_bound_session_when_one_interrupt_fails
FAILED test_concurrent_stop_interrupts_each_bound_session_once
FAILED test_stop_interrupt_wait_uses_shared_deadline
FF                                                                       [100%]
TypeError: SharedGateway.interrupt() got an unexpected keyword argument 'deadline'
```

The two negative guards for a missing id and a parked reservation were already green against the
baseline; they proved the new positive interruption path must preserve those existing exclusions.

GREEN commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py -k 'service_stop_interruption_leaves_ticket_recoverable or stop_does_not_invent_missing_employee_session_id or stop_does_not_interrupt_parked_revision_reservation or stop_attempts_each_bound_session_when_one_interrupt_fails or concurrent_stop_interrupts_each_bound_session_once or stop_interrupt_wait_uses_shared_deadline or ordinary_interruption_outside_service_stop_still_errors or stop_rejects_new_reservations or stop_waits_for_released_revision'
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds.py -k 'deadline_interrupt_does_not_spawn_missing_child or deadline_interrupt_does_not_resume_missing_live_session'
```

GREEN output:

```text
.........                                                                [100%]
..                                                                       [100%]
```

### Routed gateway cleanup

RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_core_loops.py::test_entity_routing_gateway_attempts_every_unique_gateway_before_reraising_the_first_failure
```

RED output:

```text
F                                                                        [100%]
assert chief.deadlines == [456.0]
E assert [] == [456.0]
1 failed
```

GREEN command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_core_loops.py -k 'entity_routing_gateway_attempts_every_unique_gateway or background_stop_passes_one_absolute_deadline or server_lifespan_drains_runtime_before_shutting_down_gateways'
```

GREEN output:

```text
...                                                                      [100%]
3 passed, 2 warnings
```

### Session manager zero-time observation

RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds_sessions.py::test_manager_shutdown_treats_an_expired_deadline_as_an_inconclusive_router_observation
```

RED output:

```text
F                                                                        [100%]
planner.minds.gateway.GatewayError: Hermes session ingress router did not terminate within 0.0s
1 failed
```

GREEN command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds_sessions.py -k 'manager_shutdown_treats_an_expired_deadline or manager_shutdown_reports_a_router_that_did_not_terminate'
```

GREEN output:

```text
..                                                                       [100%]
```

## Focused completion checks

Commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py tests/unit/test_core_loops.py tests/unit/test_minds_sessions.py tests/unit/test_server_shutdown_process.py
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds.py -k 'shared_gateway_shutdown or live_session_shutdown_deadline or deadline_interrupt'
.venv/bin/python -m ruff check src/planner/runtime/employee_step_runner.py src/planner/minds/shared_gateway.py src/planner/minds/sessions/service.py tests/unit/test_employee_step_runner.py tests/unit/test_core_loops.py tests/unit/test_minds_sessions.py tests/unit/test_server_shutdown_process.py tests/unit/test_minds.py
.venv/bin/python -m mypy
git diff --check
```

Full concise output:

```text
........................................................................ [ 88%]
.........                                                                [100%]
81 passed, 2 warnings
......                                                                   [100%]
6 passed
All checks passed!
Success: no issues found in 113 source files
git diff --check: no output
```

The two warnings are existing collection/deprecation warnings from `TestClock` and FastAPI's
Starlette `TestClient` import. Per dispatch, `./verify` was not run.

## Accepted implementation-review corrections

The process fake now creates a deterministic observation backlog and the test waits until partial
worker output is durable before sending `SIGINT`. This makes the zero-budget settlement race
observable without adding a production injection seam.

Correction RED commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_server_shutdown_process.py::test_serve_exits_cleanly_when_employee_uses_the_entire_shutdown_deadline
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py::test_expired_stop_settles_matched_snapshot_turn_before_returning
```

Correction RED output:

```text
F                                                                        [100%]
E AssertionError: assert 'running' == 'interrupted'
FAILED test_serve_exits_cleanly_when_employee_uses_the_entire_shutdown_deadline

F                                                                        [100%]
E AssertionError: assert ('running', 'partial before stop', 'stored-key-1')
E                  == ('interrupted', 'partial before stop', 'stored-key-1')
FAILED test_expired_stop_settles_matched_snapshot_turn_before_returning
```

Correction GREEN commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py::test_expired_stop_settles_matched_snapshot_turn_before_returning
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_server_shutdown_process.py::test_serve_exits_cleanly_when_employee_uses_the_entire_shutdown_deadline
```

Correction GREEN output:

```text
.                                                                        [100%]
.                                                                        [100%]
```

Correction completion commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py tests/unit/test_server_shutdown_process.py
.venv/bin/python -m ruff check src/planner/runtime/employee_step_runner.py tests/unit/test_employee_step_runner.py tests/unit/test_server_shutdown_process.py
.venv/bin/python -m mypy
git diff --check
```

Correction completion output:

```text
...............................................                          [100%]
47 passed
All checks passed!
Success: no issues found in 113 source files
git diff --check: no output
```

### Corrected-review late-complete race

The next independent review found that first-wins Chat settlement did not yet gate canonical Ticket
completion. A blocked gateway could return `complete` after shutdown had already settled its turn
`interrupted`, and the runner would still advance the Ticket. The complete path now inspects the
existing first-wins Chat writer's returned turn and invokes the Ticket completion writer only when
that terminal status is `complete`.

RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py::test_late_complete_after_expired_stop_does_not_advance_ticket
```

RED output:

```text
F                                                                        [100%]
E AssertionError: assert <TicketStatus.empty: 'empty'> is <TicketStatus.agent_running_step: 'agent_running_step'>
FAILED test_late_complete_after_expired_stop_does_not_advance_ticket
```

GREEN command and output:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py::test_late_complete_after_expired_stop_does_not_advance_ticket
```

```text
.                                                                        [100%]
```

Focused completion commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py tests/unit/test_server_shutdown_process.py
.venv/bin/python -m ruff check src/planner/runtime/employee_step_runner.py tests/unit/test_employee_step_runner.py tests/unit/test_server_shutdown_process.py
.venv/bin/python -m mypy
git diff --check
```

Focused completion output:

```text
................................................                         [100%]
48 passed
All checks passed!
Success: no issues found in 113 source files
git diff --check: no output
```

## Two-axis review corrections

### Interrupt lock and reply share the original deadline

Initial RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds_sessions.py::test_interrupt_command_lock_wait_consumes_request_timeout_without_sending
```

Initial RED output:

```text
F                                                                        [100%]
E assert returned_within_budget is True
E assert False is True
```

The first correction made one relative timeout cover lock acquisition and reply waiting. The root
spot-check then required the shutdown path to carry the original absolute deadline without deriving
a replacement. The regression was tightened to call the absolute-deadline path directly.

Exact-deadline RED command and output:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds_sessions.py::test_interrupt_command_lock_wait_consumes_absolute_deadline_without_sending
```

```text
F                                                                        [100%]
E TypeError: LiveSession.interrupt() got an unexpected keyword argument 'deadline'
```

GREEN commands and output:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds_sessions.py -k 'interrupt_command_lock_wait or submission_consequences_isolate_late_interrupted_completion'
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds.py -k 'deadline_interrupt or shared_gateway_interrupt_resolves_stored_key'
```

```text
...                                                                      [100%]
...                                                                      [100%]
```

The shutdown caller now passes its unchanged absolute deadline through the shared gateway and live
session manager. The manager recomputes remaining time before the command-lock wait and reply wait.
Ordinary callers still pass one relative timeout, which the live session converts to one internal
absolute deadline exactly once.

### Child cleanup shares one deadline

RED command and output:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds.py::test_shared_gateway_child_shutdown_spends_one_absolute_deadline
```

```text
F                                                                        [100%]
E assert 0.21352808305528015 < 0.12
```

GREEN command and output:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds.py -k 'shared_gateway_child_shutdown_spends_one_absolute_deadline or shared_gateway_shutdown or live_session_shutdown_deadline'
```

```text
.....                                                                    [100%]
```

`GatewayChild` now recomputes remaining time before each process wait and reader-thread join.
`SharedGateway` passes the existing absolute deadline unchanged. The no-deadline grace behavior is
unchanged.

### Ordinary interruption still errors the Ticket

RED command and output:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py::test_late_complete_after_ordinary_interruption_marks_ticket_errored
```

```text
F                                                                        [100%]
E AssertionError: assert <TicketStatus.agent_running_step: 'agent_running_step'>
E                  is <TicketStatus.errored: 'errored'>
```

GREEN command and output:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py -k 'late_complete_after_ordinary_interruption or late_complete_after_expired_stop or ordinary_interruption_outside_service_stop'
```

```text
...                                                                      [100%]
```

A non-complete first-wins visible turn now preserves the Ticket only during service shutdown.
Outside shutdown, the runner marks the still-running Ticket errored even if Hermes races back a
late `complete` result.

### Standards and final focused checks

The matched Employee session, Ticket, and turn identity now use one private immutable named
snapshot. New tests use descriptive `ticket_id` names and one shared real `GatewayStatus` value.
`docs/employee-runtime.md` owns the plain-language shutdown explanation; `docs/systems.md` now
contains only a handoff to that owner.

Final commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py tests/unit/test_minds.py tests/unit/test_minds_sessions.py tests/unit/test_core_loops.py tests/unit/test_server_shutdown_process.py
.venv/bin/python -m ruff check src/planner/runtime/employee_step_runner.py src/planner/minds/shared_gateway.py src/planner/minds/sessions/service.py src/planner/minds/gateway.py tests/unit/test_employee_step_runner.py tests/unit/test_minds.py tests/unit/test_minds_sessions.py tests/unit/test_core_loops.py tests/unit/test_server_shutdown_process.py
.venv/bin/python -m mypy
git diff --check
```

Final output:

```text
........................................................................ [ 42%]
........................................................................ [ 85%]
.........................                                                [100%]
169 passed, 2 warnings
All checks passed!
Success: no issues found in 113 source files
git diff --check: no output
```

The warnings are the existing `TestClock` collection warning and FastAPI `TestClient` deprecation
warning. Per dispatch, `./verify` was not run.

### Final accepted public-interface and documentation corrections

The final contract review rejected adding an absolute-deadline keyword to the public
`LiveSession.interrupt(*, timeout: float)` method. The public signature is restored exactly. A
clearly named private `_interrupt_before_absolute_deadline(*, deadline: float)` seam carries the
already-derived shutdown deadline unchanged to the manager. The ordinary gateway path continues to
call the public relative-timeout interface; only the shutdown path calls the private seam.

The lock-budget regression was changed to exercise that private seam directly. Before the seam was
implemented, it produced this meaningful RED:

```text
F                                                                        [100%]
E AttributeError: 'LiveSession' object has no attribute
E     '_interrupt_before_absolute_deadline'
```

Final correction commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds_sessions.py -k 'interrupt_command_lock_wait or submission_consequences_isolate_late_interrupted_completion'
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds.py -k 'deadline_interrupt or shared_gateway_interrupt_resolves_stored_key or shared_gateway_child_shutdown_spends_one_absolute_deadline'
.venv/bin/ruff check src/planner/minds/sessions/service.py src/planner/minds/shared_gateway.py tests/unit/test_minds_sessions.py tests/unit/test_minds.py
.venv/bin/mypy src
git diff --check
```

Final correction output:

```text
...                                                                      [100%]
....                                                                     [100%]
All checks passed!
Success: no issues found in 113 source files
git diff --check: no output
```

The plain-language shutdown documentation now names the worker and Chief-of-Staff connections
directly instead of describing them as two Employee roles. Per dispatch, `./verify` was not run.

## Closing SQLite-deadline correction

The closing review found that shutdown snapshot and settlement connections still used the normal
SQLite busy timeout after the shared deadline was exhausted. The shared `connect` helper also
applied `busy_timeout_ms` only after opening the connection, leaving initial journal setup on
SQLite's default timeout.

The initial-connect tracer was observed RED before the core change:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_db.py::test_connect_applies_busy_timeout_to_initial_connect_and_pragma
```

```text
F                                                                        [100%]
E AssertionError: assert [(('/.../bounded-connect.db',), {'isolation_level': None})]
E     == [(('/.../bounded-connect.db',),
E          {'isolation_level': None, 'timeout': 0.275})]
```

After `connect(..., busy_timeout_ms=N)` passed `N / 1000` to `sqlite3.connect` and retained the
matching `PRAGMA busy_timeout`, the same test was GREEN:

```text
.                                                                        [100%]
```

The runner tracer used a live matched Employee turn, a separate `BEGIN IMMEDIATE` writer lock, a
200 ms normal busy timeout, and an already-expired stop deadline. Before the runner correction it
was observed RED because stop remained blocked beyond the tighter 100 ms bound:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py::test_expired_stop_does_not_wait_for_locked_shutdown_settlement
```

```text
F                                                                        [100%]
E assert stop_finished.wait(0.1) is True
E assert False is True
```

The runner now recomputes a millisecond busy timeout for each shutdown connection and again
immediately before each Ticket snapshot and each settlement attempt, so connection setup cannot
leave a stale larger wait behind. Each Ticket snapshot uses one read transaction after that second
recomputation; its reads therefore share one acquired SQLite snapshot instead of independently
reacquiring a lock with the old budget. Settlement's `BEGIN IMMEDIATE` is likewise the single writer
lock acquisition after its recomputation. The timeout is capped by the configured normal value and
is zero after deadline exhaustion. SQLite operational failures are logged per item so later
snapshot, interrupt, and settlement work is not skipped. If settlement cannot acquire the lock,
the canonical Ticket remains `agent_running_step` with the same Employee session id for startup
recovery.

Focused completion commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py -k 'expired_stop_does_not_wait_for_locked_shutdown_settlement or expired_stop_settles_matched_snapshot_turn_before_returning or stop_interrupt_wait_uses_shared_deadline or stop_attempts_each_bound_session_when_one_interrupt_fails or service_stop_interruption_leaves_ticket_recoverable or late_complete_after_expired_stop_does_not_advance_ticket'
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_db.py -k 'connect_applies_busy_timeout_to_initial_connect_and_pragma or fresh_schema_drops_enumerating_stage_and_ceiling_checks or fresh_schema_has_chat_turn_recovery_column_and_index'
.venv/bin/ruff check src/planner/core/db.py src/planner/runtime/employee_step_runner.py tests/unit/test_db.py tests/unit/test_employee_step_runner.py
.venv/bin/mypy src
git diff --check
```

Focused completion output:

```text
......                                                                   [100%]
...                                                                      [100%]
All checks passed!
Success: no issues found in 113 source files
git diff --check: no output
```

Per dispatch, `./verify` was not run.

## Canonical acceptance-fixture contention correction

The corrected-source canonical gate exposed a contradiction in the real-process acceptance fixture,
not a production deadline regression. Its fake emitted 5,000 `message.delta` frames. By shutdown,
Panels had written 2,241 update events and accumulated 51,520 output characters, and the server log
reported `database is locked` during shutdown settlement. The fixture's contract is only to retain
one nonempty partial reply and settle that visible turn as `interrupted`; flooding the database
manufactured the lock contention owned by the separate locked-database runner regression.

The minimal fixture correction emits exactly one nonempty `message.delta`, preserves the
`interrupted` assertion, and additionally asserts that the log does not contain `employee shutdown
settlement could not acquire SQLite`. The focused
`test_expired_stop_does_not_wait_for_locked_shutdown_settlement` regression remains byte-for-byte
unchanged and continues to own the deliberate-lock behavior. No production file changed for this
correction. The final canonical `./verify` remains.

Stability command:

```sh
for run_number in {1..10}; do PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_server_shutdown_process.py::test_serve_exits_cleanly_when_employee_uses_the_entire_shutdown_deadline || exit 1; done
```

Exact output — ten consecutive passes:

```text
.                                                                        [100%]
.                                                                        [100%]
.                                                                        [100%]
.                                                                        [100%]
.                                                                        [100%]
.                                                                        [100%]
.                                                                        [100%]
.                                                                        [100%]
.                                                                        [100%]
.                                                                        [100%]
```

Focused completion commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py::test_expired_stop_does_not_wait_for_locked_shutdown_settlement tests/unit/test_employee_step_runner.py::test_expired_stop_settles_matched_snapshot_turn_before_returning
.venv/bin/ruff check tests/unit/test_server_shutdown_process.py
git diff --check
```

Exact output:

```text
..                                                                       [100%]
All checks passed!
git diff --check: no output
```

## Final canonical verification

After the acceptance-fixture correction and the fresh read-only Codex review reported
`NO VIOLATIONS`, the orchestrator ran the complete canonical gate with this worktree's source pinned:

```sh
PYTHONPATH="$PWD/src" ./verify 2>&1 | tee data/verify/t_s0q8d2ln-pass.log
```

The gate passed Ruff; mypy across 115 source files; 791 unit tests; compile/static and CSS checks;
Svelte check with zero errors and warnings; the production build; frontend tests; and 94 Playwright
tests. Its exact closing output was:

```text
[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: ok
[verify] gate build check: ok
[verify] gate frontend: ok
[verify] gate e2e suite: ok

VERIFY: PASS
```

The full transcript is `data/verify/t_s0q8d2ln-pass.log`. The earlier corrected-source run that
exposed the real fixture race is retained as `data/verify/t_s0q8d2ln-process-race-fail.log`; it is not
the completeness claim.

## Implementation choices

- The first `stop` caller owns the immutable sorted active-Ticket snapshot. Concurrent callers take
  no interrupt snapshot and only join the same drain.
- Canonical Ticket and running-turn reads happen on one normal Planner connection after releasing
  the runner condition. The visible turn binding is used only as shutdown-control identity.
- Interrupts are best-effort: each bound snapshot is attempted once, failures are logged, and the
  remaining snapshots and gateway cleanup are not skipped.
- Each matched snapshot retains its turn id. After the bounded drain, the runner snapshots which
  matched Tickets remain active and synchronously first-wins-settles only those turns as
  interrupted. An already-terminal worker result wins the race; parked, missing-id, and mismatched
  turns never enter this snapshot.
- A gateway `complete` result advances the Ticket only when the first-wins Chat writer returns that
  worker turn as terminal `complete`. If shutdown already won with `interrupted`, the runner returns
  without changing canonical Ticket or Employee session identity.
- The concrete deadline-aware interrupt path uses only an already-live session and carries the
  original absolute deadline through lock acquisition and reply waiting through a private live-
  session seam. Public and ordinary callers retain the exact timeout-only interface.
- Child process waits and reader joins recompute time remaining on that same absolute deadline.
  No-deadline child shutdown retains its existing per-phase grace behavior.
- Shutdown SQLite operations recompute a per-item busy timeout from the same absolute deadline,
  capped by the runner's normal timeout. An expired deadline uses zero, and operational lock
  failures are best-effort so durable recovery identity and later cleanup are preserved.
- Routed cleanup catches ordinary `Exception` values only, preserves identity-based deduplication,
  and re-raises the first failure object after all attempts.
- The session manager suppresses only an alive observation after an exactly zero-second join. Any
  positive join budget that expires keeps the existing `GatewayError` behavior.

## Final files

- `src/planner/runtime/employee_step_runner.py`
- `src/planner/minds/shared_gateway.py`
- `src/planner/minds/sessions/service.py`
- `src/planner/minds/gateway.py`
- `src/planner/core/db.py`
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_core_loops.py`
- `tests/unit/test_minds_sessions.py`
- `tests/unit/test_minds.py`
- `tests/unit/test_db.py`
- `tests/unit/test_server_shutdown_process.py`
- `docs/employee-runtime.md`
- `docs/systems.md`
- `orchestration/tickets/t_s0q8d2ln-shutdown-zero-budget/implementation-report.md`
