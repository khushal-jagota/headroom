# t_arch01 final planning-boundary race fix

## Finding

The automatic employee path resolved today's planning day before
`tickets_data.start_run_if_runnable` acquired its `BEGIN IMMEDIATE` transaction. If the runner
waited behind a writer across the configured planning boundary, its transaction-time guard still
queried membership on the day that had just ended. A Ticket present only on that old day could be
claimed and prompted after it was no longer on today.

## RED evidence

The regression test uses a `TestClock` at `04:59:59` and puts the Ticket only on the old planning
day. A wrapper around the real `start_run_if_runnable` creates a barrier immediately before the
real call, after the old implementation had captured `today_id`. The test advances the clock to
`05:00:00`, releases the barrier, and requires no claim, status event, Chat row, or gateway call.

Command:

```sh
.venv/bin/pytest -q tests/unit/test_employee_step_runner.py \
  -k resolves_today_inside_claim_transaction_across_boundary
```

Result before the fix:

```text
F                                                                        [100%]
E           AssertionError: assert [{'ticket_status': 'agent_running_step'},
E                                    {'ticket_status': 'empty'}] == []
1 failed
```

The failure proves the stale old-day guard admitted a run and wrote both status transitions.

## Narrow fix

`EmployeeStepRunner` now calls `dates.resolve_day_id` inside `ready_on_today`. That guard is invoked
by `start_run_if_runnable` only after its writer transaction begins, so day resolution and the
membership/readiness decision use the same transaction-time planning boundary. No contract,
database, gateway, doorbell, or settlement behavior changed.

## GREEN evidence

Targeted regression:

```sh
.venv/bin/pytest -q tests/unit/test_employee_step_runner.py \
  -k resolves_today_inside_claim_transaction_across_boundary
```

```text
.                                                                        [100%]
```

Focused t_arch01 regressions:

```sh
.venv/bin/pytest -q \
  tests/unit/test_return_for_revision.py \
  tests/unit/test_employee_step_runner.py \
  tests/unit/test_ticket_readiness_loop.py \
  tests/unit/test_core_loops.py \
  tests/unit/test_minds.py \
  tests/unit/test_worker_context.py \
  tests/unit/test_request_identity.py \
  tests/unit/test_chief_external_work.py \
  tests/unit/test_chat_images.py
```

```text
........................................................................ [ 47%]
........................................................................ [ 95%]
.......                                                                  [100%]
```

This is 151 passing focused unit tests. The output retained the three pre-existing warnings: one
Starlette `httpx` deprecation warning and two pytest collection warnings for `TestClock` imports in
`test_ticket_readiness_loop.py` and `test_core_loops.py`.

Full static checks:

```sh
.venv/bin/ruff check .
.venv/bin/mypy src/planner
git diff --check
```

```text
All checks passed!
Success: no issues found in 102 source files
```

`git diff --check` returned no output. Full `./verify` was intentionally not run. Nothing was
staged or committed for this fix.

## Final review

The first cumulative Codex review returned:

```text
NO VIOLATIONS
```

Two separate cumulative standards/spec reviewers independently found the planning-boundary race
described above. After the RED/GREEN correction, both follow-up reviewers returned:

```text
NO VIOLATIONS
```

The required Codex follow-up inspected only the staged three-file correction and returned:

```text
NO VIOLATIONS
```
