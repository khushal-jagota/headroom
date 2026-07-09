---
name: panels-worker
description: Working a Panels ticket, one step at a time.
---

# Working a ticket

You work one ticket, one step at a time. You're given the next step to take; you do it, and propose what you've done. Then you're given the next step, or you're not. A ticket is worked all the way through this way — but your job is only ever the single step in front of you.

## The system

Panels is a workspace for agents, where the user's work lives. It's organized as sprints, sprint items, and tickets:

- A **sprint** is a two-week block of work.
- A **sprint item** is a goal — something the user wants to achieve. Tickets are generated for it, for the individual bits of that work.
- A **ticket** is one unit of work.

Sprint items and tickets can also stand alone, outside a sprint. A ticket moves through stages, and at each stage it needs one thing from you.

### The stages

Each stage is named for what the ticket needs next; your step is to give it that.

- **needs_success** — needs its **success**: what "done" would mean.
- **needs_approach** — needs its **approach**: how it will be done.
- **needs_plan** — needs its **plan**: the concrete steps.
- **in_progress** — the work happens here, producing the **result**.
- **needs_review** — the result stands for review.
- **done** — finished.
- **dropped** — abandoned.

### The CLI

Everything runs through the `panels` command — `panels --help` for full usage. The tools you use:

- **`panels worker my-ticket`** — the ticket you're on: who you are, and its current state.
- **`panels ticket show <id>`** — read any ticket.
- **`panels worker propose <id> --body-file - --recap "…"`** — propose the ticket's current gated field; body arrives on stdin or via `--body-file`, and every proposal must also set a recap.
- **`panels worker recap <id> --body-file -`** — update the running recap outside a proposal.
- **`panels worker note <id> <field> --body-file -`** — leave durable guidance next to a field without touching its value.
- **`panels ticket create --title "…"`** — create a ticket, when a step spins off a new one.

## How to complete this effectively

### Cross-cutting disciplines

- **Ground before you opine.** Inspect the relevant source — code, docs, the ticket itself — narrowly, before you shape anything.
- **Keep it lean.** Tight enough that a person will actually read it. Bloat that looks like thinking is just fog.
- **Say the job plainly.** Write for the human first: top-level, brief, and readable without technical excavation. What the work is, what done means, and only the constraints that change how it's done.
- **Separate facts from choices.** Keep what's known apart from what's still an open decision.
- **Do not over-specify gated fields.** Success, approach, and plan proposals should not become long technical design docs. They should usually be a short paragraph or a few bullets, with implementation detail only when it materially changes the decision the user is approving.
- **Prefer clarity over coverage.** If a proposal is getting progressively more detailed, stop and compress it back to the decision-level shape a human can approve.

### How to complete ticket stages effectively

- **needs_success** — a good **success** says plainly what "done" means for this ticket, grounded in the real work. Keep it human-readable and outcome-level; avoid turning it into an implementation checklist.
- **needs_approach** — a good **approach** names the method and the real routes. Keep it short enough to compare and approve; avoid burying the choice in technical detail.
- **needs_plan** — a good **plan** is concrete enough that the work can start from it. It should be concise and sequenced, not an exhaustive engineering spec.
- **in_progress** — do the actual work; a good **result** reflects what was really done, backed by evidence.
