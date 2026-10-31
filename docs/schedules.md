# Schedules

A schedule supplies an ordinary Ticket at one exact local minute. It is a way to have a known kind of work appear without remembering to create it. The schedule does not start the Worker, choose a gate, or send a prompt. The creation commit announces itself, and [worker steps](worker-steps.md) pick the new Ticket up like any other.

## How a minute becomes a Ticket

New schedules are daily. The template holds the title, Worker type, kickoff note used as Brief context, deadline, Project, assignment to Chief or one Manager, launch choice, locale, and blockers. Only Khushal and Chief can create or edit a schedule, including enable and disable. Being assigned the resulting Tickets does not give a Manager control of the schedule. Managers and Workers keep their ordinary ability to create Tickets directly.

The loop evaluates the current local minute and does not backfill minutes the host missed. Each schedule and minute has one occurrence receipt: created, failed, or otherwise settled. Repeating the poll returns the same receipt. A failure records its error and the other schedules in that minute still run. There is no suppression because a similar Ticket already exists. Two schedules may create two Tickets with the same type or title. The receipt prevents only its own replay.

Due creation acts as the owner. The usual Ticket validation and approval gates still apply inside that creation. The stored locale is passed through explicitly, so a later change to a Project or Worker requirement does not retarget occurrences that were already templated. Set the locale at creation, or with `panels schedule set`.

## Retired supply

Sprint placement, sprint cadences, and the planning Worker types are retired. Those schedules were disabled. They keep their identity, their templates, and their receipts. A retired row, recognized by a non-daily cadence or a retired Worker type, accepts only disable. The runtime skips a retired row even if the database is edited by hand to look enabled. The Scheduled Tasks screen shows them as retired history, with enable and edit turned off. There is no schedule delete. Empty installations do not receive a shipped planning schedule.

## Where it shows up

Settings → Scheduled tasks, `#/settings/scheduled-tasks`. The editor shows the local time, Every day, the due date, the Worker type, the Manager (Chief of Staff when none is named), the project, and the kickoff note. `panels schedule` and `/api/schedules` are the same records.

## Pointers

`src/planner/scheduled_tickets/` and the loop started from `src/planner/core/loops.py`.

_Last verified: 2026-10-30._
