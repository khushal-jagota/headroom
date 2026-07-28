# Days

A day is one page per day: the small overview you use to orient the day. It has four
morning fields — focus, brief take, watchout, and what makes the day land — followed
by a separate **Midday reconciliation** field. The reconciliation records where things
actually stand without replacing or rewriting the morning plan. The day
has a deliberate quirk — it flips at **5am, not midnight** — so a late night still
belongs to the day it felt like.

```
   5am   05:05                                      14:30
   boundary + planning-day Ticket                   midday-check Ticket
   ────────   ───────────────────                   ───────────────────
   Day becomes current                              compare intent to reality
              gather current evidence                    │
                         │                               ▼
                         ▼                          agree any intervention
                    plan with you                         │
                         │                               ▼
                         ▼                          record reconciliation
                    commit the Day
```

## How a day flows

The current Day page is an overview, not a dashboard. It does not show the plan tree,
today's ticket list, the Review queue, or chat. Each field saves independently when
you edit it, and a refresh restores the same values from the server. An empty midday
reconciliation renders the same `(none)` edit state as the other Markdown sections.
Crossing the 5am boundary creates the new day record. It does not copy forward a plan or
start a separate rollover workflow.

At 05:05 local time, just after the 5am planning-day boundary, the internal schedule
creates a `planning-day` Ticket for the current planning day. Its specialist Worker
gathers current evidence, plans the four morning fields with the user, and writes the
agreed Day only at Closeout. At 14:30, a
`planning-midday-check` Ticket compares that intent with current execution, agrees any
useful intervention, carries it out, and records the reconciliation. Scheduling places
each Ticket in the Panels project's fallback item in the then-current sprint; repeat or
pre-laid matching Tickets suppress duplicates.

If a scheduled run is missed, recovery is ordinary creation of the intended planning
Ticket with `panels ticket create --worker-type planning-day` or
`--worker-type planning-midday-check`. There is no backfill and no rollover fallback.

## Quick capture

Loose capture is not in the Day UI right now. Tickets, backlog items, and ideas are
created through their own surfaces or the command-line tool.

_Code paths:_ `src/planner/days/` (the day record and 5am planning date),
`web/src/routes/DayRoute.svelte` (the overview fields).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the tickets a day lists and the
  scope that governs whether the day's work advances on its own.
- **The conversation system** (`conversation-system.md`) — a Ticket's conversation is
  separate from the Day page.
- **Backlog & Ideas** (`backlog-and-ideas.md`) — where a captured idea lands.

---

_Last verified: 2026-07-28._
