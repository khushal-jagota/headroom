# P2 Implementation Plan — Legacy onto the shared registry (GatewayChild reader + per-employee rekey)

Implements P2 of `orchestration/hermes-relay-redesign/shared-registry-plan.md` (C2, C3) on top of P1
(`1851a74`), **revised for the owner ruling of 2026-07-19 and the Codex plan review (round 1).** All
file:line anchors are against the current tree. The registry (`minds/employee_child_registry.py`) is
**not modified**; where P2 needs something it doesn't expose, this plan flags it as a candidate P1 gap
(§8) rather than reshaping it.

## 0. The overriding decision + scope

**Drop the shared-process topology outright — no dual-mode, no vestige.** Two independent axes stay
distinct: process topology (shared-process-multiplex **DELETED** → one-child-per-employee only) and
communication method (relay flag-on **untouched**; legacy flag-off keeps its full typed →
`LiveSessionManager` → DB-translate → `ChatPanel` path; only its *transport* moves per-employee onto the
registry). Both flag states survive.

**Owner ruling on the catalog/status source (item C — supersedes the earlier standalone-catalog
gateway):** **no standalone catalog gateway; drop EVERY shared process, no exception.** The flag-off
command catalog is sourced **per-employee** from the employee's own child once it exists. Before the
first message (no child yet), the `/` menu shows only the built-in commands (compact / new / interrupt —
Panels-side, always available); Hermes skills simply don't appear until the child is spawned. The owner
confirmed this graceful degradation is acceptable (future per-worker-type skill configs are noted, **not
now**). Gateway **status** is sourced independently of any catalog/child (Codex should-fix #1) so a
missing/not-yet-spawned child never rejects healthy per-ticket work.

**The multiplex is CONSTRAINED away, not left dormant (Codex #4).** `SharedGateway` is made structurally
incapable of binding a second concurrent session (one employee/session per gateway); the
`LiveSessionManager` per-session admission/settlement engine stays. This replaces the earlier
"re-home a two-bind test" idea.

**Scope note (surfaced, not buried).** The C ruling + Codex blockers push this ticket **beyond the
contract's "Must NOT touch" list.** In addition to `minds/`, `core/server.py`, and the named
`test_minds`/`test_minds_sessions` cases, P2 now must touch: the catalog API (`chat/api.py`) + its
frontend (`web/src/lib/resourceCatalogue.ts`, `ChatComposer`), a new narrow day-session-key DB writer,
the composition ownership guard + its flag-OFF guard tests (`test_hermes_backend_ticket_composition.py`,
`test_hermes_backend_chief_composition.py`), the `minds smoke --concurrency` tool, and a new
production-like flag-off integration test. Each is justified below; flagged for the lead as a deliberate
scope expansion the owner's ruling requires.

**Green guard.** The behavior-unchanged proof is (1) a NEW production-like flag-off integration test
that actually drives `GatewayChild → LiveSessionManager → DB translation` through the registry (§5.1 —
the ordinary e2e run under `PLAN_TEST_MODE=1` uses `EchoGatewayAdapter` and does **not** exercise the
legacy transport, so it is *not* the proof, Codex #2), plus (2) the relay flag-on suite staying green.
Tests that assert the deleted shared/internal-spawn/multiplex topology are rewritten, each justified as
"asserts/uses deleted behavior" (§5.2). `sessions/service.py` and `employee_child_registry.py` are not
edited.

---

## 1. `GatewayChild` → registry-spawned `ChildReader` (internal-spawn removed)

`src/planner/minds/gateway.py`. Today the ctor builds argv (`:229`) + spawns internally (`:230-233`) and
auto-starts the reader threads (`:245-252`); it owns the ready gate, the typed pending table +
`request`/`begin_request`, and the session/process ingress.

### 1.1 Constructor — pre-spawned child only
```python
def __init__(self, child: ChildProcess, *, stderr_tail_lines: int = STDERR_TAIL_LINES) -> None:
```
Drop `hermes_python`/`env`/`spawn`, the argv build and the `spawn()` call. Reader threads are **not**
started in `__init__` (two-phase). Set no-op frame sinks in `__init__`. Keep the pending table, ready
gate, typed session/process ingress, `_dead`, stderr tail — they are the legacy comm method's substrate.

### 1.2 Two spawn helpers sharing one primitive (Codex #9)
`_spawn_child_process(hermes_python, env, spawn) -> ChildProcess` — the single spawn primitive: build
`argv = [hermes_python, "-m", "tui_gateway.entry"]` (match `:229`), `spawn(argv, dict(env))`, translate
`OSError → GatewayError` (match `:230-233`). Used by **both** callers below, so spawn failure is a
`GatewayError` everywhere (the `run_ticket_step`/`run_human_turn` `except GatewayError` at
`shared_gateway.py:307,329` catches it; a raw `OSError` would escape).
- `spawn_gateway_child(hermes_python, env, *, spawn=spawn_popen) -> GatewayChild` (standalone callers):
  `_spawn_child_process(...)` → `GatewayChild(child)` → `start_reading()` → return. Reproduces today's
  spawn+auto-start behavior exactly.
- The **registry legacy factory** (§2.1) does NOT use `spawn_gateway_child`: it calls
  `_spawn_child_process(...)` → `GatewayChild(child)` → `register_frame_sinks(...)` → returns (the
  registry itself calls `start_reading`/`wait_ready`, `employee_child_registry.py:321-322`).

### 1.3 Lifecycle interlock + shutdown-once (Codex #8 — the registry shutdown race)
The registry publishes a **partial** reader before `start_reading` (`employee_child_registry.py:302-310`)
and can concurrently shut it down (`…:528`), and failure cleanup calls `shutdown` again (`…:336-337`).
Idempotent-start + guarded-join is not enough. Give `GatewayChild` the **exact** interlock
`RawFrameChildTransport` already has (`raw_frame_transport.py:156-171,329-378`):
- `_lifecycle_lock`, `_stdout_started`, `_shutdown_begun`, `_teardown_complete`.
- `start_reading()` under the lock: **raise `GatewayError`** if `_shutdown_begun` (never start a reader
  on an already-closing child → no full-ready-timeout block); else start once, set `_stdout_started`.
- `shutdown(*, deadline)` **leader/follower**: the first caller (leader) sets `_shutdown_begun`, wakes
  `_dead`/ready gates immediately, runs the full teardown once (close_stdin/kill/wait/join, guarding the
  stdout join on `_stdout_started`), then sets `_teardown_complete`. A follower waits on
  `_teardown_complete` up to its own remaining deadline and force-kills if still alive — no
  double-teardown, no double-join. Mirror `raw_frame_transport.py:329-378`.

### 1.4 `request()` — the pre-send id hook (C2 uniform)
Add `on_request_id` to `begin_request` (optional, keyword), fired after id allocation (`:344-345`) and
**before** the send (`:352`); `request` forwards it. GatewayChild owns its own id space (`_next_id`,
`:237`); it needs **no** `allocate_request_id` (not a `ChildReader` member,
`employee_child_registry.py:68-75`). `LiveSessionManager` calls `begin_request` without `on_request_id`
— unaffected.

### 1.5 `register_frame_sinks` — the honest two-phase mapping (SEAM #1)
Rework `_stdout_loop` (`:256-287`) to emit `on_deliver` (sink1, no-op for legacy) → **internal typed
routing (sink2, unchanged)** → `on_fold` (sink3, no-op for legacy), mirroring
`raw_frame_transport.py:198-201`. The internal routing (`:270-286`: ready gate, session-event→
`_child_session_events`, process-event→`_process_events`, response→`_pending`) is **untouched**, so
`LiveSessionManager` (which claims `_child_session_events`, `sessions/service.py:284`) rides the pull
ingress unchanged — this keeps its per-session engine intact (SEAM #2, §2.3). `on_dead` is invoked from
`_on_child_dead` (`:289`) alongside the existing pending-fail/ingress-close/process-close. The legacy
subscriber is a no-op (§2.1); the ordered subscription is real but the legacy consumer deliberately
rides the pull ingress. Confirmed safe by Codex (typed pending/ingress/deliver→routing→fold preserve
`LiveSessionManager` behavior).

### 1.6 Rest of the `ChildReader` surface
`enqueue_frame(frame)` (write under `_send_lock`); `dead_event` property → `self._dead` (`:243`);
`wait_ready`/`shutdown(*, deadline)`/`alive` already conform.

### 1.7 Process kill/wait ownership → the registry
Lifecycle decisions (spawn/respawn/teardown) move to the registry; the mechanical close/kill/wait/join
stays in `GatewayChild.shutdown` (now leader/follower, §1.3). No `ChildProcess` handle leaves
GatewayChild.

### 1.8 The 7 standalone call sites (not 8 — arithmetic corrected per Codex)
Route each through `spawn_gateway_child`: `shared_gateway.py:638` (direct-mode branch, §2.2),
`runner.py:68`, `config.py:105`, `smoke.py:302,324,365,400`. (`smoke.py:324` also carries the deleted
multiplex use — §5.2.)

---

## 2. Per-employee rekey (registry-backed; the multiplex constrained away)

### 2.1 The legacy registry wiring (NEW `minds/legacy_registry.py` or in composition)
Build **one** `EmployeeChildRegistry` (legacy), mirroring the relay pool (`employee_child_pool.py:249-259`)
but with a `GatewayChild` reader + a no-op subscriber:
- `reader_factory` — `_spawn_child_process(hermes_python, env, spawn)` → `GatewayChild(child)` →
  `register_frame_sinks(on_deliver=on_deliver, on_fold=on_fold, on_dead=on_dead)` → return. **No
  `allocate_request_id`** (GatewayChild owns its id space). Faithful `OSError → GatewayError` via the
  shared primitive (Codex #9).
- `subscriber` — `LegacyChildFrameSubscriber`: all methods no-ops (no relay; frames ride the typed
  ingress). Satisfies `ChildFrameSubscriber` (`employee_child_registry.py:97-121`).
- `identity_env_strategy` — legacy per-employee env (C3, §3). **Legacy SETS `HERMES_TUI_SKILLS=<role>`**
  (the relay strategy POPS it, `employee_child_pool.py:302`).
- `session_source="planner"`, `session_cols=100` — a **single** source per child (Codex confirmed I
  safe; no flag-off test asserts the source; the `planner-chat` label collapses, surfaced).
- `on_stored_session_bound` / `stored_session_resolver` — chief/ticket/**day** DB adapters (§2.6),
  mirroring `hermes_backend/composition.py:68-121`.

### 2.2 `SharedGateway` — per-employee, single-session-constrained (Codex #4)
`SharedGateway.__init__` gains optional `registry` + `employee_entity_id` (signature otherwise unchanged,
so the ~40 direct-construct unit tests keep compiling). **New invariant, enforced in both modes:** a
`SharedGateway` serves exactly **one** employee/session. It latches its entity on first use (registry
mode: the constructed `employee_entity_id`; direct mode: the first `entity_id` passed to
`run_ticket_step`/`run_human_turn`); any op for a **different** entity raises a new
`SharedGatewaySingleEmployee` error, and `_resume_or_create`/`_bind_live_session` refuse to introduce a
**second concurrent** stored key (a `/new` rebind that replaces the single binding is allowed — it does
not coexist). This makes multiplexing structurally impossible; the single-entity tests (one entity per
gateway, `/new` = replace) are unaffected, and the multi-entity tests (`test_minds.py:761,914,1518,2003`)
now fail at the second entity → they are the rewrites (§5.2).

**Registry mode** (both kwargs set — composition only):
- `_child_or_spawn()` (`:632-646`) → `self._registry.get_or_spawn(self._employee_entity_id).transport`;
  cache a `LiveSessionManager(child)` keyed by child identity, rebuilding (and shutting the old) when the
  registry returns a respawned child — the child-change rebuild the direct path already does (`:637,644`).
- `_resume_or_create(...)` (`:655-700`) → the session is the registry's one session; do **not** issue a
  second `session.create`/`session.resume`. Return `(registry.live_session_id_for(employee), stored)`
  (`employee_child_registry.py:497`) and `bind` it with `{"running": False}` (§2.4). `/new` →
  `registry.rebind_fresh_session(employee, old_live)` (`…:385`) then bind the new session.
- All turn plumbing (`run_ticket_step`/`run_human_turn`/`_submit_employee_consequence`/…) is unchanged.

**Direct mode** (tests): `:632-700` byte-unchanged **except** `_child_or_spawn`'s `GatewayChild(...)`
(`:638`) → `spawn_gateway_child(...)` (behavior-identical) plus the single-employee latch. Codex must
verify the added conditionals + latch leave the single-entity direct paths unchanged (§8-D).

### 2.3 Demux collapse (SEAM #2)
`sessions/service.py` is **not edited.** Per-employee, `_sessions_by_live_id`/`_sessions_by_stored_key`
hold one entry — the degenerate single-session case. With the §2.2 constraint, no direct-construct path
can bind two sessions, so the engine's multi-session code paths are unreachable in P2 (the constraint,
not a re-homed two-bind test, is how E/F are resolved — Codex #4). The admission/submission/settlement
engine is untouched.

### 2.4 Session snapshot at the registry boundary (SEAM #3 — Codex confirmed G safe)
Bind with `snapshot={"running": False}` in registry mode: the child is freshly spawned, so no turn is in
flight, and Hermes `running` is process-local, not persisted
(`orchestration/runtime-redesign/spikes/07-gateway-config-and-topology.md:224`). The registry discards
the session-open snapshot and must not be modified; `{"running": False}` matches the at-spawn reality
and lifecycle events correct it thereafter. No registry change.

### 2.5 `EntityRoutingGateway` — the single flag-off transport (chat + step) (Codex #5)
Keep the old `(default, entity_gateways)` constructor (`test_minds.py:267` stays green). Add a
registry-backed per-employee mode + the `StepGateway` surface so the flag-off runner can inject it
directly (today the runner needs a `StepGateway`: `run_ticket_step` + `interrupt(*, deadline)` +
`status()`, `runtime/step_gateway.py:23-45`; the bare `EntityRoutingGateway` has neither
`run_ticket_step` nor a keyword `deadline`, so injecting it today `AttributeError`s / `TypeError`s —
Codex #5). Additively:
- `_gateway_for(entity_id)` (registry mode) get-or-creates a per-employee `SharedGateway(registry,
  employee_entity_id=entity_id)` (lock-guarded, cached in a dict) — **one** per-employee gateway pool
  shared by both the chat and step surfaces (no duplicate caches).
- `run_ticket_step(session_key, entity_id, …)` → `_gateway_for(entity_id).run_ticket_step(…)`.
- `interrupt(session_key, entity_id, *, deadline=None)` → forward with `deadline` (the chat callers
  pass none; the step runner passes `deadline`, `employee_step_runner.py:310-314`).
- `catalog(entity_id=None)` → per-employee (§2.7).
- `status()` → **child-independent** (§2.6/Codex #1): available iff the Hermes interpreter resolves
  (from config / `resolve_hermes_python`), **not** keyed on any child's aliveness — so
  `_gateway_available` (`employee_step_runner.py:365-369`) never rejects healthy per-ticket work because
  some unrelated/not-yet-spawned child is absent. `status_for_entity(entity_id)` stays per-entity (the
  connection indicator reports the specific employee's child).

### 2.6 Days per-employee + durable session key (Codex #7)
Day chat is **not** ephemeral: it resolves `days.chat_session_key` (`chat/service.py:75-89` →
`days/data.py:31` `read_day(...).chat_session_key`) and persists it. Under P2, days become per-employee
via the legacy registry; their durable key must survive restart (a `None` resolver loses continuity).
Add day branches to the legacy adapters (composition-level, **no registry change**):
- `stored_session_resolver(day_…)` → `SELECT chat_session_key FROM days WHERE id=?` (or `read_day`).
- `on_stored_session_bound(day_…, new, old)` → a **NEW narrow writer** `record_day_session_key(conn,
  day_id, chat_session_key, now)` (mirror `record_agent_session_key`, `chat/data.py:985`) — there is no
  standalone call-only day writer today (the only writer is the turn-scoped `bind_human_turn_session`,
  `chat/data.py:889-960`), so this is net-new but narrow.
- Identity env (day) → `HERMES_TUI_SKILLS=<worker_role>`, `PLAN_ACTOR="worker"`, no `PLAN_TICKET_ID`
  (matches today's day-on-worker behavior).

**Flag (§8):** verify the registry-owned day key and the existing turn-scoped `bind_human_turn_session`
binder do not fight — in registry mode the human-turn binder must resolve to the registry-owned session
(the `/new`/human-turn binding routes through the registry, §2.2), so the registry key stays
authoritative. Confirm against the flag-off day-chat path.

### 2.7 Per-employee catalog + graceful degradation (owner's C ruling)
Source the catalog from the employee's own child, **without spawning one** for the catalog:
- **Backend.** `SharedGateway.catalog()` (registry mode) **peeks its cached child** (set by a prior turn,
  §2.2) — if `self._child` is alive, issue the session-less `commands.catalog` (`shared_gateway.py:470-479`)
  on it; else return an **empty** catalog (no `get_or_spawn`). `EntityRoutingGateway.catalog(entity_id)`
  → `_gateway_for(entity_id).catalog()`. Flag-on keeps the global catalog (the `entity_id` param is
  optional; flag-on ignores it → the residual worker gateway, unchanged).
- **API.** `/chat/commands` (`chat/api.py:127-135`) gains an **optional `entity_id`** query param (mirror
  the sibling `/chat/{entity_id}/status`, `:138-147`); `service.catalog(gateway, entity_id)` and
  `EntityRoutingGateway.catalog(entity_id)` thread it. The TTL cache (`chat/api.py:25-37`) becomes
  per-entity-keyed. Backward-compatible: no `entity_id` → today's global behavior (flag-on).
- **Frontend.** `resourceCatalogue.ts:338` `chatCommands` is a global `staticDefinition` → convert to a
  `parameterizedDefinition(entityId)` mirroring `chatGatewayStatus` (`:333-337`); update the accessor
  (`:85,389`) and `ChatPanel.svelte:38` to pass the open entity. Merge **Panels-side built-ins**
  (`/compact`, `/new`, `/interrupt`) into the composer's `/` menu so they always render even when the
  fetched catalog is empty — mirror `ChiefNeutralPane.svelte:171-189`'s `COMMAND_ITEMS`. When no child
  exists the fetched catalog is empty → only built-ins show (the accepted graceful degradation); once a
  turn spawns the child, the Hermes skills appear. **Keep this minimal + staged.**

### 2.8 History via non-lifecycle `session.history` (Codex #10)
`read_employee_session_history` (`shared_gateway.py:247-296`) today issues `session.resume` (`:254-263`),
which — after the registry has already resumed the employee's session — is a **second** lifecycle op that
violates the registry's sole-ownership (`employee_child_registry.py:3`). Replace it in registry mode with
the non-lifecycle read the relay already uses (`NATIVE_SESSION_HISTORY = "session.history"`,
`hermes_frame_translation.py:46,279`; issued in `neutral_downstream_session.py:194-201`; **not** on the
lifecycle denylist, `employee_child_relay.py:43-55`):
- `child = self._child_or_spawn()` — the registry `get_or_spawn` **resumes** the durable session **once**
  (sole owner); `live_id = registry.live_session_id_for(employee)`; `child.request("session.history",
  {"session_id": live_id})`; parse via the existing `_normalize_employee_session_history_messages`.
- `session.history` keys on the **LIVE** id (not the stored key), so an employee with no live child
  returns empty history. A history read now spawns the ticket's per-employee child (resuming its durable
  session) — surfaced. No registry change (session.history is a plain reader request; the registry
  already owns/resumed the session). Note `FakeGateway` must script `session.history` for the tests.

### 2.9 Interrupt sole-ownership — genuine P1 surface mismatch (Codex #10, STOP + FLAG)
The registry declares itself sole issuer of `session.interrupt` (`employee_child_registry.py:3`), yet the
flag-off interactive interrupt goes through `LiveSession.interrupt`, which returns a receipt / maps RPC
errors (NOT_FOUND → 404) that the chat UI relies on (`shared_gateway.py:394-439`). The registry's
`interrupt_live_turn` (`…:502-518`) is shutdown-shaped: best-effort, returns `None`, swallows errors —
switching to it loses those semantics. This is a real surface mismatch the clean fix cannot resolve
without a registry change. **STOP + FLAG for owner approval (§8-A).** Recommendation, pending the
ruling: keep interactive interrupt on `LiveSession` as a **documented exception** — `session.interrupt`
does not mint/destroy session identity (unlike create/resume/close), so the leak-safety invariant the
sole-ownership rule protects still holds; the alternative is a P1 registry `interrupt` that returns a
receipt/error. Do not silently bypass either way.

---

## 3. C3 — the legacy per-employee identity env (a real flag-off change, surfaced)

`identity_env_strategy(employee_entity_id)`: base `dict(base_env)` scrubbed of ambient `PLAN_TICKET_ID`
(`server.py:106`) + `HERMES_PYTHON_SRC_ROOT` + `HERMES_HOME` (mirror `SharedGateway._env`, `:648-651`).
- **Chief** (`agent_panels_chief_of_staff`) → `HERMES_TUI_SKILLS="panels-chief-of-staff"`,
  `PLAN_ACTOR="chief"`, no `PLAN_TICKET_ID`.
- **Ticket** (`t_…`) → `HERMES_TUI_SKILLS=<worker_role>`, `PLAN_ACTOR="worker"`, **`PLAN_TICKET_ID=
  <entity>`** (new per-employee isolation).
- **Day** (`day_…`) → `HERMES_TUI_SKILLS=<worker_role>`, `PLAN_ACTOR="worker"`, no `PLAN_TICKET_ID`.

**Stated flag-off change (not laundered):** ticket children now carry `PLAN_TICKET_ID` in the **process**
env (today none; identity was per-session). Legacy **keeps** `HERMES_TUI_SKILLS` (relay pops it). Surface
in `decisions.md` + PROGRESS.md.

---

## 4. Composition + ownership guard (`server.py`) — flag-off to registry; flag-on untouched

**Flag-OFF** (`not test_mode` and `not relay_backend_enabled`): replace the role-gateway build
(`server.py:339-361`) with: build the legacy `EmployeeChildRegistry` (§2.1, adopt the Chief key +
on-demand resolve tickets/days); build the registry-mode `EntityRoutingGateway` (§2.5) as **both** the
chat adapter (`app.state.adapters = Adapters(gateway=…)`) **and** the step transport (`start(…,
step_gateway=<the same EntityRoutingGateway>)`), since it now satisfies `StepGateway`. `EmployeeStepRunner`
is untouched (its `StepGateway` surface is satisfied).

**Ownership guard rewrite (Codex #6).** `_assert_single_employee_owner` (`server.py:134-171`) currently
defines valid flag-off as *dedicated Chief gateway + nonempty entity map* (`chief_gateway is not None`,
`len(entity_gateways)==0` false) — registry inputs (`chief_gateway=None`, `entity_gateways={}`) make the
unconditional call at `:391` raise. Rewrite so flag-OFF valid = *pool None, no legacy Chief gateway, empty
entity map, step transport is the registry `EntityRoutingGateway` (NOT `PoolStepGateway`), registry
present*; keep the flag-ON assertions (pool present, `chief_gateway None`, empty map, `PoolStepGateway`)
**verbatim**. Rewrite the flag-OFF valid rows of the guard tests to registry ownership, preserving every
flag-ON row:
- `test_hermes_backend_ticket_composition.py:124-131` (flag-off valid) and its flag-off inconsistent row
  (`:144-146`).
- `test_hermes_backend_chief_composition.py:230-236` (flag-off valid) + inconsistent rows (`:238-254`).
- `test_hermes_backend_chief_composition.py:180-190` `test_flag_off_composition_is_todays_wiring` — today
  asserts `_build_role_gateways` returns a non-None Chief gateway; rewrite to assert the registry
  composition (flag-off no longer calls `_build_role_gateways`). Keep
  `test_flag_on_composition_pool_owns_chief_no_legacy_child` (`:193-204`) verbatim.

**Flag-ON** (`relay_backend_enabled`): **unchanged** — `_build_role_gateways(chief_owned_by_pool=True)`
residual worker gateway (days+catalog+status+history) + pool. Test-mode relay (`:261-318`) untouched.
`_build_role_gateways` is now a flag-ON-only helper.

**Shutdown** (`:414-436`): sequence per §7. `config.yaml`, the neutral vocabulary, and
`hermes_backend/composition.py` are untouched (each path builds its own registry instance; §8).

---

## 5. Test plan

### 5.1 The behavior-unchanged proof — a NEW flag-off integration test (Codex #2)
`test_flows_a` and the other flag-off e2e run under `PLAN_TEST_MODE=1` (`tests/e2e/conftest.py:171`) →
`EchoGatewayAdapter` (`core/adapters/registry.py:27`); the production registry composition runs only
under `not test_mode` (`server.py:319`). So they prove ChatPanel/HTTP/DB against a canned catalog — **not**
`GatewayChild → LiveSessionManager → DB`. Add a production-like integration test that composes the real
flag-off objects — legacy `EmployeeChildRegistry` + registry-mode `EntityRoutingGateway` + a real
`GatewayChild` over `FakeGateway.spawn` (`minds/fake.py:72`, scripting `session.create`/`resume`/
`prompt.submit`/`session.history`/`commands.catalog`) + a real SQLite + `ChatTurnLifecycle` — drives a
human turn and a ticket step and asserts the typed→DB translation (chat_messages/chat_turns rows,
history, catalog). This is the flag-off behavior-unchanged proof. (Alternative, if the lead prefers a
server-level harness: a test-mode legacy composition symmetric to the relay's `scripted_relay_spawn` path
at `server.py:261-318` — larger, and it risks the EchoGatewayAdapter e2e; the standalone integration test
is recommended.)

### 5.2 Tests rewritten (each "asserts/uses deleted behavior")
Internal-spawn (→ `spawn_gateway_child`): `test_minds.py:114` (`gw()` helper), `test_minds_sessions.py:32`
(`_child` helper), `test_minds.py:1874`. *Exhaustive grep found no other internal-spawn ctor sites
(Codex).*

Multiplex (rewrite to per-employee / remove):
- `test_minds.py:548` `test_concurrency_smoke_rejects_cross_session_delivery_from_single_feed` — two
  session ids through one `GatewayChild` via `_check_distinct_sessions_demux`. Remove with the smoke
  demux (below).
- `test_minds.py:761` — two entities (`t_sync`/`t_stream`) on one `SharedGateway`. Rewrite to two
  per-employee gateways.
- `test_minds.py:914` — three entities on one `SharedGateway`. Rewrite to per-employee.
- `test_minds.py:1518` `test_shared_gateway_run_reuses_child_for_multiple_sessions` — rewrite to the
  per-employee inverse (two employees → two children/sessions).
- `test_minds.py:2003` `test_two_employee_sessions_share_one_child_without_cross_settlement` — the
  scenario is structurally impossible per-employee; delete (the §2.2 constraint + engine untouched cover
  isolation).

Production tool: `minds smoke --concurrency` (`smoke.py:323-361`, `_run_concurrency_smoke` /
`_check_distinct_sessions_demux`) opens two sessions on one child — remove the distinct-sessions-on-one-
child path (the single-session `4009` busy-guard check is not multiplex and may stay); its `GatewayChild`
constructions route through `spawn_gateway_child`.

Composition guard flag-OFF arms: §4 (`test_hermes_backend_ticket_composition.py:124`,
`test_hermes_backend_chief_composition.py:180,230`) — rewrite to registry ownership; flag-ON preserved.
(These were listed in the contract's green guard as staying green; that assumption is now corrected.)

### 5.3 New coverage
- **GatewayChild `ChildReader` conformance**: `register_frame_sinks` ordering (deliver→routing→fold) +
  typed ingress/pending still route; `request(on_request_id=…)` fires pre-send; `dead_event`/`alive`;
  two-phase `start_reading`; **the §1.3 interlock** — `start_reading` after `shutdown` raises;
  concurrent leader/follower `shutdown` tears down once (mirror `RawFrameChildTransport`'s race tests).
  Structural `ChildReader` satisfaction.
- **Per-employee rekey + single-employee constraint**: registry-backed `SharedGateway` runs a step + a
  human turn (one child per employee; resumes the persisted `employee_session_id`; `/new` →
  `rebind_fresh_session`); a second **distinct** entity on one gateway raises `SharedGatewaySingleEmployee`.
- **StepGateway conformance for `EntityRoutingGateway`**: `run_ticket_step` forwards per entity;
  `interrupt(*, deadline)`; child-independent `status().available` stays True when no child is spawned.
- **RED-first** where feasible.

### 5.4 Both flag states green via `./verify`.

---

## 6. Per-file edit list + landing order (keeps the tree green)

1. **`minds/gateway.py`** — remove internal spawn; `_spawn_child_process` + `spawn_gateway_child`; the
   §1.3 interlock/shutdown-once; `ChildReader` surface (`register_frame_sinks`, `on_request_id`,
   `enqueue_frame`, `dead_event`); sink-wired `_stdout_loop`/`_on_child_dead`. **Lands with step 2 + the
   §5.2 helper swaps in one wave** (the ctor change breaks the 3 internal-spawn constructions until they
   route through `spawn_gateway_child`).
2. **`runner.py` / `config.py` / `smoke.py`** — the 7 sites → `spawn_gateway_child`; remove smoke's
   two-session demux (§5.2).
3. **NEW `minds/legacy_registry.py`** — reader_factory, no-op subscriber, legacy identity env, single
   source. Green (unused until wired).
4. **`minds/shared_gateway.py`** — `SharedGateway` registry mode + single-employee constraint + cached-
   child `catalog()` + `session.history` in registry mode; `EntityRoutingGateway` registry mode
   (per-employee routing + `run_ticket_step` + `interrupt(*, deadline)` + `catalog(entity_id)` +
   child-independent `status`). Direct paths unchanged bar the `spawn_gateway_child` swap + latch.
5. **`chat/api.py`** (optional `entity_id` on `/chat/commands` + per-entity TTL) and **`chat/service.py`**
   (`catalog(gateway, entity_id)`).
6. **`web/src/lib/resourceCatalogue.ts`** (`chatCommands` → parameterized) + **`ChatPanel.svelte`/
   `ChatComposer`** (pass entity; merge built-ins). Minimal + staged.
7. **day session-key adapters** — new narrow `record_day_session_key` writer + the resolver read; wire
   into the legacy adapters.
8. **`core/server.py`** — flag-off registry composition + the rewritten ownership guard; step transport =
   the registry `EntityRoutingGateway`; shutdown sequencing. Flag-on untouched.
9. **Composition guard tests** (§4) + **the new flag-off integration test** (§5.1) + **new unit coverage**
   (§5.3) + **the §5.2 rewrites**. Then `./verify` both flag states.

`sessions/service.py` and `employee_child_registry.py` are not edited.

---

## 7. Shutdown ordering

Per-employee `LiveSessionManager`s shut **before/independently of** the registry's child teardown
(extend `server.py:414-436`): (1) stop loops/step runner (drain step threads); (2)
`EntityRoutingGateway.shutdown(deadline)` — each per-employee `SharedGateway` shuts **only** its manager
(the child belongs to the registry); (3) `legacy_registry.shutdown(deadline=…)` tears down all children
on one shared deadline (`employee_child_registry.py:525-560`), run off-loop on the registry's own
`shutdown_executor` (mirror `_shutdown_relay_pool_with_deadline`, `server.py:182-192`).

---

## 8. Uncertainties / candidate P1 gaps — for the next Codex round + owner ruling

- **A (interrupt sole-ownership — genuine P1 surface mismatch; §2.9).** `session.interrupt` is claimed by
  the registry, but `registry.interrupt_live_turn` is best-effort/receipt-less while the flag-off
  interactive interrupt needs receipt/error mapping. Recommend keeping interactive interrupt on
  `LiveSession` as a documented exception (interrupt does not touch session identity); the alternative is
  a P1 registry `interrupt`-with-receipt. **Needs an owner ruling.**
- **B (day binder interaction; §2.6).** Confirm the registry-owned day session key and the turn-scoped
  `bind_human_turn_session` binder agree in registry mode (the registry key must stay authoritative). If
  they fight, resolve at composition level; if it needs a registry change, STOP + flag.
- **C (scope expansion; §0).** The C ruling + blockers require touching the catalog API/frontend, a new
  day writer, the composition guard tests, `minds smoke`, and a new integration test — beyond the
  contract's "Must NOT touch." Confirm the expanded scope with the lead.
- **D (single-employee constraint regression risk; §2.2).** The latch + registry-mode conditionals must
  leave the single-entity direct paths byte-identical; highest regression surface — Codex's closest read
  + the direct-construct `test_minds` suite as the guard.
- **E (catalog peek staleness; §2.7).** `catalog()` reads the cached child without spawning; a stale/dead
  cached child degrades to built-ins-only until the next turn. Accepted graceful degradation — confirm.
- **Safe, keep as-is (Codex-confirmed):** G (`{"running": False}` bind), I (single `"planner"` source),
  the deliver→routing→fold ordering preserving `LiveSessionManager`, and the 7-not-8 call-site count.
