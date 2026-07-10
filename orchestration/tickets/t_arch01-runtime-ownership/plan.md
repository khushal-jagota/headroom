# t_arch01 implementation plan — employee runtime ownership

## Fixed intent and current-tree constraints

Implement this ticket from the current authoritative dirty worktree, not from `HEAD`.
The current tree already contains Chief external-work intake, request-identity changes,
pending worker context, chat-image support, and the required
`SharedGateway.run_ticket_step(session_key, ticket_id, prompt, ...)` call. A file rename
must carry that current content forward; it must not recreate either runtime module from
the committed version.

The existing responsibilities map as follows:

- `runtime/system_a.py::SystemA.poll_once` owns today's-board discovery and
  `is_runnable` filtering. It also wrongly owns next-step prompt construction and the
  direct revision relay.
- `runtime/system_b.py::SystemB` owns thread creation, the canonical empty-to-running
  claim, worker Chat turns, `SharedGateway.run_ticket_step`, session-key persistence,
  and settlement. It has no stop/admission state, and its `role` argument is unused.
- `core/loops.py` currently constructs both objects only after the dispatch flag and
  polling lock pass. This is why direct Review guidance disappears when no poller runs.
- `tickets/api.py::return_ticket_for_revision` mutates through
  `tickets.data.return_for_revision` first and only then optionally calls System A. The
  nullable call currently permits HTTP success with no employee handoff.
- `tickets.data.return_for_revision` both performs the canonical transaction and frames
  the worker prompt. Prompt framing moves to the runner; the data writer remains the one
  canonical Ticket mutation.
- `SharedGateway._submit_and_drain` is the pending-worker-context boundary. The runner
  must continue to call `run_ticket_step` with `ticket_id` and must not call a lower-level
  gateway method or append a Panels chat message. The one permitted gateway change is a
  strict-existing-session keyword/path for revision; it must not move context preparation
  or change ordinary create-or-resume behavior.

No migration, event kind, status, retry, durable reservation, gateway correlation fix,
gateway composition redesign, or frontend change is part of this plan.

## Contract shapes to lock before implementation

Add `src/planner/runtime/contracts.py` containing only the Ticket action's narrow,
framework-free ports:

```python
class EmployeeRevisionHandoff(Protocol):
    def release(self) -> None: ...
    def cancel(self) -> None: ...

class EmployeeRevisionRunner(Protocol):
    def reserve_revision(
        self, ticket_id: str, guidance: str
    ) -> EmployeeRevisionHandoff: ...
```

`release()` and `cancel()` are idempotent and non-raising. They perform no I/O; they only
choose the parked thread's terminal path. Do not expose active-count, thread, gateway,
or readiness-loop details through this protocol.

Add `src/planner/tickets/actions.py` with one action for this ticket:

```python
return_ticket_for_revision(
    conn,
    ticket_id,
    *,
    message,
    actor,
    now,
    employee_revision_runner: EmployeeRevisionRunner | None,
) -> Ticket
```

The action sequence is fixed:

1. Validate the non-empty guidance before reserving, preserving the current empty-body
   validation result.
2. If the runner is missing, raise `PlannerError(ErrorCode.gateway_offline, ...)`.
3. Call `reserve_revision(ticket_id, message.strip())`. A stopped runner or a runner
   whose gateway reports unavailable raises the same `gateway_offline` contract here.
4. Call the existing canonical data mutation. On any exception, call `cancel()` and
   re-raise. This includes missing session, stale proposal, terminal Ticket, duplicate
   send, a pre-existing running Chat turn, and database failure.
5. After the data transaction commits, call the handoff's non-raising `release()` and
   return the Ticket.

Change `tickets.data.return_for_revision` to return only `Ticket`. Keep its defensive
guidance validation and resolution transaction, but remove prompt framing from the data
layer. Inside that same `BEGIN IMMEDIATE`, after loading the Ticket and before clearing a
proposal or changing status, query for a `chat_turns` row for this Ticket whose status is
`running`. Raise the existing `already_running` contract if one exists. This closes the
known pre-mutation collision and causes the action to cancel the parked reservation. Do
not add an event or worker-context row for the guidance.

The Ticket route depends on `EmployeeRevisionRunner | None` from app state and calls the
action once. It must not import either concrete runtime class.

## Runtime implementation

### `EmployeeStepRunner`

Rename the current `system_b.py` to `employee_step_runner.py` and `SystemB` to
`EmployeeStepRunner`. Its constructor owns:

