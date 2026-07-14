# AD03 implementation plan — one complete Automatic Employee-step eligibility decision

## Outcome and consistency check

AD03 replaces the shallow readiness/runnability predicate, poller, doorbell, and optional claim guard
with one deep eligibility module. Its interface answers the whole question: whether Planner may
automatically start this Ticket's next Employee step on this planning day now. Read-only discovery and
the race-safe transactional claim call that same function with the Ticket's explicitly resolved
`WorkerTypeDefinition`.

There is no contradiction among the ticket, `PRINCIPLES.md`, `CONTEXT.md`, or the four binding runtime
decisions. The eligibility function is framework-free but intentionally reads through the supplied
SQLite connection because today's membership and active blockers are part of the locked question.
This is the ticket's explicit seam; it does not introduce a provider adapter or another fact shape.

The change is a deletion replacement:

- add `automatic_employee_step_eligibility.py`,
  `automatic_employee_step_discovery_loop.py`, and
  `automatic_employee_step_eligibility_wake.py`;
- delete `readiness.py`, `ticket_readiness_loop.py`, and `readiness_doorbell.py`;
- rename every live consumer, test, state attribute, thread/log string, and instruction/doc reference;
- replace `start_run_if_runnable` with one required-callback claim writer; and
- add no compatibility module, facade alias, forwarding wrapper, optional guard, or parallel automatic
  start predicate.

Persisted values, events, HTTP payloads, scheduling cadence, action wake set, lock behavior, Hermes
sessions, prompt text, settlement, and UI behavior remain unchanged. There is no schema migration,
event kind, queue, retry, Ticket-id wake payload, or cross-process delivery.

## Locked interfaces

### Complete eligibility module

Create `src/planner/runtime/automatic_employee_step_eligibility.py` with this sole production rule:

```python
def is_eligible_for_automatic_employee_step(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    planning_day_id: str,
    worker_type_definition: WorkerTypeDefinition,
) -> bool: ...
```

The two keyword-only inputs are required and have no defaults. The module may import only stdlib,
`planner.core.links`, the Ticket contract/rules, and `WorkerTypeDefinition`; it imports no FastAPI,
clock, discovery loop, runner, registry configuration, or gateway code. The caller owns planning-day
resolution and explicit Worker-type resolution.

The answer is true exactly when all seven checks pass:

1. `day_tickets` contains `(planning_day_id, ticket.id)`;
2. `ticket.ticket_status is TicketStatus.empty`;
3. `worker_type_definition.is_terminal(ticket.stage)` is false;
4. `worker_type_definition.gating_field(ticket.stage)` is not `None`;
5. `machine.has_pending_parked_proposal(ticket,
   worker_type_definition=worker_type_definition)` is false;
6. scope permits another proposal: it is not the combination of
   `machine.at_or_beyond_ceiling(ticket.stage, ticket.ceiling,
   worker_type_definition=worker_type_definition)` and `ticket.at_cap is AtCap.stop`; and
7. `core_links.is_blocked(conn, ticket.id)` is false.

Use cheap checks before blocker resolution, but do not encode an alternative fast path. Terminal
Stages return false before scope ordering is asked. A malformed stored Stage/ceiling continues to
raise through the definition/rule contract rather than being treated as ineligible; boot/write
integrity remains AD02's responsibility.

### Discovery loop

Create `src/planner/runtime/automatic_employee_step_discovery_loop.py`:

```python
class AutomaticEmployeeStepDiscoveryLoop:
    def __init__(
        self,
        db_path: str,
        clock: Clock,
        employee_step_runner: EmployeeStepRunner,
        *,
        boundary_hour: int,
        busy_timeout_ms: int = 5000,
    ) -> None: ...

    def wake(self) -> None: ...
    def poll_once(self) -> list[str]: ...
    def start(self, interval: int) -> None: ...
    def stop(self) -> None: ...
```

Keep the current wakeable `threading.Event` timer and stop/join behavior. Rename the thread to
`automatic-employee-step-discovery-loop`, the duplicate-start error to
`"automatic employee-step discovery loop already started"`, and the poll error log to
`"automatic employee-step discovery poll failed"`.

