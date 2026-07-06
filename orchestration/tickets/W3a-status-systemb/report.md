# W3a — status field + System B: implementation report

Implemented by `w3a-lead` (single Opus lead) from the codex-reviewed plan. Delivered to the
integrator as messages (harness blocked agent report-files); persisted here. Codex raw output:
`plan-review.out`, `impl-review.out`, `impl-review-2.out`.

## Checks — integrator ran full `./verify` → **PASS**

- `ruff check .` clean · `mypy src/` clean (80 files) · `pytest tests/unit tests/e2e` = **107 passed,
  0 skips** · fresh DB builds at `SCHEMA_VERSION` 3 · skip-scan clean. Integrator `./verify`: PASS.

## Schema (`core/db.py`, 2→3)

`tickets`: dropped `claim_lock`, `claim_expires`, `auto_blocked`, `consecutive_failures`; added
**`status`** (`empty`/`agent_working`/`awaiting_approval`/`errored`, default `empty`) + **`worker`**
(nullable). Dropped the `runs` table + index. **Kept `alias` + index** — verified load-bearing for
seed-importer cutover idempotency (NOT dead; resolves the open `alias` question). Grant
(`ceiling`/`at_cap`) + `chat_session_key` untouched. Fresh-build only.

## Status machine + System B (as built)

- **`tickets/data.set_run_status(...)`** — the single-door writer: one atomic UPDATE (status + worker,
  + optional `chat_session_key`) inside a txn, then one `ticket_status_changed` event; `error` rides
  the event payload only. Typed `_Unset` sentinel = "leave key untouched"; `None` = clear. Verified
  **sole writer** of `tickets.status` (only caller in src is System B's `_run`).
- **`src/planner/runtime/`**: `system_b.py` (SystemB), `lock.py` (relocated machine-lock, kept for
  W3b System A), `__init__.py`.
- **`SystemB.set_off(ticket_id, role, prompt)`** → `MindQueue` keyed on **`ticket_id`** (stable
  per-mind identity). `_run_item` resolves the CURRENT `session_key` from the DB at execution time,
  then `run_step` (kickoff = create/step-0, else resume), maps `RunResult` + the proposal-present
  invariant, writes end status. The end write ALWAYS fires (guarded) — never stuck at `agent_working`.
- **Proposal-present invariant** (robust to auto-accept): `did_job = state advanced OR the pre-run
  gating field now carries a proposal` → `awaiting_approval`; else `errored`. `RunResult`
  errored/interrupted/child-death → `errored`.
- **System B is DORMANT in W3a** — nothing calls `set_off`; System A wiring is W3b (intended seam).

## Removed (whole dispatch package + spawn adapter) + repairs

Deleted all of `src/planner/dispatch/`. Removed the spawn-adapter subsystem (adapters
base/real/fakes/registry + `config.spawn_adapter`) — superseded by `run_step`. `authctx` collapsed
to actor-only human/agent (`require_claim`/`validate_carried_claim`/`X-Plan-Run-Id`/`X-Plan-Claim`
gone; `reject_agents`/`reject_agent_fields` kept). `tickets/api`: removed `/unblock` + `/runs` +
claim gates. `days/api`: removed `validate_carried_claim`. `cli`: removed the `run` group +
run/claim headers. `server`: dropped `dispatch_router`. `loops`: dropped the dispatcher loop + flock.
`testmode`: dropped `/test/tick-dispatcher`. `EventKind`: −6 run/claim/breaker kinds,
+`ticket_status_changed`. Frontend: board/ticket read `status` (agent-working/errored markers),
Unblock control + dead runHistory removed. `board_view` reuses `machine.has_pending_gating_proposal`
(DRY). `_pickup()` → `[]` (readiness = W3b, queue shape kept). **No dangling refs** (repo-wide sweep;
only docstrings mention removed names).

## Tests

NEW `tests/unit/test_system_b.py` (8, all via `minds/fake.py`, spawn injected): 4 transitions;
parked→awaiting; auto-accept-below-ceiling→awaiting (driven through the real `file_proposal` writer);
no-proposal→errored; gateway-error→errored; kickoff serialization; current-key resolution;
key-rotation-across-two-runs (hardened to block run A mid-run so B is provably enqueued before the
rotation persists); spawn-crash→errored (never stuck). Deleted `test_dispatch.py`, `test_runtimes.py`.
Fixed `test_authctx_routes.py`, `test_value_edit_logic.py`. E2E: `test_e30_` rewritten to a
dispatcher-free review→approve flow (keeps the anchor + approve coverage); `test_e31_` drops the
dispatcher tick+claim (keeps parked-proposal reload-restore).

## Codex dispositions

- **Plan-review = REPLAN, 6 findings — all ACCEPTED + amended:** F1 auto-accept latent bug (robust
  advanced-OR-proposal invariant + faithful test via the real writer); F2 keying (see below); F3
  machine-lock KEEP (→ `runtime/lock.py`); F4 typed `_Unset` sentinel; F5 fresh-fake-per-spawn
  sequencer; F6 seed-importer INSERT.
- **Diff-review = APPROVE-WITH-FIXES, 2 — both FIXED:** F1 (real bug) keying the queue on the
  rotating `session_key` split a mind across two keys → concurrent resume; fixed by keying on stable
  `ticket_id` + resolving the current key at execution time (rotation test proves it). F2 status could
  stick at `agent_working` on an unexpected exception; fixed so `_run` always writes the end status
  (spawn-crash test proves it).
- **Confirming pass (`impl-review-2.out`) = APPROVE-WITH-FIXES, production CONFIRMED correct:** 5
  confirmations incl. "MindQueue FIFO per ticket_id + execution-time DB key resolution avoids
  split-brain/stale-key/double-run", "System B remains the only production caller of `set_run_status`
  (one atomic write)", "no new production issue". Its one finding was TEST-strength only (rotation
  test wasn't a hermetic proof) → hardened; production untouched.
- **Reconciliation:** plan-review F2 preferred keying on `session_key` + a separate kickoff lock;
  diff-review F1 + spike 01 (the durable key rotates via auto-compression) show `session_key` is not a
  stable identity, so the final design keys on `ticket_id` and resolves the current key at execution
  time — satisfying the W1 carry-forward and subsuming kickoff serialization. A deliberate reversal on
  spike evidence.

## Seams for W3b

System A readiness poll + fast path (calls `SystemB.set_off`); the machine-lock consumer
(`runtime/lock.py`); the propose→approve gate (makes the invariant exact by parking); `queue pickup`
rebuild/drop + `--sprint-item` + `list --day`; planner-home + worker skill; wiring System B into the
server.

## Delegated calls (integrator-approved, logged)

- `alias` KEPT (load-bearing for seed cutover) — resolves the open question.
- Inert dispatcher config knobs left in place (config-knob cleanup is a deferred ledger item; W3b's
  System A may reuse the master switch).
- `e30` kept + rewritten (preserves anchor + approve coverage) rather than deleted; `board_view` DRY.
- advisor tool unavailable → leaned on the mandated codex reviews as the second opinion.

## Integrator

Spot-checked `system_b.py` (sole writer, always-fires end write, robust proposal-present invariant,
ticket_id keying + exec-time key resolution) and `set_run_status` (one atomic UPDATE dissolving the
runless orphan) — sound. Full `./verify` PASS. Committed to `main`.
