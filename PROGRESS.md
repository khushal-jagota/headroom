# PROGRESS

Read this first after any context compaction. It is the build's memory — a snapshot of where
things stand right now, not a history log.

## Where we are (2026-07-07): the employee runtime is wired end-to-end, but the core loop can't run yet

The **runtime redesign** is essentially built. A ticket now has a single durable **employee** (a
Hermes session) that takes it through its stages; the human's job is to approve and to set how far
the employee may go on its own (its **scope**). The authoritative fix-spec is
`orchestration/runtime-redesign/notes.md`; the visual language is `DESIGN.md`.

**`./verify` is PASS** (ruff / mypy / unit / build / e2e all green) at the current HEAD.

### Built and green

- **The runtime rewire — three waves, all committed to main.**
  - **W1 — the employee primitive** (`63152e5`, `src/planner/minds/`): spawns and talks to a Hermes
    gateway child, one child per run, with a per-session queue that keeps one step in flight per
    employee.
  - **W2 — deletions** (`f2ed748`): removed the old dispatcher/claim machinery's now-dead surface
    (plan-tree, seed API, freeze columns, dormant columns).
  - **W3a — ticket status + System B** (`b2bef50`): the whole `dispatch/` package is gone; a ticket
    carries a `status` (empty / agent_working / awaiting_approval / errored) written through one
    atomic door (`set_run_status`). System B runs an employee's step.
  - **W3b — System A + the gate + one CLI** (`6481356`): System A polls today's tickets for ones
    ready to move and fires the employee; an approval or unblock pokes it immediately (the timer is
    a backstop). The propose→approve gate wires an approval into the next run. The CLI is a single
    `plan` binary.
- **Chat talks to a real employee** (`c5b65ba`): the ticket chat reaches a live Hermes mind over the
  real gateway subprocess, replacing a broken in-process import that always read "gateway offline".
  It uses the owner's default Hermes home for now.
- **Slash commands + skills in the chat** (`ae0936d`): typing `/` in a ticket's chat opens the
  gateway's own catalogue (135 commands + 62 skills, cached); picking a **skill** runs it into that
  ticket's employee. Display commands are insert-only for this first slice.
- **An editable scope row in the ticket header** (`e43ac37`, `29fd53d`): "approved until [stage]
  then [stop / propose]", rendered as enum pills, changeable anytime — the scope is now visible and
  settable from the ticket itself, not only inside an approval. A shared `ceilingOptions` feeds both
  this row and the approval picker, so neither can offer approving back past where the ticket is.
- **Errors are visible in the event log** (`6ed409b`): an errored run shows its reason (amber-marked,
  e.g. "agent init failed: Unknown skill(s): planning-worker"), and the chat's spurious "Gateway
  Offline" is fixed (it was a stale session after a gateway restart; the adapter now mints a fresh
  session and re-persists the new key).

## The one blocker: the core loop can't actually run

The employee is spawned with a role skill named **`planning-worker`**, and that skill is not
installed in the Hermes home. So every real run errors with **"Unknown skill(s): planning-worker."**
The plumbing is all there — the employee spawns, the gate fires, the error surfaces — but the loop
does no work.

**Fixing it needs two things:** (1) a **dedicated planner Hermes home** (so planner employees are
isolated from the owner's real `~/.hermes`), and (2) the **authored `planning-worker` skill**
installed in it. This is the top next step.

## Immediate next step

Stand up the dedicated planner Hermes home and author the `planning-worker` role skill, then prove
one ticket runs a step end-to-end through the live employee.

## Also not done (known gaps, none blocking the step above)

- **Errored has no recovery** — a failed ticket is stuck; there's no retry or clear.
- **Rollover / daily "today"** auto-generation isn't wired (the boundary-rebuild wave owns this, plus
  rewriting the stale `skills/planning-boundary.md` / `planner-main.md`).
- **Chat isn't behind the per-session queue** yet.
- **Real streaming** of the reply (currently thinking-dots → full reply, no token stream).
- **Slash display commands** (exec/plugin) — insert-only for now.
- **The employee/scope rename sweep** — `orchestration/runtime-redesign/notes.md` still says "one
  mind per ticket"; some code identifiers keep the "mind" name (`src/planner/minds/`, `MindQueue`).

## Reference

- `orchestration/runtime-redesign/notes.md` — the authoritative fix-spec (what v2 got wrong, what we
  fix, how).
- `orchestration/runtime-redesign/spikes/01-hermes-linkage.md` — proved the gateway linkage;
  `spikes/02-slash-commands-in-chat.md` — the slash-commands spike.
- `DESIGN.md` — the visual language. `decisions.md` — every judgment call. `DOCS.md` — plain-language
  documentation of what exists.
