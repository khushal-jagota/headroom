# t_arch02 implementation plan — domain-owned readiness ringing

## Dependency and integration order

This ticket starts only after `t_arch01-runtime-ownership` is integrated and its focused
tests pass. Use its live names and contracts as the base:

- `TicketReadinessLoop` is the optional polling loop and exposes a no-argument immediate
  wake operation (`wake()` in this plan; if `t_arch01` lands another descriptive name,
  rename that operation before starting this ticket rather than adding an alias).
- `EmployeeStepRunner` always exists with the worker gateway, exposes
  `run_ready_step(ticket_id)`, and owns worker settlement.
- `tickets/actions.py` already owns direct Review revision reservation and delivery. Extend
  that module; do not create a second Ticket service or change revision to use the doorbell.
- `core/loops.py` already constructs the runner independently of the dispatch switch and
  polling lock. This ticket adds the doorbell to that composition; it must not undo the
  runner/loop lifetime split or shutdown ordering established by `t_arch01`.

Integrate serially in this order: `t_arch01` -> this ticket -> `t_arch03`. The three tickets
overlap in `tickets/actions.py`, `tickets/api.py`, `core/loops.py`, and `core/server.py`.
In particular, `t_arch03` must retain the action/doorbell dependency added here but must not
ring for ordinary Ticket attribute `PATCH`.

## Contract skeleton first

Add `src/planner/runtime/readiness_doorbell.py` with exactly these runtime contracts:

1. `ReadinessDoorbell`, a protocol with `ring() -> None`. Callers rely on `ring` being
   non-raising.
2. `LoopReadinessDoorbell`, the production adapter around one no-argument loop wake
   callback. `ring` invokes the callback once, catches `Exception`, and logs the failure
   with a traceback. It has no retry, queue, ticket key, DB access, or cross-process work.
3. `NoOpReadinessDoorbell`, whose `ring` returns without work. Test recording/throwing
   doubles stay in tests and are not production classes.

The database and the loop timer remain the source of truth. The adapter is only a cheap
same-process “check now” signal.

Add the doorbell to the application dependency surface:

- `create_app` installs a `NoOpReadinessDoorbell` before lifespan startup, including test
  mode.
- Production startup replaces it with the `BackgroundLoops.readiness_doorbell` selected by
  runtime composition.
- Ticket and Day routes may depend on the `ReadinessDoorbell` protocol and pass it to a
  domain action. They never receive or import `TicketReadinessLoop`, and they never call
  `ring` themselves.
- Remove `get_system_a`, `Sa`, `_poke`, and `app.state.system_a`. Do not expose the loop as
  route state under a new name.

## Exact ownership seams

### Ticket actions

Extend `src/planner/tickets/actions.py` with explicit action functions that call the existing
low-level writer and then call `doorbell.ring()` after that writer has returned (Ticket
writers commit their own `BEGIN IMMEDIATE` transaction before returning). Do not use a
generic mutation decorator. The explicit wrappers are the audit trail for which actions
ring.

| Action wrapper | Existing canonical writer | Ring rule |
| --- | --- | --- |
| create | `tickets.data.create_ticket` | once after every successful create |
| Chief external create | `create_ticket_from_external_work` | once after every successful create; writer stays unchanged |
| Chief external reconcile | `reconcile_ticket_from_external_work` | once only when canonical Ticket content or control status semantically changed |
| delete | `delete_ticket` | once after the whole delete transaction, even if that transaction removed several day memberships or links |
| accept proposal | `accept_proposal` | once after success |
| approve Review | `approve_review` | once after success |
| edit settled field value | `edit_field_value` | once after success |
| change scope | `change_scope` | once after success |
| direct state change | `set_state` | once after success |
| drop | `drop_ticket` | once after success |
| takeover | `take_over_ticket` | once after success (this is the currently missed path) |
| release | `release_ticket` | once after success |