The candidate query is locked to membership only:

```sql
SELECT t.id
FROM tickets t
JOIN day_tickets dt ON dt.ticket_id = t.id
WHERE dt.day_id = ?
```

Do not retain the current `ticket_status` or terminal-Stage SQL clauses, and do not add proposal,
scope, blocker, field, chat, or Worker-type clauses. Preserve the current database row order rather
than adding a new dispatch ordering rule.

`poll_once()` resolves `planning_day_id` once with
`dates.resolve_day_id("today", self._clock.now(), self._boundary_hour)`, opens one read connection,
gets the configured registry once, reads every membership-bounded Ticket, resolves that Ticket's
stored Worker type, and calls `is_eligible_for_automatic_employee_step` with the explicit day id and
definition. It closes the read connection before calling
`employee_step_runner.try_run_automatic_step(ticket_id)` once for each eligible Ticket. It returns
those submitted ids. It passes no prompt, worker, definition, day, or partial check to the runner.

### Payload-free eligibility wake port

Create `src/planner/runtime/automatic_employee_step_eligibility_wake.py`:

```python
class AutomaticEmployeeStepEligibilityWake(Protocol):
    def wake(self) -> None: ...


class LoopAutomaticEmployeeStepEligibilityWake:
    def __init__(self, deliver: Callable[[], None]) -> None: ...
    def wake(self) -> None: ...


class NoOpAutomaticEmployeeStepEligibilityWake:
    def wake(self) -> None: ...
```

The loop adapter invokes its delivery callable once and catches/logs every exception as
`"automatic employee-step eligibility wake delivery failed"`. The no-op returns normally. The
interface accepts no Ticket id or state; the module has no SQLite, queue, event-bus, socket, file,
process, or IPC dependency. `wake()` is acceleration only. SQLite plus discovery's periodic timer
remain canonical.

### Transactional claim writer

In `tickets/data.py`, delete `start_run_if_runnable` and its optional `guard`. Add this private typing
interface and writer:

```python
class _AutomaticEmployeeStepEligibilityCheck(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        ticket: Ticket,
        *,
        planning_day_id: str,
        worker_type_definition: WorkerTypeDefinition,
    ) -> bool: ...


def claim_automatic_employee_step(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    planning_day_id_resolver: Callable[[], str],
    eligibility_check: _AutomaticEmployeeStepEligibilityCheck,
    now: int,
) -> Ticket | None: ...
```

Both callbacks are required, non-optional, and have no defaults. The private Protocol exists only to
type the locked keyword-only eligibility interface; it is not a second rule.

The exact transaction body is:

1. enter the existing `_txn`, whose first operation is `BEGIN IMMEDIATE`;
2. load the current `(Ticket, WorkerTypeDefinition)` through
   `_load_ticket_and_worker_type_definition_for_write`;
3. invoke `planning_day_id_resolver()` now, after the write transaction began;
4. invoke `eligibility_check(conn, ticket, planning_day_id=planning_day_id,
   worker_type_definition=worker_type_definition)` exactly once;
5. return `None` without writes/events when it returns false; otherwise write the sole
   `agent_running_step` status transition and return the reloaded Ticket.

The writer must not pre-check `ticket_status`, Stage, membership, proposal, scope, or blocker. Such a
check would recreate the reduced predicate this ticket deletes. It must not import the runtime
eligibility implementation; the runner injects the real function across the existing domain/runtime
seam.

### Employee-step runner

Keep the constructor's position and defaults, changing only the wake dependency name/type:

```python
class EmployeeStepRunner:
    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        gateway: SharedGateway,
        automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
        boundary_hour: int,
        busy_timeout_ms: int = 5000,
    ) -> None: ...

    def try_run_automatic_step(self, ticket_id: str) -> None: ...
    def reserve_revision(self, ticket_id: str, guidance: str) -> _EmployeeRevisionHandoff: ...
    def wait_idle(self, timeout: float | None = None) -> bool: ...
    def stop(self) -> None: ...
```

