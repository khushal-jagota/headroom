# AD03 contract lock

The orchestrator generated this lock after the corrected implementation plan passed independent
review. Implementation must consume these names and boundaries exactly. It may replace the old modules
and rewire the reviewed allowlist, but it must not add an alias, optional bypass, partial eligibility
rule, second claim path, wake payload, or direct-revision eligibility check.

## Complete eligibility

`src/planner/runtime/automatic_employee_step_eligibility.py` owns the sole production rule:

```python
def is_eligible_for_automatic_employee_step(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    planning_day_id: str,
    worker_type_definition: WorkerTypeDefinition,
) -> bool: ...
```

Both keyword-only inputs are required. The result is true exactly when the Ticket is attached to
`planning_day_id`, has `ticket_status=empty`, is at a non-terminal Stage with a gated field, has no
parked proposal, is permitted by its `(ceiling, at_cap)` scope, and has no active blocker. Stage and
scope interpretation use the explicitly supplied definition. No caller or SQL query owns a reduced
version of these rules.

## Discovery

`src/planner/runtime/automatic_employee_step_discovery_loop.py` owns:

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

Its candidate SQL contains only today's `day_tickets` membership. Every candidate is read, resolved
through its stored Worker type, and passed to the complete eligibility function. The connection closes
before each eligible id is passed to `EmployeeStepRunner.try_run_automatic_step(ticket_id)`. Discovery
is advisory and read-only; the periodic timer remains canonical.

## Transactional claim

`tickets.data.start_run_if_runnable` is deleted. `tickets.data` owns this private typing seam and writer:

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

Both callbacks are required. After `BEGIN IMMEDIATE`, the writer reloads the Ticket and definition,
invokes the day resolver, then invokes the eligibility check exactly once with the explicit day and
definition. False returns without a write or event; true performs the sole `agent_running_step` status
transition. The writer contains no pre-status check, partial eligibility condition, optional guard,
`None` bypass, or runtime import.

The runner injects `dates.resolve_day_id("today", self._clock.now(), self._boundary_hour)` as the
resolver and the exact module function above as `eligibility_check`. This keeps planning-day resolution
inside the claim transaction and makes discovery and claim consume the same rule object.

## Employee runner and direct revision

`EmployeeStepRunner` keeps execution ownership and exposes
`try_run_automatic_step(ticket_id) -> None`. It keeps the existing asynchronous count/drain, prompt,
session, Chat, ownership, and settlement behavior. A false claim exits before a status event, Chat row,
gateway call, or prompt.

`reserve_revision` and its two-phase handoff remain separate. A directly requested revision never calls
automatic eligibility or the planning-day resolver and still reaches the stored Hermes Employee session.

## Payload-free wake

`src/planner/runtime/automatic_employee_step_eligibility_wake.py` owns exactly:

```python
class AutomaticEmployeeStepEligibilityWake(Protocol):
    def wake(self) -> None: ...


class LoopAutomaticEmployeeStepEligibilityWake:
    def __init__(self, deliver: Callable[[], None]) -> None: ...
    def wake(self) -> None: ...


class NoOpAutomaticEmployeeStepEligibilityWake:
    def wake(self) -> None: ...
```

The loop adapter catches and logs ordinary delivery exceptions; a successful committed action remains
successful. The port carries no Ticket id or state and owns no queue, persistence, retry, process, or
IPC. Ticket, Day-membership, blocker-Link, and runner-settlement owners retain the reviewed wake/no-wake
matrix and commit-before-wake ordering. Processes without the polling lock receive the no-op adapter.

## Deletion and naming rule

`runtime/readiness.py`, `runtime/ticket_readiness_loop.py`, `runtime/readiness_doorbell.py`,
`is_runnable`, `TicketReadinessLoop`, `run_ready_step`, `start_run_if_runnable`, `ring()` on this port,
and readiness/doorbell state attributes are deleted. The three old unit-test modules are replaced by the
four eligibility/discovery/wake modules in the reviewed plan. Wake-specific helpers, aliases, parameters,
variables, logs, docs, root instructions, and the `config.yaml` line-9 comment use eligibility-wake or
automatic-discovery language. Historical orchestration/seed input and unrelated generic English remain
unchanged.

Any discovered need to change this skeleton or cross the corrected plan's implementation allowlist
returns to the orchestrator before work continues.
