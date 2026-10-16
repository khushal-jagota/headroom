# Scheduled Ticket creation

A useful ordinary schedule supplies a Ticket at an exact local minute. It does not start
its Worker. The ordinary readiness loop handles that after the creation commit.

```
schedule → current local minute → ordinary Ticket + occurrence receipt
                                              ↓
                                normal Worker readiness
```

New schedules use the `daily` cadence. Their template holds the title, Worker type,
Brief context, priority, deadline, Project, explicit orchestrator assignment, launch
choice, and blockers. An assignment is either Chief or one ordinary orchestrator.
The canonical wire field is `orchestrator`, with `kind` and `id`.

Sprint placement, Sprint cadences, and planning Worker templates are retired. The
migration disables the old morning, midday, Sprint review, and Sprint checkpoint supply.
Unrelated daily schedules retain their identity and time. No recurring review replaces
those planning routines. Empty installations receive no shipped planning schedule.

Only the current local minute can run. The loop does not backfill missed minutes.
The occurrence key retains its timezone-aware local-minute format. Repeated polls and
restarts return the same durable receipt. A failure records its error and does not stop
other schedules. Ordinary Ticket validation still checks registered Worker types,
launch settings, blockers, and canonical creation rules.

There is no Day-based duplicate suppression. Two distinct schedules can supply the same
Worker type or title. Neither a preexisting Ticket nor an old Day membership suppresses
an occurrence. The durable schedule/occurrence identity prevents only its own replay.

Existing receipt `target_day_id` values remain unchanged. New receipts label the local
calendar date as `day_YYYY-MM-DD`, including times before the former 5am boundary. That
label is historical storage vocabulary. It creates no Day row or membership.

The old cadence and placement enum values remain readable for historical configurations
and receipts. Historical Sprint placement columns do not affect new Ticket placement.
Calendar schedules cannot be reenabled through the live API. Useful ordinary schedules
can still be edited or disabled. There is no schedule deletion operation.

The CLI uses `panels schedule create/list/show/set`. The schedule API uses
`/api/schedules`. Final frontend changes follow the approved design handoff.

_Code paths:_ `src/planner/scheduled_tickets/` and `src/planner/core/loops.py`.
