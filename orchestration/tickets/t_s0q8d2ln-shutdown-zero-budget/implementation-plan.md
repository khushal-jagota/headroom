# Implementation plan — t_s0q8d2ln

This repair completes restart-recovery tracer bullet 5 without changing public contract/type shapes,
configuration, durable state, or server composition. The concrete `SharedGateway.interrupt` method
gains only a compatible optional keyword-only absolute deadline. `src/planner/core/server.py` already
derives the one monotonic absolute deadline and must remain unchanged.

## File ownership

- Production: `src/planner/runtime/employee_step_runner.py`,
  `src/planner/minds/shared_gateway.py`, and `src/planner/minds/sessions/service.py`.
- Tests: `tests/unit/test_employee_step_runner.py`, `tests/unit/test_core_loops.py`,
  `tests/unit/test_minds_sessions.py`, `tests/unit/test_minds.py`, and one new
  `tests/unit/test_server_shutdown_process.py` for the real-process seam.
- Current docs: `docs/employee-runtime.md` and `docs/systems.md`, limited to the corrected shutdown
  description.
- Ticket artifacts: this directory only.

No contract/type file, `server.py`, `loops.py`, config, migration, frontend, `PROGRESS.md`, or
`decisions.md` change belongs to the implementation sub-agent.

## TDD order and vertical slices

Add and run the real-process regression first, while production is unchanged, so the system seam is
observed RED before any lower-layer repair. Keep it red while the three focused seams are repaired;
then close that slice GREEN. Every command below is run from the worktree root with the checked-in
virtual environment.

### 1. Real `python -m planner serve` shutdown: RED, held until slice 4

Create `tests/unit/test_server_shutdown_process.py` with one subprocess test. In its temporary
directory, create a tiny executable fake Hermes interpreter which emits `gateway.ready`, answers
session create/submit, holds the Employee submission active, and deliberately withholds the
`session.interrupt` reply. Seed one automatically eligible Ticket, launch
`sys.executable -m planner serve` on a free port with isolated DB/home/log/lock paths, the fake
interpreter, and `PLAN_SHUTDOWN_GRACE_SECONDS=0`. Poll SQLite until the Ticket is
`agent_running_step` with a durable `employee_session_id`, send `SIGINT`, and bound the subprocess
wait in the test itself.

Assert the process exits, the log contains `Application shutdown complete`, and it contains neither
`Hermes session ingress router did not terminate within 0.0s` nor
`Application shutdown failed`. Also assert the Ticket retains `agent_running_step` and the same
stored Employee session id. The fake is created by the test at runtime; no new repository helper or
production injection seam is added.

RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_server_shutdown_process.py::test_serve_exits_cleanly_when_employee_uses_the_entire_shutdown_deadline
```

The current code fails with the exact zero-time router error. Leave this test in place while the
next slices make the causal seams green.

### 2. Employee runner: interrupt owned durable sessions, then bounded drain

In `tests/unit/test_employee_step_runner.py`, replace the passive release in the existing
service-stop test with a gateway whose `run_ticket_step` durably binds the session and remains active
until its public `interrupt(session_key, ticket_id)` is called. Assert one exact interrupt call, an
interrupted visible worker turn with partial output, and a canonical Ticket still at
`agent_running_step` with the same `employee_session_id`. Keep the existing ordinary-interruption
test beside it to prove a non-shutdown interruption still marks the Ticket errored. Add a narrow
guard case showing that an active step which has not yet acquired a durable session id produces no
invented interrupt call. Add a two-Ticket case proving one interrupt failure is logged without
skipping the other bound snapshot. Add explicit tests proving two concurrent `stop` callers produce
one interrupt per bound Ticket and a missing Hermes interrupt reply cannot make `stop` exceed the
supplied deadline.

In `tests/unit/test_minds.py`, add direct concrete-gateway tests for the deadline-aware interrupt
path: an unstarted `SharedGateway` must not spawn a child, and a started gateway with no live matching
session must not issue `session.resume`. Both fail immediately and send no Hermes request. These are
the RED proof for the exact fallback path the ordinary Chat interrupt currently takes.

Focused RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py -k 'service_stop_interruption_leaves_ticket_recoverable or stop_does_not_invent_missing_employee_session_id or stop_does_not_interrupt_parked_revision_reservation or stop_attempts_each_bound_session_when_one_interrupt_fails or concurrent_stop_interrupts_each_bound_session_once or stop_interrupt_wait_uses_shared_deadline'
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds.py -k 'deadline_interrupt_does_not_spawn_missing_child or deadline_interrupt_does_not_resume_missing_live_session'
```

