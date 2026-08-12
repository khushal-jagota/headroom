# Projects

Projects are a small catalog, not a fixed enum. Each project has a stable ID, a
display name, and a priority. Priority is either P0–P3 or unassessed. Unassessed is
stored and returned as `null`; it is never interpreted as P3. The live rows include a
Personal Project for planning work. New rows can be added without changing code.

```
Project ──► Ticket ──► optional Sprint Item classification
   └─────► Sprint Item
   └─────► Idea
```

Existing and built-in Projects may be unassessed. Creating a new Project through the
ordinary API or CLI requires an explicit P0–P3. The ordinary API and CLI can reassess
an existing Project to P0–P3. They cannot return it to the unassessed state.

`Learning` remains recognized when importing the legacy markdown format. If an
import names it and the project is missing, the importer creates it as part of the
same transaction. It is not recreated when a fresh database is initialized.

Each project also has one free-text summary. That summary is the project context
for humans and agents: what the project is, what matters about it, and any repo or
file locations worth remembering. There is no separate repo-location field yet;
locations belong in the summary text when they matter.

Tickets, Sprint Items, and ideas store `project_id`. API responses
also include the legacy `project` field as the display name so older callers can keep
reading it. Placement and categorization writes accept either `project_id` or the
legacy project name. If both are sent and they point to different rows, the server
rejects the request.

A Ticket carries its own Project and optional Sprint. When it also names a Sprint Item,
that Item must have the same Project and Sprint. Moving an Item to another Project or
Sprint moves its classified Tickets with it in the same transaction.

The Workspace board groups Tickets by their direct Project. Tickets without one appear
under `No project`.

## Surfaces

- `GET /api/projects` lists available projects.
- `GET /api/projects/{project_id}` returns one canonical Project record.
- `POST /api/projects {name, priority, summary?}` creates an assessed project.
- `PATCH /api/projects/{project_id}` updates the project name, summary, or priority.
- `panels project list`, `panels project show <project_id> [summary]`,
  `panels project create --name ... --priority P0|P1|P2|P3 --summary ...`, and
  `panels project set <project_id> priority --value P0|P1|P2|P3` expose the same
  catalog. The `set` command also supports the `name` and `summary` fields.
- Frontend project selectors fetch the `projects` resource and use project IDs as
  values with project names as labels.

_Code paths:_ `src/planner/projects/`, `src/planner/core/db.py`,
`src/planner/tickets/views.py`, `web/src/routes/BacklogRoute.svelte`,
`web/src/routes/IdeasRoute.svelte`, `web/src/routes/TicketRoute.svelte`,
`web/src/routes/BoardRoute.svelte`.

## Handoffs

- **Sprints** (`sprints.md`) — Tickets and Sprint Items use Projects in sprint tracking.
- **Tickets & the gates** (`tickets-and-gates.md`) — all Tickets carry direct Project
  placement.
- **Backlog & Ideas** (`backlog-and-ideas.md`) — both capture surfaces use the catalog.

## Deferred

- **Project removal.** There is no delete or archive flow. Trigger: the catalog needs
  lifecycle management beyond reassessment.

---

_Last verified: 2026-08-12._
