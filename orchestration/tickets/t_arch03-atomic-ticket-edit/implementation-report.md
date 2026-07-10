# t_arch03 implementation report

## Outcome

Implemented the reviewed atomic ordinary Ticket edit on top of `3fa3a14`.

- Added the parsed `TicketEdit` contract with exactly the six approved attributes.
- Ordinary `PATCH /api/tickets/{ticket_id}` now parses and resolves the complete request,
  then calls one `edit_ticket` writer once.
- The writer validates the intended final Ticket under one `BEGIN IMMEDIATE`, rechecks
  project and sprint references under that lock, and issues one Ticket `UPDATE` only
  when at least one value actually changed.
- Real changes retain the existing per-field event payloads in canonical order and
  produce one direct-user `ticket_changed` context signal. Equal values are a true
  no-op.
- The six public one-field ordinary writers and all their callers were removed.

## RED evidence

The route-level acceptance file was added before production changes and run with:

```sh
.venv/bin/pytest -q tests/unit/test_ticket_edit_api.py
```

Result: 6 failed, 1 passed.

- A late unknown sprint committed the earlier title, timestamp, event, and context.
- An all-six edit advanced worker context to revision 6 instead of revision 1.
- Both non-null equal values and explicit null-to-null clears changed the timestamp,
  appended events, and advanced context.
- Rejected compound selector/deadline/parent requests leaked the earlier title edit.
- A compound edit during active work advanced context once per field.
- Existing successful project selector behavior already passed.

The authority regressions were then run before production changes:

```sh
.venv/bin/pytest -q tests/unit/test_authctx_routes.py \
  -k 'worker_mixing or worker_can_compound or unattributed_and_chief_keep'
```

Result: 1 failed, 2 passed. Unattributed and Chief compound edits advanced context to
revision 2 instead of 1; mixed worker authority was already rejected before mutation,
and the permitted worker compound edit already succeeded without direct-user context.

The post-write rollback proof and compound trace were also installed before production
changes and run with:

```sh
.venv/bin/pytest -q tests/unit/test_ticket_edit_api.py \
  -k 'changes_all_fields or event_insert_fails'
```

Result: 2 failed. A SQLite trigger aborting the second field event left the first field,
event, timestamp, and context committed. The compound edit also still exposed the
six-context behavior. The completed test additionally requires exactly one
`BEGIN IMMEDIATE`, one Ticket `UPDATE`, and project/sprint revalidation after the begin.

## Implemented contract

- Unknown-key, empty-body, complete field-authority, type/enum/null parsing, and project
  selector handling stay at the route before the writer call.
- Omitted edit keys preserve stored values. Present nullable keys explicitly clear.
- Validation order in the writer is title, deadline, project, then sprint. Parent-derived
  project/sprint restrictions use key presence, including explicit equal/null values.
- Project and sprint existence are revalidated after `BEGIN IMMEDIATE` even though the
  project wire aliases were already resolved by the route.
- Actual changes are compared and emitted in title, user note, priority, deadline,
  project ID, sprint ID order.
- One SQL update, all existing `ticket_updated` events, the one context signal, and the
  final reload share the transaction. The trigger regression proves a failure after the
  row update rolls the row, earlier event, and context state back together.
- Ordinary edits add no status, worker-claim, or running-chat guard and no readiness
  action or doorbell dependency.

## GREEN evidence

Focused acceptance and regression gate:

```sh
.venv/bin/pytest -q \
  tests/unit/test_ticket_edit_api.py \
  tests/unit/test_authctx_routes.py \
  tests/unit/test_tickets_engine.py \
  tests/unit/test_worker_context.py \
  tests/unit/test_chief_external_work.py
```

Result: 71 passed. The only warning was the existing Starlette `httpx` deprecation.

Item 2 and runtime-boundary regressions:

```sh
.venv/bin/pytest -q \
  tests/unit/test_readiness_doorbell.py \
  tests/unit/test_readiness_actions.py \
  tests/unit/test_ticket_readiness_loop.py
```

Result: 38 passed. Warnings were the existing Starlette deprecation and imported
`TestClock` collection warning.

Existing real-server CLI Ticket edit flow:

```sh
.venv/bin/pytest -q tests/e2e/test_cli_verbs.py \
  -k ticket_approval_copy_events_and_worker_note_shape
```

Result: 1 passed.

Static gates:

```sh
.venv/bin/ruff check .
.venv/bin/mypy src/planner
```

Results:

```text
All checks passed!
Success: no issues found in 102 source files
```

`git diff --check` passed. The qualified old-writer search returned no matches in
`src` or `tests`. The only changed production files relative to `3fa3a14` are:

```text
src/planner/tickets/api.py
src/planner/tickets/contracts.py
src/planner/tickets/data.py
```

## Review and exclusions audit

The two-axis standards review found one documentation placement/plain-language issue;
it was accepted and fixed by giving ordinary edits their own short heading and product-
level explanation. It also suggested moving the pre-existing parent-project persistence
check to `tickets/logic/admission.py`. That was not applied: the reviewed plan explicitly
keeps the deterministic transaction validation in `tickets/data.py`, and
`logic/admission.py` is outside this ticket's exact approved write scope. No new variant
of that rule or extra mechanism was added. The separate spec review reported
`NO FINDINGS`.

The diff contains no frontend, CLI, schema, migration, event-kind, Chief external-work
writer, readiness action/doorbell, runtime, gateway, candidate 4, candidate 6, or generic
patch-engine change. Chief create/reconcile remains separate and passed its full focused
suite. Ordinary PATCH has no readiness ring. The existing frontend wire shape is
unchanged.

No unresolved implementation failure. I did not stage, commit, or run `./verify`; the
root integrator owns the independent implementation review, serial integration, and the
one authoritative full verification run. I did not edit the unrelated unstaged
`PROGRESS.md`, `decisions.md`, or `.claude/` state.
