# W3b — implementation plan (System A + gate + one-CLI + server wiring)

Single Opus lead. Baseline: committed W3a (`main`). Seams in place: `runtime/lock.py`,
`runtime/system_b.py` (`SystemB.set_off`, dormant), `_pickup()→[]`. This plan maps the
build against the post-W3a tree read on disk.

## The runtime loop this closes

`approval / readiness → System A → System B.set_off → run_step → status`. System B (W3a) is
the sole writer of `tickets.status` and already maps a run end to `awaiting_approval` /
`errored`. W3b adds the **readiness authority** (System A) that decides *which* tickets to set
off and *when*, plus the poke wiring so an approval/unblock/step-completion drives the next
step immediately.

Key W3a fact this builds on: **every successful step ends at `awaiting_approval`** — whether
the proposal auto-accepted (state advanced, field cleared) or parked (field carries a
proposal). So `awaiting_approval` is a *run* status ("the run finished; the next step may be
due"), not "a human must act." System A distinguishes the two cases by inspecting the gating
field's proposal (below).

## 1. System A — `src/planner/runtime/system_a.py` (new)

`class SystemA` — the readiness poll + fast path. Owns a daemon thread; drives
`SystemB.set_off`. Never touches the model, never writes `tickets.status` (System B is the
sole writer — notes.md Principles).

### Readiness predicate

Candidate query (candidate-only **result set** — no per-tick full-table unpack; a
`(status)`/`(status,state)` index is a deferred optimization, schema stays as W3a — codex F4):

```sql
SELECT id FROM tickets
WHERE status IN ('empty','awaiting_approval') AND state NOT IN ('done','dropped')
```

This excludes `agent_working` (already running), `errored` (stopped + surfaced; no
auto-retry), and the two terminal states at the SQL level. For each candidate, load the
`Ticket` (`tickets_data.read_ticket`) and refine with the pure predicate
`runtime.readiness.is_runnable(conn, ticket)` (a shared module — see §4a; codex F2), which
checks steps 1–5 below. Readiness = `is_runnable(conn, t) and not system_b.has_inflight(t.id)`
(step 6):

1. `machine.is_terminal(state)` → not ready (defensive; already excluded).
2. `gating = machine.gating_field(state)`; `gating is None` (i.e. `needs_review`) → not ready
   — needs_review has no agent step; the human approves it to `done`.
3. `fields_codec.get_slot(fields, gating).proposal is not None` → not ready — a proposal is
   already parked awaiting a human decision (this is the `awaiting_approval`-means-await-human
   case). When the field is empty (fresh state, or the prior step auto-accepted and cleared
   it) we fall through.
4. `machine.at_or_beyond_ceiling(state, ceiling) and at_cap is AtCap.stop` → not ready — the
   grant says stop here (mirrors `admission.check_agent_proposal`, which would reject the
   agent's proposal anyway). Below ceiling, or at ceiling with `at_cap=propose`, we continue.
5. `core_links.is_blocked(conn, ticket.id)` → not ready (an open `blocks` link targets it).
6. `system_b.has_inflight(ticket.id)` → not ready (a run is already enqueued/in-flight for
   this mind; see the race note below).

Ready → `system_b.set_off(ticket.id, self._role, self._prompt_for(ticket))`.

`self._role = config.worker_skill` (default `planning-worker`). `_prompt_for(ticket)` is a
**minimal** next-step prompt naming the ticket and the gating field to propose; the real
"how to work a ticket" intelligence lives in the worker skill (out-of-band, §7). The prompt
content is never exercised by hermetic verify (System A does not run in test mode).

**Grant / ceiling realises auto-approve-up-to-ceiling** across two mechanisms already present
+ System A: (a) the resolution engine auto-accepts a proposal below ceiling inside
`file_proposal` (state advances, field cleared); (b) System A then finds the ticket
`awaiting_approval` with an *empty* gating field below ceiling and sets off the next step. At
the ceiling the proposal parks (or `at_cap=stop` blocks the run) → System A stops. From the
model's view every step still just "proposes and awaits"; **approval never travels to it** —
only the next-step prompt does.

### Class shape

```python
class SystemA:
    def __init__(self, db_path, clock, system_b, *, role, busy_timeout_ms=5000): ...
    def poke(self, _key: str | None = None) -> None:   # wake now; _key ignored (codex F1)
        self._wake.set()                               # accepts the ticket_id the idle cb passes
    def poll_once(self) -> list[str]:  # one readiness pass; returns the ids set off
        conn = connect(...); try: candidates → _is_ready → set_off each; finally close
    def start(self, interval: int) -> None:   # spawn the daemon poll thread
    def stop(self) -> None:                    # stop flag + wake + join
    def _run_loop(self, interval) -> None:
        while not self._stop.is_set():
            try: self.poll_once()
            except Exception: log (never kill the loop)
            self._wake.wait(interval); self._wake.clear()
```

`poll_once()` is the synchronous unit-test seam (mirrors `run_boundary_tick`). The loop is a
plain `threading.Thread(daemon=True)` — the fast-path poke just `Event.set()`s, which is far
simpler than waking an `asyncio.sleep`.

### The double-set-off race (fast-path race safety)

`set_off` returns after `MindQueue.submit` marks the key active but *before* the queue thread
writes `agent_working`. Two poll passes in that window would both see `status='empty'` and
double-enqueue the run. Guard: **`system_b.has_inflight(ticket_id)`** (step 6) delegates to
`MindQueue.is_active(key)`; `submit` adds the key to `_active` synchronously under the lock
before returning, so the second pass skips it. The candidate query (status) is the primary
"don't re-run a running ticket" gate; `has_inflight` closes the enqueue→start-write window.

## 2. Fast path

- **Timer backstop:** the poll loop wakes every `config.tick_seconds`.
- **Poke on human decision:** the API endpoints that change readiness poke System A (§4).
- **Poke on step completion:** `MindQueue` gains an optional `on_idle(key)` callback fired
  when a key's queue drains (the ticket is free again); System B forwards it; System A
  registers `poke`. This is what makes an auto-accepted step immediately drive the next step
  rather than waiting a full tick. The callback fires *after* `_active.discard(key)`, so the
  subsequent `poll_once` sees `has_inflight=False` and can set off the next step.

## 3. `MindQueue` seam — `src/planner/minds/queue.py` (additive)

Add `on_idle: Callable[[str], None] | None = None` to `__init__`. In `_drain`, when the deque
is empty, after `self._active.discard(key)` + `notify_all()`, release the lock and call
`self._on_idle(key)` (guarded) before returning. Backward-compatible: default `None`, all W1
tests construct `MindQueue(run)` positionally (verified). System B passes
`on_idle=self._on_idle_cb`; a setter lets System A register its `poke` after construction:
`system_b.set_idle_callback(system_a.poke)`.

## 4. System B additions — `src/planner/runtime/system_b.py` (additive; `_run` untouched)

- `has_inflight(ticket_id) -> bool` → `self._queue.is_active(ticket_id)`.
- `MindQueue.is_active(key) -> bool` (new, tiny; reads `_active` under lock).
- Optional idle callback: `set_idle_callback(cb)` stores it; wire the queue's `on_idle` to a
  method that forwards to the stored cb. Constructed dormant-safe: no cb → no-op (W3a tests
  unaffected). `_run`, `set_off`, key resolution, status writes: **unchanged**.

## 4a. Readiness predicate + execution-time guard (codex F2)

`src/planner/runtime/readiness.py` (new, pure): `is_runnable(conn, ticket) -> bool` = steps
1–5 of §1 (terminal / has-gating / no-parked-proposal / grant-not-stopped-at-ceiling /
not-blocked). **No `has_inflight`** — that is System A's concern, and at execution time the
ticket is inflight by definition. Imports only `tickets.logic` + `core.links` → no cycle with
`system_a`/`system_b`.

`SystemB.set_off(ticket_id, role, prompt, *, guard=None)` where
`guard: Callable[[Connection, Ticket], bool] | None`. `_Item` carries it. In `_run`, right
after opening the conn and re-reading the ticket, **before the `agent_working` start-write**:
if `guard is not None and not guard(conn, ticket)` → log + `return` (no status write, ticket
untouched). Closes the read→execution stale-set-off window (human drop / grant-stop / park /
block in the gap). `guard` defaults `None`, so W3a's committed System B tests (bare `set_off`)
are unaffected. System A passes `guard=is_runnable`, so the SAME predicate gates both the poll
and the run — no duplication, System A stays the sole readiness authority, System B the sole
status writer (a skip writes nothing).

## 5. Server wiring — `src/planner/core/loops.py` + `core/server.py`

`start_background_loops(config, clock, adapters, conn_factory)`:
- Keep the boundary loop as-is.
- Master switch + singleton guard: if `config.dispatch_enabled` (W3a delegated: System A
  reuses the master switch) **and** `ensure_machine_lock(config.dispatcher_lock_path)` →
  construct `SystemB(config.db_path, clock, home=resolve_planner_home(),
  hermes_python=resolve_hermes_python(), spawn=spawn_popen)` and `SystemA(config.db_path,
  clock, system_b, role=config.worker_skill)`, wire `system_b.set_idle_callback(system_a.poke)`,
  `system_a.start(config.tick_seconds)`. Expose `loops.system_a`.
- `BackgroundLoops.stop`: `await asyncio.to_thread(system_a.stop)` (join the poll thread) then
  cancel the boundary task; `release_machine_lock` on shutdown.

`create_app` lifespan already gates loops behind `if not config.test_mode`. After starting
loops, set `app.state.system_a = loops.system_a` (else `None`). **So System A — and the only
real `run_step` spawn path — never runs under `PLAN_TEST_MODE`; `./verify` stays hermetic.**

Home/python come from `minds.config` resolvers (`PLAN_HERMES_HOME` / `PLAN_HERMES_PYTHON`,
with defaults), matching the smoke path; `spawn_popen` is the real spawn but is only reachable
outside test mode. If the planner home is unprovisioned the child fails loudly at run time and
System B writes `errored` — it never corrupts state.

## 6. Poke wiring in the API — `src/planner/tickets/api.py` (+ links)

Add `get_system_a(request) -> "SystemA | None"` returning `getattr(request.app.state,
"system_a", None)` (imported under `TYPE_CHECKING` only, to avoid any import-time cycle;
runtime just `getattr` + `.poke()`), and `Sa = Annotated[..., Depends(get_system_a)]`. Poke
(null-guarded) at the end of: **`create_ticket`** (codex F3 — a fresh empty ticket is ready
immediately; without this kickoff waits a full tick), `accept_field`, `approve_ticket`,
`grant_ticket`, `set_state`, `drop_ticket`, and `remove_link` (unblock). These are the
readiness-changing decisions ("an approval / unblock pokes System A immediately"). In test mode
`system_a is None` → no-op.

## 7. Out-of-band (seam built; provisioning carved to a follow-up)

Planner-home provisioning (dedicated `HERMES_HOME` + model config + creds + the v2 worker
skill) and the worker role skill need live hermes + creds. The **seam** is wired: System B is
constructed with the resolved home + interpreter + `worker_skill` role, real spawn off by
default (test mode). I will **carve** the actual home provisioning + worker-skill authoring +
a live System-A→B→run_step smoke to a W3-follow-up, and note it in the report. `minds/smoke.py`
already validates the real-gateway `run_step` path; the incremental live check (System A poll
→ real spawn → ticket advances) is the follow-up. Hermetic verify uses `minds/fake.py` only.

## 8. One-CLI rework — `src/planner/cli/main.py` (+ `tickets/api.py`, `tickets/views.py`)

- `ticket create`: rename option `--item` → `--sprint-item` (still → `sprint_item_id`). No
  test uses `ticket create --item` (verified). Keep `--sprint`.
- `ticket list --day/--date`: add `--day` (accepts `today` | ISO) and `--date` (ISO) coalesced
  into a `day` query param. `GET /api/tickets` gains a `day` param resolved via a NEW shared
  pure `days/logic/dates.resolve_day_id(seg, now, boundary_hour)` (refactored out of
  `days/api.resolve_day_id`, which now delegates — one home, no duplication, no `days.api`
  import → no cycle); `views.list_tickets` gains a `day_id` param adding
  `AND id IN (SELECT ticket_id FROM day_tickets WHERE day_id = ?)`.
- Drop `queue pickup`: remove the CLI command; remove the now-dead `_pickup` function and the
  `pickup` key from `queues_view` (nothing consumes it — no test/frontend/CLI, verified).
  `queue approvals` / `queue overdue` stay.

## 9. Tests (hermetic — `minds/fake.py`, no real gateway)

New `tests/unit/test_system_a.py`, reusing the `test_system_b.py` fake-gateway harness
(`_Spawner`, `_ProposingFake`, real `file_proposal` writer):

- **readiness selects a fresh empty ticket** (default grant, `at_cap=propose`) → `poll_once`
  sets it off (session.create sent; status → agent_working then awaiting_approval).
- **readiness excludes** done / dropped / agent_working / errored / blocked / awaiting-with-
  parked-proposal / at-ceiling-with-`at_cap=stop` → `poll_once()` returns `[]`, nothing set off.
- **below-ceiling auto-accept chain**: ceiling raised; a parked→cleared (auto-accepted)
  `awaiting_approval` ticket with an empty gating field → `poll_once` sets off the next step.
- **at ceiling + `at_cap=propose`** → ready (parks); **+ `at_cap=stop`** → not ready.
- **no double set_off**: a run blocks mid-run; a second `poll_once` while in-flight does not
  re-set-off (exactly one session.create) — proves `has_inflight`.
- **fast path (poke)**: start the loop with a long interval; create a ready ticket + `poke()`;
  it is set off well under the interval (proves poke, not timer).
- **on_idle drives the next step**: with the loop running + ceiling above, an auto-accepted
  step's `on_idle` poke sets off the following step automatically (bounded wait).

`tests/unit/test_minds.py`: add `is_active` + `on_idle` unit coverage for `MindQueue`.

E2E (`tests/e2e/`): `ticket create --sprint-item <id>` parents correctly; `ticket list --day today`
filters to the day's tickets; `queue pickup` is gone (invocation exits non-zero). No e2e touches
System A (test mode) → no real gateway.

## 10. Acceptance / checks (self-run; integrator runs full ./verify)

`.venv/bin/ruff check .` · `.venv/bin/mypy src/` · `.venv/bin/pytest tests/unit -q` ·
`.venv/bin/pytest tests/e2e -q`. No skip/xfail/empty tests. Schema unchanged from W3a (no
SCHEMA_VERSION bump; no new index — the status WHERE is selective enough for a single-user
board; a `status` index is noted as a deferred optimization).

## Candidate scope — RESOLVED (owner ruling, post-implementation)

Originally flagged as open: the literal predicate made *every* non-terminal ticket a candidate,
including backlog. **Owner ruling: scope readiness to TODAY's day only** — backlog and other-day
tickets are not auto-started. Implemented as a `day_tickets` join on today's planning day in the
candidate query (`system_a._CANDIDATE_SQL`); "today" is resolved via `dates.resolve_day_id("today",
clock.now(), boundary_hour)` (System A now takes `boundary_hour` from config). Everything else is
unchanged: default-grant auto-start, grant/ceiling auto-advance, and the approval fast-path poke.
Tests: `test_poll_is_scoped_to_today` (same ticket — backlog no-op, on-today set off),
`test_poll_excludes_ticket_on_another_day`, `test_poll_sets_off_today_ticket_after_approval_advance`.
