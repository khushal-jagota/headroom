# Ticket t_p9chftfg — implementation dispatch

## Contract

Restore Workspace to the current Panels planning day's Ticket membership and add one project
filter to the left-panel roster. Preserve the current status buckets, title-only rows, Ticket
inspector and URL behavior, Chief of Staff conversation, and row signals. Do not add Hide done,
status filtering, project metadata to rows, or a new backend project contract.

## Source scope

- `src/planner/tickets/api.py`
- `src/planner/tickets/views.py`
- `web/src/routes/BoardRoute.svelte`
- `assets/app.css` only if the filter needs a scoped style
- focused board unit and Workspace browser tests
- `docs/frontend.md`

## Required behavior

- `/api/board` resolves `today` through the configured 5am-aware planning calendar and passes the
  exact day ID to the board projection.
- The board query includes only Tickets in that day's `day_tickets` membership and retains its
  existing card shape and dropped-Ticket exclusion.
- Project choices derive from each returned card's effective `group_project_id` and
  `group_project`, including sprint-parented Tickets and a `No project` choice.
- `All projects` is the unfiltered roster. Filtering happens before status buckets are built.
- Ticket selection remains based on the complete day-scoped board, so filtering the roster does
  not close an already open inspector or invalidate a direct Workspace Ticket URL.

## Gates

Run focused board unit tests, frontend checks and production build, and focused Workspace
Playwright coverage. The integrator owns independent diff review and the one canonical
repository-wide `./verify`.