For Chief reconcile, preserve the landed atomic writer byte-for-byte. The action reads the
Ticket before the call and compares it with the returned Ticket while ignoring only
`updated_at`; this distinguishes an exact semantic replay from the writer's internal
timestamp refresh without changing its transaction, events, validation, or result. An
`errored -> empty` normalization counts as a change and rings. Do not share or merge Chief
create/reconcile with ordinary edit internals.

All other successful listed writers currently make a durable mutation/event even when a
requested value happens to match (for example a repeated scope/status write). Preserve that
existing semantic and ring. “No-op” here means an operation whose canonical writer reports
or returns no semantic change, not a newly invented idempotency rule.

Keep `t_arch01`'s return-for-revision action separate. It reserves the direct runner handoff,
commits, and releases that handoff; it does not ring. Worker proposal routes (`propose`,
field proposal, recap, notes) remain direct data calls and do not ring.

### Day membership actions

Add `src/planner/days/actions.py` with `add_ticket_to_day` and
`remove_ticket_from_day`. Each action owns:

1. any domain guard (including Ticket existence for add);
2. `BEGIN IMMEDIATE` / rollback / commit around the existing non-self-transacting Day
   writer;
3. a single ring only after commit and only when membership actually changed.

Change `days.data.remove_day_ticket` to return `bool`, matching `add_day_ticket`: `True`
after a removal and its event, `False` for the existing absent-membership no-op. Do not
change its event, position repack, timestamp, or no-op behavior. Add rings for membership
changes on **any** day, not just today; readiness will cheaply re-derive today's scope.
Duplicate add and absent remove return normally and ring zero times. Day text editing and
materialize-on-read stay outside these actions.

### Link actions

Add `src/planner/core/link_actions.py` beside the existing Link data module. It owns
`BEGIN IMMEDIATE` / rollback / commit for add/remove and calls the runtime-free
`core.links` writers. Ring once after commit only when `kind is LinkKind.blocks`, for both
add and remove. `belongs_to`, `parent_child`, and `relates` never ring. Validation,
duplicate-add, cycle, constraint, and missing-remove failures roll back and ring zero.

Do not move Link rules, add a new Links package, or make `core.links` import runtime code.

### Employee runner settlement

Inject a `ReadinessDoorbell` into `EmployeeStepRunner`; remove the old mutable idle/poke
callback left by the pre-`t_arch01` runtime if it still exists. Make the internal run method
report whether it reached a canonical settlement. After the execution thread has finished
the settlement writes and decremented its active count, ring exactly once for:

- completed worker turn + closed running step;
- gateway busy / ownership-lost cleanup that closed the accepted turn;
- interrupted or errored worker turn + committed errored status;
- accepted revision work that reaches any of those settlements.

Do not ring when `run_ready_step` rejects a stale/non-runnable Ticket before claim, when a
second claim loses, or for the internal `empty -> agent_running_step` claim itself. This
replaces the old “always poke in thread finally” behavior and preserves fast automatic
continuation without false rings for discoveries that never ran.

### Runtime composition

`core/loops.py` owns the only production choice of implementation:

- dispatch disabled or polling lock not acquired: construct the always-available
  `EmployeeStepRunner` with one `NoOpReadinessDoorbell`; no loop, no lock (when disabled),
  and expose that same no-op doorbell to the app;
- polling lock acquired: construct one `LoopReadinessDoorbell` targeting
  `TicketReadinessLoop.wake`, inject that same instance into the runner, and expose it to
  domain actions.

Resolve the runner/loop constructor cycle locally in composition, before the lifespan
yields: acquire/decide the polling lock first, create a callback closed over the loop,
construct runner -> loop, bind the local loop reference, then start the loop. The callback
cannot run before startup finishes. If loop construction/start fails, stop any partial loop,
release the lock, discard the not-yet-served runner, and construct a fresh runner with a
`NoOpReadinessDoorbell`; never leave a real adapter pointing at `None`.