`try_run_automatic_step` preserves the current asynchronous, counted-thread behavior and `None`
return. Rename `_run_ready_thread` to `_run_automatic_step_thread`; keep the thread name
`employee-step-{ticket_id}` because it names execution, not eligibility.

For an automatic attempt, pass these exact required arguments to the claim writer:

```python
claimed = tickets_data.claim_automatic_employee_step(
    conn,
    ticket_id,
    planning_day_id_resolver=lambda: dates.resolve_day_id(
        "today", self._clock.now(), self._boundary_hour
    ),
    eligibility_check=(
        automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step
    ),
    now=now,
)
```

The lambda is invoked by the writer only after `BEGIN IMMEDIATE`, so a planning-boundary wait uses the
new planning day. Passing the module attribute, rather than copying the rule, lets tests prove the
runner and discovery share the exact production function. A false claim logs
`"employee runner skipped a no-longer-eligible Ticket (ticket=%s)"` and returns before prompt
construction, Panels Chat creation, or any gateway call.

After a successful claim, keep prompt construction, session-key persistence before prompt submit,
gateway ownership checks, Chat turn lifecycle, and success/interrupted/error settlement byte-for-byte
in behavior. `_finish_active` calls the renamed wake's `wake()` only when the attempt settled, exactly
where the current runner rings. A losing claim and a cancelled revision reservation do not wake.

## Required call traces

### Read-only discovery

```text
timer or payload-free wake
  -> AutomaticEmployeeStepDiscoveryLoop.poll_once
  -> resolve current planning_day_id
  -> membership-only candidate query
  -> read Ticket
  -> registry.require(ticket.worker_type)
  -> is_eligible_for_automatic_employee_step(
       conn, ticket,
       planning_day_id=planning_day_id,
       worker_type_definition=definition)
  -> close discovery connection
  -> EmployeeStepRunner.try_run_automatic_step(ticket.id)
```

Discovery writes nothing and its true result is advisory. Any fact may change before the asynchronous
runner claims.

### Race-safe automatic execution

```text
EmployeeStepRunner automatic thread
  -> open its connection
  -> tickets.data.claim_automatic_employee_step
  -> BEGIN IMMEDIATE
  -> reload current Ticket + explicit WorkerTypeDefinition
  -> resolve current planning_day_id now
  -> same is_eligible_for_automatic_employee_step function
  -> false: COMMIT with no status event/chat/gateway/prompt
  -> true: write agent_running_step + status event; COMMIT
  -> construct prompt
  -> create Panels worker turn
  -> create/resume Hermes session and persist key before submit
  -> submit and settle through existing Ticket/Chat writers
  -> payload-free eligibility wake after settlement
```

Day removal, a planning-boundary change, non-empty control status, terminal/changed Stage, a parked
proposal, restrictive scope, or an active blocker appearing after discovery therefore prevents the
claim and every downstream side effect.

### Direct revision bypass

`tickets.actions.return_ticket_for_revision` remains unchanged in responsibility: validate guidance,
reserve an `_EmployeeRevisionHandoff`, call the existing `tickets.data.return_for_revision` writer,
then release only after that writer commits; cancel on failure. The reserved runner branch reads the
already claimed `agent_running_step` Ticket, requires its existing session key, sets
`require_existing_session=True`, and submits the guidance to that same Hermes session. It never calls
`claim_automatic_employee_step`, the planning-day resolver, or automatic eligibility. Its normal
settlement may wake later, as today.

## Composition, state, and commit-before-wake

### Runtime facade and application state

`runtime/__init__.py` exports only the new eligibility function, discovery loop, wake Protocol/two
adapters, and `EmployeeStepRunner`. It exports no old-name alias.

In `tickets/api.py`, use:

```python
def get_automatic_employee_step_eligibility_wake(
    request: Request,
) -> AutomaticEmployeeStepEligibilityWake: ...

AutomaticEmployeeStepEligibilityWakeDependency = Annotated[
    AutomaticEmployeeStepEligibilityWake,
    Depends(get_automatic_employee_step_eligibility_wake),
]
```

