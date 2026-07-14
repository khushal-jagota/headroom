# t_s0q8d2ln — Clean shutdown after the shared deadline is exhausted

## Outcome

Stopping `panels serve` while an Employee step is active must stay within the one configured
shutdown deadline, preserve the unfinished Ticket for same-session restart recovery, clean up both
role gateways, and exit without falsely claiming that a healthy Hermes ingress router is stuck.

## Existing contracts

- `orchestration/tickets/t_yctn6m7m-restart-recovery/contract.md`: shutdown is bounded and
  unfinished work remains recoverable.
- `orchestration/tickets/t_yctn6m7m-restart-recovery/implementation-plan.md`, tracer bullet 5:
  admission and discovery close before remaining owned sessions are interrupted; worker and Chief
  gateways share one absolute deadline.
- `src/planner/core/adapters/base.py::GatewayAdapter.interrupt`: the existing session-control door
  for an owned Employee session. Do not add another interruption protocol.
- `src/planner/minds/shared_gateway.py`: routed role gateways are deduplicated and receive the same
  absolute deadline.
- `src/planner/minds/sessions/service.py::LiveSessionManager.shutdown`: a genuinely stuck router is
  reported only after it had positive time to observe the closed ingress.

These contracts and public contract/type shapes are fixed for this ticket. The concrete
`SharedGateway.interrupt` method may gain one compatible optional keyword-only absolute deadline;
the generic `GatewayAdapter` protocol and every existing caller remain unchanged.

## Required behavior

1. `EmployeeStepRunner.stop(deadline=...)` closes admission, marks shutdown state, interrupts each
   currently owned Employee session whose running worker turn is bound to the Ticket's durable
   `employee_session_id`, and waits only for the time remaining on the supplied absolute deadline.
   A parked revision reservation without a running bound worker turn is not interrupted. The existing
   concrete gateway interrupt method is used exactly once per owned session snapshot and its request
   wait is bounded by that same deadline. A missing or mismatched session id is not invented, and the
   deadline-aware path does not spawn or resume a session that is no longer live.
2. A shutdown-time interruption settles the visible worker turn as `interrupted` and leaves the
   Ticket at `agent_running_step` with the same `employee_session_id`. Ordinary interruptions outside
   service shutdown retain their current errored behavior.
3. `EntityRoutingGateway.shutdown(deadline=...)` attempts every unique role gateway exactly once even
   when an earlier gateway raises. After all cleanup attempts, it re-raises the first failure in
   routing order. It does not restart or extend the deadline.
4. When `LiveSessionManager` receives an already-expired deadline, it closes ingress and performs
   non-blocking cleanup without claiming that a healthy router is stuck merely because it had zero
   scheduling time. A positive router wait that expires while the router is still alive retains the
   existing `GatewayError` behavior. Child forced-kill cleanup remains bounded by the same deadline.
5. The server continues deriving exactly one absolute deadline. Runtime, worker gateway, and Chief
   gateway cleanup never reset that clock.
6. No new config value, durable state, Ticket status, scheduler, retry queue, or background lifecycle
   owner is introduced.

## TDD seams and acceptance tests

The owner accepted these seams when authorizing the diagnosed repair:

- Real process seam: a temporary `python -m planner serve` process with fake gateways, an exhausted
  grace, and `SIGINT` exits with `Application shutdown complete` and without the exact `0.0s` router
  error or `Application shutdown failed`.
- Employee-runner seam: an active bound Employee step is interrupted during `stop`, its visible turn
  settles, and its canonical Ticket/session identity remains restart-recoverable. An interrupt whose
  Hermes reply never arrives cannot overrun the absolute deadline, and concurrent `stop` callers do
  not duplicate the interrupt. A parked revision reservation with an old durable session id is not
  mistaken for a running owned session.
- Routed-gateway seam: two unique gateways are both called with the same deadline when the first
  raises, and the first error is surfaced after cleanup.
- Session-manager seam: a healthy router accepts an expired deadline without a false stuck-router
  error; the existing positive-time genuinely-stuck-router test remains green.

Each acceptance test must be observed RED before its production change and GREEN afterward. The
final `./verify` run is the only completeness claim.

## File ownership

The implementation sub-agent may modify only:

- `src/planner/runtime/employee_step_runner.py`
- `src/planner/minds/shared_gateway.py`
- `src/planner/minds/sessions/service.py`
- `src/planner/minds/gateway.py`, only to make child process and reader-thread cleanup consume the
  remaining absolute shutdown deadline instead of reusing one relative grace
- `src/planner/core/db.py`, only to make the existing `busy_timeout_ms` argument also bound initial
  SQLite connection setup for shutdown's zero/remaining-time calls
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_core_loops.py`
- `tests/unit/test_minds_sessions.py`
- `tests/unit/test_minds.py`
- `tests/unit/test_db.py`, only for the existing connection busy-timeout argument's initial-connect
  behavior
- one new narrowly named server-shutdown test file if the real-process seam does not belong cleanly
  in `test_core_loops.py`
- `docs/employee-runtime.md` and `docs/systems.md` only where the live shutdown description changes
- artifacts inside `orchestration/tickets/t_s0q8d2ln-shutdown-zero-budget/`

Do not modify `src/planner/core/server.py`, `src/planner/core/loops.py`, configuration, migrations,
frontend code, or public contract/type files.

## Review and completion

- A planning sub-agent writes the TDD implementation plan.
- Codex reviews that plan against this contract and the original restart-recovery contract.
- A separate implementation turn follows the reviewed plan.
- Codex and the two-axis code review inspect the resulting diff.
- The orchestrator dispositions every finding, spot-checks deadline math and canonical Ticket
  settlement, runs one final `./verify`, updates memory, and commits the verified branch.