- database path, clock, and busy timeout;
- the worker `SharedGateway` that is already configured with `config.worker_skill` and
  `SqliteWorkerContextService`;
- the planning-day boundary hour, needed for the complete execution-time automatic
  eligibility check;
- one condition-protected `accepting` flag and active-thread count.

The worker role stays encapsulated in the configured worker gateway. Remove the unused
per-call `role` parameter rather than duplicating role ownership in the loop or work
item.

Expose these concrete runner operations only:

- `run_ready_step(ticket_id) -> None`: if accepting, account for an employee thread and
  start it. In that thread, open the DB, compute today's day from the runner clock and
  boundary, and call the canonical claim with a guard that checks both current
  `is_runnable` and current membership in today's `day_tickets`. Only after that claim
  succeeds, build the next-step prompt from the claimed Ticket and run it.
- `reserve_revision(ticket_id, guidance) -> EmployeeRevisionHandoff`: while holding the
  lifecycle condition, reject stopped/unavailable state, account for a thread, start a
  revision thread, and wait for a one-way `parked` acknowledgement from that thread
  before returning the handoff. The parked thread waits for `release` or `cancel`.
  `release` builds the existing framed guidance in the runner and enters the claimed
  revision path; `cancel` exits without opening a worker turn or submitting a prompt.
- `wait_idle(timeout=None) -> bool`: retained only as a deterministic test seam.
- `stop() -> None`: atomically set `accepting=False`, then wait until the active count is
  zero. It never shuts down the gateway itself.

The revision path re-reads the Ticket after release and proceeds only when it is still
`agent_running_step` with an existing `chat_session_key`. It uses
`show_prompt_in_chat=False`, resumes that exact session through
`SharedGateway.run_ticket_step(existing_key, ticket_id, framed_guidance, ...,
require_existing_session=True)`, and retains all current worker
Chat/session-key/result/error settlement behavior. If `start_worker_turn` loses the
narrow post-commit race to a human Chat turn and raises `already_running`, catch that
before any gateway call, mark the still-claimed Ticket `errored`, and finish the employee
thread. Do not fail or overwrite the winning human turn and do not leave the Ticket at
`agent_running_step`.

Add the smallest strict-resume path to `SharedGateway.run_ticket_step`:

- one keyword, `require_existing_session: bool = False`;
- pass that choice into `_resume_or_create` as an internal `allow_create` decision;
- with the default, automatic employee steps and every existing caller retain the current
  resume-or-create fallback;
- with `require_existing_session=True`, a null key or `session.resume` not-found result
  returns an errored `RunResult` carrying the original stored key. It must not call
  `session.create`, `prompt.submit`, `on_session_key`, context `prepare`, or context
  `acknowledge`;
- a successful strict resume continues into the unchanged `_submit_and_drain`, so pending
  worker context remains prepared and acknowledged at submit exactly as today.

The runner settles a strict-resume failure through its existing asynchronous error path:
fail the worker Chat turn, move the still-claimed Ticket to `errored`, preserve the stale
stored key for diagnosis, and never remint a replacement session. Do not alter the
separate same-session completion-event correlation code or attempt to fix that known
defect in this ticket.

The automatic and revision entry paths share only the existing claimed-step execution
and settlement core. Do not add a queue, work registry, persisted reservation, retry, or
new gateway abstraction. The strict-resume keyword above is the entire gateway behavior
change. Rename log messages and thread names to the responsibility terms while preserving
their behavior.

### `TicketReadinessLoop`

Rename the current `system_a.py` to `ticket_readiness_loop.py` and `SystemA` to
`TicketReadinessLoop`.

- Keep the today's-board candidate query, read-only readiness filtering, wake event,
  timer backstop, and start/stop lifecycle.
- Remove `_next_step_prompt`, the role field, and the direct revision relay.
- For each discovered Ticket, call only
  `employee_step_runner.run_ready_step(ticket.id)`.
- Keep `poll_once()` returning the discovered IDs as the deterministic test seam.
- Keep the temporary narrow runner-idle callback wiring needed for the existing
  automatic chain. Ticket `t_arch02` will replace that callback with the approved
  readiness doorbell; do not implement the doorbell in this ticket.

The loop's first read remains an optimisation. The runner's transaction-time check is
authoritative, including today's membership, so discovery followed by day removal does
not submit a prompt.

## Composition and shutdown

Retain `start_background_loops`/`BackgroundLoops`; renaming that generic lifecycle
container is not needed for this ticket.

Refactor `core/loops.py` in this exact order:

1. Construct one `EmployeeStepRunner` unconditionally from the already-constructed
   worker gateway.
2. If `dispatch_enabled` is false, do not call the lock function and leave the readiness
   loop absent.
3. If the polling lock is unavailable, leave the readiness loop absent but keep the
   runner.
4. If the lock is acquired, construct/start `TicketReadinessLoop` and connect the
   existing idle wake callback. If loop construction fails, release only the polling
   lock and keep the runner available for direct revisions.
5. Return a handle exposing `employee_step_runner` and optional
   `ticket_readiness_loop`.

`BackgroundLoops.stop()` is idempotent and orders shutdown as:

1. stop/join `TicketReadinessLoop`, preventing new automatic submissions;
2. stop `EmployeeStepRunner`, rejecting new reservations and draining every automatic
   run and accepted parked/released revision;
3. release the polling lock;
4. cancel any unrelated background tasks.

`core/server.py` continues to build the worker and Chief gateways exactly as it does in
the current dirty tree. Pass only the worker gateway to `start_background_loops`, expose
the real employee runner in app state, and await the runtime handle's `stop()` before
`EntityRoutingGateway.shutdown()` closes either gateway. Preserve the worker-context
service on the worker gateway, the Chief role/environment, and the chat-image-capable
gateway routing unchanged.

### Explicit test-mode and unavailable-production behavior

The distinction must be visible in code and tests:

- A successfully composed production lifespan exposes the real
  `EmployeeStepRunner`, even when dispatch is disabled or this process loses the polling
  lock.
- A production app for which runtime composition is missing or failed retains
  `employee_step_runner=None`; revision requests return `gateway_offline` before any
  mutation. There is no production no-op/accepting fallback.
- Test mode still starts no production runtime or Hermes child. Give it a clearly named
  `TestModeAcceptingEmployeeRevisionRunner` in `core/testmode.py`, used only to preserve
  the existing hermetic browser success flow. Its `reserve_revision` starts a daemon
  test thread, waits for that thread's parked acknowledgement, and returns a handoff;
  release/cancel records the decision and lets the thread exit, but performs no Hermes
  work. This keeps the in-process acceptance shape honest without pretending to test
  delivery. Tests of missing/stopped behavior explicitly replace app state with `None`
  or a stopped real runner.
- The accepting test double is not evidence for delivery. A separate FastAPI test uses
  the real `EmployeeStepRunner` plus the existing fake `SharedGateway` transport and is
  the acceptance proof for same-session Hermes delivery, asynchronous completion, Chat
  behavior, and settlement.

Do not make the test-mode accepting double available from production configuration, and
do not let it satisfy any production branch.

## RED-first test sequence

Write the new assertions before changing runtime code. Confirm each selected test fails
for the intended contract, not for an import typo.

### 1. Direct revision handoff and atomic failure

Refactor `tests/unit/test_return_for_revision.py` around the action/runner seam and add:

- A FastAPI test with no readiness loop, a real `EmployeeStepRunner`, and a blocking fake
  `SharedGateway`. Seed a Review Ticket with an existing session. The POST must return
  200 after release while `prompt.submit` remains blocked; before unblocking, assert the
  proposal is cleared, status is claimed exactly once, approval queue is empty, and no
  human guidance exists in Panels chat. Then unblock the fake, file the revised Review
  proposal through the real writer, wait idle, and assert:
  - `session.resume` used the existing key and no `session.create` occurred;
  - the submitted model text is exactly the existing framed guidance (with no pending
    context seeded in this case);
  - the assistant reply/worker turn and revised proposal settle normally;
  - the Review stage is preserved.
- Parameterised missing, stopped-real-runner, and unavailable-gateway cases returning
  503/`gateway_offline`. Snapshot the full Ticket JSON, relevant events, pending worker
  context rows, and Chat state before the request and assert all are identical after it.
- A real-runner cancellation case with valid guidance but a DB-invalid Ticket (for
  example, no existing session). Assert the original validation response, no gateway
  method, `wait_idle()` true, and unchanged Ticket/events/context/chat.
- A pre-existing running human Chat turn case. The real runner must accept and park the
  reservation first; the canonical revision transaction then returns
  409/`already_running`, cancels the reservation, submits nothing, and leaves the full
  Ticket/events/context/Chat snapshot—including the winning active turn—unchanged.
- A deterministic post-commit collision case: intercept `start_worker_turn` so a human
  turn is inserted after the revision transaction commits but immediately before the
  employee turn insert. The HTTP handoff may already have succeeded, but the runner must
  submit no gateway prompt, preserve the human turn, and settle the claimed Ticket from
  `agent_running_step` to `errored` rather than leaving it stuck.
