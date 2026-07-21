# Shared employee-child registry — port legacy to per-child, keep both paths

Owner decision (2026-07-19): port the legacy (flag-off) path to one-child-per-employee, the
same topology the live relay (flag-on) path already uses, **by extracting a shared
per-employee child registry both paths ride**. Keep both paths — we lose little by keeping
legacy once it is per-child, and it lets the owner A/B the DB-translate transport against the
relay on an identical child substrate. The payoff: with both paths per-child, the leak the
three local Hermes patches fix becomes impossible by construction in **both** flag states, so
`S3b` (revert to stock Hermes) is safe without deleting legacy or flipping the committed
default.

## The reshaped picture (from the extraction map)

The relay path does **not** use `GatewayChild`. It uses `RawFrameChildTransport`
(`hermes_backend/raw_frame_transport.py`), a deliberate **peer** of `GatewayChild` built on the
*same* spawn seam (`spawn_popen` + the `ChildProcess` protocol in `minds/gateway.py`). Both
readers spawn the identical process (`hermes_python -m tui_gateway.entry`) internally and each
owns process lifecycle. So today there are two per-child readers that each duplicate
spawn+identity+lifecycle; the reader half (raw frames vs typed JSON-RPC) is what legitimately
differs.

**What we extract** = the spawn/identity/lifecycle/session-RPC half both duplicate.
**What stays per-path** = the stdout reader (raw frame vs typed) and everything above it
(relay + neutral vocab, or `LiveSessionManager` + DB-translate).

## The shared registry

`minds/employee_child_registry.py` · `EmployeeChildRegistry`, keyed by `employee_entity_id`.
Placed in `minds/` because `hermes_backend/*` already imports from `minds/` (never the reverse)
and `minds/shared_gateway.py` must also consume it — `minds/` is the only acyclic home.

Owns (lifted from `EmployeeChildPool`'s lifecycle half):
- keyed spawn-on-demand + respawn-dead, generation counter
- identity env as an **injected strategy** (not hardcoded) — see constraint C3
- durable-session adoption/resume; sole issuer of `session.create/resume/close/interrupt`
- its own request/reply primitive over the reader
- stored/live session-id maps, per-employee rebind, fail-closed persistence callbacks
- no-reap lifecycle, one-deadline shutdown-all

Depends on an injected **reader** satisfying a uniform interface (proposed `ChildReader`):
`request(method, params, timeout, on_request_id) -> result`, ordered per-frame subscription,
`wait_ready()`, `alive`/death signal, `send`, `shutdown(deadline)`. `GatewayChild` (typed) and
`RawFrameChildTransport` (raw) both become implementations.

## Load-bearing constraints (must survive)

- **C1 — arming order.** `TurnSubmission.observe_ack_frame` (step-ACK settlement) is correct
  only because session RPCs, relay delivery, and step folding share one reader thread in
  emission order *and* the request id is handed out **pre-send**. The registry must expose an
  ordered per-frame callback and a pre-send id hook, or the pre-ACK-terminal accounting
  re-opens a hang. (Extraction-map risk #1.)
- **C2 — unified request/reply.** The registry issues its own session RPCs and reads responses;
  today that is `_transport_request`+`_PoolSessionResponder` (raw) vs `GatewayChild.request`
  (typed). The registry is reader-agnostic only if both readers expose a uniform `request()`.
  Folding the pool's request/reply into the raw reader is part of P2. (Risk #2.)
- **C3 — identity env is real behavior, not cosmetic.** Legacy sets `HERMES_TUI_SKILLS=role`
  and no `PLAN_TICKET_ID`; relay sets `PLAN_TICKET_ID=<employee>` and scrubs skills. The
  registry takes identity-env as an injected strategy; unifying changes how the **legacy** path
  selects skills — a deliberate flag-off semantics change, surfaced not laundered. (Risk #3.)
- **C4 — lifecycle move.** Moving no-reap + shutdown-all to the registry must preserve the
  one-shared-deadline discipline and the never-join-an-unstarted-thread guard. (Risk #4.)
- **C5 — fail-closed persist.** `on_stored_session_bound` runs inside the spawn/rebind guard;
  a persist failure must still tear the child down (keeps the two-owner invariant). (Risk #5.)
- **C6 — the on_frame weld.** The pool's one `on_frame` fans a single raw stream to three sinks
  (relay deliver, session-RPC responder, step fold) in a load-bearing order. Re-express as
  "registry emits ordered frames → consumer subscribes" without losing that order. (Risk #6.)

## Stages (serial — all touch the gateway substrate; no parallelism; per-wave commits)

- **P1 — Extract the registry; relay rides it; flag-on unchanged.** Introduce
  `EmployeeChildRegistry` + the `ChildReader` interface; fold the pool's request/reply into the
  raw reader so it satisfies `ChildReader`; re-point `EmployeeChildPool` onto the registry as a
  thin relay consumer (TurnSubmission + relay wiring + `submit_step_prompt`). Legacy untouched.
  **Green guard (zero test edits):** entire flag-ON set — `test_hermes_backend_pool`,
  `test_pool_ticket_adoption`, `test_pool_step_gateway`, `test_hermes_backend_ticket_step_composition`,
  `relay_routing`/`relay_ids`/`verbatim_order`, e2e `test_chief_neutral_pane` + `test_ticket_neutral_pane`.
  Validates the registry against the known-good path. Exercises C1/C4/C5/C6.
- **P2 — Legacy onto the registry** (one ticket; the old P2 "GatewayChild reader" and P3
  "per-employee rekey" are folded — decoupling `GatewayChild` from spawn has no value except to
  let legacy ride the registry, and both share the one flag-off green guard). Two coupled moves:
  (a) make `GatewayChild` satisfy `ChildReader` — constructor accepts a pre-spawned
  `ChildProcess`, drop internal argv/`spawn()`, keep the reader thread/ready gate/pending
  table/ingress, move process kill/wait/no-reap to the registry; update 8 call sites
  (`shared_gateway.py:638`, `runner.py:68`, `config.py:105`, `smoke.py:302/324/365/400`); and
  (b) rekey `SharedGateway`/`EntityRoutingGateway` to one gateway per employee, each holding one
  session, spawned through the registry with the unified per-employee identity env;
  `LiveSessionManager` per-session engine stays, its multi-session demux (~18-22%) collapses to a
  single-entry lookup; `_build_role_gateways`/`entity_gateways` wiring updated. Exercises C2 + C3.
  **Green guard (flag-off unchanged):** `test_minds`, `test_minds_sessions`,
  `test_employee_step_runner`, flag-off composition, plus flag-off chat e2e
  (`test_chief_of_staff`, `test_live_chat_state`, `test_chat_images`, `test_flows_a`,
  `test_flows_b`).
- **S3b — Revert to stock Hermes (orchestrator does this directly, not a sub-agent).**
  `git revert -m 1 047ba8298` in `~/.hermes/hermes-agent` (confirmed clean isolated bubble;
  pre-patch parent `3a1a3c7e6`; touches only `tools/environments/base.py` +
  `tests/tools/test_local_env_session_leak.py`). Then `./verify` green in BOTH flag states.
  Committed default may stay flag-off — both paths are now leak-safe on stock Hermes.

## Per-ticket pipeline (each stage)
Plan (Opus) → Codex plan review vs this doc + the contracts → implement (Opus) → Codex diff
review → orchestrator integrates serially + full `./verify` + per-wave commit. The reader
interface + C1 arming order + C2 request/reply unification get explicit Codex design scrutiny
at the P1 plan review — that is the highest-risk seam.