Implement `EmployeeStepRunner.stop` in this order:

1. Under `_active_cond`, close admission and use the existing `_stopping` transition to identify the
   first stop caller. That caller copies the sorted `_active_ticket_ids` to an immutable tuple;
   repeated/concurrent callers take an empty interrupt snapshot and only join the remaining drain.
2. Release `_active_cond`. Open one normal Planner SQLite connection and read each captured Ticket
   together with its active Panels turn. Copy an immutable `(employee_session_id, ticket_id)` only
   when the Ticket has a non-empty durable id and its active turn is a running `worker` /
   `worker_step` turn bound to that exact same id. A parked revision reservation has no such turn and
   is excluded. Then close the connection. No SQLite read occurs while the condition is held, and a
   Panels row is used only as owned session-control identity—not as worker conversation context.
3. Outside both the condition and SQLite connection, call the existing concrete gateway `interrupt`
   exactly once per bound Ticket snapshot, passing the unchanged absolute deadline. Extend that
   concrete method with an optional keyword-only `deadline`: the ordinary no-deadline Chat path keeps
   its current behavior, while the shutdown path uses only an already-owned child/live session and
   passes `max(0.0, deadline - monotonic())` to the existing session interrupt wait. If the active
   session is no longer live, fail immediately instead of spawning or resuming it. Log an interrupt
   failure and continue to the remaining snapshots; do not let one best-effort control failure
   prevent gateway cleanup or make up another identity.
4. Reacquire `_active_cond` and wait for `_active == 0` only for
   `max(0.0, deadline - monotonic())`; retain the existing unbounded behavior when no deadline is
   supplied. Never derive a replacement deadline.

`_stopping` is set before interruption, so the existing result/exception settlement maps the worker
turn to `interrupted` and deliberately does not call the Ticket error writer. No new Ticket writer or
status is introduced.

GREEN command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py -k 'service_stop_interruption_leaves_ticket_recoverable or stop_does_not_invent_missing_employee_session_id or stop_does_not_interrupt_parked_revision_reservation or stop_attempts_each_bound_session_when_one_interrupt_fails or concurrent_stop_interrupts_each_bound_session_once or stop_interrupt_wait_uses_shared_deadline or ordinary_interruption_outside_service_stop_still_errors or stop_rejects_new_reservations or stop_waits_for_released_revision'
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds.py -k 'deadline_interrupt_does_not_spawn_missing_child or deadline_interrupt_does_not_resume_missing_live_session'
```

### 3. Routed gateways: exhaustive deduplicated cleanup

In `tests/unit/test_core_loops.py`, strengthen/rename
`test_entity_routing_gateway_passes_same_deadline_to_each_unique_gateway`: make the default gateway
raise a distinctive exception, include one alias of the Chief gateway, make the Chief raise a
different exception, and assert both unique gateways receive the identical deadline exactly once
before the exact first exception object is re-raised. Both failures are required so overwriting the
first error cannot pass.

RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_core_loops.py::test_entity_routing_gateway_attempts_every_unique_gateway_before_reraising_the_first_failure
```

In `EntityRoutingGateway.shutdown`, preserve the current identity-based deduplication and routing
order, but retain the first raised failure, continue through every unseen gateway with the unchanged
absolute deadline, and re-raise that same first failure only after all attempts. Do not calculate
remaining time here: each gateway already receives the absolute deadline and owns its own remaining
budget calculation.

