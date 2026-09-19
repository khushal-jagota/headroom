# The command-line tool

`panels` is the command-line tool. It speaks to the server over HTTP and answers in
machine-readable JSON with `--json`.

Single-record reads use one grammar. With no part list, a read returns an identity and
state header plus a manifest. The manifest lists every authored part in stable order,
including empty parts. It reports the Unicode character count for
each part. Pass one optional comma-separated positional list to expand only those parts.
Each expanded part contains `value` and `proposal`. Ticket field parts contain saved
values, and the separate `proposal` part contains the one current draft:

```sh
panels ticket show t_example
panels ticket show t_example success,approach --json
panels sprint show current primary_bet,kickoff
panels sprint item show si_example body
panels day show 2026-08-10 focus,watchout
panels day show --date 2026-08-10 focus
panels project show project_panels summary
panels worker my-ticket plan,implementation,proposal
```

The same projection serves text and JSON. An unknown or duplicate part name fails and
lists the valid names. Day records contain authored Day text only. Use `day list-tickets`
for the linked Tickets.

On the production host, `~/.local/bin/panels` follows
`~/Deployments/Panels/current/app/bin/panels`. The deployed command locates its own
interpreter, so it keeps following atomic app replacements and works from any directory.
It preserves the caller's environment, including the server address and Ticket Worker
identity. It sets the application root and exact deployed SHA that belong to the
selected app, then uses Python's isolated mode so `PYTHONPATH` and the working
directory cannot replace the deployed package.

The command tree matches the system model:

- `send-message ...` — send text to the owner or one Panels employee.
- `day ...` — plan and inspect a day.
- `project ...` — list and create project catalog rows.
- `feedback ...` — list open feedback and mark notes as used in a Ticket.
- `worker-type ...` — discover the Worker types registered on this Panels server.
- `schedule ...` — configure exact-time creation of ordinary Tickets.
- `ticket ...` — create, inspect, organize, and approve tickets.
- `sprint ...` — create, inspect, edit, and populate sprints and sprint items.
- `worker ...` — worker-only writes such as ticket proposals, recaps, and notes.

Ordinary command groups do not expose internal runtime controls. Ticket `ticket_status`
and run claiming remain code-owned. No command performs an arbitrary Stage jump.

## Bounded list reads

The five agent-facing list commands return summary rows in pages of 30 by default:

- `ticket list`
- `sprint list`
- `sprint item list`
- `day list-tickets`
- `project list`

Use `--limit` to select the page size. Use `--offset` to select its starting match.
Text and JSON responses report the number of matches and returned rows. They also report
omissions before and after the page, whether the response is complete, and the next
offset. An empty match set and an empty page at a later offset are different results.

These commands use separate summary reads. Browser collection routes keep their rich
record shapes. Direct `show` commands also keep their full record shapes.

## The verbs

- **`send-message`** — send one text message through the existing conversation path.
  Select exactly one destination with `--owner`, `--chief`, `--ticket <id>`, or
  `--sprint-item <id>`.
  Supply the text with `--message` or `--body-file`; `--body-file -` reads stdin. Use
  `--mode steer`, `--mode queue`, or `--mode send_now`. Steer injects into current work.
  Queue holds a busy message. Send now interrupts current work. The default is `steer`.
  An employee's `--owner` send records the addressed message in that employee's current
  conversation and reports `recorded`; it does not invoke a backend and fails if the
  sender has no current conversation. Every mode starts a turn when an employee recipient
  is idle and can create its normal conversation on the first message.
  The recipient uses the shared principal shape: a kind and its stable ID. Agent keys and
  conversation resolution stay inside the server. The result names the recipient,
  conversation, and delivery fate. Started means delivery began. Queued names its position
  while it waits for the busy conversation. Injected means the current turn admitted a
  steer. Refused includes the reason that delivery was impossible. Uncertain means a steer
  may have crossed the backend boundary, but Panels cannot confirm admission. Panels does
  not retry an uncertain send. The result does not report whether the recipient completed
  the requested work.
- **`day show / list-tickets / set / add-ticket / remove-ticket`** — plan a day and
  assign tickets to it. `day show` returns the Day header and authored parts;
  `day list-tickets` returns the ticket list explicitly. `day set
  midday-reconciliation` writes the day’s separate mid-day check.
- **`project list / show / create / set`** — inspect, add, and update projects. `show`
  exposes the Project header and `summary` part. Project availability is
  data-backed, not enum-backed. `project create` requires
  `--priority P0|P1|P2|P3`; existing Projects may report `null` priority when they
  have not yet been assessed. `project set <project_id> priority --value P0|P1|P2|P3`
  reassesses an existing Project. It cannot clear an assessed priority.
