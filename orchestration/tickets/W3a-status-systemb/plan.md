# W3a — Ticket status field + System B — implementation plan

Baseline: committed W2 (`main` @ `f2ed748`), `./verify` green. Single Opus lead; no sub-agents;
codex is a read-only reviewer only. This plan maps exact edit/delete points against the on-disk
post-W2 tree.

Scope reminder (from `ticket.md` + `W3-runtime-rewire/ticket.md` + `runtime-redesign/notes.md`):
BUILD the ticket status field (the reframed lock) + System B (set-off/run primitive on W1's
`run_step`); REMOVE the old claim/`runs`/breaker/dispatcher machinery + the `run` CLI group +
`X-Plan-Run-Id`/`X-Plan-Claim` headers + run/claim/heartbeat/close endpoints; repair every
reference. OUT of scope (W3b): System A readiness poll + fast path, the propose→approve gate UX,
the CLI verb rework (`--sprint-item`, `list --day`, drop `queue pickup`), planner-home + worker
skill. Leave clean seams.

---

## 1. Schema (`src/planner/core/db.py`) — `SCHEMA_VERSION` 2 → 3

`tickets` table:
- **REMOVE columns:** `auto_blocked`, `consecutive_failures` (breaker), `claim_lock`,
  `claim_expires` (old claim lock).
- **ADD columns:**
  - `status TEXT NOT NULL DEFAULT 'empty' CHECK (status IN ('empty','agent_working','awaiting_approval','errored'))`
  - `worker TEXT` (nullable — "who/what is working it"; NULL when not `agent_working`).
- **KEEP `alias`** (+ `idx_tickets_alias`). Confirmed NOT a dead leftover: `seed/importer.py:210-211`
  reads it for idempotent cutover dedup, and notes.md keeps the importer as a one-shot cutover
  script. The `alias` OPEN in notes.md ("review separately") is a naming/links question, not a
  removal for W3a.
