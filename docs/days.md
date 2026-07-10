# Days

A day is one page per day: the small overview you use to orient the day. It has four
editable fields — focus, brief take, watchout, and what makes the day land. The day
has a deliberate quirk — it flips at **5am, not midnight** — so a late night still
belongs to the day it felt like.

```
   5am — the boundary
   ──────────────────
   read yesterday       count what got done, note what didn't
        │
   draft kickoff        write the likely four-field overview
        │               note obvious carryover pending review
        ▼
   you review           tickets move onto today only after agreement
```

## How a day flows

The current Day page is an overview, not a dashboard. It does not show the plan tree,
today's ticket list, the Review queue, or chat. Each field saves independently when
you edit it, and a refresh restores the same values from the server. Crossing the
5am boundary creates the new day record.

The repo-owned `panels-rollover` role skill prepares the kickoff when the user starts
rollover or a thin scheduled check finds it missing. An automatic run writes the likely
four-field overview and records only obvious carryover candidates pending review; it
never adds tickets to today before the user agrees. The morning check drafts if missing.
The afternoon check is only a failsafe: it drafts if still missing and otherwise does
nothing. Broad reprioritization remains sprint-planning work.

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

- **The app does not schedule rollover itself.** The repository owns and provisions the
  rollover role skill, which expects thin morning and afternoon prompts outside the
  deterministic server runtime. See `employee-runtime.md`.

---

_Last verified: 2026-07-10._
