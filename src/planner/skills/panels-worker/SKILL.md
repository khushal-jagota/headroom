---
name: panels-worker
description: Working a Panels ticket, one step at a time.
---

# Working a Ticket

You work one Ticket, one Stage at a time. You're given the current Stage only; do
that work, then propose what you've done. Your job is only ever the
single Stage in front of you.

## Your ticket and your specialist
Which Stages a Ticket has, and what each needs, depend on its Worker type. Run
`panels worker my-ticket` — it names your worker skill and reports the current
Stage, effective ownership, and scope. Invoke that skill.

Worker skills:
- `panels-worker-coding` — coding tickets.
- `panels-worker-general` — general tickets (a catch-all worker for arbitrary work with a minimal lifecycle).
- `panels-worker-debugging` — debugging tickets (understanding a reported bug, diagnosing its structural cause, and defining the implementation handoff).
- `panels-worker-new-worker` — new_worker tickets (designing another worker).
- `panels-worker-exploration` — exploration tickets.
- `panels-worker-initiative-planning` — initiative_planning tickets (planning a confirmed direction across multiple Tickets).
- `panels-worker-product-design` — product_design tickets (designing holistic product flows and implementation-ready interactive artifacts).
- `panels-worker-planning-day` — planning-day tickets (planning the morning's Day with the user).
- `panels-worker-planning-midday-check` — planning-midday-check tickets (checking execution against the morning intent and carrying out any agreed intervention).
- `panels-worker-planning-sprint` — planning-sprint tickets (reviewing the current sprint and planning the next).
- `panels-worker-personal-task` — personal task tickets owned by the user, with optional explicit agent support.
- `probe-worker` — the probe fixture Worker type (test genericity proof).

### Who owns the current Stage

Every non-terminal Stage has a default ownership mode. A Ticket can override that
default for one Stage; the current Stage's override wins, otherwise its default applies.
Ownership says who drives the current Stage. It is separate from Ticket scope.

- **Worker** — the Employee may be discovered and run automatically when every other
  eligibility condition also allows it. Scope still decides whether a proposal is
  accepted below the ceiling, parked through **Continue** at the ceiling, or prevented
  by **Stop**.
- **User** — the Ticket rests at `user` and is never dispatched automatically.
- **Paired** — the Ticket gets one automatic opening turn for the current Stage, then
  rests at `paired` and is never started automatically again. Ordinary Ticket Chat is
  the continuation path: the user's
  message reaches this Ticket's durable Employee session and worker context. When clarity is reached, the worker can propose.

**Take over** sets an explicit `user` override on the current Stage, including while a
run is active. **Release** clears that current-Stage override; it does not reveal an
older override, and the Stage default applies again. Respect the Ticket's effective
ownership. Do not treat Take over, Release, or an owner change as a scope change.

### The CLI

Everything runs through the `panels` command — `panels --help` for full usage. The tools you use:

- **`panels worker my-ticket`** — the Ticket you're on: who you are, its current Stage,
  effective ownership, and scope.
- **`panels ticket show <id>`** — read any ticket.
- **`panels ticket ownership <id> --stage <stage> --mode worker|user|paired|default`** —
  set or clear a Stage ownership override when the user directly instructs that change.
- **`panels worker propose <id> --body-file - --recap "…"`** — propose the ticket's current gated field; body arrives on stdin or via `--body-file`, and every proposal must also set a recap.
- **`panels worker recap <id> --body-file -`** — update the running recap outside a proposal.
- **`panels worker request-user-help [ticket-id]`** — use this only when you cannot responsibly continue without important user input. Put the free-form request in your ordinary Ticket Chat response, then call this no-payload command. The Ticket enters `needs_user`: automatic work stays paused and Chat remains available until the user explicitly releases it. Do not use this for ordinary discussion, proposals or approvals, permission prompts, Stop, or confirmed Worker errors.
- **`panels worker note <id> <field> --body-file -`** — replace user guidance next to a field without touching its value. Add `--append` to preserve the existing guidance and add new text.
- **`panels ticket create --worker-type <id> --title "…"`** — create a Ticket when the
  current approved step spins off a new one. Before creating it, load and follow
  `panels-ticket-creation`; this Worker skill still owns the current Stage's authority
  and approved scope.


## How to complete this effectively

### Cross-cutting disciplines

- **Treat deployed apps as immutable.** Repository work must happen in a Git checkout
  under the assigned workspace, normally in an isolated worktree. Never edit or run
  tests from `~/Deployments/Panels/current/app` or another deployed app artifact. If
  the source checkout cannot be found, request user help instead of changing the
  running installation.
- **Do not over-specify fields.** 
- **Explain your proposal judgment in chat.** After you propose a gated field, your chat reply should very briefly explain why you shaped the proposal that way. Do not merely announce that the field is ready, repeat which field you proposed, or restate approval/status details, the UI already shows this. 
- **Use recap as cold-user orientation.** The recap is not a work log. Keep it short and scannable, so a cold user can read it alongside the title and understand what the ticket is and what was done before this proposal to refresh their mind before reviewing this proposal.
- **Preserve direct user guidance with field notes.** When the user gives direction during a worker step that should survive the turn, write it to the relevant field with `panels worker note` and phrase it as user-directed guidance.

### Ticket-owned planning artifacts

Ticket-owned artifacts are durable work products that make the work easier to understand; they are not a reason to bloat a gated field. Store them in the ticket's managed file tree and link them from the relevant proposal, note, implementation, or closeout using a served `/files/tickets/...` Markdown link. For example, create `data/files/tickets/<ticket-id>/artifacts/ui-plan.html` and link it as `[UI plan](/files/tickets/<ticket-id>/artifacts/ui-plan.html)`.

For frontend changes, normally include a small HTML planning artifact that shows the intended layout, important states, and interactions before implementation starts. When the ticket's purpose is to experiment in HTML to discover the design, that HTML is the exploration and work product; do not require a second planning artifact first. Apply this as judgment-based guidance, not a mechanical gate: create an artifact when seeing the thing will materially improve planning, approval, or execution.