- **`feedback list / use`** — list open feedback with its page and time, or mark one or
  more notes as used in a Ticket. The use operation is atomic. A Ticket Worker can use
  notes only in its own Ticket. A Sprint Item supervisor can use notes only in a current
  child Ticket.
- **`worker-type show <type>`** — print one Worker type's stored record, in the shape
  `worker-type save` takes back.
- **`worker-type save`** — declare a Worker type, or replace the one with that id, from a
  record on stdin. An optional `skill` block declares the specialist skill with it.
- **`worker-type skill <type> --description "..."`** — replace that Worker type's skill
  text, with the markdown body on stdin.
- **`worker-type list`** — list the registered Worker type identifiers in registry
  order. Its normal output is one identifier per line; `--json` returns the complete
  served Worker-type manifest for automation. Commands that require `--worker-type`
  point to this list instead of embedding a second catalog.
- **`schedule create / list / show / set`** — manage generic internal schedules that
  create and place an ordinary Ticket at an exact local time. A schedule uses
  `every-planning-day`, `current-sprint-day-four`, or `current-sprint-final-day`. It
  carries the same Worker type and placement context as `ticket create`, and it can be
  enabled or disabled. With no kickoff
  context, the created Ticket has no pending proposal, so readiness can start its
  Worker-owned Kickoff. Supplying kickoff context creates the ordinary proposed Kickoff
  and waits for approval. By default each
  occurrence resolves direct placement in the current Sprint; `--sprint` selects a fixed
  Sprint, `--backlog` leaves it unscheduled, and independent `--sprint-item` supplies
  Outcome context. Planning templates use the Personal Project without manufacturing
  containers. Use
  `schedule set … placement --value current-sprint|backlog` to switch the reusable
  placement mode. `show` includes its durable created, suppressed, or failed occurrence
  receipts. These commands
  configure Ticket supply only; they do not contain planning behavior or start Workers
  directly.
- **`ticket create / show / list / set / approve / block / unblock / delete`** — manage
  tickets. `ticket create` requires `--worker-type` and can take a `--kickoff-note` /
  `--kickoff-note-file` intake body for the Kickoff field. `--employee-backend` overrides
  the Worker type's registered default, and when it names a different backend
  `--employee-launch-model` has to say which model that backend runs the new Ticket's
  worker on — the Worker type's own model belongs to the Worker type's own backend.
  When `--priority` is omitted, creation uses the parent Sprint Item priority, then an
  assessed Project priority, then P3. An explicit `--priority P0|P1|P2|P3` overrides
  that default. `ticket list` excludes done and dropped Tickets unless
  `--include-terminal` is present. Repeat `--stage` or `--exclude-stage` for Stage
  inclusion or exclusion. Repeat `--ticket-status` or `--exclude-ticket-status` for
  control-status inclusion or exclusion. Values inside one filter type use OR. Different
  filter types use AND, and exclusions apply last. A terminal `--stage` also requires
  `--include-terminal`. An unknown Stage produces no matches.
  `--search` performs a case-insensitive substring match across the title, recap, field
  values, the pending proposal, and Ticket guidance. Search keeps stable Ticket order and combines
  with placement filters and page controls. Results include Ticket state, placement, and
  a short recap preview. Search does not rank matches or return snippets.
  `ticket create` uses Today and the current Sprint when placement is omitted.
  `--sprint <id|current>` selects a Sprint, `--backlog` selects no Sprint, and
  `--sprint-item <id>` adds coherent Item classification.
  The creating principal becomes the ceiling holder. A proposal that parks at that
  ceiling is addressed to that exact principal.
  `ticket set` names one field (`ceiling`, `title`, `kickoff-note`, `priority`, or
  `deadline`). Every one of those goes through `PATCH /api/tickets/{id}`, which is the
  only way to change a field on a Ticket. `ceiling` takes either the stage name or the
  plain name of the field that stage needs, so `closeout` and `needs_closeout` mean the
  same thing. Setting it makes the setting principal the ceiling holder, and it is
  refused while a proposal is pending.
  `ticket complete <ticket-id> <field>` is not a field edit: the user does a user-owned
  Stage's work themselves, and the Ticket advances exactly one Stage.
  `ticket place <ticket-id>` updates Project, Sprint, and optional Sprint Item as one
  coherent change. Select a Project with `--project` or `--project-id`. Select a Sprint
  with `--sprint <id|current>` or `--backlog`. Select classification with
  `--sprint-item <id>` or `--clear-sprint-item`. Omitted dimensions keep their current
  values, and the server rejects an incoherent final combination.
  `ticket approve` works for the addressed holder and for the owner override. It requires
  `--ceiling`, and it sends the full next holder with every approval.
  `--holder-kind owner|chief|sprint_item|ticket` and `--holder-id <id>` name that holder.
  The direct command defaults to the owner holder. Use an explicit ID for a Sprint Item
  or Ticket holder.
  `ticket delete` is permanent and requires `--yes`. The user deletes any Ticket, and a
  Sprint Item supervisor deletes a current child Ticket of its own Item. For the user it
  normally refuses a Ticket that is running, either because its
  status says a worker step is out or because its conversation is mid-turn. `--force`
  deletes such a Ticket anyway, for a Ticket whose status is stuck with no worker
  running. A supervisor meets no such guard on its own child Tickets, and `--force` adds
  nothing for it. No actor can delete a Ticket that holds another Ticket's ceiling.
  Force changes nothing else: the same cascade. Whenever a delete goes ahead over a
  running worker, that worker's turn is killed first.