GREEN command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_core_loops.py -k 'entity_routing_gateway_attempts_every_unique_gateway or background_stop_passes_one_absolute_deadline or server_lifespan_drains_runtime_before_shutting_down_gateways'
```

### 4. Session manager: zero-time observation is inconclusive

In `tests/unit/test_minds_sessions.py`, add a deterministic healthy-router test whose proxy records a
zero-second join and reports alive at that immediate observation while delegating eventual cleanup to
the real router. Call `LiveSessionManager.shutdown` with an already-expired deadline and assert it
does not raise; then join the real router under the test's own cleanup bound and prove it exits. Keep
`test_manager_shutdown_reports_a_router_that_did_not_terminate` unchanged and green as the positive
wait/stuck case.

RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds_sessions.py::test_manager_shutdown_treats_an_expired_deadline_as_an_inconclusive_router_observation
```

In `LiveSessionManager.shutdown`, close ingress exactly as today, compute the remaining router wait
once, and record whether it was positive. Join with that value. If the router still reports alive
after a positive wait, retain the existing `GatewayError`; if the supplied absolute deadline was
already exhausted and the join budget was exactly zero, finish the manager's non-blocking cleanup
without manufacturing a stuck-router error. Preserve `_closing`, `_shutdown_error`, and
`_shutdown_complete` settlement for concurrent callers. Do not add a sleep or grace floor.
`SharedGateway.shutdown` continues to run child shutdown in `finally` with
`max(0.0, deadline - monotonic())`, so forced kill remains under the same deadline.

GREEN command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds_sessions.py -k 'manager_shutdown_treats_an_expired_deadline or manager_shutdown_reports_a_router_that_did_not_terminate'
```

Now close the held real-process slice GREEN with its exact command from slice 1. This proves the
unchanged server deadline flows through runtime, worker gateway, and Chief gateway without being
reset, and that Uvicorn no longer turns an exhausted but healthy cleanup into application failure.

## Documentation and focused completion checks

After all four seams are green, update the existing shutdown paragraphs in
`docs/employee-runtime.md` and `docs/systems.md`: admission/discovery close first; the runner snapshots
and interrupts bound owned Employee sessions before its remaining-time drain; every unique role
gateway is attempted; an expired zero-time router observation is not proof of a stuck router. Keep
the text in present tense and do not add a new subsystem.

Run these focused regressions once after the final code/doc edit:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_employee_step_runner.py tests/unit/test_core_loops.py tests/unit/test_minds_sessions.py tests/unit/test_server_shutdown_process.py
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_minds.py -k 'shared_gateway_shutdown or live_session_shutdown_deadline or deadline_interrupt'
.venv/bin/python -m ruff check src/planner/runtime/employee_step_runner.py src/planner/minds/shared_gateway.py src/planner/minds/sessions/service.py tests/unit/test_employee_step_runner.py tests/unit/test_core_loops.py tests/unit/test_minds_sessions.py tests/unit/test_server_shutdown_process.py
.venv/bin/python -m mypy
git diff --check
```

Do not run `./verify` during RED/GREEN implementation. The orchestrator runs it once, after reviews
and disposition, as the only completeness claim.

## Delegated choices

- Use canonical Ticket reads after the active-id snapshot rather than adding an in-memory session
  registry. Require the active running worker turn's bound session id to match the Ticket's durable
  `employee_session_id`, which excludes parked revision reservations without duplicating lifecycle
  state. Panels Chat supplies control identity here; it is not treated as model context.
- Treat Employee interrupt calls as best-effort cleanup: log and continue, leaving the Ticket in its
  already recoverable running state. Re-raising here would skip later interrupts and both gateways.
- In routed cleanup, retain and re-raise the first ordinary `Exception` in routing order after all
  unique gateways are attempted; do not swallow or defer process-control `BaseException` values.
- Suppress only the `router_timeout == 0.0` alive observation. Any positive join budget followed by
  `is_alive()` remains the existing genuine stuck-router failure.
