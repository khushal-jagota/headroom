# Projects

Projects are a small catalog, not a fixed enum. Each project has a stable ID, a
display name, an optional folder path, and a summary. New rows can be added without
changing code.

```
Project ──► Ticket ──► optional Sprint Item classification
   └─────► Sprint Item
   └─────► Idea
```

Each Project folder is explicit and does not depend on the Project name. Panels expands
paths that start with `~` and stores an absolute path. The folder can be absent on disk.
Panels does not create it. A clear operation returns the stored path to `null`.

Each project also has one free-text summary. That summary is the project context
for humans and agents: what the project is, what matters about it, and any repo or
file locations worth remembering. A repository can be one item inside the Project
folder. Panels does not define or manage the folder contents.

Sprint Items, ideas, and standalone Tickets store `project_id`. A parented Ticket derives
its Project from its Sprint Item. API responses
also include the legacy `project` field as the display name so older callers can keep
reading it. Placement and categorization writes accept either `project_id` or the
legacy project name. If both are sent and they point to different rows, the server
rejects the request.

A Ticket has an effective Project. When it names a Sprint Item,
the Item owns its Project classification. Moving a Sprint Item changes each child Ticket's
effective Project and updates schedule templates.

Workspace groups Tickets by their assigned Chief or ordinary orchestrator. Project facts
remain visible on individual Ticket and orchestrator pages as text. The removed global
Workers page no longer supplies a project-filtered list.

## Surfaces

- `GET /api/projects?detail=summary|full` reads the catalog at the level asked for.
  `detail` is required and has no default. Add `id=<project_id>` with `detail=full` to
  read one canonical Project record.
- `POST /api/projects {name, summary?, folder_path?}` creates a Project.
- `PATCH /api/projects/{project_id}` updates the name, summary, or folder path.
- `panels project list`, `panels project show <project_id> [summary]`,
  `panels project create --name ... --summary ... --folder-path ...`, and
  `panels project set <project_id> name|summary|folder-path --value ...` expose the same
  catalog. Use `--clear` to clear the folder path.
- Frontend project selectors fetch the `projects` resource and use project IDs as
  values with project names as labels.

_Code paths:_ `src/planner/projects/`, `src/planner/core/db.py`,
`src/planner/tickets/views.py`,
`web/src/routes/IdeasRoute.svelte`, `web/src/routes/TicketRoute.svelte`,
`web/src/routes/BoardRoute.svelte`.

## Handoffs

- **Orchestrators** (`sprints.md`) — shared context and Project classification.
- **Tickets & the gates** (`tickets-and-gates.md`) — Tickets expose effective Project
  placement.
- **Backlog & Ideas** (`backlog-and-ideas.md`) — both capture surfaces use the catalog.

## Deferred

- **Project removal.** There is no delete or archive flow. Trigger: the catalog needs
  lifecycle management beyond name and context edits.

---

_Last verified: 2026-09-23._
