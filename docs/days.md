# Days

A Day is the daily orientation surface. Its four morning fields are focus, brief take,
watchout, and what makes the day land. A separate **Midday reconciliation** field records
where things stand without replacing the morning plan. The planning date flips at
**5am, not midnight**, so late-night work stays with the day it belongs to.

The four morning fields steer attention, motivation, and behavior. They do not act as
literal summaries of the Tickets on the Day:

- **Focus** (`focus`) tells the user what to keep their mind and attention on.
- **Brief take** (`brief_take`) gives the shortest useful framing for a clear, intentional, and manageable day.
- **Watchout** (`watchout`) names the likely psychological or behavioral trap and the response that defeats it.
- **What makes the day land** (`if_today_lands`) states the user's personal gain from completing the day, not the work completed or a system state.

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

The Home page combines the morning orientation with live Ticket progress. It shows one
mark per Ticket and action tiles for work that needs the user, needs review, is working,
is paired, or is done. The tiles lead to Review or Workspace. The page does not edit the
Day fields or show the Midday reconciliation.

The four morning fields and Midday reconciliation remain canonical Day data. Their
planning Workers write them through guarded Day actions. Reading or writing a planning
date creates its row when needed. Crossing 5am only changes which planning date is
current. It does not copy a plan or start a rollover workflow.

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

_Code paths:_ `src/planner/days/` (the Day record, guarded writes, and 5am planning
date), `web/src/routes/DayRoute.svelte` (the daily hub), and
`web/src/lib/dayPresentation.ts` (Ticket progress and action tiles).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the tickets a day lists and the
  scope that governs whether the day's work advances on its own.
- **The conversation system** (`conversation-system.md`) — a Ticket's conversation is
  separate from the Day page.
- **Backlog & Ideas** (`backlog-and-ideas.md`) — where a captured idea lands.

---

_Last verified: 2026-08-09._
