# Days

A day is one page per day: a morning brief, a plan for the day, the day's ticket
list, and the day's chat. It is the surface you actually plan and work on. The day
has a deliberate quirk — it flips at **5am, not midnight** — so a late night still
belongs to the day it felt like.

```
   5am — the boundary
   ──────────────────
   close yesterday      count what got done, note what didn't
        │
   carry forward        the unfinished tickets become today's candidates
        │
   ask the brief-writer a morning brief + a proposed plan
        │               (skipped entirely if you already planned the day)
        ▼
   you wake to a brief and a suggested plan
   accept it line by line · accept it all · or throw it out and plan by hand
```

## How a day flows

At the 5am boundary the system closes yesterday — counting what got done, noting what
didn't, and carrying the unfinished tickets forward as candidates — then asks the
brief-writer for a morning brief and a proposed plan for the new day. If you'd
already planned the day yourself, it doesn't presume: the proposal step is skipped.
Through the day, employees work whatever tickets their scope allows, filing proposals
that either advance automatically or queue for your morning approval walk.

## Quick capture

Loose capture works through the day's chat. The system provides the chat panel and
the command-line verbs for creating tickets and ideas; the day-chat worker's own
instructions tell it to file anything you toss in that sounds like work as a ticket
or an idea right away, at the lowest priority, for later sorting. The filing is the
worker doing its instructed job through the command-line tool — it is not something
the server does on its own.

_Code paths:_ `src/planner/days/` (the day, its boundary, and the ticket list).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the tickets a day lists and the
  scope that governs whether the day's work advances on its own.
- **Chat** (`chat.md`) — the day chat panel quick capture runs through.
- **Backlog & Ideas** (`backlog-and-ideas.md`) — where a captured idea lands.

## Deferred

- **Automatic rollover isn't wired yet.** The 5am boundary that closes yesterday and
  rebuilds today does not run on its own; the day isn't auto-generated. The two role
  skills that would drive it — `planning-boundary` (writing the brief and plan) and
  `planner-main` (the day-chat worker) — still describe the removed dispatcher flow
  and are being rewritten. Trigger: the boundary-rebuild work lands. See
  `employee-runtime.md`.

---

_Last verified: 2026-07-07._
