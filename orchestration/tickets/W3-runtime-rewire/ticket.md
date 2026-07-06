# W3 — Runtime rewire (remove old scheduling → status field + System A/B + gate + one CLI)

## Scope

Replace the old claim/`runs`/breaker/dispatcher scheduling with the DECIDED runtime model. This is
the interlocking heart of the redesign — the ticket status field, System A (readiness), System B
(set-off on the W1 primitive), the bundled propose→approve gate, and the one-CLI surface all move
together. Plan the WHOLE rewire coherently, then implement in phases.

**Baseline: the post-W2 tree** (W2 has removed plan-tree/`boundary_runs`/seed-surface/dormant
columns/freeze surface and bumped `SCHEMA_VERSION` to 2). The Opus planner maps exact
removal/replacement points against that stable code — do not plan against pre-W2 state.

## Contracts (read fully before planning — law)

- `orchestration/runtime-redesign/notes.md` → **Scheduling & runs**, **Tickets**, **Agent runtime**,
  **CLI**, and the **Principles** (esp. "approval machinery is invisible to the model", "code owns
  all state stamps"). These are the design.
- `src/planner/minds/` (W1, landed) → the agent-operation primitive. `run_step(...) -> RunResult`
  is what System B calls. Read `runner.py`, `queue.py`, `gateway.py`, and **W1's report + the
  `queue.py` docstring** for the binding carry-forwards below.
- `orchestration/runtime-redesign/spikes/01-hermes-linkage.md` → protocol/behavior ground truth.
- The post-W2 code + tests as they exist on disk.

## Binding carry-forwards from W1 (do not re-derive)

- **System B must serialize kickoff itself** and **resolve the mind's CURRENT `session_key` at
  execution time** (not at enqueue). The per-session `MindQueue` cannot serialize two step-0 runs
  (both have no key yet). Wire continuing steps through `MindQueue`; own kickoff serialization in
  System B.
- Every run ends on `message.complete`; System B maps `RunResult` → ticket status. No agent-side
  "blocked"/"close"; no run deadline; `errored` **stops and surfaces**, no auto-retry.

## What to BUILD (the replacement)

1. **Ticket status field (the reframed "lock").** `status ∈ {empty, agent_working,
   awaiting_approval, errored}` + a `worker` field (who/what is working it), written **together in
   one atomic UPDATE** by the code (System B) — never by the agent. Replace `claim_lock`/
   `claim_expires`/the `runs` table/breaker columns (`auto_blocked`/`consecutive_failures`) with
   this. `SCHEMA_VERSION` 2→3. The UI/board reads this one field.
2. **System B — set-off / run primitive.** Given "run step N of ticket X": assemble role env,
   call W1's `run_step` (through `MindQueue` for continuing steps; serialize kickoff), observe the
   single run-end, run the **proposal-present invariant** (the code knows what step it asked for →
   the expected proposal must be present → `awaiting_approval`; absent → `errored`, "you haven't
   done your job"), and write status atomically. Single writer. Kickoff = step 0 through the same
   call. Remove `dispatch/runtime.py`, `dispatch/logic/{breaker,claims}.py`, the dispatch API's
   claim/run endpoints.
3. **System A — readiness / poll.** Decide what is *ready* (deps met, not blocked, nothing running)
   by **querying candidate tickets only** (not a full-table rescan); a **fast path** so an approval/
   unblock pokes System B immediately, with the timer as a backstop. Polls state, never the model.
   "Can't proceed yet" = simply don't start the agent (no agent stop-condition).
4. **Bundled propose→approve gate (tickets).** A content change (title/recap/a field) rides the
   agent's end-of-step proposal — one approve-act (DECIDED: bundle, not a separate diff). Grant /
   ceiling (`ceiling`/`at_cap`) STAYS — the code's auto-approve-up-to-a-ceiling policy; from the
   model's view every step still "awaits approval". Approval NEVER travels to the model.
5. **One CLI, scope via naming.** Rework the `run` CLI group + run/claim headers out (code observes
   run end; no agent `heartbeat`/`close`; claim → code-owned status). `--item`→`--sprint-item`;
   `list tickets --day/--date`; drop `queue pickup`. Single `plan` binary, responsibilities by verb/
   skill naming.

## Out-of-band sub-part (built, validated separately — NOT in hermetic verify)

- **Planner-home provisioning** (dedicated `HERMES_HOME`: model config + creds + v2 skills) and the
  **worker role skill** (v2-namespaced; port intent from v1 productivity skills + kanban
  worker-protocol lessons). These need live hermes + creds → validate via a top-level smoke (like
  W1's `smoke.py`), not the verify gate. If this bloats the wave, carve it to a W3-follow-up ticket.

## Testing (hermetic)

Test System A/B/gate/status against **W1's fake gateway** (`minds/fake.py`) — no real gateway, no
model calls. Cover: status transitions (all four), the proposal-present invariant (present→
awaiting_approval, absent→errored), kickoff-as-step-0, readiness (candidate query + fast path),
grant/ceiling auto-approve, and the bundled propose→approve gate. Remove the old dispatcher/claim/
breaker/runs tests. Real assertions, no skip/xfail (verify's skip-scan fails on them).

## Acceptance for integration

- `.venv/bin/ruff check .` + `.venv/bin/mypy src/` clean; full `./verify` **green** (integrator runs
  it). Fresh DB builds at `SCHEMA_VERSION` 3.
- The old scheduling machinery is gone with no dangling refs; the new status model + System A/B +
  gate are exercised by tests via the fake gateway.
- Integrator load-bearing spot-check: System B's status writer (single atomic write, proposal-present
  invariant), kickoff serialization + current-key resolution, System A readiness/fast-path.

## Pipeline (dispatch instruction)

**Opus orchestrator, Opus planner, Opus implementers, codex reviews (plan + diff).** The planner
produces a PHASED implementation plan (schema+status → System B → System A → gate → CLI) mapped to
post-W2 code; codex plan-review; sense-check; implement phase by phase (Opus), integrating
internally; codex diff-review; report. Given the interlock, keep it coherent under one orchestrator.

## Boundaries

Repo only. Never run git. Never modify `~/.hermes` (read-only research of `tui_gateway/` allowed for
protocol facts), PROGRESS.md, decisions.md, CLAUDE.md, notes.md, or this ticket. Do not run the real
gateway in a verify-gated test.
