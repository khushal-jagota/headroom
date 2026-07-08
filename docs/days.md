# Days

A day is one page per day: the small overview you use to orient the day. It has four
editable fields — focus, brief take, watchout, and what makes the day land. The day
has a deliberate quirk — it flips at **5am, not midnight** — so a late night still
belongs to the day it felt like.

```
   5am — the boundary
   ──────────────────
   close yesterday      count what got done, note what didn't
        │
   carry forward        unfinished work becomes today's raw material
        │
   future rollover      may draft the overview
        │               (not wired yet)
        ▼
   you read or edit the day's four fields
```

## How a day flows

The current Day page is an overview, not a dashboard. It does not show the plan tree,
today's ticket list, the Review queue, or chat. Each field saves independently when
you edit it, and a refresh restores the same values from the server. Crossing the
5am boundary creates the new day record, but it stays empty until a human or a future
rollover worker writes it.

## Quick capture

Loose capture is not in the Day UI right now. Tickets, backlog items, and ideas are
created through their own surfaces or the command-line tool.

_Code paths:_ `src/planner/days/` (the day record and 5am planning date),
`web/src/routes/DayRoute.svelte` (the overview fields).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the tickets a day lists and the
  scope that governs whether the day's work advances on its own.
- **Chat** (`chat.md`) — ticket chat is separate from the Day page.
- **Backlog & Ideas** (`backlog-and-ideas.md`) — where a captured idea lands.

## Deferred

- **Automatic rollover writing isn't wired yet.** A new planning date can be
  materialized, but no worker writes the next day's overview. Trigger: the
  boundary-rebuild work lands. See `employee-runtime.md`.

---

_Last verified: 2026-07-08._
