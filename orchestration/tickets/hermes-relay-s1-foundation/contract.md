# Hermes relay S1 — foundation contract (revised: child-per-employee upstream)

Plan and grounding: `orchestration/hermes-relay-redesign/plan.md` and `s0-spike.md`.
Decisions bound here: `D-hermes-relay-architecture`, `D-child-per-employee`,
`D-runtime-neutral-vocabulary` is S2 — S1 forwards native frames verbatim,
`D-stock-hermes-only` (no Hermes-side change of any kind in this ticket).

Revision note: this supersedes the earlier draft whose upstream was one shared
`hermes serve` WebSocket backend. The upstream is now a pool of per-employee stdio
children. The downstream half (id namespacing, tee, routing shape) is unchanged.

## Outcome

Panels can own a pool of Hermes child processes — one per employee (entity), each holding
exactly one session — and relay each child's native JSON-RPC protocol between browser
clients and that child: many downstream connections, request ids namespaced per
downstream, frames routed by employee, and a tee seam observing every frame. Nothing
user-visible changes; the existing role-children gateway paths are untouched and remain
the only production consumers of Hermes in this stage.

## Existing contracts to preserve

- No file under `src/planner/minds/`, `src/planner/chat/`, or `src/planner/runtime/`
  changes. Importing `planner.minds.gateway` (`GatewayChild`, `SpawnFn`, `spawn_popen`)
  is expected; modifying it is not.
- `src/planner/core/server.py::create_app` remains the FastAPI composition owner; this
  ticket may only add mounting of the new route and lifecycle hooks for the new
  components, gated off by default.
- The application's one shutdown budget (`Config.shutdown_grace_seconds` discipline)
  governs the new components' shutdown too; no second deadline model.
- `./verify` stays green; every existing test passes unchanged.

## New components

New module area: `src/planner/hermes_backend/` (final module and class names are the
implementation plan's to propose, per the naming rules — descriptive, exactly what each
thing is).

### Employee child pool

- One child per employee entity, spawned on demand via the existing
  `planner.minds.gateway` spawn seam — `SpawnFn` / `ChildProcess` / `spawn_popen`,
  running `hermes_python -m tui_gateway.entry` — against the configured planner Hermes
  home. The relay speaks raw newline-delimited frames on the child's stdio itself.
  `GatewayChild`'s typed client methods are explicitly NOT the relay path: they rebuild
  request frames under their own ids, unwrap responses (dropping the frame), and consume
  `gateway.ready` (`gateway.py:139-155,273-277,335-358`), so they cannot carry frames
  verbatim. `GatewayChild` remains untouched, serving the legacy role children.
- Each child's spawn environment carries its identity: `PLAN_TICKET_ID` (the existing
  CLI convention, `src/planner/cli/main.py:27`) for ticket employees, and the actor
  distinction for the Chief — exact variable set is the implementation plan's to state.
- One child holds exactly one stored session. The pool maps employee → child and never
  lets two children own the same stored session. In S1 the pool creates fresh sessions
  or resumes only sessions it created; it must not resume sessions owned by the live
  role children (the store does not lock across processes — S0 phase 7).
- No idle reaping (`D-child-per-employee`): a child lives until its employee is
  released (ticket closed/dropped — not wired in S1), the pool shuts down, or the child
  dies. A dead child is respawned on next demand; session durability lives in the
  home's `state.db`, not the process.
- Pool shutdown terminates only its own children within the shared deadline.
- Config-gated and **off by default** in production config. When off, nothing spawns
  and the relay surface reports unavailable.
- The spawn seam is injectable (`SpawnFn`) so unit tests never launch a real child.

### Relay

- Exposes one downstream WebSocket route on the Panels FastAPI app (same
  authentication posture as the existing `/api/events` route).
- A downstream declares which employee entities it is following (subscribe message over
  the relay connection); the relay routes each subscribed employee's child frames to it.
  All frames from a child — including its process-level frames such as
  `gateway.ready` — are scoped to that child's employee, never broadcast.
- Per-downstream JSON-RPC request-id namespacing onto each child's id space, correct
  under concurrent interleaved requests from multiple downstreams to the same child; a
  downstream disconnect cancels only its own pending mappings.
- Downstream requests address an employee; the relay resolves employee → child (asking
  the pool to spawn on demand) and forwards. Child death surfaces to subscribed
  downstreams as an explicit child-reset event frame; a later request may trigger
  respawn. S1 does not resume the previous session on respawn unless the pool created
  it (see pool rules).
- A tee seam: registered observers see every frame in both directions, labeled with the
  employee. S1 ships the seam with test observers only — no product consumer.
- The relay never interprets, stores, or rewrites frame payloads — ids, the method
  name, and routing metadata only. Frames forward in child emission order: one reader
  per child, responses and events interleaved exactly as the child wrote them.

### Session lifecycle ownership

The pool owns which stored session a child serves; downstreams talk to an employee,
never to session lifecycle. The relay rejects session-binding methods arriving from a
downstream with a JSON-RPC error frame and does not forward them: `session.create`,
`session.resume`, `session.close`, `session.delete`, `session.activate`, plus any
further binding-capable verbs the implementation plan identifies by inspecting the
`tui_gateway` method registry (the plan fixes the exact set with justification). This
is a denylist, deliberately: every other native method flows verbatim, which is the
point of the relay — and the denylist is re-checked whenever the Hermes checkout
advances. Same-session operations that cannot rebind ownership (e.g. `session.steer`,
`session.compress`, `prompt.submit`, `clarify.respond`, `session.interrupt`) forward
untouched.

## Acceptance tests (named; all run inside `./verify`)

New unit test files (final names are the plan's, under `tests/unit/`):

1. Pool: spawn on demand with exact env (home, `PLAN_TICKET_ID`, actor), one child per
   employee, one session per child, no cross-employee session reuse, config-off means
   no spawn, dead-child respawn on demand, shutdown terminates all children within the
   deadline with a hung fake child.
2. Relay ids: two concurrent fake downstreams issue interleaved requests to the same
   employee; each receives exactly its own responses; the child sees one coherent id
   space; a downstream disconnect cancels only its pending mappings.
3. Relay routing: frames from a child reach only downstreams subscribed to that
   employee (including `gateway.ready`); the tee observer sees every frame both
   directions with employee labels.
4. Child death: mid-traffic child exit → subscribed downstreams receive the child-reset
   frame; a subsequent request respawns per pool rules.
5. Verbatim and order: frames with unknown top-level fields, id-less notifications, and
   error-response frames pass through byte-identical (modulo the id rewrite); a mixed
   burst of responses and events arrives downstream in exact child emission order.
6. Lifecycle policy: each denylisted session-binding method from a downstream is
   answered with an error frame and never reaches the child; a same-session
   non-binding method forwards untouched.

All against fakes (fake spawned children via the injectable `SpawnFn`); no test spawns
a real Hermes. The S0 spike script remains the manual real-backend proof.

## Out of scope

Chat pane changes, the neutral vocabulary, worker steps, any consumer swap, session
resume of role-children sessions, ticket-close child release wiring, transcript changes,
Hermes-side changes, `panels` CLI changes, production enablement.
