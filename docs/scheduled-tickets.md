# Scheduled Ticket creation

Scheduled Ticket creation supplies ordinary Tickets at an exact local minute. A
schedule describes when a Ticket must appear and the ordinary creation context it must
carry. It contains no planning judgment and does not start a Worker.

```
saved schedule ──► current local minute ──► occurrence receipt
                                               │
                         created Ticket ◄──────┤
                         suppressed duplicate ◄┤
                         recorded failure ◄────┘

created Ticket ──commit signal──► Worker readiness
```

## The schedule

A schedule uses one of three cadences: every planning day, day four of the current
sprint, or the current sprint's final day. It stores an exact local time, title, Worker
type, priority, optional Kickoff context, launch choice, blockers, and placement.
Placement can resolve the current Sprint, name one coherent Sprint Item classification,
or keep the Ticket in backlog. The scheduled planning Worker types use the Personal
Project and that Sprint's Planning Item. Other schedules retain their selected Project.
A migrated legacy schedule can retain a fixed Sprint through its canonical `sprint_id`.
New current-Sprint schedules leave that field empty and resolve the Sprint at occurrence time.

The Scheduled tasks screen lists schedules and opens the create or edit form. It can
change the reusable template and enable or disable future occurrences. The CLI also
shows durable occurrence receipts. There is no delete operation.

_Code paths:_ `src/planner/scheduled_tickets/`,
`web/src/routes/ScheduledTasksRoute.svelte`, and
`web/src/lib/scheduledTasks.ts`.

## Occurrences

The server loop checks only schedules that match the current local minute. It does not
backfill minutes missed during downtime. The planning-date boundary selects the target
Day, and the canonical sprint dates decide whether a sprint cadence applies.

One transaction settles each due occurrence as created, suppressed, or failed. A
durable occurrence identity makes repeated polls and restarts safe. A matching
specialist Ticket already on the target Day suppresses creation. Personal schedules
also require an exact title match. A failed occurrence does not stop later schedules.

Creation uses the ordinary Ticket writer, so Worker registration, launch defaults,
priority, blockers, lifecycle initialization, placement, and Day membership follow the
same rules as manual creation. The resulting commit emits the ordinary change signal.
Worker readiness then decides whether work can start.

_Code paths:_ `src/planner/scheduled_tickets/actions.py`,
`src/planner/scheduled_tickets/logic.py`, `src/planner/scheduled_tickets/runtime.py`,
and `src/planner/core/loops.py`.

## Handoffs

- **Days** (`days.md`) — the planning date and built-in daily schedules.
- **Sprints** (`sprints.md`) — sprint-day cadences and Ticket placement.
- **Worker orchestration** (`worker-orchestration.md`) — the separate readiness system
  that receives the committed Ticket.
- **The command-line tool** (`cli.md`) — schedule configuration and receipt inspection.

## Deferred

- **Missed occurrences.** The loop does not backfill downtime. Trigger: the product
  adopts an explicit recovery policy for missed schedule minutes.
- **Schedule removal.** Schedules can be disabled but not deleted. Trigger: disabled
  schedules need permanent lifecycle management.

---

_Last verified: 2026-08-12._