Every affected route, action, and `days/api.py` parameter is named
`automatic_employee_step_eligibility_wake`; action calls use `.wake()`. The FastAPI state attribute is
`app.state.automatic_employee_step_eligibility_wake`. Keep
`app.state.employee_step_runner` and the direct-revision dependency unchanged.

### Lock/start/stop composition

In `core/loops.py`, rename the `BackgroundLoops` constructor attributes to
`automatic_employee_step_discovery_loop` and `automatic_employee_step_eligibility_wake`. Preserve this
matrix exactly:

- dispatch disabled: do not acquire the polling lock; always build the runner with the no-op wake;
- lock loser: do not release another process's lock; always build the runner with the no-op wake;
- lock winner: create a loop wake adapter around the new discovery loop's `wake`, create the runner,
  create the discovery loop, populate the loop slot before `start`, and start at `tick_seconds`;
- constructor/start failure: stop a partially created discovery loop, stop/discard its runner, release
  the lock, rebuild a runner with the no-op wake, and keep direct revision available; and
- success: retain loop, runner, wake, and lock ownership in `BackgroundLoops`.

Rename all logs to automatic Employee-step discovery language. Preserve shutdown order: stop discovery
first so it can submit no more ids, drain the runner second, release the machine lock third, then
cancel/gather other tasks. Server lifespan still stops the runtime before shutting down gateways.
`runtime/lock.py` changes its docstring only, from readiness poller to automatic Employee-step
discovery poller; lock functions and config names remain unchanged.

`core/server.py` installs `NoOpAutomaticEmployeeStepEligibilityWake` before lifespan startup. On
successful non-test composition it publishes the handle's runner and renamed wake. Composition failure
leaves direct revision unavailable exactly as now and leaves the no-op wake installed. Test mode keeps
its accepting revision-runner fake and no-op wake.

### Domain actions

Rename the dependency and operation in `tickets/actions.py`, `days/actions.py`, and
`core/link_actions.py` without changing which successful operations wake:

- Ticket create, Chief create, semantic Chief reconcile, delete, accept, settled-value edit, scope,
  direct Stage, drop, takeover, and release;
- actual Day membership add and removal; and
- blocking-link add and removal.

Keep exact no-wake behavior for validation/database failure, exact Chief replay, non-blocking excluded
writes, and excluded project/sprint/parentage/chat operations. Ticket data writers commit before the
action calls `wake()`. Day and link actions retain their explicit `BEGIN IMMEDIATE -> COMMIT -> wake`
ordering. Ticket deletion emits one wake after its whole transaction, not one per removed day/link.
The loop adapter swallows delivery failure, so a successful domain action stays successful.

## RED-first verification plan

Write/rename the tests first and observe focused failures from missing new modules/symbols. Do not keep
old modules temporarily to make RED tests import.

### Complete decision table

Add `tests/unit/test_automatic_employee_step_eligibility.py`. Its table calls the public function
directly with an explicit `planning_day_id` and explicit definition. It must include:

- eligible baselines for both shipped `coding` and `new_worker` Tickets at their real first Employee
  Stages;
- no membership, membership on another day, and membership on the supplied day;
- every non-empty `TicketStatus` value (`agent_running_step`, `awaiting_approval`, `user_takeover`,
  `errored`);
- both terminal Stages (`done`, `dropped`);
- a focused definition-double case with non-terminal `is_terminal=False` and `gating_field=None`, to
  exercise the next-gate conjunct that valid registered definitions normally guarantee;
- a parked current-field proposal;
- scope rows for below ceiling + stop (true), at ceiling + propose (true), and at/beyond ceiling + stop
  (false), including the novel `new_worker` Stage;
- active blocking source (false) and completed/dropped blocking source (true); and
- one all-conjuncts-true row, followed by one-factor-at-a-time false rows so no conjunction is hidden by
  another.

Keep the existing Worker-type regression: a `new_worker` Ticket at a novel Stage is interpreted by its
own definition and never through coding.

### Discovery and timer

Delete `test_ticket_readiness_loop.py` and add
`tests/unit/test_automatic_employee_step_discovery_loop.py`. Preserve its useful integration coverage,
renamed to eligibility/discovery language, and add proofs that target the interface:

- monkeypatch the shared eligibility module with a recording function and put terminal plus non-empty
  Tickets on today; assert the membership-only query presents every one to the function. Return true
  for one selected id and assert discovery passes only that id to
  `try_run_automatic_step`. This proves SQL is not a parallel predicate;
- run the real function over the complete ineligible set and assert no id is submitted;
- prove off-day/no-day Tickets are absent from candidates and today's Ticket is considered with the
  exact explicit day id;
- preserve new-Worker-type discovery and the full fake-gateway proposal flow;
- preserve immediate wake under a long interval, settlement-wake auto-advance, and FastAPI Day-action
  wake integration;
- add a timer-backstop test: use a no-op/dropped wake, add an eligible Ticket, and assert a later periodic
  poll submits it without any delivered wake; and
- preserve start-once, stop/join, and poll-exception survival behavior.

### Transactional claim and stale discovery

Update `test_tickets_engine.py` and `test_employee_step_runner.py`:

- call `claim_automatic_employee_step` with required callbacks; the day resolver and eligibility spy
  each assert `conn.in_transaction` is true, and the spy asserts the current Ticket plus its matching
  definition and explicit planning-day id;
- assert a non-empty Ticket still reaches the complete eligibility callback and returns `None`. There
  is no pre-status short circuit and no `None` bypass;
