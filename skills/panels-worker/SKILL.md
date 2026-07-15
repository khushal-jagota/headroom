---
name: panels-worker
description: Working a Panels ticket, one step at a time.
---

# Working a Ticket

You work one Ticket, one Stage at a time. You're given the current Stage only; do
that work, then propose what you've done. The Ticket may move to the next Stage, rest
with the user, rest in paired work, or stop for approval. Your job is only ever the
single Stage in front of you.

## The system

Panels is a workspace for agents, where the user's work lives. It's organized as sprints, sprint items, and tickets:

- A **sprint** is a two-week block of work.
- A **sprint item** is a goal — something the user wants to achieve. Tickets are generated for it, for the individual bits of that work.
- A **ticket** is one unit of work.

Sprint items and tickets can also stand alone, outside a sprint. A ticket moves through stages, and at each stage it needs one thing from you.

## Your ticket and your specialist
Which Stages a Ticket has, and what each needs, depend on its Worker type. Run
`panels worker my-ticket` — it names your worker skill and reports the current
Stage, effective ownership, and scope. Invoke that skill with
`skill_view("<name>")` and follow it for the stage-by-stage work. You handle the one
current step only.

Worker skills:
- `panels-worker-coding` — coding tickets.
- `panels-worker-new-worker` — new_worker tickets (designing another worker).
- `panels-worker-exploration` — exploration tickets.
- `probe-worker` — the probe fixture Worker type (test genericity proof).

### Who owns the current Stage

Every non-terminal Stage has a default ownership mode. A Ticket can override that
default for one Stage; the current Stage's override wins, otherwise its default applies.
Ownership says who drives the current Stage. It is separate from Ticket scope.

- **Worker** — the Employee may be discovered and run automatically when every other
  eligibility condition also allows it. Scope still decides whether a proposal is
  accepted below the ceiling, parked through **Continue** at the ceiling, or prevented
  by **Stop**.
- **User** — the Ticket rests in user takeover and is never dispatched automatically.
  User-completed work returns through the Chief's external-work create or reconcile
  operation; there is no worker self-settle or self-proposal substitute. The Chief moves
  the ceiling to the reconciled Stage, preserves an explicit Stop (otherwise Continue
  remains), and the entered Stage's ownership then controls what happens next.
- **Paired** — the Ticket rests in paired work and is never dispatched automatically.
  Ordinary Ticket Chat is the continuation path: the user's message reaches this
  Ticket's durable Employee session and worker context. If the turn ends without a
  proposal, the Ticket remains paired. A real proposal always parks for approval, even
  when scope would auto-accept the same proposal from a worker-owned Stage. Approval
  advances normally and the next Stage's ownership takes effect.

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
- **`panels worker note <id> <field> --body-file -`** — preserve user guidance next to a field without touching its value.
- **`panels ticket create --worker-type <id> --title "…"`** — create a Ticket, when a
  step spins off a new one. Worker type is required; choose it for the work being created.

Never invoke `panels chief`.

## How to complete this effectively

### Cross-cutting disciplines

- **Ground before you opine.** Inspect the relevant source — code, docs, the ticket itself — narrowly, before you shape anything.
- **Keep it lean.** Tight enough that a person will actually read it. Bloat that looks like thinking is just fog.
- **Say the job plainly.** Write for the human first: top-level, brief, and readable without technical excavation. What the work is, what done means, and only the constraints that change how it's done.
- **Separate facts from choices.** Keep what's known apart from what's still an open decision.
- **Do not over-specify gated fields.** Success, approach, and plan proposals should not become long technical design docs. They should usually be a short paragraph or a few bullets, with implementation detail only when it materially changes the decision the user is approving.
- **Use structure to improve scanning, not to add content.** Prefer short headings, labeled bullets, or numbered steps when they make a proposal easier to scan. Do not add new categories of information, filler sections, or boilerplate just because the proposal is structured. If one plain sentence is clearest, use one sentence.
- **Keep proposal shapes predictable.** A success proposal should foreground the user-visible outcome. An approach proposal should separate the chosen route from important constraints or tradeoffs. A plan proposal should usually be numbered steps. An implementation proposal should make the ticket’s intent, how the work was done, and evidence of completion easy to review. A closeout proposal should separate what was actually closed out (merge, deploy, follow-up, bookkeeping) from how it was verified. Keep each section short.
- **Prefer clarity over coverage.** If a proposal is getting progressively more detailed, stop and compress it back to the decision-level shape a human can approve.
- **Explain your proposal judgment in chat.** After you propose a gated field, your chat reply should briefly explain why you shaped the proposal that way: the user direction, source facts, judgment calls, and any real alternatives considered or ruled out. Do not merely announce that the field is ready, repeat which field you proposed, or restate approval/status details the UI already shows. Keep the formal proposal itself normal, concise, and approval-ready; keep the chat rationale short and separate.
- **Use recap as cold-user orientation.** The recap is not a work log. Keep it short and scannable, so a cold user can read it alongside the title and understand what the ticket is, where the current step stands, and the one or two key facts that matter now.
- **Honor Kickoff and field user notes.** The `kickoff` field is the user-approved premise and intake context: the user's wording, source context, boundaries, and advice. Each field's `user_note` is user direction for that step. Treat both as guidance to honor, not as hidden agent scratchpad and not as canonical success/approach/plan/implementation/closeout text to copy blindly.
- **Preserve direct user guidance with field notes.** When the user gives direction during a worker step that should survive the turn, write it to the relevant field with `panels worker note` and phrase it as user-directed guidance.

### Ticket-owned planning artifacts

Ticket-owned artifacts are durable work products that make the work easier to understand; they are not a reason to bloat a gated field. Store them in the ticket's managed file tree and link them from the relevant proposal, note, implementation, or closeout using a served `/files/tickets/...` Markdown link. For example, create `data/files/tickets/<ticket-id>/artifacts/ui-plan.html` and link it as `[UI plan](/files/tickets/<ticket-id>/artifacts/ui-plan.html)`.

For frontend changes, normally include a small HTML planning artifact that shows the intended layout, important states, and interactions before implementation starts. When the ticket's purpose is to experiment in HTML to discover the design, that HTML is the exploration and work product; do not require a second planning artifact first. Apply this as judgment-based guidance, not a mechanical gate: create an artifact when seeing the thing will materially improve planning, approval, or execution.
