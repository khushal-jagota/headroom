# P1 — Extract `EmployeeChildRegistry`; relay rides it; flag-on unchanged

Contract-scoped ticket. Implements the P1 stage of
`orchestration/hermes-relay-redesign/shared-registry-plan.md`. Read that plan and the two
constraints it names for this stage (C1 arming order, C4 lifecycle move, C5 fail-closed persist,
C6 on_frame weld) before planning.

## Goal
Lift the spawn/identity/lifecycle/session-RPC half out of `EmployeeChildPool` into a new
provider-neutral, reader-agnostic `EmployeeChildRegistry`, and re-point the pool onto it as a
thin relay consumer. The relay (flag-on) path's observable behavior is **unchanged** — proven
by the existing flag-on test set passing with **zero edits**.

## New / modified files (scope — touch nothing else)
- NEW `src/planner/minds/employee_child_registry.py` — `EmployeeChildRegistry` (keyed by
  `employee_entity_id`) + a `ChildReader` Protocol (the uniform reader interface:
  `request(method, params, timeout, on_request_id) -> result`, ordered per-frame subscription,
  `wait_ready()`, `alive`/death signal, `send`, `shutdown(deadline)`) + an injected
  identity-env strategy type. The registry owns: keyed spawn-on-demand + respawn-dead,
  generation counter, `adopt_stored_session`, sole issuance of
  `session.create/resume/close/interrupt`, its request/reply primitive over the reader,
  stored/live session maps, per-employee rebind, fail-closed `on_stored_session_bound`,
  no-reap, one-deadline shutdown-all.
- `src/planner/hermes_backend/raw_frame_transport.py` — make the raw transport satisfy
  `ChildReader`: fold in the pool's request/reply (today `_transport_request` +
  `_PoolSessionResponder`, `employee_child_pool.py:757-812,74-88`) so the raw reader itself
  exposes `request()` and the ordered pre-send id hook (C1/C2).
- `src/planner/hermes_backend/employee_child_pool.py` — becomes a thin **relay consumer** over
  the registry: keep `TurnSubmission` (frame fold + ACK disposition + settlement,
  `91-233`), the turn-submission map, `submit_step_prompt`, and all `EmployeeChildRelay`
  wiring. Delegate spawn/identity/session-RPC/lifecycle to the injected registry. Re-express
  the three-sink `on_frame` weld as "registry emits ordered frames → pool subscribes" without
  changing fan-out order (C6).
- `src/planner/hermes_backend/composition.py` — construct the registry (with the raw
  `ChildReader` impl + the relay identity-env strategy) and inject it into the pool. Additive
  wiring only.
- `src/planner/hermes_backend/pool_step_gateway.py` — only if the consumer surface shifts; its
  external `StepGateway` surface (`run_ticket_step`/`interrupt`/`status`) must be byte-unchanged.

## Must NOT touch
`minds/gateway.py` (that is P2), `minds/shared_gateway.py`, `minds/sessions/service.py`, the
legacy path, any `contracts.py`, the neutral vocabulary, `core/server.py` composition of legacy
gateways. Do not edit any existing test to make it pass.

## Acceptance
1. The entire flag-ON set passes with **zero edits to existing tests** (the unchanged proof):
   `tests/unit/test_hermes_backend_pool.py`, `test_pool_ticket_adoption.py`,
   `test_pool_step_gateway.py`, `test_hermes_backend_ticket_step_composition.py`,
   `test_hermes_backend_relay_routing.py`, `test_hermes_backend_relay_ids.py`,
   `test_hermes_backend_verbatim_order.py`, `test_hermes_backend_child_death.py`,
   `test_hermes_backend_scripted_child.py`; e2e `tests/e2e/test_chief_neutral_pane.py`,
   `tests/e2e/test_ticket_neutral_pane.py`.
2. NEW unit tests for `EmployeeChildRegistry` in isolation (spawn/reuse/respawn, identity-env
   strategy application, adopt/resume, session-RPC issuance, fail-closed persist, no-reap,
   shutdown-all one-deadline). Add a mutation-style proof for C1: the pre-send id hook + ordered
   frame callback preserve step-ACK arming.
3. `./verify` green.

## Notes for the planner
- The registry depends only on the `ChildReader` **Protocol** (in `minds/`); the concrete raw
  reader stays in `hermes_backend/` and is injected at composition — keeps `minds/ ← hermes_backend/`
  acyclic. GatewayChild becomes the second `ChildReader` impl in P2 (out of scope here).
- Identity env is an **injected strategy** even though only the relay strategy exists now — P3
  injects the legacy strategy. Do not hardcode `_env_for_employee` into the registry.
- The highest-risk seam is C1 (arming order) + C2 (request/reply on the raw reader). Give the
  plan an explicit account of how ordered per-frame delivery and the pre-send request id are
  preserved once the request/reply lives in the reader and the fan-out lives in the pool.
