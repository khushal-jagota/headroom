# The command-line tool

`panels` is the command-line tool. It speaks to the server over HTTP and answers in
machine-readable JSON with `--json`.

On the production host, `~/.local/bin/panels` follows
`~/Deployments/Panels/current/app/bin/panels`. The deployed command locates its own
interpreter, so it keeps following atomic app replacements and works from any directory.
It preserves the caller's environment, including the server address and Ticket Worker
identity. It sets the application root and exact deployed SHA that belong to the
selected app, then uses Python's isolated mode so `PYTHONPATH` and the working
directory cannot replace the deployed package.

The command tree matches the system model:

- `day ...` — plan and inspect a day.
- `project ...` — list and create project catalog rows.
- `schedule ...` — configure exact-time creation of ordinary Tickets.
- `ticket ...` — create, inspect, organize, and approve tickets.
- `sprint ...` — create, inspect, edit, and populate sprints and sprint items.
- `worker ...` — worker-only writes such as ticket proposals, recaps, and notes.
- `chief ...` — explicit intake of work completed outside Panels.

Ordinary command groups do not expose internal runtime controls. Ticket `ticket_status`
and run claiming remain code-owned. The direct `ticket ownership` command changes a
declared Stage override; it is not a runtime-status setter. The exceptional `chief` group
can establish a coherent Ticket Stage from externally completed work; it is not a
generic Stage setter.

## The verbs

- **`day show / list-tickets / set / add-ticket / remove-ticket`** — plan a day and
  assign tickets to it. `day show` includes the day's tickets; `day list-tickets`
  returns the ticket list explicitly.
- **`project list / create`** — inspect and add projects. Project availability is
  data-backed, not enum-backed.
- **`schedule create / list / show / set`** — manage generic internal schedules that
  create and place an ordinary Ticket at an exact local time. A schedule uses either
  `every-planning-day` or `current-sprint-final-day`, carries the same Worker type and
  creation context as `ticket create`, and can be enabled or disabled. `show` includes
  its durable created, suppressed, or failed occurrence receipts. These commands
  configure Ticket supply only; they do not contain planning behavior or start Workers
  directly.
- **`ticket create / show / list / set / approve / block / unblock / delete`** — manage
  tickets. `ticket create` requires `--worker-type` and can take a `--kickoff-note` /
  `--kickoff-note-file` intake body for the Kickoff field. `--employee-backend` overrides
  the Worker type's registered default, and when it names a different backend
  `--employee-launch-model` has to say which model that backend runs the new Ticket's
  worker on — the Worker type's own model belongs to the Worker type's own backend.
  `ticket list --stage`
  compares the stored Stage directly. `ticket set` names one field (`title`, `kickoff-note`, `priority`, `deadline`,
  or `project` / `project-id`). Sprint placement is a sprint command,
  not a ticket setter.
  `ticket delete` is a permanent direct operation
  and requires `--yes`.
- **`ticket employee-configuration <id> --backend <key> --model <id> [--reasoning-effort <e>]`**
  — set what this Ticket's worker launches on. All three go together, because a model id
  belongs to the backend that named it; leave `--reasoning-effort` out for a model that
  takes none. It changes that choice only during pristine Kickoff, before a conversation
  exists.
- **`ticket ownership <id> --stage <stage> --mode worker|user|paired|default`** — set or
  clear one Stage's ownership override. `default` clears the override so the Worker
  type's Stage default applies. Terminal and unknown Stages are rejected.
- **`ticket copy`** — copy one ticket's plain-text packet.
- **`sprint create / list / show / set / add-ticket / remove-ticket`** — plan and
  populate sprints. `current` resolves through `/api/sprint/current`; `none` means the
  backlog where a list supports it.
- **`sprint item create / list / show / set / add-ticket / remove-ticket / block / unblock / delete`**
  — manage sprint items and their ticket membership. Creating a ticket is still
  `ticket create`; adding an existing ticket to an item is a sprint-item command.
  `sprint item block <item-id> --by <ticket-id>` records a Ticket blocking an item.
  Item status is read-only and derived from child tickets and active blocking links.
  `sprint item delete <item-id> --yes` permanently removes a childless item. An item
  with child tickets must have that work explicitly moved or removed first.
- **`worker propose / recap / note / my-ticket`** — worker actions. `worker propose`
  infers the current gating field from the Ticket Stage and requires a short recap
  (`--recap` or `--recap-file`) in the same request. `worker note` preserves
  field-specific user guidance without changing the field's value. `worker my-ticket`
  reports the current Ticket, and names the **specialist skill** for its Worker type —
  the one the base worker loads to learn that Worker type's Stages (see
  `worker-types.md`).