- A stale stored Hermes key case through the real runner. Script `session.resume` as
  not-found and make `session.create`/`prompt.submit` fail the test if reached. After the
  accepted asynchronous handoff settles, assert the worker Chat turn and Ticket are
  errored, the old key is unchanged, no `chat_session_created` remint event exists, the
  proposal was cleared by the accepted revision, and pending worker context remains
  unacknowledged because submit was never reached.
- Preserve explicit tests for existing-session-required, duplicate revision returning
  `already_running`, final Review stage, and direct-only authority.

The default test-mode accepting double keeps the existing browser revision flow in
`tests/e2e/test_flows_a.py` successful and hermetic. Update only terminology/comments;
do not change the Review UI or use that browser test as the Hermes-delivery proof.

### 2. Runner ownership and stale readiness

Rename `tests/unit/test_system_b.py` to
`tests/unit/test_employee_step_runner.py` and update the current dirty version in place.
Adapt existing settlement, chat, session ownership, busy, crash, and concurrency tests
to `run_ready_step` or `reserve_revision` as appropriate. Automatic fixtures must put
the Ticket on the runner clock's current day; direct revisions deliberately need no day
membership.

Add these focused cases:

- the next-step prompt is constructed by the runner and reaches
  `run_ticket_step(..., ticket_id, ...)`;
- a captured discovery is removed from today before forwarding the ID to
  `run_ready_step`, producing no claim, worker Chat turn, or gateway prompt;
- an accepted parked revision keeps `stop()` blocked, a concurrent new reservation is
  rejected, release lets the real run finish, and only then does stop return;
- cancellation drains the active reservation without submitting;
- after stop, both new reservations and any API call using that runner fail before a
  Ticket mutation.

Retain the current worker-context regression at the unchanged SharedGateway boundary:
the runner passes `ticket_id` as the worker entity; prompt preparation/acknowledgement
continues to be covered by `test_minds.py` and `test_worker_context.py`.

Add two exact gateway regressions to `tests/unit/test_minds.py`:

- strict revision resume + stored-key not-found sends only `session.resume`, returns an
  errored result with the original key, never creates/submits, and never prepares or
  acknowledges pending worker context;
- the same stored-key not-found script with the default keyword still performs the
  existing `session.resume -> session.create -> prompt.submit` automatic fallback. This
  proves strictness is revision-only.

### 3. Readiness loop interface

Rename `tests/unit/test_system_a.py` to
`tests/unit/test_ticket_readiness_loop.py` and update the current dirty file in place.

- Use a recording object that exposes only `run_ready_step(ticket_id)` to prove
  `poll_once` does not pass role, prompt, or readiness guard.
- Preserve predicate coverage, today scoping, wake-before-timer behavior, and automatic
  chaining.
- Pair a recorded discovery with the real runner stale-membership test above so both
  sides of the discovery/claim race are covered.

### 4. Startup and shutdown composition

Expand `tests/unit/test_core_loops.py`:

- dispatch disabled: real runner present, loop absent, lock function never called;
- lock already owned: real runner present, loop absent;
- lock acquired: both present and the loop receives the runner;
- handle stop order is loop, runner drain, lock release;
- a loop-construction failure releases the lock but leaves the runner usable.

Add a lightweight server-lifespan regression (in `test_core_loops.py` or a narrowly
named server-runtime test) with recording runtime/gateway doubles to prove the awaited
runtime stop completes before worker/Chief routing-gateway shutdown. Keep the real
runner drain test separate so the lifecycle double does not substitute for thread
behavior.

## Focused commands

After the RED assertions are written:

```sh
.venv/bin/pytest -q \
  tests/unit/test_return_for_revision.py \
  tests/unit/test_employee_step_runner.py \
  tests/unit/test_ticket_readiness_loop.py \
  tests/unit/test_core_loops.py
.venv/bin/pytest -q tests/unit/test_minds.py \
  -k 'strict_existing_session or resume_fallback'
```

