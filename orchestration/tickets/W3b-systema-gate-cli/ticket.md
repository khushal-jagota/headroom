# W3b — System A + propose→approve gate + one-CLI + server wiring (runtime rewire, phase B)

## Scope

Finish the runtime rewire on top of W3a: build **System A** (readiness poll) that drives
`SystemB.set_off`, wire the **bundled propose→approve gate** to set off the next step, wire **System
B into the running server**, and land the **one-CLI** rework. This completes the loop: approval /
readiness → System A → System B → `run_step` → status.

**Baseline: committed W3a (`main` @ `b2bef50`).** W3a left explicit seams: `runtime/lock.py`
(machine-lock for System A), `runtime/system_b.py` (`SystemB.set_off`, dormant — nothing calls it
yet), `_pickup()` → `[]` (readiness stub, queue shape kept). Map against that tree.

You are a single Opus lead: plan, codex reviews, implementation, checks, report — all yourself. Do
NOT spawn sub-agents. Use codex ONLY as a read-only reviewer.

## Contracts (read fully before planning — law)

- `orchestration/tickets/W3-runtime-rewire/ticket.md` — the full rewire design (System A / gate / CLI
  parts) + the model-invisible-approval principle + grant/ceiling.
- `orchestration/tickets/W3a-status-systemb/report.md` — W3a as built + the **seams for W3b**.
- `orchestration/runtime-redesign/notes.md` — **Scheduling & runs** (System A: candidate-query poll,
  fast path, "can't proceed yet" = don't start; DECIDED items), **Tickets** (bundled propose→approve
  gate; grant/ceiling KEEP), **CLI**, **Principles** (approval invisible to the model; code owns
  state stamps).
- `src/planner/runtime/system_b.py` + `src/planner/minds/` (W1+W3a) — what System A calls.
- The post-W3a code on disk.

## What to BUILD

1. **System A — readiness / poll.** Decide which tickets are *ready* to be worked (dependencies met,
   not blocked, and NOT already running per the `status` field) by **querying candidate tickets
   only** (a WHERE on status/readiness — not a full-table rescan). For each ready ticket, call
   `SystemB.set_off(ticket_id, role, prompt)`. Polls state, never the model. "Can't proceed yet" =
   simply don't start it (no agent stop-condition). Reuse `runtime/lock.py`. Rebuild the readiness
   query that W3a stubbed to `_pickup()→[]`.
2. **Fast path.** Keep the timer as a backstop; add a fast path so an approval / unblock pokes System
   A/B **immediately** rather than waiting a full tick.
3. **Bundled propose→approve gate.** On a human approval of a ticket's gating proposal, the next step
   is set off (kickoff = step 0 through the same `set_off`). Grant / ceiling (`ceiling`/`at_cap`)
   drives the code's auto-approve-up-to-a-ceiling policy — from the model's view every step still
   "awaits approval"; **approval never travels to the model**. A content change rides the proposal
   (one approve-act — the DECIDED bundle model), not a separate diff.
4. **Wire System B into the server.** Replace the removed dispatcher loop: `core/loops.py` (or the
   equivalent) runs the System A poll (timer + fast path) which drives `SystemB.set_off`. Construct
   `SystemB` with the planner home + hermes interpreter from config.
5. **One-CLI rework.** The `run` group is already gone (W3a). Add `list tickets --day/--date`; rename
   `--item` → `--sprint-item`; drop `queue pickup` (the board's status shows what's ready). Single
   `plan` CLI; responsibilities by naming.

## Out-of-band sub-part (build the seam, validate separately — NOT in hermetic verify)

- **Planner-home provisioning** (dedicated `HERMES_HOME` + model config + creds + the v2 worker
  skill) and the **worker role skill**. These need live hermes → validate via a top-level smoke (like
  `minds/smoke.py`), NOT the verify gate. If wiring System B into the server would make `./verify`
  spawn a real gateway, gate that behind config/env so the **hermetic tests never touch the real
  gateway** (use `minds/fake.py` in tests; the real spawn is off by default / in the out-of-band
  smoke). If this bloats the wave, carve the home+skill to a follow-up ticket and note it.

## Testing (hermetic)

Test System A (readiness query + fast path) and the gate→set_off wiring against **W1's fake gateway**
(`minds/fake.py`) — no real gateway, no model calls. Cover: readiness selects only ready tickets
(status/deps/blocked), approval triggers set_off (kickoff = step 0), grant/ceiling auto-approve
behavior, the CLI verb changes. Real assertions, no skip/xfail.

## Acceptance

- `.venv/bin/ruff check .` + `.venv/bin/mypy src/` clean; System A / gate / CLI tests green via the
  fake gateway; full `./verify` **green** (integrator runs it). Fresh DB builds (schema unchanged
  from W3a unless you have a reason — flag it).
- Run your own checks (ruff/mypy/pytest unit+e2e) — NOT full `./verify`. Deliver `report.md` +
  `impl-review.md` as MESSAGES if the harness blocks report-files.
- Integrator will spot-check: the readiness query (candidate-only, respects `status`), the
  approval→set_off fast path, and that hermetic tests never spawn a real gateway.

## Pipeline (single Opus lead)

Plan (`plan.md`) → codex read-only plan-review (`plan-review.out`) → sense-check → implement → codex
read-only diff-review (`impl-review.out`) → fix real findings → checks green → report. Drive it in
one continuous pass; do not idle between steps.

## Boundaries

Repo only. **Never run git** (integrator commits). Never modify `~/.hermes` (read-only `tui_gateway/`
research allowed), PROGRESS.md, decisions.md, CLAUDE.md, notes.md, or the ticket files. If any file
changes under you unexpectedly, STOP and escalate. Do not run the real gateway in a verify-gated test.