- capture the runner's claim arguments and assert `eligibility_check is
  automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step`;
- preserve the two simultaneous automatic attempts test: one status event/prompt wins, the loser writes
  nothing and does not wake;
- parameterize stale-discovery mutations for day removal, non-empty control status, terminal Stage,
  parked proposal, scope stop at cap, and active blocker. Take the post-mutation baseline, then call
  `try_run_automatic_step` and assert no new `agent_running_step` event, Panels Chat row/turn, gateway
  method, or prompt;
- preserve and sharpen the 04:59:59 -> 05:00 planning-boundary test: block before entering the real
  claim, advance the mutable clock, then prove the resolver runs inside the transaction and the old-day
  Ticket produces none of those side effects; and
- keep all existing claim/session/prompt/ownership/settlement/error tests and rename only the automatic
  operation/wake vocabulary.

Non-eligibility tests that previously used `guard=None` merely to force running state must instead add
the Ticket to an explicit planning day and call the real required eligibility function through a small
local test helper. No test-only bypass is added to production.

### Direct revision proof

In `test_return_for_revision.py`, install a fail-if-called automatic eligibility function around the
real-runner HTTP revision test. The revision must still reserve before mutation, return after the
handoff is accepted, resume the exact stored session with `require_existing_session=True`, deliver the
guidance in Hermes history, and settle even when discovery is disabled/no lock is owned. Retain the
reservation cancel/release and runner stop/drain tests. This proves direct revision does not silently
inherit automatic eligibility.

### Wake acceptance matrix and composition

Delete `test_readiness_doorbell.py` and add
`test_automatic_employee_step_eligibility_wake.py`; delete `test_readiness_actions.py` and add
`test_automatic_employee_step_eligibility_actions.py`.

The wake unit tests assert one no-argument delivery, `TypeError` for a Ticket-id argument, logged/swallowed
delivery failure with the exact new log text, and normal no-op behavior. The action matrix retains all
current Ticket/Day/blocking-Link/runner-settlement success paths; failures and excluded/no-op writes;
exactly-once deletion; and second-connection observations proving commit occurred before wake. Rename
the static route ownership test to assert routes inject/pass the port but never call `.wake()` or own
discovery policy.

Across every allowed test file, rename helpers, aliases, parameters, and variables that specifically
represent this port: for example `RecordingDoorbell`, `_RecordingDoorbell`, `_DoorbellSpy`, `Doorbell`,
`doorbell`, `doorbell_spy`, and ring-count variables become eligibility-wake names, and their operation
becomes `.wake()`. Do not leave a generic `Doorbell` test alias hiding the deleted concept. Unrelated
generic uses of “ready”, “readiness”, “ring”, or “doorbell” that do not name this Employee-runtime port
remain untouched.

Update `test_core_loops.py` for the exact disabled/lock-loser/lock-winner/start-failure/partial-start
matrix, the no-op vs loop adapter, the renamed state attributes, stop order, and server publication.

### Typing and static architecture locks

Update `tests/typing/tt02b_field_seam_cases.py` to import the new eligibility function and wake Protocol,
pass the renamed action keyword, call eligibility with both required keyword-only inputs, and type-check
the required claim callbacks. Update `test_engine_parameterization.py` to assert both
`planning_day_id` and `worker_type_definition` are keyword-only with no defaults and omission raises
`TypeError`. Update AD02's `test_worker_type_registry.py` semantic-module path from deleted
`runtime/readiness.py` to the new eligibility module.

Add AST/path locks (owned by the new eligibility/discovery tests) that assert:

- the three old runtime files and three renamed old test files do not exist;
- no live source/test import or identifier contains `readiness.py`, `ticket_readiness_loop`,
  `readiness_doorbell`, `is_runnable`, `TicketReadinessLoop`, `run_ready_step`,
  `start_run_if_runnable`, any `*ReadinessDoorbell`, or the old state attributes;
- runtime/actions/application code contains no `.ring()` call and the wake interface has exactly
  `wake(self)`;
- `claim_automatic_employee_step` has required `planning_day_id_resolver` and `eligibility_check`
  parameters, contains no `guard`/`None` branch or direct partial eligibility condition, and invokes the
  callback with `planning_day_id` plus `worker_type_definition`;
- `_CANDIDATE_SQL` normalizes to the membership-only SQL above and contains no status, Stage, proposal,
  scope, blocker, field, chat, or Worker-type predicate;
- discovery calls the one eligibility function and runner method, but contains none of the six
  non-membership decision rules itself;
- the wake module contains no Ticket-id parameter/state or queue/socket/process/IPC dependency;
- the renamed thread/error/log strings are present and readiness/runnable runtime strings are absent;
  and
- live docs/root instructions listed below and the line-9 comment in `config.yaml` contain the new names
  and no rejected old runtime names. The allowed tests contain no wake-port helper/alias/variable using
  `Doorbell`, `doorbell`, or ring-count vocabulary.

Scope the vocabulary guard to live Employee-runtime code/tests/docs. Historical `orchestration/`,
`decisions.md`, `PROGRESS.md`, migration snapshots, and the seed importer's historical Markdown
`Readiness:` field remain history/input. Generic server/browser “ready” and frontend “runnable code”
wording do not name this domain concept and remain unchanged.

## Live documentation

- Update `AGENTS.md` and `CLAUDE.md` system maps to name the optional
  `automatic_employee_step_discovery_loop.py`, always-composed runner, payload-free eligibility wake,
  `commit -> wake`, SQLite, and timer backstop.
- Change only the comment on `config.yaml` line 9 to
  `# automatic Employee-step discovery/dispatch startup switch (§7.1)`; keep the
  `dispatch_enabled: true` key/value and every other configuration line unchanged.
- Rewrite the runtime sections/diagrams/code paths in `docs/employee-runtime.md` and `docs/systems.md`
  around the complete Automatic Employee-step eligibility decision, separate discovery and execution,
  transaction-time recheck, direct-revision bypass, and best-effort wake.
- Update the designed companion `docs/systems.html` in lockstep, preserving its CSS/interaction and
  changing only affected semantic copy/code paths plus its last-verified date.
- Update `docs/tickets-and-gates.md` wake references and action handoffs.

`CONTEXT.md` already has the exact canonical term and rejected terms, so it is deliberately not edited.
`docs/frontend.md`'s “runnable code” is generic security English and is not edited. Historical smoke facts
in live docs are restated with the new names; historical orchestration and migration/seed source text is
not rewritten.

## Serial implementation order

1. Add the new RED eligibility, discovery, wake, claim, same-function, stale-race, typing, and static
   deletion tests; add renamed test files directly rather than copying compatibility imports.
