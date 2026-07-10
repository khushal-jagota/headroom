# t_arch01 — Name and deepen employee runtime ownership

## Outcome

Replace the opaque System A/System B split with two responsibility-named runtime modules:

- `TicketReadinessLoop` discovers automatically runnable Tickets on today's board.
- `EmployeeStepRunner` owns accepting and running one employee step, including Review revision turns.

An accepted Review return-for-revision request must always have an in-process runner handoff before the Ticket is changed. The readiness loop may be disabled or owned by another process without making direct revision guidance disappear.

## Public contracts

- The employee runner is constructed whenever the worker gateway is constructed. It is independent of the dispatch flag and machine polling lock.
- The readiness loop remains optional behind the dispatch flag and machine lock.
- Automatic discovery calls one descriptive runner operation, `run_ready_step(ticket_id)`. The runner performs the execution-time eligibility check, claims the Ticket, builds the prompt, resumes/creates the Hermes session, owns worker Chat state, and settles the result.
- Direct revision uses a two-phase in-process handoff: reserve an employee revision turn before the canonical Ticket mutation, then release it only after commit; cancel it if mutation fails.
- A reservation is accepted only after the runner owns a parked execution thread. Runner shutdown rejects new reservations and drains accepted work before the worker gateway shuts down.
- The direct revision action sends the human guidance through the real Hermes worker session. A Panels chat row is not delivery and must not be added as a substitute.
- Revision resumes the stored Hermes session strictly. A stale/missing stored session must never fall back to creating a replacement session; the accepted async run settles as errored without submitting the revision prompt.
- A running Panels chat turn that already owns the Ticket is rejected inside the canonical revision transaction before Ticket mutation. If a turn wins the narrow race after commit but before the worker turn opens, the runner settles the claimed Ticket as errored instead of leaving it stuck.
- Missing, stopped, or unavailable runner returns the existing gateway-unavailable contract before Ticket state, proposal, events, or chat change.
- The runner rechecks the complete automatic eligibility condition, including membership on today's board, immediately before claim. A stale discovery cannot start work after day removal.
- Rename the live source/tests/docs/config terminology. Historical orchestration records keep the names that were true when written.

## Red-first acceptance tests

1. Through FastAPI with a real runner and fake worker gateway, while no readiness loop exists, returning a Review for revision:
   - returns success once the handoff is accepted, before Hermes finishes;
   - resumes the existing employee session;
   - delivers the exact framed guidance to Hermes;
   - clears the proposal and claims the Ticket once;
   - adds no human-guidance copy to Panels chat;
   - settles the revised worker reply/proposal normally.
2. A missing or stopped runner returns HTTP 503 and leaves proposal, status, events, worker context, and chat unchanged.
3. A DB validation failure after reservation cancels the handoff, submits no Hermes prompt, and leaves no active reservation.
4. With dispatch disabled, the runner exists, the readiness loop does not, and no polling lock is acquired.
5. When another process owns the polling lock, the runner still exists and the readiness loop does not.
6. Shutdown rejects new reservations and drains an already accepted reservation/run before gateway shutdown.
7. The readiness loop invokes only `run_ready_step(ticket_id)`; prompt construction and the execution-time check live in the runner.
8. If a Ticket is discovered and then removed from today's board before claim, no worker prompt is submitted.
9. Preserve current revision semantics: existing session required, same-session resume, Review stage preserved, duplicate send is `already_running`, and no Panels chat copy.
10. A stale Hermes session key causes no `session.create` and no revision prompt; after the accepted asynchronous handoff settles, the Ticket is coherently errored rather than reminted onto a new session.
11. A pre-existing running Panels chat turn returns `already_running` with Ticket/events/chat unchanged and the reserved handoff cancelled. A turn-start collision after commit submits no prompt and settles the claimed Ticket as errored.

## Contract and implementation scope

- Rename `src/planner/runtime/system_a.py` and `system_b.py` to responsibility-named modules and rename their classes/tests.
- Add only the narrow runner/revision-handoff protocol needed by the Ticket action seam.
- Add or extend `src/planner/tickets/actions.py` for return-for-revision coordination.
- Update `src/planner/core/loops.py`, `src/planner/core/server.py`, runtime exports, Ticket API/data seams, and the live documentation/configuration that currently says System A/System B.
- Keep prompt delivery at `SharedGateway.run_ticket_step`; preserve the pending worker-context boundary there.
- Permit only the narrow SharedGateway strict-existing-session option needed by revision delivery; automatic steps retain create-or-resume behavior and pending worker context remains at the same submit boundary.

## Explicit exclusions

- No durable queue, crash recovery, retry scheduler, new database state, new Ticket status/event, or cross-process doorbell.
- No SharedGateway completion-correlation fix or other gateway behavior change beyond strict no-create revision resume; no gateway composition redesign, Review UI work, readiness-rule expansion, or frontend change.
- No candidate 4 or candidate 6 work.