- KEEP `ceiling`/`at_cap` (grant), `chat_session_key` (the ticket-mind's durable `session_key`).
- KEEP `idx_tickets_state`.

**DROP the `runs` table** and `idx_runs_ticket`.

Fresh DB builds at `SCHEMA_VERSION` 3 (no migration path — DB/logs are gitignored dev state; the
one-shot seed importer is the cutover tool).

---

## 2. Ticket contract + status writer

### `src/planner/tickets/contracts.py`
- Add `class TicketStatus(StrEnum)`: `empty`, `agent_working`, `awaiting_approval`, `errored`.
- `Ticket` dataclass: **remove** `auto_blocked`, `consecutive_failures`, `claim_lock`,
  `claim_expires`; **add** `status: TicketStatus`, `worker: str | None`. Keep `alias`,
  `chat_session_key`, `ceiling`, `at_cap`.

### `src/planner/core/contracts.py` — `EventKind`
- **Remove** `run_started`, `run_closed`, `claim_heartbeat`, `claim_reclaimed`, `auto_blocked`,
  `auto_block_cleared` (all tied to runs/claims/breaker).
- **Add** `ticket_status_changed = "ticket_status_changed"` (payload `{status, worker}`).

### `src/planner/tickets/data.py` (the sole ticket-row writer — invariant preserved)
- `_row_to_ticket`: read `status`/`worker`, drop the four removed columns.
- `create_ticket` INSERT: drop `claim_lock, claim_expires, auto_blocked, consecutive_failures`;
  add `status` (default `'empty'`) + `worker` (NULL). New tickets are `status='empty'`.
- **`seed/importer.py` INSERT (codex F6):** the ticket INSERT (`importer.py:231-238`) still lists
  `auto_blocked, consecutive_failures` (+ values `0, 0`) — drop both columns and both values; let
  `status`/`worker` take their schema defaults. Otherwise a fresh v3 import fails.
- **Add writer** `set_run_status(conn, ticket_id, *, status: TicketStatus, worker: str | None,
  session_key: str | None | _Unset = _UNSET, now: int) -> Ticket`: one `BEGIN IMMEDIATE` txn; a
  **single** `UPDATE tickets SET status=?, worker=?[, chat_session_key=?], updated_at=? WHERE id=?`
  (status + worker written together — dissolves the runless orphan); append one
  `ticket_status_changed` event `{status, worker}`; return the reloaded ticket.
  - **Typed sentinel (codex F4):** define `class _Unset: ...` and `_UNSET: Final = _Unset()`;
    include the `chat_session_key=?` clause only when `not isinstance(session_key, _Unset)` (so
    `None` explicitly clears it, `_UNSET` leaves it untouched). `result.session_key` from
    `run_step` is `str | None`, which the `str | None | _Unset` annotation accepts.
  - This is the ONE door for the status transition; System B is its only caller.

---

## 3. System B — new package `src/planner/runtime/`

Files: `runtime/__init__.py`, `runtime/system_b.py`, `runtime/lock.py` (the relocated machine-lock,
§4). (System A lands here in W3b — clean seam.)

`class SystemB`:
- Constructed with `conn_factory`, `clock`, the run parameters `home`, `hermes_python` (from
  `minds.config`), plus `*, spawn: SpawnFn = spawn_popen`. The `spawn` seam is what the unit tests
  inject a fresh-per-spawn `FakeGateway` sequencer through.
- Holds ONE `MindQueue[_Item]` **keyed on the durable `session_key`** (per W1's contract — the
  queue's correctness proof is written against `session_key`), run callable `self._run_by_key`.
- Holds a **System-B-owned per-`ticket_id` kickoff serializer** (a `dict[str, Lock]` + a meta-lock)
  for step-0 (NULL key) runs — because the per-session queue cannot serialize two step-0 runs
  (neither has a key yet). This is W1's binding carry-forward "System B must serialize kickoff
  itself"; it is NOT folded into the queue.
- An in-flight tracker (counter + `Condition`) so `wait_idle()` covers BOTH the queue and kickoff
  threads (test helper).

**`set_off(ticket_id, role, prompt) -> None`** (async — spawns a daemon dispatch thread so System A
never blocks):
- The dispatch thread acquires the **per-ticket kickoff lock**, then **resolves the current
  `session_key` from the DB at execution time** (NOT at `set_off` time):
  - `key is None` ⇒ **kickoff/step-0**: run synchronously *inside* the kickoff lock via
    `self._run(ticket_id, None, role, prompt)` (which `session.create`s and stores the key at the
    end). A concurrent same-ticket set_off blocks on this lock, then re-resolves the now-non-NULL
    key and takes the continuing path — so two step-0 runs never both `session.create`.
  - `key is not None` ⇒ **continuing/step-N**: release the kickoff lock and hand to the
    **per-session MindQueue** (`self._queue.submit(key, _Item(ticket_id, role, prompt, done))`,
    `done.wait()`), so continuing steps go through the queue keyed on `session_key` exactly as the
    contract requires.

**`self._run(ticket_id, key, role, prompt)`** — the actual step (called by kickoff directly and by
the queue callback `_run_by_key`):
1. **Start write (single door):** `set_run_status(conn, ticket_id, status=agent_working,
   worker=role, now=now)`.
2. **Capture pre-run snapshot:** `pre = read_ticket(conn, ticket_id)`; `pre_state = pre.state`;
   `pre_gating = GATING_FIELD.get(pre_state)`.
3. **Run:** `result = run_step(key, role, prompt, on_event=None, home=self._home,
   hermes_python=self._python, spawn=self._spawn)`. (`key` is the value resolved in `set_off` at
   execution time — the same durable key the queue is serializing on for continuing steps.)
4. **Map RunResult → end status:**
   - `result.status == "complete"` ⇒ run the **proposal-present invariant** (below).
   - `result.status in {"interrupted", "errored"}` ⇒ `errored` (surface `result.error`); no
     proposal check (the run did not finish its step).
5. **End write (single door):** `set_run_status(conn, ticket_id, status=<mapped>, worker=None,
   session_key=result.session_key, now=now)` — one atomic UPDATE writes status + cleared worker +
   the resolved durable key together, so a later step resumes the right session (also covers the
   kickoff's freshly created key).

**Proposal-present invariant (robust — handles auto-accept, per codex plan-review F1).** After a
`complete` run, re-read the ticket. The agent "did its step" iff **the ticket advanced OR the
expected gating field carries a proposal**:
`did_job = (post.state != pre_state) or (pre_gating is not None and
get_slot(post.fields, pre_gating).proposal is not None)`.
- Parked (proposal at ceiling): state unchanged, `pre_gating` proposal present ⇒ `awaiting_approval`.
- **Auto-accepted (below ceiling):** `plan propose` auto-accepts (`resolution.decide_file_proposal`
  → `_accept_gating_proposal`, `resolution.py:107`), advancing state and CLEARING the proposal, so
  `post.state != pre_state` ⇒ `awaiting_approval`. (A naive "proposal present only" check would
  wrongly mark this `errored` — the F1 latent bug; the `advanced` disjunct fixes it.)
- Neither (agent burned a turn, left nothing) ⇒ `errored` ("agent left no proposal — you haven't
  done your job", surfaced in the event payload).
An agent step can only advance state forward via auto-accept (jump/drop/approve are human-only,
`reject_agents`), so `post.state != pre_state` unambiguously means "the agent's proposal was
accepted." From the model's view every step still awaits approval (auto-approve is invisible).

**System B is dormant in W3a:** nothing in the running server calls `set_off` (that wiring is
System A = W3b). It is exercised only by `tests/unit/test_system_b.py` via the fake gateway. Clean seam.

---

## 4. Removal ledger — every file + reference repair

### Delete the whole `src/planner/dispatch/` package
`runtime.py`, `api.py`, `data.py`, `contracts.py`, `logic/{breaker,claims,eligibility,ordering,__init__}.py`,
`dispatch/__init__.py`. (Claim/`runs`/breaker/dispatcher-tick/eligibility/ordering are all removed
machinery; readiness/eligibility is System A = W3b, rebuilt fresh there.)

**Reuse the existing gating helper (no relocation):** `board_view` currently calls the deleted
`gating_field_pending(state, JsonDict)`. `machine.has_pending_gating_proposal(state, TicketFields)`
already exists and is equivalent — switch `board_view` to parse `fields` via
`fields_codec.fields_from_json` and call `machine.has_pending_gating_proposal`. No helper is
relocated; the dispatch dependency in `tickets/views.py` disappears.

**Relocate the machine-lock (codex F3, notes.md "DECIDED KEEP · dispatcher machine-lock").** Move
`_ensure_dispatcher_lock` / `release_dispatcher_lock` / `_LOCK_FDS` out of `dispatch/runtime.py`
into a new `src/planner/runtime/lock.py` BEFORE deleting the dispatch package. Nothing in W3a
consumes it (the dispatcher loop is gone), but W3b's System A needs a singleton machine guard — the
KEEP decision is honored by preserving the helper, not dropping it.

### Spawn-adapter subsystem (old dispatcher spawn mechanism, superseded by `run_step`)
Its only consumer was `dispatch/runtime.py` (deleted); `real.py:96` imports the deleted
`dispatch.runtime._pid_alive`, so a change here is forced. Remove it cleanly rather than leave
dormant superseded machinery:
- `core/adapters/base.py`: remove `SpawnRequest`, `SpawnResult`, `SpawnAdapter`.
- `core/adapters/real.py`: remove `RealSpawnAdapter` (+ its dangling `_pid_alive` import).
- `core/adapters/fakes.py`: remove `FakeSpawnAdapter`.
- `core/adapters/registry.py`: drop `spawn` from `Adapters`, remove `_select_spawn`; `build_adapters`
  returns `Adapters(boundary=…, gateway=…)`. (`adapters.spawn` is referenced nowhere else —
  verified: only `runtime.py` + `test_runtimes.py`, both deleted.)
- `core/config.py`: remove the `spawn_adapter` key (Config field + `_str_value` line). Keep
  `hermes_bin`/`hermes_profile` (boundary adapter uses them) and `worker_skill` (System B's role,
  wired in W3b). Leave the now-inert dispatcher timing knobs (`claim_ttl_seconds`, `max_runs`,
  `failure_limit`, `run_max_seconds`, `dispatcher_lock_path`) in place — they are unused config
  keys, not dangling references; a config-knob cleanup is its own notes.md ledger item (deferred).

### `core/authctx.py` — collapse claim classification to the human/agent boundary
The X-Plan-Run-Id/X-Plan-Claim headers ARE the claim mechanism; removing them + the claim columns
forces this:
- Remove constants `X_PLAN_RUN_ID`, `X_PLAN_CLAIM`; remove the `from planner.dispatch.logic.claims
  import has_active_claim, is_expired` (module deleted).
- `RequestContext`: drop `run_id`, `claim`, `is_claimed_agent`; keep `actor`, `is_human`.
- `_classify`: two cases only — `actor` present ⇒ agent (`is_human=False`); else human.
  `request_context` reads only `X-Plan-Actor`.
- **Remove** `require_claim`, `validate_carried_claim`, `_reject_stale` (claim validation is gone).
- **KEEP** `reject_agents`, `reject_agent_fields` (the invisible-approval human boundary stays —
  agents still can't do human-only actions; notes.md "approval machinery invisible to the model").

### `tickets/api.py`
- Imports: drop `require_claim`, `validate_carried_claim`; drop `from planner.dispatch import data
  as dispatch_data`. Keep `reject_agents`, `reject_agent_fields`, `request_context`, `RequestContext`.
- `patch_ticket`: remove `validate_carried_claim(...)` call (keep `reject_agent_fields`).
- `propose_field`, `put_notes`, `put_recap`: remove the `if ctx.is_claimed_agent: require_claim(...)`
  blocks (no per-request claim now; agents are trusted — notes.md CLI section).
- `_validate_link_claim` + its calls in `add_link`/`remove_link`: remove (claim gone); links become
  human-or-agent with no claim check.
- **Remove** the `/tickets/{ticket_id}/unblock` route (breaker gone; `clear_auto_block` deleted).
- **Remove** the `/tickets/{ticket_id}/runs` route + `ticket_runs` (no `runs` table).

### `tickets/views.py`
- `ticket_json`: drop `auto_blocked`, `consecutive_failures`, `claim_expires`, `claim_active`; add
  `status`, `worker`. Drop the `has_active_claim` import.
- `run_json`, `get_run`, `runs_for_ticket`: **remove** (no `runs` table).
- `ticket_detail`: remove the `run_summary` block (the `runs` COUNT/running/latest queries).
- `board_view`: SELECT `status` instead of `claim_lock`/`claim_expires`; card exposes `status`
  (drop `has_running_claim`); keep `has_pending_proposal` via `machine.has_pending_gating_proposal`
  (parse `fields` with `fields_codec.fields_from_json`).
- `_pickup`: **stub to return `[]`** (readiness = System A = W3b; it currently calls the deleted
  `load_candidates`/`is_eligible`/`ordering_key`). Keep the `pickup` key in `queues_view` and the
  `queue pickup` CLI intact (response shape stable; W3b rebuilds/drops). Drop the dispatch imports.

### `days/api.py`
- Remove `validate_carried_claim` import + its two calls (`add_day_ticket`, `remove_day_ticket`).
  Keep `reject_agents`.

### `cli/main.py`
- Remove the `run` group (`run`, `run_heartbeat`, `run_close`) + the `_require_run_id` helper.
- KEEP `queue pickup`, `queue approvals/overdue`, and `--item` on `ticket create` (W3b reworks the
  verb surface; out of scope here).

### `cli/http.py`
- Stop sending `X-Plan-Run-Id`/`X-Plan-Claim` (drop the `PLAN_RUN_ID`/`PLAN_CLAIM` env reads). Keep
  `X-Plan-Actor` (default `agent`).

### `core/server.py`
- Remove `from planner.dispatch.api import router as dispatch_router` and drop it from the
  `include_router` loop.

### `core/loops.py`
- Remove the dispatcher loop + `from planner.dispatch.runtime import ConnFactory,
  release_dispatcher_lock, run_tick`. Keep only the boundary loop. Simplify `BackgroundLoops` to
  drop the dispatcher flock (lock_path / release). Define `ConnFactory` locally (was imported from
  runtime).
- **Machine-lock:** relocated to `runtime/lock.py` (§4, codex F3) — the KEEP decision is honored;
  loops.py no longer imports it (the boundary loop needs no machine-lock). W3b's System A consumes it.

### `core/testmode.py`
- Remove the `/test/tick-dispatcher` route (lazy-imported the deleted `run_tick`). Keep
  `/test/set-now` and `/test/tick-boundary`.

### `core/adapters/real.py`
- Covered by the spawn-adapter removal above (removes the dangling `dispatch.runtime._pid_alive`
  import).

---

## 5. Frontend repair (keep e2e green; the UI reads the one status field)
- `assets/screens-board.js:40`: `card.has_running_claim ? marker("running-claim")` →
  `card.status === "agent_working" ? marker("agent-working") : null`.
- `assets/screens-ticket.js:346-368`: replace the `claim_active` marker + `auto_blocked`
  marker/Unblock control with a `detail.status === "agent_working"` marker (and an
  `errored` marker when `detail.status === "errored"`); remove the Unblock control entirely
  (breaker + `/unblock` gone). Keep the `detail.blocked` marker (links-derived, unchanged).
- `assets/components.js` run-history (`runHistory`, `runs`, ~lines 856-864): remove (no `runs`
  table / `/runs` endpoint). Verify no remaining caller renders it (screens-ticket).
- Syntax-check every touched JS with `node --check`.

---

## 6. Tests

### New — `tests/unit/test_system_b.py` (hermetic, via `minds/fake.py`, fresh-per-spawn)
**Test spawn sequencer (codex F5):** `FakeGateway.spawn` returns `self` and the fake is closed
after one `run_step` (`fake.py:43`), so multi-run tests need a fresh child per spawn. Provide a
sequencer: `spawn=seq.spawn` where `seq.spawn(argv, env)` returns the next pre-built `FakeGateway`
(each scripted with `session.create`/`resume` + `prompt.submit` + a `message.complete` event).
**Faithful proposal (codex F1):** the "agent files a proposal during its run" is simulated by a
`FakeGateway` variant whose `send()` calls the REAL writer `tickets_data.file_proposal(...)` when it
observes `prompt.submit` — so the proposal (and any below-ceiling auto-accept + state advance) lands
through the production resolution engine mid-run, before System B's post-check. No masking direct
`fields` write.

Cases, all against the fake, no subprocess/model:
1. **Four status transitions:** fresh ticket `empty` → `set_off` writes `agent_working`
   (worker=role) → run ends → `awaiting_approval` or `errored` (assert the mid-run + end states and
   the `ticket_status_changed` events).
2. **Parked proposal (at ceiling) ⇒ `awaiting_approval`:** default ceiling, propose-hook parks a
   gating proposal; System B sees the proposal present.
3. **Auto-accepted proposal (below ceiling) ⇒ `awaiting_approval` (the F1 regression):** grant a
   higher ceiling first, propose-hook files a gating proposal that auto-accepts and clears itself
   + advances state; System B's `advanced` disjunct still yields `awaiting_approval` (a naive
   present-only check would wrongly `errored`).
4. **No proposal ⇒ `errored`:** propose-hook files nothing; assert `errored` + the event message.
5. **Kickoff-as-step-0:** `chat_session_key` NULL ⇒ the fake receives `session.create` (not
   `resume`); the created `stored_session_id` is persisted to `chat_session_key`.
6. **Kickoff serialization:** two `set_off`s on the same fresh ticket ⇒ exactly one `session.create`
   across both runs (the second resolves the created key at execution time via the per-ticket
   kickoff lock and resumes); asserted via each fake's recorded methods + `wait_idle`.
7. **Current-key resolution:** a continuing `set_off` resolves and resumes the CURRENT stored key
   (set between enqueue and execution), proving resolution is at execution time.
8. **RunResult mapping:** gateway `error` event / child-death ⇒ ticket `errored` with the error
   surfaced; no proposal check on that path.

### Delete
- `tests/unit/test_dispatch.py` (claims/breaker/close/reclaim/eligibility — all removed).
- `tests/unit/test_runtimes.py` (dispatcher tick + spawn adapter — all removed).

### Fix (narrowed after grep — most earlier hits were false positives)
- `tests/unit/test_authctx_routes.py`: remove the `_claim` helper + the stale-claim test
  (`test_patch_ticket_with_stale_claim…`); rewrite `test_patch_ticket_agent_permitted_fields_succeed`
  to a plain agent (no claim) setting priority/deadline. Keep every human/agent-boundary test
  (`reject_agents`/`reject_agent_fields` unchanged). Drop the `dispatch_data` import.
- `tests/unit/test_value_edit_logic.py:50-71`: the `_ticket(...)` `Ticket(...)` constructor lists
  `auto_blocked`, `consecutive_failures`, `claim_lock`, `claim_expires` — swap those four kwargs for
  `status=TicketStatus.empty`, `worker=None`.
- (test_instrument / test_value_edit_api / test_seed / test_sprints grep hits are comments/CSS
  strings — NO change needed; confirmed.)

### E2E
- **Delete** `test_e30_dispatcher_e2e_to_done` (whole dispatcher/claim/run-close flow — removed
  machinery). Remove its helpers `_read_claim`, `_tick_dispatcher` if unused after.
- **Rewrite** `test_e31_refresh_restores_state`: drop the `_tick_dispatcher` call, the `_read_claim`
  and claim assertions; keep the parked-proposal reload-restore on ticket/board/day (the claimless
  `propose result` still parks at ceiling `in_progress`). Update `_snap_ticket`/`_snap_board` to
  drop the `claim` key (nothing runs the ticket ⇒ no `agent_working`); keep `state`/`meta`/`body`
  and `title`/`pend`.
- `tests/e2e/conftest.py` `cli` fixture: drop the `run_id`/`claim` params + `PLAN_RUN_ID`/`PLAN_CLAIM`
  env writes.
- The `test_eNN_` verify anchor for item 30 will no longer exist; that is the integrator's
  full-`./verify` concern (this ticket runs only ruff/mypy/pytest unit+e2e). Noted in the report.

---

## 7. Checks & acceptance (self-run; NOT full `./verify`)
- `.venv/bin/ruff check .` clean.
- `.venv/bin/mypy src/` clean.
- `.venv/bin/pytest tests/unit -q` green; `.venv/bin/pytest tests/e2e -q` green.
- Fresh DB builds at `SCHEMA_VERSION` 3; no dangling refs from removed machinery
  (`grep -rn` sweep for `claim_lock|claim_expires|auto_blocked|consecutive_failures|runs\b|
  run_tick|dispatch|require_claim|validate_carried|X-Plan-Run|X-Plan-Claim`).
- No skip/xfail/empty tests (verify's skip-scan would fail).
- Grant/ceiling (`ceiling`/`at_cap`) untouched.

## 8. Seams left for W3b (explicit)
System A readiness poll + fast path (rebuild eligibility from candidate query; poke `SystemB.set_off`);
the singleton machine-lock (System A owns it); `queue pickup` rebuild/drop + `--item`→`--sprint-item`
+ `list --day`; the propose→approve gate (makes the proposal-present invariant exact by parking);
planner-home provisioning + worker skill; wiring `SystemB` into the server (a `/test/run-step`-style
seam or the System-A loop).

## 9. Delegated decisions (to log in decisions.md by the integrator/owner)
- Keep `alias` (load-bearing for the seed importer; not a dead leftover).
- Key the System-B MindQueue on `session_key` (continuing) + a System-B-owned per-`ticket_id`
  kickoff lock (step-0), resolving the current key at execution time — honors the W1/W3 carry-forward
  (codex plan-review F2, adopted over the earlier ticket_id-keyed simplification).
- Remove the spawn-adapter subsystem now (superseded, consumerless, dangling import) vs. leaving dormant.
- Relocate the dispatcher machine-lock to `runtime/lock.py` (KEEP honored; codex F3), not delete.
- Robust proposal-present invariant = advanced-OR-proposal-present, so a below-ceiling auto-accept
  is not misread as `errored` (codex F1), tested via the real `file_proposal` writer.
- Leave inert dispatcher timing config knobs; defer the config-knob cleanup to its own pass.
