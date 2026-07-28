# Sprints

A sprint is a two-week push. It is two pages, switched by a small pair of tabs at the
top: **Sprint Overview** (where the thinking lives) and **Sprint Tracking** (the list
of items and their tickets).

```
   [ Overview ]   [ Tracking ]
   ─────────────  ───────────────────────────
   Kickoff           the sprint's items
   Mid-sprint Review   └ each item's tickets + a progress rollup
   Sprint Review
   (one open at a time, by how far the sprint has gone)
```

## Overview — the thinking

Overview is three headed sections you read top to bottom: **Kickoff** (why this
sprint, the bet, what it rests on, what could go wrong), **Mid-sprint Review** (where
we stand, what's changed, what to adjust — written at the halfway point), and
**Sprint Review** (how it went, at the end). Every section is headed writing you edit
in place — click a line, type, click away, and it saves on its own, the same feel as
the daily page. One section is open at a time depending on how far the sprint has
gone: a brand-new sprint opens on Kickoff, one with a mid-point note opens on the
Mid-sprint Review, one being wrapped up opens on the Sprint Review.

Nothing on this page locks or commits — there are no buttons and no colour. That is a
deliberate change from an earlier design where the Kickoff and Review could be
"frozen" shut. Freezing is gone from the sprint; the only place an edit is still made
permanent by a button is a ticket's Approve. The old freeze machinery and the old
weekly-addenda notes still exist underneath, switched off and out of the way, so the
change can be undone if it's ever wanted.

## Tracking — the items

Tracking is the sprint's items and, under each, the tickets that carry it, with a
progress rollup. Each sprint item stores plain fields and placement only: title, body,
priority, deadline, project, and optional sprint. Its status is derived when read:
an item is done when all non-dropped child tickets are done, in progress when any
child ticket is active or an agent is working, blocked when it has an open blocking
ticket or blocked/errored child, and todo otherwise.

Each sprint item stores a `project_id` from the projects catalog; child tickets
inherit that classification from the item.

Planning writes validate the full requested change before altering the sprint or item,
then commit the compound change once. Repeating an add or remove membership request is
safe. In particular, a stale removal names both the item and Ticket and cannot detach a
Ticket that was subsequently moved to a different item. Sprint creation remains an
explicit non-idempotent operation: after an ambiguous response, read the sprint list
before trying another create.

An unwanted Sprint Item can be permanently deleted through
`panels sprint item delete <item-id> --yes`. Panels refuses deletion while the item
has child Tickets, so existing work cannot disappear as a side effect. Deleting a
childless item also removes its blocking links and refreshes sprint, backlog, board,
and linked-Ticket views.

_Code paths:_ `src/planner/sprints/` (the sprint, its items, and the overview
fields), `web/src/routes/SprintRoute.svelte` (both tabs).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the tickets a sprint item is
  made of.
- **The front end** (`frontend.md`) — the two-tab Sprint screen and its edit-in-place
  fields.
- **Projects** (`projects.md`) — the catalog used by sprint items.

---

_Last verified: 2026-07-24._
