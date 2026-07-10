# t_hs03 — Move employee execution onto the ordered session ingress

## Outcome

An employee Ticket step settles only from the Hermes execution that follows its own accepted
submission. A completion from preceding human, memory, review, delegation, notification, or other
session activity cannot mark the Ticket errored or complete while the real employee prompt continues.

## Public contracts

- Implement against `src/planner/minds/contracts.py` and the existing employee runner/worker-chat
  contracts. Do not add a new Hermes identity or change Hermes.
- Preserve Hermes's submit disposition. A `queued` employee input owns the next user execution after
  the active execution; the active execution's terminal observation cannot settle it.
- A `steered` input is not treated as a separately running employee step and cannot claim a later
  independent completion.
- At most one independently consequential pending Panels submission may exist for a session. Panels
  does not turn Hermes's one merging pending slot into several Ticket operations.
- Employee workflow consequences remain Panels-owned: worker Chat projection, Ticket status, proposal
  handling, and runner claim/release rules keep their existing product meaning.
- Pending worker context is acknowledged only after the employee input is observed started/delivered,
  not merely because Hermes accepted it into a volatile queued slot.
- Unknown transport outcome settles coherently without blind retry or pretending another session
  completion belongs to the employee step.

## Red-first acceptance tests

1. While prior/internal session work is active, submit an employee step and receive `queued`; the
   prior work's completion does not settle the employee Chat turn or Ticket.
2. The subsequent employee start/deltas/completion settle the existing employee Chat and Ticket
   contracts exactly once.
3. Reproduce the reported immediate `0.0s` interrupted Ticket failure and prove the real employee
   response is no longer orphaned.
4. A `steered` receipt cannot masquerade as a separate employee result.
5. Worker context survives queue acceptance and is acknowledged on proved start/delivery; interruption
   or transport loss before delivery retains it.
6. Existing automatic steps, direct revision delivery, strict stored-session resume, shutdown drain,
   claim/release, and proposal settlement behavior remains green.

## Contract and implementation scope

- `src/planner/minds/contracts.py`
- Employee runner and worker Chat projection integration.
- Focused fake-gateway, employee-runner, revision, and worker-context regressions.

## Explicit exclusions

- No Hermes source change, protocol ID, new Ticket status/event, automatic retry, multiple-operation
  queue, frontend change, or alteration to readiness/claim rules.

## Blocked by

- `t_hs01 — Add one ordered ingress for each live Hermes session`

This migration may be planned independently from `t_hs02`, but both touch the same gateway integration
surface and must be integrated serially.
