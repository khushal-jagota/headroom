# t_hs01 — Add one ordered ingress for each live Hermes session

## Outcome

Each existing role-configured gateway child owns one lightweight Panels session object for every
live Hermes session it is currently using. That object receives the session's events once, in order,
and preserves Hermes's own submission and lifecycle observations without creating a Panels queue or
running-state authority.

This is the expand step. Existing human-chat and employee callers remain available until their
separate migration tickets land.

## Public contracts

- Implement the session boundary against `src/planner/minds/contracts.py` and the existing Hermes
  JSON-RPC event and response shapes. Do not change Hermes.
- Keep the current two-child role topology: one employee-configured gateway child and one
  Chief-configured gateway child.
- A live Hermes session has exactly one ordered ingress inside its owning role gateway. Product
  callers never create competing ingress streams.
- Preserve the exact `prompt.submit` disposition: `streaming`, `queued`, or `steered`.
- Preserve interrupt acknowledgement separately from later message/session lifecycle observations.
  An acknowledgement does not mean Hermes has finished unwinding.
- Register event ingress before releasing create/resume to a caller and register submission intent
  before writing `prompt.submit`, so immediate events cannot fall into a listener gap.
- A narrow per-session command lock may preserve the order Hermes receives submit/interrupt writes.
  It must not wait for locally inferred idle, retry an uncertain prompt, or maintain a FIFO.
- A live session may go dormant only after Hermes is observed idle and Panels has no pending product
  consequence. Its stored Hermes session ID survives and can reopen the same conversation.
- Transport death produces an explicit unknown/offline observation. Panels does not blindly retry a
  prompt whose delivery is uncertain.

## Red-first acceptance tests

1. Two callers using one live session cannot create two raw event drains; one ingress receives each
   Hermes event once and preserves order.
2. An event emitted immediately around create, resume, or submit acknowledgement is retained.
3. `streaming`, `queued`, and `steered` receipts remain distinguishable to the caller.
4. Interrupt acknowledgement leaves the session active until later Hermes observations establish
   its next state.
5. Two sessions in one role gateway remain independent, and Chief and employee sessions remain in
   their separate role gateway children.
6. An idle ingress detaches without deleting the stored Hermes session; reopening resumes the same
   session and reconciles its current history/running snapshot.
7. Gateway shutdown closes all live ingresses. Child death settles uncertain local work as
   unknown/offline and sends no automatic retry.

## Contract and implementation scope

- `src/planner/minds/contracts.py`
- The shared gateway/session transport boundary and its focused fake-gateway tests.
- Only compatibility glue needed to leave existing callers working during the expand phase.

## Explicit exclusions

- No Hermes source change, protocol extension, causal turn ID, database schema, frontend change, new
  queue, retry scheduler, or product transcript change.
- Do not combine the Chief and employee gateway children.
- Do not migrate human chat or employee workflow settlement in this ticket.

## Blocked by

None — can start immediately.