- **`ticket employee-configuration <id> --backend <key> --model <id> [--reasoning-effort <e>]`**
  — set what this Ticket's worker launches on. All three go together, because a model id
  belongs to the backend that named it; leave `--reasoning-effort` out for a model that
  takes none. It changes that choice only during pristine Kickoff, before a conversation
  exists.
- **`ticket copy`** — copy one ticket's plain-text packet.
- **`sprint create / list / show / set`** — plan sprints. `current` resolves through
  `/api/sprint/current`; `none` means the backlog where a list supports it.
- **`sprint item create / list / show / set / add-ticket / remove-ticket / delete`**
  — manage durable Outcome context through the existing Item identity. Item records
  have no single Sprint and no derived Outcome status. Classification aligns the
  Ticket's Project and preserves its Sprint; removal preserves Project and Sprint.
  A stale removal cannot detach a different current Outcome. Ordinary Ticket placement
  owns scheduling. `list --search` searches the bounded Project catalog. Deleting an
  Outcome still requires `--yes` and refuses children. It also refuses deletion while
  the Sprint Item holds any Ticket ceiling.
- **`sprint outcome add / remove / list / carry`** — choose Outcomes for a Sprint,
  including before Tickets exist. Add/remove changes the commitment only. Carry takes
  source Sprint and Outcome, `--to` target Sprint, and repeatable `--ticket` IDs. It
  atomically commits the Outcome and moves only that explicit unfinished selection;
  no IDs means commitment only. The source commitment and completed history stay put.
- **`sprint item supervisor show / context / send / reset`** — inspect the supervisor
  and launch configuration, read its scoped brief and current Tickets, send a direct
  user message, or reset its current conversation.
- **`sprint item supervisor approve / reject`** — resolve a parked proposal on a current
  child Ticket when that exact Sprint Item is its ceiling holder. Approval requires the
  next ceiling. It also sends the full next holder. The holder defaults to the
  same Sprint Item; `--holder-kind` and `--holder-id` can address the next proposal to a
  different principal. Rejection can carry focused revision guidance. When it does, the
  Ticket appends
  that exact comment to guidance, invalidates worker context, and returns the Stage to
  rest in one SQLite commit. The holder stays the same for the revised proposal.
- **`sprint item supervisor ticket-context / history / message-worker`** — read one
  current child Ticket, page through its current Worker conversation, or send attributed
  guidance to that Ticket's current conversation. `message-worker` resolves the current
  conversation at send time and refuses when the Ticket is no longer a current child or
  has no current Worker conversation.
- **`sprint item supervisor restart-worker`** — start a current child Ticket's worker
  step again, when its Worker is dead. A dead Worker leaves the Ticket looking claimed,
  because the claim is the Ticket's status, so clearing the conversation on its own would
  leave it stuck. This does both: it clears the conversation and gives the claim back, and
  then starts the step. It answers with whether a Worker started, and names the reason
  when none did. `--backend` and `--model` restart the Ticket on a different agent, and
  they go together, because a model belongs to the backend that named it. Add
  `--reasoning-effort` for a model that takes one. A named configuration is what the
  Ticket launches on from then on, so a Worker that died on its backend does not come
  back on the same one. Leave the options out to restart on what the Ticket already has.
  A worker step gets its first five minutes before it may be restarted, so a Worker that
  is merely slow is left alone. Only a Worker-owned Stage can be restarted. A user-owned
  conversation belongs to the user.
- **`sprint item supervisor set-item / set-ticket`**
  — use item-scoped canonical actions for the owning Item and its current child Tickets.
  `set-ticket` names one field, and `ceiling` is one of them. Setting the ceiling makes
  that Sprint Item the ceiling holder and cannot retarget a pending proposal.
- A supervisor changes Day membership and Ticket blocks with the ordinary `day add-ticket`,
  `day remove-ticket`, `ticket block`, and `ticket unblock`. The supervisor identity
  carries the authority, so the server still holds it to its own current child Tickets.