After implementation, run the runtime tests plus regressions at the touched boundaries:

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
.venv/bin/pytest -q tests/e2e/test_flows_a.py -k return_for_revision
```

Do not run `./verify` inside this ticket implementation. The orchestrator runs the one
authoritative full `./verify` after all three approved tickets are integrated.

## Exact file scope

Production/runtime files:

- add `src/planner/runtime/contracts.py`;
- rename `src/planner/runtime/system_a.py` to
  `src/planner/runtime/ticket_readiness_loop.py`;
- rename `src/planner/runtime/system_b.py` to
  `src/planner/runtime/employee_step_runner.py`;
- update `src/planner/runtime/__init__.py`, `src/planner/runtime/readiness.py`, and
  `src/planner/runtime/lock.py` terminology only where needed;
- add `src/planner/tickets/actions.py` and update the revision seam only in
  `src/planner/tickets/api.py` and `src/planner/tickets/data.py`;
- update `src/planner/minds/shared_gateway.py` only to add the
  `require_existing_session`/internal no-create path described above; no other gateway
  behavior is in scope;
- update `src/planner/core/loops.py`, `src/planner/core/server.py`, and the explicit
  test-mode double in `src/planner/core/testmode.py`.

Tests:

- rename/update `tests/unit/test_system_a.py` and `tests/unit/test_system_b.py`;
- update `tests/unit/test_return_for_revision.py`, `tests/unit/test_core_loops.py`, the
  strict-resume cases in `tests/unit/test_minds.py`, the narrowly scoped server-lifecycle
  test if split out, and terminology in the existing e2e revision/CLI test comments.

Live naming/documentation:

- `AGENTS.md`, `config.yaml`, `docs/employee-runtime.md`, `docs/systems.md`,
  `docs/systems.html`, and `docs/chat.md`;
- live source/test comments, log strings, app-state names, and imports found by the final
  naming search.

Do not mechanically rewrite `orchestration/`, `decisions.md`, or historical sections of
`PROGRESS.md`. `CONTEXT.md` intentionally retains “Avoid: System A/System B” as rejected
vocabulary. Do not touch Chief external-work writers/routes, worker-context logic,
chat-image code, frontend source/build output, schema, or migrations. Within
`SharedGateway`, touch only the strict-existing-session keyword/path and its focused
tests; explicitly leave the separate completion-correlation defect unchanged.

## Integration and rollback-risk checklist

- [ ] Diff the renamed runtime and test files against their pre-rename dirty versions;
      every Chief/worker-context-era line is still present.
- [ ] `run_ready_step` has no caller-supplied prompt, role, or guard.
- [ ] The automatic claim guard checks both today's membership and `is_runnable` inside
      the claim transaction.
- [ ] Revision validation syntax happens before reservation; Ticket/DB validation happens
      after reservation and cancels on failure.
- [ ] HTTP 200 cannot occur with `employee_step_runner=None`, stopped, or unavailable.
- [ ] Release happens only after commit and cannot raise; cancel submits no prompt.
- [ ] A real reservation is acknowledged only after its worker thread is parked.
- [ ] Runner stop closes admission before waiting; loop stop precedes runner stop; gateway
      shutdown follows runner drain.
- [ ] Dispatch-off and lock-loss paths still expose the real production runner.
- [ ] Test mode uses only the named accepting fake; production has no accepting fallback.
- [ ] Revision uses the existing session, exact framing, `show_prompt_in_chat=False`, and
      no Panels human-guidance row.
- [ ] Strict revision resume never calls `session.create`, never remints the stored key,
      and settles stale-key failure asynchronously as errored with no prompt.
- [ ] Default automatic gateway calls retain the existing create-or-resume fallback.
- [ ] A pre-existing running Chat turn is rejected inside the canonical revision
      transaction, cancels the reservation, and leaves every durable surface unchanged.
- [ ] A post-commit worker-turn insert collision submits no prompt, preserves the winning
      turn, and settles the claimed Ticket errored rather than stuck.
- [ ] The runner still calls `SharedGateway.run_ticket_step` with `ticket_id`, preserving
      pending-context prepare/acknowledge semantics.
- [ ] Existing busy, session-ownership-loss, crash, interrupted, proposal, and settlement
      tests still pass under the new names.
- [ ] Chief and chat-image focused regressions pass without production changes in those
      areas.
- [ ] `rg -n 'System A|System B|system_a|system_b' src tests docs AGENTS.md config.yaml`
      reports no live terminology (apart from an explicitly justified historical/avoid
      occurrence outside that live scope).
- [ ] No new DB object, event, status, queue, retry, IPC, frontend file, or gateway
      correlation/composition change appears in the diff; the only gateway diff is the
      reviewed strict-resume option/path.

If the ticket must be rolled back before later tickets integrate, restore the two old
module/class/app-state names and the nullable route relay as one unit; do not retain a
half-renamed import graph or a runner constructed behind the polling lock. No database
rollback is required because this ticket adds no persisted shape.
