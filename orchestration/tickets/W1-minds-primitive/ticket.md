# W1 — Agent-operation primitive (`minds/` module)

## Scope

Build the standalone agent-operation primitive that drives one Hermes "mind" over the Option 3
stdio JSON-RPC gateway, exactly as specified and **already validated** in
`orchestration/runtime-redesign/spikes/01-hermes-linkage.md`. Purely **additive**: a new
`src/planner/minds/` package plus one unit-test file. This wave does **NOT** wire the primitive
into the server, dispatcher, DB, or CLI — that is Wave 3. Do not modify any existing file.

The whole point: a later wave calls `run_step(...)` to set an agent off on one step and observe
it end. This wave delivers that primitive + a fake-gateway test double so it is unit-tested with
**no real gateway and no model calls**.

## Contracts (read fully before planning — they are law)

- `orchestration/runtime-redesign/notes.md` → **Agent runtime** (Option 3 connection; per-session
  serialized queue; the "Agent-operation primitive" VALIDATED block) and **Scheduling & runs**
  (System B topology; `run_step`). This is the design intent.
- `orchestration/runtime-redesign/spikes/01-hermes-linkage.md` → §1 evidence transcripts (the real
  frame shapes, method names, event names, error codes, timings) and §4 "The plan" (GatewayChild,
  env assembly, run_step, serialization). This is the concrete blueprint + protocol ground truth.
- **Protocol ground truth (read-only research, AUTHORIZED):** `~/.hermes/hermes-agent/tui_gateway/`
  — `transport.py` (framing), `server.py` (the `@method(...)` table + event emission), `entry.py`
  (the stdio loop). Match the REAL method/event/error names against source; do not invent shapes.

## Files owned (new only — create these, touch nothing else)

- `src/planner/minds/__init__.py`
- `src/planner/minds/gateway.py` — `GatewayChild`: spawn `<hermes_python> -m tui_gateway.entry`
  with pipes + assembled env (`HERMES_PYTHON_SRC_ROOT`, `HERMES_HOME`, `HERMES_TUI_SKILLS`, and a
  slot for per-run context env vars); a reader thread that turns stdout lines into frames, routing
  JSON-RPC **responses** (keyed by `id`) vs **events** (`gateway.ready`, `session.info`,
  `message.start/delta/complete`, `error`, …) correctly; await `gateway.ready`; a request→response
  call keyed by id; shutdown = close stdin, wait with a short grace, then kill. stderr tailed to a
  log buffer.
- `src/planner/minds/runner.py` — `run_step(session_key | None, role, prompt_text, on_event, *,
  home, hermes_python, context_env=None) -> RunResult`: spawn child with role env → await ready →
  `session.resume {session_id: session_key}` (or `session.create` for step 0) → `prompt.submit` →
  drain events (deltas / `tool.*` → `on_event`) → the single `message.complete`
  (`status ∈ {complete, interrupted, error}`, capture `text`/`usage`) OR an `error` event OR child
  death → `RunResult`. Reap the child. `RunResult` = dataclass `{status, text, usage, session_key,
  error}` where `status ∈ {complete, interrupted, errored}` and `session_key` is the durable
  `stored_session_id` (persisted by the caller in a later wave).
- `src/planner/minds/queue.py` — the per-session serialized queue: **one in-flight run per
  `session_key`** (the load-bearing correctness property — the gateway busy-guard is per-process
  only). Producers (chat / next-step / wake) enqueue a request; a drainer runs `run_step` when no
  run is live for that key; different keys may run concurrently.
- `src/planner/minds/fake.py` — a fake-gateway double: an injectable fake transport/child that
  emits canned frame sequences (`gateway.ready`; `session.create`/`resume` results; `message.start`
  + deltas + `message.complete`; `error` events; `4009` busy; `4007` not-found) so `runner` and
  `queue` are unit-tested deterministically with **no subprocess, no model calls**. Design
  `GatewayChild`/`run_step` so this double is injectable (e.g. a transport factory / spawn hook).
- `src/planner/minds/config.py` — resolve the hermes interpreter + home: config value with the
  `~/.hermes/hermes-agent/venv/bin/python` default; a cheap boot smoke-check helper
  (launch → `gateway.ready` → exit) usable later. (Do not call it from anything this wave.)
- `tests/unit/test_minds.py` — unit tests against the fake double:
  - `run_step` happy path: create → prompt → `message.complete{complete}` → `RunResult.complete`
    with `session_key` captured.
  - resume path: a non-None `session_key` issues `session.resume` (not create).
  - error mapping: `error` event → `errored`; child death mid-run → `errored`; `message.complete`
    `status=interrupted` → `interrupted`; unknown-skill `error` frame → `errored` (loud).
  - queue serialization: two producers on the SAME `session_key` → never two runs in flight at
    once (assert ordering/one-at-a-time); DIFFERENT keys → may overlap.
  - frame routing: responses-by-id vs events dispatched correctly; a `4009` busy response handled.
  Real assertions, no `skip`/`xfail`/empty bodies (the verify instrument scans for these).

## Behavior notes

- **Child-per-run topology.** `run_step` spawns a fresh child, does exactly one run, reaps it.
  Role = the `HERMES_TUI_SKILLS` env (per gateway process). Per-ticket context rides `context_env`.
- **No wiring this wave.** Nothing imports `minds/` yet; no server/DB/dispatcher/CLI changes. The
  package is importable and fully unit-tested in isolation.
- **Verify stays hermetic.** The verify-gated tests use ONLY the fake double. The REAL-gateway
  smoke test goes in a **separate** `src/planner/minds/smoke.py` (or `scripts/minds_smoke.py`) that
  a human runs top-level (mirrors the spike) — it is NOT collected by pytest / the verify gate.
- Stdlib only (`subprocess`, `threading`, `json`, `queue`) — no new dependencies. Match the repo's
  existing ruff + mypy strictness (`mypy src/` must stay clean).

## Acceptance for integration

- `.venv/bin/ruff check .` clean; `.venv/bin/mypy src/` clean.
- `.venv/bin/pytest tests/unit/test_minds.py -q` green — every test above, real assertions.
- Full `./verify` stays green (additive change; nothing else touched).
- Integrator (me) will spot-check: frame response-vs-event routing, `run_step` status mapping, and
  the queue single-in-flight-per-`session_key` invariant.

## Boundaries

Work only inside this repo. Read-only research of `~/.hermes/hermes-agent/tui_gateway/` is
authorized for protocol ground truth. Never run the real gateway inside a verify-gated test. Never
modify anything under `~/.hermes`. Never run git.
