# Projects

Projects are a small catalog, not a fixed enum. Each project has a stable ID, a
display name, and a priority. Priority is either P0–P3 or unassessed. Unassessed is
stored and returned as `null`; it is never interpreted as P3. The live default rows
are `project_vylo`, `project_tribe`, and `project_other`, but new rows can be added
without changing code.

Existing and built-in Projects may be unassessed. Creating a new Project through the
ordinary API or CLI requires an explicit P0–P3. There is no reassessment surface yet;
that belongs with a future Project page rather than the current name-and-summary editor.

`Learning` remains recognized when importing the legacy markdown format. If an
import names it and the project is missing, the importer creates it as part of the
same transaction. It is not recreated when a fresh database is initialized.

Each project also has one free-text summary. That summary is the project context
for humans and agents: what the project is, what matters about it, and any repo or
file locations worth remembering. There is no separate repo-location field yet;
locations belong in the summary text when they matter.

Unparented backlog Tickets, Sprint Items, and ideas store `project_id`. API responses
also include the legacy `project` field as the display name so older callers can keep
reading it. Existing direct Project write surfaces accept either `project_id` or the
legacy project name; if both are sent and they point to different rows, the server
rejects the request.

Tickets on Sprint Items do not carry their own Project or sprint placement. Their
`project_id`, `project`, and `effective_sprint_id` response values are derived from the
parent item.

The Workspace board groups Tickets by effective Project. Unparented backlog Tickets use
their own Project. Tickets under a Sprint Item use the parent item's Project. Tickets
with neither source appear under `No project`.

## Surfaces

- `GET /api/projects` lists available projects.
- `POST /api/projects {name, priority, summary?}` creates an assessed project.
- `PATCH /api/projects/{project_id}` updates the project name or summary.
- `panels project list`,
  `panels project create --name ... --priority P0|P1|P2|P3 --summary ...`, and
  `panels project set <project_id> summary ...` expose the same catalog.
- Frontend project selectors fetch the `projects` resource and use project IDs as
  values with project names as labels.

There is no delete or archive flow yet.

_Code paths:_ `src/planner/projects/`, `src/planner/core/db.py`,
`src/planner/tickets/views.py`, `web/src/routes/BacklogRoute.svelte`,
`web/src/routes/IdeasRoute.svelte`, `web/src/routes/TicketRoute.svelte`,
`web/src/routes/BoardRoute.svelte`.

---

_Last verified: 2026-07-28._
