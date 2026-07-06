# W3a — Ticket status field + System B (runtime rewire, phase A)

## Scope

Replace the old claim / `runs` / breaker scheduling with the **ticket status field** and **System B**
(the set-off / run primitive that drives W1's `run_step`). This is the foundation of the rewire;
System A (readiness poll), the propose→approve gate, and the CLI rework are **W3b** (next ticket) —
do NOT build them here. Remove the old machinery this replaces.

**Baseline: committed W2 (`main` @ `f2ed748`).** Clean, `./verify` green. Map exact removal/edit
points against that tree.

You are a single Opus lead: do the plan, the codex reviews, and the implementation yourself. Do NOT
spawn sub-agents or use codex as an autonomous file-editor. Run codex only as a **read-only reviewer**.

## Contracts (read fully before planning — law)

- `orchestration/tickets/W3-runtime-rewire/ticket.md` — the full rewire design + **binding
  carry-forwards from W1** (System B serializes kickoff itself and resolves the mind's CURRENT
  `session_key` at execution time; the queue can't serialize step-0). Read it; W3a implements its
  status-field + System B parts.
- `orchestration/runtime-redesign/notes.md` — **Scheduling & runs**, **Tickets**, **Agent runtime**,
  **Principles** (esp. "approval machinery is invisible to the model"; "code owns all state stamps").
- `src/planner/minds/` (W1, committed) — `run_step(session_key|None, role, prompt, on_event, *, home,
  hermes_python, ...) -> RunResult`, `MindQueue`, `minds/fake.py` (the test double). Read `runner.py`,
  `queue.py`, and W1's report for the carry-forwards.
- The post-W2 code + tests on disk (authoritative for call sites / schema).

## What to BUILD

1. **Ticket status field** (the reframed "lock"). `status ∈ {empty, agent_working,
   awaiting_approval, errored}` + a `worker` field (who/what is working it), written **together in
   one atomic UPDATE** by the code — never by the agent. `SCHEMA_VERSION` 2→3. The board/UI read this
   one field. Replace the removed claim/runs/breaker columns.
2. **System B — set-off / run primitive.** "Run step N of ticket X": assemble role env → call
   `run_step` (through `MindQueue` for continuing steps; **serialize kickoff itself** in System B) →
   observe the single run-end → run the **proposal-present invariant** (code knows the step it asked
   for → the expected proposal must be present → `awaiting_approval`; absent → `errored`, "you haven't
   done your job") → write status atomically. Single writer. Kickoff = step 0 through the same call.
   **Resolve the mind's current `session_key` at execution time**, not enqueue time.
3. Grant / ceiling (`ceiling`/`at_cap`) STAYS untouched (it's the auto-approve policy; W3b wires the
   gate to it). Model-invisible approval: the mind only ever gets next-step prompts + chat.

## What to REMOVE (the old machinery this replaces)

`runs` table; `claim_lock`/`claim_expires`/`auto_blocked`/`consecutive_failures` columns (+ `alias`
if it's a dead migration leftover — confirm); `src/planner/dispatch/logic/{breaker,claims}.py`, the
dispatch runtime's claim/breaker path, the `run` CLI group + `X-Plan-Run-Id`/`X-Plan-Claim` headers +
run/claim/heartbeat/close endpoints. Repair every reference. (System A's readiness poll is W3b — if
removing the dispatch runtime leaves a gap the poll will fill, stub/park it cleanly and note it, don't
build System A here.)

## Testing (hermetic)

Test the status field + System B against **W1's fake gateway** (`minds/fake.py`) — no real gateway,
no model calls. Cover: the four status transitions, the proposal-present invariant (present →
`awaiting_approval`, absent → `errored`), kickoff-as-step-0, kickoff serialization, current-key
resolution. Remove the old claim/breaker/runs tests. Real assertions, no skip/xfail (skip-scan fails).

## Out of scope (W3b / later)

System A readiness poll + fast path; the propose→approve gate UX; the CLI verb rework
(`--sprint-item`, `list --day`, drop `queue pickup`); planner-home provisioning + the worker skill.
Don't build these; leave clean seams.

## Acceptance

- `.venv/bin/ruff check .` + `.venv/bin/mypy src/` clean; the status/System-B tests green via the fake
  gateway; no dangling refs from the removed machinery. Fresh DB builds at `SCHEMA_VERSION` 3.
- Report checks yourself (ruff/mypy/pytest unit+e2e) — NOT full `./verify` (the integrator runs that
  serially). Deliver `report.md` + `impl-review.md` as MESSAGES if the harness blocks writing them.
- Integrator will spot-check: the single atomic status write, the proposal-present invariant, kickoff
  serialization + current-key resolution.

## Pipeline (single Opus lead)

Plan (write `plan.md`: exact files, the schema change, System B's shape, every removed reference, the
test list) → codex read-only plan-review (`codex exec --model gpt-5.5 --sandbox read-only -c
model_reasoning_effort="high" ... < /dev/null`, buffered — be patient, watch file size) → sense-check
→ implement → codex read-only diff-review → fix real findings → checks green → report. Drive it in one
continuous pass; do not idle between steps.

## Boundaries

Repo only. **Never run git** (the integrator commits). Never modify `~/.hermes` (read-only research of
`tui_gateway/` allowed), PROGRESS.md, decisions.md, CLAUDE.md, notes.md, or the ticket files. If any
file changes under you unexpectedly, STOP and escalate (a concurrent writer). Do not run the real
gateway in a verify-gated test.
