# Historical Days

Days and Today membership are retired from live work. A Worker does not need Day
membership to continue. Existing ceilings, approvals, blockers, claims, stage ownership,
and conversation recovery still govern continuation.

Existing Day records retain their original writing and ordered Ticket associations.
`GET /api/day/{date}` and `GET /api/day/{date}/tickets` read those records.
A missing Day returns not found. A read never creates a Day.
`panels day show <date>` retains historical access. Day edits and membership commands
are retired, as are the HTTP write routes.

The morning, midday, and Sprint planning schedules no longer supply work. Their old
Tickets and receipts remain historical records. Retirement cancels only the reviewed
obsolete planning cohort through an explicit forward migration. It does not mark that
work Done or cancel ordinary unfinished work.

The old `day_YYYY-MM-DD` identifiers and planning-date helper remain meaningful for
historical links. New useful daily schedules use the local calendar date for receipts.
They neither create Days nor assign Workers to them.

The final frontend removal follows the approved design handoff. Backend retirement
alone does not establish that the new navigation is delivered.

_Code paths:_ `src/planner/days/`, `src/planner/membership/api.py`,
`src/planner/runtime/worker_step_readiness.py`, and `src/planner/scheduled_tickets/`.