- A supervisor creates a child Ticket with ordinary `ticket create --sprint-item`,
  the same command every other actor uses, and that Ticket is scoped like any other.
- **`ticket create --ceiling`** — state the new Ticket's ceiling at creation.
  The creator that was given the scope states it, so authorized work does not sit waiting
  for a second approval. That creator is also the ceiling holder. A stated ceiling past
  the kickoff settles the kickoff and starts the Ticket at the next Stage. Omit both
  options to keep the default: the kickoff parks for its creator's approval.
- **`sprint item supervisor artifact-list / artifact-write / artifact-delete`** — manage
  files under the owning Item's `artifacts/` directory.
- **`worker propose / recap / note / trouble / request-help / my-ticket`** — worker actions.
  `propose`, `recap`, `note`, and `trouble` take their text on stdin only; there is no
  file-path option, so no shared `/tmp` file can carry one Ticket's text onto another.
  `worker propose` infers the current gating field from the Ticket Stage and carries only
  what is being proposed. Below the ceiling it settles that field and the Ticket advances;
  at the ceiling it parks for approval. The recap is separate: a Worker keeps it current
  with `worker recap <id>` as it works. `worker note <id>` replaces the
  Ticket guidance document and accepts `--append` to add text with one blank line.
  Empty replacement clears guidance; empty append does nothing. No field argument
  or type lookup is needed. Ticket reads offer `recap` and `guidance` parts. `worker my-ticket`
  reports the current Ticket, and names the **specialist skill** for its Worker type —
  the one the base worker loads to learn that Worker type's Stages (see
  `worker-types.md`). `worker trouble` appends one short trouble note, read from stdin,
  to the current worker's Ticket during its active claimed worker step.
  `request-help` reads a message from stdin and sends one canonical addressed message.
  It defaults to the Ticket's current ceiling holder. Exactly one of `--owner`, `--chief`,
  `--ticket`, or `--sprint-item` can select another recipient.
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
  manager and name. The deployment workflow also supplies its lifecycle path and deployment ID,
  so restart, exact-SHA health, and rollback remain attached to the runner-started operation.
  Linux systemd control is user-scoped; launchctl remains supported.
  `backup-current` requires `--current-app`; it does not inspect Git.
- **`environment backup / restore / provision-skills`** — create or restore a verified
  database-and-files snapshot, or reconcile the managed Panels skills into the three
  production agent homes. Restore requires a stopped live service and the explicit
  `--live-stopped` acknowledgement.
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

## Ticket Worker identity

A request resolves to one principal with a kind and ID. The four kinds are owner, Chief,
Sprint Item, and Ticket. A browser request with no employee attribution resolves to the
owner principal.

A Ticket worker runs with `PLAN_ACTOR=worker` and its own `PLAN_TICKET_ID`. The CLI
forwards those as `X-Plan-Actor` and `X-Plan-Ticket-ID`, including when the worker uses
an ordinary command. The server resolves that pair to the Ticket principal and checks
that the Ticket exists. Any Ticket
worker can use the existing commands that move Tickets and add or remove Ticket blocks.
The exact `planning-day`, `planning-midday-check`, and `planning-sprint`
Worker types keep their other narrow day or sprint writes.

This is a truthful local process claim, not a
cryptographic login or bearer token. Requests arriving through trusted remote ingress
have both headers removed. Missing, unknown, or mismatched worker claims fail closed.

## What used to be here and isn't

Earlier documentation listed verbs that belonged to the old dispatcher-and-claim
machinery, or to old top-level homes. They no longer exist: **`run heartbeat` / `run
close`**, **`queue pickup`**, and top-level **`propose` / `recap` / `note` / `item` /
`idea` / `link` / `queue`**. A worker no longer holds a claim or a lease; Panels starts
one worker step at a time and writes the Ticket's status itself (see
`worker-orchestration.md`).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the proposals, recaps, and notes
  this tool files, and the scope the server enforces on them.
- **Worker orchestration** (`worker-orchestration.md`) — how the worker that drives
  this tool gets started.
- **Worker types** (`worker-types.md`) — the registry `worker my-ticket` reads the
  ticket's specialist skill from.

Sprint writing has four parts: `primary_bet`, `kickoff`, `checkpoint`, and `review`.
The primary bet is the short summary shown above Sprint tracking; the others are complete
Markdown documents. For example, `panels sprint set current review --body-file review.md`
replaces the review document. Sprint creation accepts `--primary-bet`, `--kickoff`,
`--checkpoint`, and `--review`; omitted text starts empty. The old per-heading fields
and creation options have been removed.

---

_Last verified: 2026-09-15._
