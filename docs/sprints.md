# Sprints

A sprint is a stored inclusive date range. Normal sprint planning creates a seven-day
range, but the record accepts any valid, non-overlapping range. Its main page tracks
Projects and Sprint Items. Each Item has a dedicated Item view. A separate documents
page holds the sprint's written record.

```
   Sprint tracking                 Sprint Item
   ───────────────────────────     ───────────────────────────
   Project                          On today
    └ Sprint Item · progress        ─ off-today Tickets
                                    ▸ done Tickets

   Sprint documents
   ───────────────────────────
   Kickoff · Checkpoint · Sprint Review
```

## Sprint documents — the thinking

The documents page has three headed sections you read top to bottom: **Kickoff** (why this
sprint, the bet, what it rests on, what could go wrong), **Checkpoint** (where we stand,
what's changed, and what to adjust on day four), and **Sprint Review** (how it went, at
the end). Every section is headed writing you edit in place — click a line, type, click
away, and it saves on its own. Kickoff opens for a new sprint. Checkpoint opens when it
contains text. Sprint Review also opens when it contains text, so both later sections
can be open near the end. The stored field names still use their historical `mid_*`
identifiers.

Nothing on this page locks a section. Each edit writes its document field directly.

The sprint day changes at 05:00 local time. Panels uses that canonical day to decide
which sprint is current and which numbered day the sprint page shows. Existing sprint
ranges remain as stored, so historical sprints keep their original dates. Planning Sprint
must propose exactly seven inclusive dates for every new sprint.

At 17:00 local time on day four, the internal schedule creates a personal Checkpoint
Ticket in the current sprint. At 17:00 on the final day, it creates a `planning-sprint`
Ticket in the Panels project's current-sprint fallback. Its specialist Worker reviews
the current sprint first, plans the next sprint with the user, and writes only the
approved result at Closeout. A matching pre-laid Ticket suppresses each scheduled
duplicate. If a run is missed, recovery uses ordinary Ticket creation. Neither schedule
backfills a missed occurrence, and Planning Sprint stays on the final day.

## Tracking — the items

Tracking groups Sprint Items under foldable Projects. The overview does not show Ticket
rows. Project priority orders the groups. Vylo comes first and Other comes last when
Projects need the stable fallback order. Done Items come last inside a Project. An
`other` fallback comes after its shaped Items.

Each Item row shows its priority, title, and Ticket completion. It says `to do` before
any Ticket is done, a fraction during progress, and `done` when all non-dropped Tickets
are done. Selecting the row opens the dedicated Item address.

The Item view shows its Project, title, body, priority, completion, and optional
deadline. Its Ticket list starts with `On today`. A seam separates off-today Tickets.
Done Tickets stay in a fold. Priority orders each block, and blocked Tickets come last
in `On today`. Ticket marks and words show the live Ticket state.

Each sprint item stores plain fields and placement only: title, body, priority,
deadline, project, optional sprint, and a machine-readable `kind` of `normal` or
`other`. Its status is derived when read:
an item is done when all non-dropped child tickets are done, in progress when any
child ticket is active or an agent is working, blocked when it has an open blocking
ticket or blocked/errored child, and todo otherwise.

Each sprint item stores a `project_id` from the projects catalog; child tickets
inherit both their Project and effective sprint from the item. A scheduled Ticket
cannot sit loose on a sprint. Schedule templates record their placement intent:
`current_sprint` resolves the current sprint's Project fallback when each occurrence
runs, `sprint_item` names an exact parent, and `backlog` explicitly stays unparented.

Every `(sprint, Project)` pair that needs a catch-all has one `other` item. Panels
creates or reuses that fallback when a Ticket is scheduled without a more specific
item. The database prevents duplicate Other items for the same pair, and Tracking
keeps them after shaped Items, so catch-all work stays visible during planning and review.

Planning writes validate the full requested change before altering the sprint or item,
then commit the compound change once. `panels sprint item move-ticket` atomically moves
a Ticket from backlog or any previous item. `move-ticket-to-backlog` names both the
current item and Ticket, so a stale request cannot detach a Ticket that was subsequently
moved. Repeating either request is safe. Sprint creation remains an explicit
non-idempotent operation: after an ambiguous response, read the sprint list before
trying another create.

An unwanted Sprint Item can be permanently deleted through
`panels sprint item delete <item-id> --yes`. Panels refuses deletion while the item
has child Tickets, so existing work cannot disappear as a side effect. Deleting a
childless item also removes its blocking links and refreshes sprint, backlog, board,
and linked-Ticket views.

_Code paths:_ `src/planner/sprints/` (the sprint, its items, and the document
fields), `web/src/routes/SprintRoute.svelte` (tracking, Item, and document views), and
`web/src/lib/sprintPresentation.ts` (presentation rules).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the tickets a sprint item is
  made of.
- **The front end** (`frontend.md`) — the Sprint routes and their shared visual system.
- **Projects** (`projects.md`) — the catalog used by sprint items.

---

_Last verified: 2026-08-09._
