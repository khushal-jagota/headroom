# Scheduled Ticket creation

A useful ordinary schedule supplies a Ticket at an exact local minute. It does not start
its Worker. The ordinary readiness loop handles that after the creation commit.

```
schedule → current local minute → ordinary Ticket + occurrence receipt
                                              ↓
                                normal Worker readiness
```

New schedules use the `daily` cadence. Their template holds the title, Worker type,
Brief context, deadline, Project, explicit manager assignment, launch
choice, resolved execution locale, and blockers. An assignment is either Chief or one ordinary Manager.
The canonical wire field is `manager`, with `kind` and `id`.

Sprint placement, Sprint cadences, and planning Worker templates are retired. The
migration disables the old morning, midday, Sprint review, and Sprint checkpoint supply.
Unrelated daily schedules retain their identity and time. No recurring review replaces
those planning routines. Empty installations receive no shipped planning schedule.

The catalog retirement migration also disables every remaining schedule for `personal`,
`planning-day`, `planning-midday-check`, and `planning-sprint`. It preserves schedule
identity and templates, every occurrence receipt, and all Tickets. Schedule creation
and edits reject retired types through the canonical action layer. Existing personal
Tickets retain their ordinary Stage gates.

Only the current local minute can run. The loop does not backfill missed minutes.
The occurrence key retains its timezone-aware local-minute format. Repeated polls and
restarts return the same durable receipt. A failure records its error and does not stop
other schedules. Ordinary Ticket validation still checks registered Worker types,
launch settings, blockers, and canonical creation rules.

There is no Day-based duplicate suppression. Two distinct schedules can supply the same
Worker type or title. Neither a preexisting Ticket nor an old Day membership suppresses
an occurrence. The durable schedule/occurrence identity prevents only its own replay.

Occurrence receipts store `target_date` as a non-null ISO calendar date.
The removal migration validates each historical `day_YYYY-MM-DD` value and retains its date suffix.
It preserves every receipt, occurrence key, outcome, Ticket link, error, and timestamp.
Malformed historical dates abort the migration.
New receipts use the local scheduling datetime's calendar date, including times before 5am.
No receipt points to a Day record.

The Sprint placement columns and placement mode are removed.
All six legacy schedules already name Chief with a null Item reference. Their assignment stays unchanged.
Ordinary due schedules create Chief-assigned Tickets outside backlog unless they explicitly name an ordinary Manager.
The stored Project remains unchanged. A null Project uses ordinary Ticket creation defaults.

Old cadence values remain solely as deliberate historical descriptions.
Retirement derives from a non-daily cadence or a retired Worker type.
A retired row accepts only a disable action. Every other configuration change fails.
The API rejects both a combined rewrite-and-enable request and a rewrite while disabled followed by an enable request.
The Scheduled Tasks screen labels the row as retired history and disables its enable and edit controls.
The runtime skips retired configurations even if external storage edits enable them.
No separate retirement flag exists. No schedule deletion operation exists.
These historical rows remain available for their receipts and cannot become new recurring work through the API.

The template resolves its locale when created and stores that value. Each occurrence
passes the stored locale explicitly, so later Project or Worker requirements do not
change its execution machine. An unrelated schedule edit preserves the stored locale.
Use `--locale` at creation or `panels schedule set <schedule> locale --value vps|macos`
to choose it explicitly. Ticket creation applies the same compatibility checks.
See [Ticket locale rules](tickets-and-gates.md#execution-locale).

The CLI uses `panels schedule create/list/show/set`. The schedule API uses
`/api/schedules`. Final frontend changes follow the approved design handoff.

_Code paths:_ `src/planner/scheduled_tickets/` and `src/planner/core/loops.py`.