- **`chief reconcile-ticket-from-external-work / create-ticket-from-external-work`** —
  record reality established outside Panels. Both require an explicit Chief request,
  a complete Kickoff field value through `--kickoff-note-file`, preserving the report and
  reconciliation reasoning, and the
  exact settled field prefix for the target `--stage`. Creation also requires
  `--worker-type`; `--employee-backend` may override that type's registered default for the
  new Ticket, and a different backend needs `--employee-launch-model` with it.
  Reconciliation refuses pending or active Ticket work; both
  operations move the ceiling to the imported Stage, preserve an explicit Stop
  (otherwise Continue remains), and apply that Stage's effective ownership.
- **`serve`** — run the server and background worker runtime in the foreground.
  It keeps ownership while Panels restarts, so the same terminal continues to show the
  server logs.
- **`environment status / cleanup / prepare / inspect / run / reset / remove`**
  — manage prepared live and staging runtime instances. `environment run` requires one explicit
  staging `--repository-root`, validates that checkout and its `.venv`, and launches
  with that checkout's interpreter on an available port. Live is launched only from
  the deployed app by its user service.
  `environment status --json` is a direct host-local snapshot command and works without the
  server. `environment cleanup` is dry-run by default; only `--apply` mutates a newly collected,
  immediately re-proven inventory under the operator's filesystem permissions. It has no HTTP
  route and never removes processes, worktrees, caches, prepared environments, or the deployed
  app.
- **`environment app-build / app-identity / app-deploy / backup-current`** — build and identify
  one exact-commit Git-free app, replace `current/app` through the serialized backup and recovery
  transaction, and create a backup labeled from the validated deployed app. `app-build` requires
  `--source-root`, a lowercase full `--requested-sha`, and `--candidate-app`. `app-deploy` requires
  `--candidate-app`, `--current-root`, database and backup paths, a health URL, and the service
  manager and name. Linux systemd control is user-scoped; launchctl remains supported.
  `backup-current` requires `--current-app`; it does not inspect Git.
- **`restart`** — ask that running `serve` command to load the current Panels code again.
  The command reports when the request is accepted. If `serve` is not running, it reports
  the connection error and stops.

_Code paths:_ `src/planner/cli/main.py` (the verbs), `src/planner/cli/http.py`
(the HTTP call, output, and exit codes), `src/planner/server_lifecycle/` (foreground
ownership and controlled restart).

The interactive `bin/panels` command is distinct from `bin/panels-launcher`.
Services, scheduled maintenance, and backups use the latter because it validates the
deployed app and constructs an allowlisted runtime environment. Interactive commands
do not cross that managed-runtime boundary.

Project-aware commands accept `--project-id` as the preferred selector and keep
`--project` as legacy name compatibility. Passing both is allowed only when they
resolve to the same project.

## One-time legacy import

There is one retained cutover command outside the `panels` command tree:

```text
python -m planner.seed --source <dir> --worker-type <id>
```

`--worker-type` is required. `--employee-backend` may override the selected type's
registered default. The importer resolves that exact configured Worker type once
and uses its Stages and fields to validate every imported Ticket. It never chooses a
default or infers a Worker type from the Markdown. An incompatible legacy Stage rejects
and rolls back the import.

This is not a product import surface. There is no `/api/seed` route and no `panels seed`
command.

## What used to be here and isn't

Earlier documentation listed verbs that belonged to the old dispatcher-and-claim
machinery, or to old top-level homes. They no longer exist: **`run heartbeat` / `run
close`**, **`queue pickup`**, **`plan seed`**, top-level **`propose` / `recap` /
`note` / `item` / `idea` / `link` / `queue`**. A worker no longer holds a claim or a
lease; Panels starts one worker step at a time and writes the Ticket's status itself
(see `worker-orchestration.md`).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the proposals, recaps, and notes
  this tool files, and the scope the server enforces on them.
- **Worker orchestration** (`worker-orchestration.md`) — how the worker that drives
  this tool gets started.
- **Worker types** (`worker-types.md`) — the registry `worker my-ticket` reads the
  ticket's specialist skill from.

## Deferred

- **No general importer verb.** The old `plan seed` command is gone. The standalone
  one-time cutover command above is the only retained Markdown importer. Chief
  external-work intake reconciles a reported outcome; it does not ingest old planner
  documents. Trigger: a decision to support document import again.

---

_Last verified: 2026-07-28._