Keep shutdown order from `t_arch01`: stop the readiness loop, drain/stop the employee runner,
release the polling lock, then let the server shut down the gateway. A process that did not
win the polling lock never attempts an IPC wake; the lock owner's periodic timer is the
backstop.

## RED-first test plan

Write the following tests before production code and run them to observe the expected
missing-contract/import and behavior failures.

### 1. Doorbell contract

Add `tests/unit/test_readiness_doorbell.py`:

- real adapter invokes its delivery callback exactly once;
- a callback that raises is logged and does not escape;
- no-op adapter returns normally;
- neither implementation accepts a ticket id or persists anything.

### 2. Complete domain-action matrix

Add `tests/unit/test_readiness_actions.py` with a shared `RecordingDoorbell` and actual
SQLite/FastAPI state builders. Cover every approved action, not just the old `_poke` sites:

| Approved action | Success assertion | Zero-ring assertion |
| --- | --- | --- |
| Ticket create | valid POST commits and rings 1 | invalid title rings 0 |
| Chief external create | valid Chief POST commits and rings 1 | malformed/unauthorized request rings 0 |
| Chief reconcile | real field/state change rings 1; an otherwise identical `errored -> empty` normalization rings 1 | exact semantic replay while already `empty` rings 0; invalid/active Ticket rings 0 |
| Ticket delete | delete (including an attached block/day) rings exactly 1 | unauthorized or missing Ticket rings 0 |
| Accept proposal | seeded pending proposal accepts and rings 1 | missing proposal/auth failure rings 0 |
| Approve Review | seeded Review approves and rings 1 | wrong state/auth failure rings 0 |
| Settled-field edit | valid passed-field edit rings 1 | future field/auth failure rings 0 |
| Scope change | valid scope write rings 1 | invalid scope/auth failure rings 0 |
| Direct state change | valid jump rings 1 | same/invalid state or auth failure rings 0 |
| Drop | valid drop rings 1 | terminal/auth failure rings 0 |
| Takeover | valid takeover rings 1 | auth/missing Ticket rings 0 |
| Release | valid release rings 1 | auth/missing Ticket rings 0 |

Assert response/event/state semantics as well as the ring count so the tests exercise the
real operation rather than only a mocked callback. Preserve and update the focused existing
tests in `test_value_edit_api.py`, `test_chief_external_work.py`, and
`test_ticket_delete.py` so they inject a recording doorbell instead of `app.state.system_a`.

### 3. Day and Link matrices

Extend `tests/unit/test_day_api.py` (or keep the cases in the new matrix file) to prove:

- actual add and actual remove each commit and ring once;
- this is true for today and for a non-today day;
- duplicate add and absent remove return normally and ring zero;
- invalid Ticket/DB failure rings zero;
- day text PATCH rings zero.

Add API-level Link cases proving blocks add/remove each ring once, every non-block link kind
rings zero, and duplicate/cycle/missing removal failures ring zero. Explicitly retain a
regression named for the previously missed takeover and day-removal paths.

Add a normalization-only Chief regression distinct from the existing broad reconciliation
case: seed an otherwise fully matching Ticket at `ticket_status=errored`, reconcile with the
same user note, recap, settled values, state, and scope, then assert only the existing
`errored -> empty` normalization occurs and the doorbell rings once. Follow it with the same
request while already `empty` and assert no semantic event/change and zero rings.

### 4. Every intentionally non-ringing path

In `tests/unit/test_readiness_actions.py`, use one shared app/`RecordingDoorbell` fixture and
a parameterized or small table-driven regression for every explicit exclusion. Exercise a
real successful write on each path, assert its own canonical state/event effect, and assert
the cumulative doorbell delta is zero:

| Excluded path | Required successful exercise |
| --- | --- |
| ordinary Ticket attribute edit | `PATCH /tickets/{id}` for a non-readiness attribute; retain this assertion after `t_arch03` makes the PATCH atomic |
| field/user notes | `PUT /tickets/{id}/notes/{field}` |
| recap | `PUT /tickets/{id}/recap` |
| worker proposal | one real worker proposal route that files its canonical proposal/recap |
| sprint-item parentage | add and remove a Ticket through `/items/{item_id}/tickets` |
| project edits | successful Project create/update fixture path, with at least the update asserted zero-ring |
| sprint edits | successful Sprint create/update fixture path, with at least the update asserted zero-ring |
| chat/session persistence | a stable fake-gateway human chat send/turn that persists its chat/session state |
| day text | `PATCH /day/{date}` |
| non-block links | successful add and remove for every non-`blocks` `LinkKind` |

Keep these as boundary assertions in the new readiness test module (reusing existing state
builders where practical); do not add no-op action wrappers or doorbell dependencies to the
excluded production modules merely to make them testable. The source-boundary test should
also fail if a route outside the approved Ticket/Day/Link action set starts depending on the
doorbell.

### 5. Commit-before-ring and best effort

Use a `LoopReadinessDoorbell` whose callback opens an independent DB connection, asserts the
new state is already visible, then raises. Through FastAPI prove:

- one Ticket action still returns success and remains durably committed;
- one Day membership action still returns success and remains durably committed;
- each failure is logged once and the response is not changed.

This test is the ordering oracle: a callback invoked before commit cannot observe the write
on the second connection.

### 6. Real fast path

Rename/update the `t_arch01` readiness-loop tests and add one FastAPI integration test with
a very long polling interval. Start a real `TicketReadinessLoop`, a real
`LoopReadinessDoorbell`, and a recording fake exposing `run_ready_step(ticket_id)`. Wait for
the loop's first empty scan, then seed a runnable Ticket directly through the low-level data
writer (which has no action and therefore cannot ring). Place only that seeded Ticket on
today through the HTTP Day-add route and assert the fake runner receives it well inside the
long timer interval. The asserted and isolated chain is:

`route -> day action commit -> doorbell.ring -> TicketReadinessLoop.wake -> readiness scan -> run_ready_step`.

Wrap the loop's first empty scan with a test-only event and wait for that event before the
direct seed. Because no Ticket-create HTTP action runs, no earlier doorbell can race with the
Day add. Do not use a sleep as either synchronization or assertion; wait on the scan event
and then the fake runner event with short bounded timeouts.

### 7. Settlement continuation

Update the renamed employee-runner/readiness-loop auto-advance test that currently wires
`set_idle_callback(loop.poke)`. Inject the real doorbell instead and preserve its long-timer,
two-step assertion: the first worker settlement rings, the loop performs the next readiness
pass, and the approach proposal appears before the timer.

Add or amend recording-doorbell assertions on every runner branch named by the contract:

| Runner branch | Required doorbell assertion |
| --- | --- |
| normal complete | exactly 1, after turn/status settlement |
| gateway busy cleanup | exactly 1, after the accepted turn is failed and running status closes |
| worker-session ownership-lost cleanup | exactly 1, after its accepted turn cleanup commits |
| interrupted result | exactly 1, after interrupted turn and errored status commit |
| gateway error result | exactly 1, after failed turn and errored status commit |
| raised gateway/crash path | exactly 1, after crash cleanup and errored status commit |
| accepted Review revision | exactly 1 after the reserved/released revision turn settles |
| stale/non-runnable discovery before claim | 0 |
| concurrent second claim that loses | 0 for the losing attempt; the winning run's later settlement still rings once |
| internal `empty -> agent_running_step` claim | 0 while the fake gateway is deliberately blocked immediately after claim; becomes 1 only after settlement is released |

Reuse the existing busy, ownership-lost, interrupted/error, crash, duplicate-claim, stale
guard, and revision tests rather than creating parallel behavioral implementations. The
ring-count assertions are additions to those real runner paths.

### 8. Startup and source boundary

Extend `tests/unit/test_core_loops.py` for both dispatch-disabled and polling-lock-loss
branches:

- employee runner exists;
- readiness loop is absent;
- doorbell is `NoOpReadinessDoorbell`;
- disabled startup does not acquire the lock;
- lock-loss startup does not release another process's lock.

Add a source-level AST/text regression over `tickets/api.py` and `days/api.py` proving they
do not import/reference `TicketReadinessLoop`, `get_system_a`, `Sa`, `_poke`, `system_a`, or
call `.ring()`. Assert `core/server.py` has no `app.state.system_a`. The allowed dependency is
only the `ReadinessDoorbell` protocol passed into domain actions.

## Focused implementation loop

Use these focused commands during RED/GREEN; do not run `./verify` mid-ticket:

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
  tests/unit/test_employee_step_runner.py

.venv/bin/ruff check \
  src/planner/runtime/readiness_doorbell.py \
  src/planner/tickets/actions.py src/planner/tickets/api.py \
  src/planner/days/actions.py src/planner/days/api.py src/planner/days/data.py \
  src/planner/core/link_actions.py src/planner/core/loops.py src/planner/core/server.py \
  tests/unit/test_readiness_doorbell.py tests/unit/test_readiness_actions.py

.venv/bin/mypy \
  src/planner/runtime/readiness_doorbell.py \
  src/planner/tickets/actions.py src/planner/days/actions.py \
  src/planner/core/link_actions.py
```

After serial integration with the other approved tickets, the root integrator runs the one
authoritative `./verify` and records its full output. This ticket is not complete merely
because its focused tests pass.

## Exact file scope

Production files allowed for this ticket:

- add `src/planner/runtime/readiness_doorbell.py`;
- extend `src/planner/tickets/actions.py` from `t_arch01`;
- add `src/planner/days/actions.py` and `src/planner/core/link_actions.py`;
- edit `src/planner/tickets/api.py`, `src/planner/days/api.py`, and only the return type/body
  needed in `src/planner/days/data.py`;
- edit `src/planner/runtime/employee_step_runner.py`, and
  `src/planner/runtime/ticket_readiness_loop.py` only for the wake/settlement seam;
- edit `src/planner/core/loops.py`, `src/planner/core/server.py`, and runtime exports if the
  new contract is publicly exported;
- update the current descriptions in `AGENTS.md`, `docs/employee-runtime.md`,
  `docs/systems.md`, `docs/systems.html`, and `docs/tickets-and-gates.md` so they say that
  actions ring a best-effort doorbell and the DB/timer remain canonical.

Test files allowed are the two new focused test files and the existing focused runtime,
startup, Day, value-edit, Chief external-work, and Ticket-delete files named above. Root
memory updates (`PROGRESS.md`, `decisions.md`) remain the integrator's responsibility.

## Explicit exclusions and regression risks

- No event bus, generic mutation service, durable queue, retry, IPC, database field/table,
  event kind, migration, or readiness-rule change.
- No ring for ordinary Ticket attribute PATCH, field/user notes, recap, worker proposals,
  sprint-item parentage, project/sprint edits, chat/session persistence, day text, or
  non-`blocks` links.
- No ring in Review return-for-revision; its accepted handoff remains `t_arch01`'s direct
  runner path.
- Do not alter, merge, or split the atomic Chief external-work writers. The wrapper-only
  semantic-change comparison is the sole special handling.
- Do not move transactions into routes. A ring must never happen inside a transaction or
  affect the already-committed response.
- Do not expose the loop on app state or teach actions whether a loop is running.
- No frontend changes, SharedGateway correlation changes, candidate 4, candidate 6, or
  historical orchestration rewrites.

Serial review must specifically look for: a double ring when Ticket deletion removes links
or days; a ring on duplicate day add/absent removal; runner rings from stale discovery;
gateway shutdown before runner drain; a real doorbell left bound to a failed loop; Chief
writer edits; route-level `.ring()` calls; and `t_arch03` accidentally adding readiness
ringing to ordinary PATCH.
