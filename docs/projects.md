# Projects

Projects are a small catalog, not a fixed enum. Each project has a stable ID and a
display name. The default rows are `project_vylo`, `project_tribe`,
`project_learning`, and `project_other`, but new rows can be added without changing
code.

Tickets, sprint items, and ideas store `project_id`. API responses also include the
legacy `project` field as the display name so older callers can keep reading it.
Existing write surfaces accept either `project_id` or the legacy project name; if both
are sent and they point to different rows, the server rejects the request.

Parented tickets do not carry their own project. Their `project_id` stays null because
the parent sprint item owns the project.

## Surfaces

- `GET /api/projects` lists available projects.
- `POST /api/projects {name}` creates a project and records a `project_created` event.
- `panels project list` and `panels project create --name ...` expose the same catalog.
- Frontend project selectors fetch the `projects` resource and use project IDs as
  values with project names as labels.

There is no rename, delete, or archive flow yet.

_Code paths:_ `src/planner/projects/`, `src/planner/core/db.py`,
`web/src/routes/BacklogRoute.svelte`, `web/src/routes/IdeasRoute.svelte`,
`web/src/routes/TicketRoute.svelte`.

---

_Last verified: 2026-07-08._