2. Add the complete eligibility module and make its full decision table green.
3. Replace the claim writer and automatic runner operation; make transaction-time day resolution,
   stale-race, and direct-revision tests green.
4. Add the discovery loop and wake module, delete all three old runtime modules, and update the runtime
   facade.
5. Rewire loops/server/actions/routes/state and preserve the lock/start/stop plus commit-before-wake
   matrices.
6. Update remaining unit/e2e comments, strict typing, static guards, root instructions, and live docs;
   update `systems.html` in lockstep.
7. Run targeted formatter, strict typing, and affected unit/e2e commands chosen by the implementer.
   Do not run canonical `./verify` inside the implementation ticket. After independent implementation
   review fixes, the orchestrator runs it once and records the full output.

## Bounded implementation allowlist

Only the following paths may change. A newly discovered required path stops implementation and returns
to the orchestrator for an allowlist decision.

### Explicit deletes

- `src/planner/runtime/readiness.py`
- `src/planner/runtime/ticket_readiness_loop.py`
- `src/planner/runtime/readiness_doorbell.py`
- `tests/unit/test_ticket_readiness_loop.py`
- `tests/unit/test_readiness_doorbell.py`
- `tests/unit/test_readiness_actions.py`

### Explicit adds

- `src/planner/runtime/automatic_employee_step_eligibility.py`
- `src/planner/runtime/automatic_employee_step_discovery_loop.py`
- `src/planner/runtime/automatic_employee_step_eligibility_wake.py`
- `tests/unit/test_automatic_employee_step_eligibility.py`
- `tests/unit/test_automatic_employee_step_discovery_loop.py`
- `tests/unit/test_automatic_employee_step_eligibility_wake.py`
- `tests/unit/test_automatic_employee_step_eligibility_actions.py`

### Production modifications

- `src/planner/runtime/__init__.py`
- `src/planner/runtime/employee_step_runner.py`
- `src/planner/runtime/lock.py`
- `src/planner/tickets/data.py`
- `src/planner/tickets/actions.py`
- `src/planner/tickets/api.py`
- `src/planner/days/actions.py`
- `src/planner/days/api.py`
- `src/planner/core/link_actions.py`
- `src/planner/core/loops.py`
- `src/planner/core/server.py`

`src/planner/runtime/contracts.py` is unchanged: its direct-revision seam already has the right name and
shape. No Ticket/Worker-type contract, database, gateway, Chat, or frontend file changes.

### Test and typing modifications

- `tests/typing/tt02b_field_seam_cases.py`
- `tests/unit/test_chief_external_work.py`
- `tests/unit/test_core_loops.py`
- `tests/unit/test_day_api.py`
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_engine_parameterization.py`
- `tests/unit/test_return_for_revision.py`
- `tests/unit/test_ticket_delete.py`
- `tests/unit/test_ticket_edit_api.py`
- `tests/unit/test_tickets_engine.py`
- `tests/unit/test_value_edit_api.py`
- `tests/unit/test_worker_type_registry.py`
- `tests/e2e/conftest.py`
- `tests/e2e/test_cli_verbs.py`

The e2e files change only their runtime-name comments. Seed tests/fixtures keep historical Markdown
`Readiness` vocabulary.

### Comment-only configuration, root instructions, and live/generated docs

- `config.yaml` (line-9 comment only)
- `AGENTS.md`
- `CLAUDE.md`
- `docs/employee-runtime.md`
- `docs/systems.md`
- `docs/systems.html`
- `docs/tickets-and-gates.md`

`CONTEXT.md`, `PRINCIPLES.md`, `decisions.md`, and `PROGRESS.md` are not implementation-agent edits.
The orchestrator owns memory updates after review/integration.

## Completion claim

AD03 is complete only when the old paths/names and optional claim bypass are absent; the full decision
table is green for both shipped Worker types; discovery and transactional claim demonstrably invoke the
same function; stale discovery cannot create a status event, Panels Chat row, gateway call, or prompt;
direct revision still reaches the real stored Hermes session; lock/timer/wake/action semantics are
unchanged; docs are current; independent review reports no unresolved violations; and the orchestrator's
single canonical `./verify` run passes.
