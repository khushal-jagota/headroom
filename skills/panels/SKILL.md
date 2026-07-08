---
name: panels
description: Orientation to the Panels system — its pieces, the daily cycle, where it lives, the CLI, and the skills.
---

# Panels

Panels is a **workspace for agents**, where the user's work lives. It includes a planning system.

The planning system is built from sprints, sprint items, and tickets:

- A **sprint** is a two-week block of work.
- A **sprint item** is a goal — something the user wants to achieve. Tickets are generated for it, for the individual bits of that work.
- A **ticket** is one unit of work, done by agents alongside the user.

Sprint items and tickets can also stand alone, outside a sprint.

A **day** is a day in the user's life — what they want to get done that day. Each day is planned.

## Rollover

When a day finishes, it rolls up into the sprint, and a new day is planned.

## The CLI

Everything runs through the `panels` command — run `panels --help` to see what it can do. It talks to the Panels server and database.

The command groups are the system's nouns:

- `panels day ...` for planning and operating on a day.
- `panels ticket ...` for creating, inspecting, organizing, and approving tickets.
- `panels sprint ...` and `panels sprint item ...` for planning and populating sprints.
- `panels worker ...` for worker-only writes such as proposals, recaps, notes, and item status proposals.

## Skills

- **`panels-worker`** — working a single ticket: shaping it through its stages, executing it, and reviewing it.
- **`panels-rollover`** — carrying the plan across a day boundary.
- **`panels-sprint-planning`** — planning and reconciling at the sprint level.
