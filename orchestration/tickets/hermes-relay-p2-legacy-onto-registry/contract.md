# P2 — Legacy onto the shared registry (GatewayChild reader + per-employee rekey)

Contract-scoped ticket. Implements the P2 stage of
`orchestration/hermes-relay-redesign/shared-registry-plan.md` (read it + constraints C2, C3).
Builds on P1 (landed `1851a74`): `EmployeeChildRegistry` (`minds/employee_child_registry.py`) is the
provider-neutral per-employee child substrate; `ChildReader`/`ChildFrameSubscriber`/`ReaderFactory`
Protocols exist; the relay path already rides it. **Do not modify the registry** unless P2 reveals a
genuine P1 gap — if it does, STOP and flag it (don't reshape the registry to fit).

## Goal
Put the flag-OFF legacy path on the same registry — **one Hermes child per employee** (each active
ticket + the Chief), **one session per child** — so legacy is leak-safe by construction on stock
Hermes, identical in topology to the relay path.

## Owner ruling (2026-07-19) — DROP the shared-process mode; keep both communication methods
Two independent axes: (1) **process topology** — shared-process-multiplex (old) vs one-child-per-employee
(new); (2) **communication method** — relay (raw frames → browser, neutral panes) vs legacy (typed
JSON-RPC → `LiveSessionManager` → DB-translate → `ChatPanel`). The owner ruled: **delete the
shared-process topology outright — NO additive dual-mode / no vestige.** `GatewayChild` becomes
registry-spawned only; `SharedGateway`/`EntityRoutingGateway` become per-employee only; the
multi-session multiplex is removed. **Both communication methods are PRESERVED** — the relay path
(flag-on) is untouched, and the legacy path (flag-off) keeps its `LiveSessionManager` + DB-translate +
`ChatPanel` consumption; only its *transport* moves to per-employee. Both flag states survive (the A/B).

Consequence for the green guard: it is **no longer "zero test edits."** The specific unit tests that
assert the deleted shared-process / internal-spawn topology (`test_minds.py:1518` multi-session;
`test_minds_sessions.py:32`, `test_minds.py:114,1874` internal-spawn + auto-start) **are rewritten** to
the per-employee registry mode — they test behavior we are deliberately removing. The real
behavior-unchanged proof is the **flag-off e2e suite** (which exercises the full legacy communication
method end-to-end) staying green, plus the relay flag-on suite staying green. Do NOT edit any test
except the ones that assert the removed shared/internal-spawn topology; each such edit must be justified
in the plan as "asserts deleted behavior."

Two coupled moves (one ticket — old P2+P3 folded per the owner):

### (a) `GatewayChild` satisfies `ChildReader`, decoupled from spawn
`src/planner/minds/gateway.py`. Today `GatewayChild` spawns its own process internally via an injected
`SpawnFn` and owns the reader thread + `gateway.ready` gate + typed pending table +
`ChildSessionEventIngress`. Make it:
- accept a **pre-spawned `ChildProcess`** (the registry's `reader_factory` news it up); drop the
  internal argv build + `spawn()` call; keep the reader thread, ready gate, pending table, ingress.
- satisfy the `ChildReader` Protocol: `request(method, params, *, timeout, on_request_id)` with the
  **pre-send id hook** (id allocated at its current site, exposed before send), `register_frame_sinks`
  (a two-phase `on_deliver`/`on_fold` adapter over its session-event ingress + response path — you
  decide the honest mapping; the legacy consumer is `LiveSessionManager`, not the relay), `enqueue_frame`,
  a two-phase `start_reading`, `wait_ready`, `shutdown`, `alive`, `dead_event`.
- move process kill/wait/no-reap ownership to the registry (the registry owns the `ChildProcess`).
- update the 8 call sites: `shared_gateway.py:638`, `minds/runner.py:68`, `minds/config.py:105`,
  `minds/smoke.py:302/324/365/400`.

### (b) `SharedGateway`/`EntityRoutingGateway` rekey per-role → per-employee onto the registry
`src/planner/minds/shared_gateway.py` (+ `sessions/service.py`, composition). Today one child per ROLE
(`HERMES_TUI_SKILLS=<worker_role>`) multiplexes many sessions via `LiveSessionManager`. Make it one
gateway per EMPLOYEE, each holding exactly one session, spawned through an `EmployeeChildRegistry`
built with a **legacy identity-env strategy** and a **GatewayChild `reader_factory`**. The
`LiveSessionManager` per-session turn engine (admission/submission/settlement/ordering) STAYS; its
multi-session routing collapses to a single-entry lookup. `EntityRoutingGateway` routes every entity
to its own per-employee gateway via the registry.

## Constraints
- **C2** — the registry issues its session-lifecycle RPCs through `reader.request()` uniformly; both
  `GatewayChild` (legacy) and `RawFrameChildTransport` (relay) satisfy the one `ChildReader` interface.
- **C3 — identity env is a real semantics change, surface it.** The legacy strategy differs from the
  relay strategy (legacy sets `HERMES_TUI_SKILLS=<role>` and no `PLAN_TICKET_ID`; per-employee legacy
  now needs per-employee identity). Decide the exact legacy per-employee env (role/skill selection +
  the per-employee isolation the leak-safety needs) and STATE the flag-off behavior change plainly in
  the plan — do not launder it.

## Green guard (flag-OFF BEHAVIOR unchanged; only the shared/internal-spawn-asserting tests rewritten)
The behavior-unchanged proof is the **flag-OFF chat e2e** (full legacy communication method, end to
end) staying green with the transport moved per-employee: `tests/e2e/test_chief_of_staff.py`,
`test_live_chat_state.py`, `test_chat_images.py`, `test_flows_a.py`, `test_flows_b.py`,
`test_connection_status.py`, plus the flag-OFF arms of `test_hermes_backend_ticket_composition.py` /
`chief_composition.py`, and `test_employee_step_runner.py` / `test_employee_session_history.py`.
`test_minds.py` / `test_minds_sessions.py` **are updated** where — and only where — they assert the
deleted shared-process / internal-spawn topology (multi-session multiplex, internal-spawn+auto-start);
rewrite those cases to the per-employee registry mode and justify each edit in the plan. Add new unit
coverage for GatewayChild `ChildReader` conformance + the per-employee rekey. `./verify` green in BOTH
flag states (the relay P1 path must stay green — untouched).

## Must NOT touch
The registry (`minds/employee_child_registry.py`) internals; the relay path
(`hermes_backend/employee_child_pool.py`, `raw_frame_transport.py`, neutral vocabulary — except
composition additively wiring the shared registry factory); `config.yaml`; the Hermes checkout
(`~/.hermes/hermes-agent`); and any test EXCEPT the specific `test_minds`/`test_minds_sessions` cases
that assert the removed shared/internal-spawn topology.

## Notes for the planner
- This is large. You MAY stage the implementation internally, but it lands as one green wave (the
  owner folded old P2+P3 because they share the flag-off green guard).
- The sharp seams: (1) mapping `GatewayChild`'s typed `ChildSessionEventIngress` onto the two-phase
  `on_deliver`/`on_fold` the `ChildReader` subscription expects; (2) collapsing `LiveSessionManager`'s
  demux without disturbing its per-session ordering engine; (3) the C3 legacy identity env. Give each
  an explicit account for the Codex plan review.
- Read-only on the Hermes checkout. No git write commands.
