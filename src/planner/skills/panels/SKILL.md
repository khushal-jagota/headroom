---
name: panels
description: Orientation to the Panels system — its pieces, the daily cycle, where it lives, the CLI, and the skills.
---

# Panels

Panels is a **workspace for agents**, where the user's work lives. It includes a planning system.

The planning system has these pieces:

- A **day** is what the user wants to get done on a planning date.
- A **sprint** is a two-week block of work.
- A **sprint item** is a goal or outcome inside a sprint, or in the backlog when unscheduled.
- A **ticket** is one unit of work, often done by an agent alongside the user.
- An **idea** is a loose thought that may or may not become committed work.

Tickets have a Worker type that sets their stages and worker. Worker types include
`coding` (product or repo work), `new_worker` (creating a new kind of worker),
`exploration` (making an undefined direction clearer), and `initiative_planning`
(working out shared decisions before creating downstream Tickets). New Worker types
are added here as they ship.

Ticket workers shape a Ticket through **Kickoff → Success → Approach → Plan →
Implementation → Closeout → Done**, filling one canonical field at each step.

## Communication

Inspect the relevant source, docs, or workspace state before advising. Keep communication
concise, practical, and easy to scan: say the job plainly, separate facts from judgment
and required user decisions, and use structure only when it improves clarity. Name things
for exactly what they are, avoid speculative machinery, and preserve direct user guidance.

## Ticket-owned artifacts

A ticket can own durable work products such as HTML, images, Markdown documents, and other files. These live in Panels-managed ticket storage — by default under `data/files/tickets/<ticket-id>/...` — and appear in ticket Markdown through ordinary links such as `[UI plan](/files/tickets/<ticket-id>/artifacts/ui-plan.html)`. Use the served `/files/tickets/...` link rather than exposing a local filesystem path; Panels owns how the file is previewed or opened.

Artifacts complement the ticket record. Kickoff, success, approach, plan, implementation, closeout, recaps, and notes remain concise canonical Markdown, with links to richer work when it helps.

## Rollover

The `panels-rollover` skill carries the plan across the 5am boundary. It drafts the
new day's likely overview and records only obvious carryover candidates pending review.
It does not put tickets on today until the user agrees; broad sprint reprioritization
stays in sprint planning.

## The CLI

Everything runs through the `panels` command — run `panels --help` to see what it can do. It talks to the Panels server and database.

The command groups describe both the object being changed and the operation's authority:

- `panels day ...` for planning and operating on a day.
- `panels ticket ...` for ordinary, actor-neutral ticket creation, inspection, organization, and approval.
- `panels sprint ...` and `panels sprint item ...` for planning and populating sprints.
- `panels worker ...` for the gated worker flow: proposals, recaps, and notes.
- `panels chief ...` only for importing reality established outside Panels. Its two explicit operations reconcile an existing ticket or create a populated ticket from external work; it is not a general ticket-editing surface.

## Skills

- **`panels-worker`** — working a single ticket: shaping it through its stages, executing it, and reviewing it.
- **`panels-rollover`** — carrying the plan across a day boundary.
- **`panels-sprint-planning`** — planning and reconciling at the sprint level.
