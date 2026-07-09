# Projects

Projects are a small catalog, not a fixed enum. Each project has a stable ID and a
display name. The default rows are `project_vylo`, `project_tribe`,
`project_learning`, and `project_other`, but new rows can be added without changing
code.

Each project also has one free-text summary. That summary is the project context
for humans and agents: what the project is, what matters about it, and any repo or
file locations worth remembering. There is no separate repo-location field yet;
locations belong in the summary text when they matter.

Tickets, sprint items, and ideas store `project_id`. API responses also include the
legacy `project` field as the display name so older callers can keep reading it.
Existing write surfaces accept either `project_id` or the legacy project name; if both
are sent and they point to different rows, the server rejects the request.

Parented tickets do not carry their own project. Their `project_id` stays null because
the parent sprint item owns the project.

The Workspace board groups tickets by an effective display project. Standalone tickets
use their own project. Tickets under a sprint item use the parent item's project.
Tickets with neither source appear under `No project`.

## Surfaces

- `GET /api/projects` lists available projects.
- `POST /api/projects {name, summary?}` creates a project and records a
  `project_created` event.
- `PATCH /api/projects/{project_id}` updates the project name or summary and records
  a `project_updated` event.
- `panels project list`, `panels project create --name ... --summary ...`, and
  `panels project set <project_id> summary ...` expose the same catalog.
- Frontend project selectors fetch the `projects` resource and use project IDs as
  values with project names as labels.

There is no delete or archive flow yet.

_Code paths:_ `src/planner/projects/`, `src/planner/core/db.py`,
`src/planner/tickets/views.py`, `web/src/routes/BacklogRoute.svelte`,
`web/src/routes/IdeasRoute.svelte`, `web/src/routes/TicketRoute.svelte`,
`web/src/routes/BoardRoute.svelte`.

---

_Last verified: 2026-07-09._
