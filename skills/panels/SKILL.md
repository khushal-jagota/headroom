---
name: panels
description: Orientation to the Panels system — its pieces, the daily cycle, where it lives, the CLI, and the skills.
---

# Panels

Panels is a **workspace for agents**, where the user's work lives. It includes a planning system.

The planning system is built from sprints, sprint items, and tickets:

- A **sprint** is a two-week block of work.
- A **sprint item** is a goal — something the user wants to achieve. Tickets are generated for it, for the individual bits of that work.
- A **ticket** is one unit of work, done by agents alongside the user. A ticket moves through six stages — **Success → Approach → Plan → Implementation → Closeout → Done** — filling one of five canonical outputs each step it needs: `success`, `approach`, `plan`, `implementation`, and `closeout`.

Sprint items and tickets can also stand alone, outside a sprint.

A **day** is a day in the user's life — what they want to get done that day. Each day is planned.

## Ticket-owned artifacts

A ticket can own durable work products such as HTML, images, Markdown documents, and other files. These live in Panels-managed ticket storage — by default under `data/files/tickets/<ticket-id>/...` — and appear in ticket Markdown through ordinary links such as `[UI plan](/files/tickets/<ticket-id>/artifacts/ui-plan.html)`. Use the served `/files/tickets/...` link rather than exposing a local filesystem path; Panels owns how the file is previewed or opened.

Artifacts complement the ticket record. Success, approach, plan, implementation, closeout, recaps, and notes remain concise canonical Markdown, with links to richer work when it helps.

## Rollover

When a day finishes, it rolls up into the sprint, and a new day is planned.

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
