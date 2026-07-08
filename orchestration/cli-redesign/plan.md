# Plan — reshape the `panels` CLI around day, ticket, sprint, and worker

## Objective

Move the CLI from its current backend-route shape into the owner's product shape:

```
panels day ...
panels ticket ...
panels sprint ...
panels worker ...
```

The product surfaces are day, ticket, and sprint. `worker` stays separate because worker commands are
not general ticket management; they are the things an employee/agent does while working a ticket.

This is a command-surface redesign with two backend write-model additions: a worker proposal must
also carry a recap update in the same operation, and an existing ticket must be movable under or out
of a sprint item. Do not expose manual ticket state, `ticket_status`, or runtime controls. Code owns
those.

## Owner decisions captured

- There are things you do on a day, things you do to a ticket, and things you do to a sprint.
- Worker functions are segmented away from ticket-management functions.
- Creating a ticket is always a `ticket` command. Sprints may place existing tickets; they do not
  create tickets through a second creation path.
- `ticket create` may still accept `--sprint` and `--sprint-item` placement metadata.
- `day show` should be clear about whether it includes tickets. It should include them, and
  `day list-tickets` should exist as the focused scanner/script command.
- `set` commands should consistently name the field they write.
- Worker `propose` should stay a simple function, not a field-shaped family of commands.
- `recap` is not a fifth proposal field. A worker may update recap alone, but every worker proposal
  must also include and write a recap update.
- Old top-level CLI homes (`propose`, `recap`, `note`, `item`, and `idea`) should not remain visible
  compatibility aliases in the new product CLI. Tests, docs, and skills must migrate in the same
  integration that removes them.

## Target command surface

### Day

`day show` returns the full day view: overview fields plus that day's tickets. `day list-tickets`
returns only tickets for scanning and scripting.

```
panels day show [DATE|today]
panels day list-tickets [DATE|today]

panels day set focus --value "Main thing" [--date today]
panels day set brief-take --body-file brief.md [--date today]
panels day set watchout --body-file watchout.md [--date today]
panels day set if-today-lands --body-file lands.md [--date today]
panels day set notes --body-file notes.md [--date today]

panels day add-ticket <ticket-id> [DATE|today]
panels day remove-ticket <ticket-id> [DATE|today]
```

`DATE|today` defaults to `today` for `show`, `list-tickets`, `add-ticket`, and `remove-ticket`.
`day set` uses `--date` so the field position is never ambiguous.

### Ticket

Ticket commands manage ticket records and the one human approval gate. They do not force state,
`ticket_status`, worker runtime, or takeover/release.

```
panels ticket create --title "..." \
  [--project Vylo] [--priority P1] [--deadline 2026-07-10] \
  [--sprint current|<sprint-id>] [--sprint-item <item-id>]

panels ticket show <ticket-id>
panels ticket list [--day today|DATE] [--sprint current|<sprint-id>] \
  [--sprint-item <item-id>] [--project Vylo] [--state needs_plan]

panels ticket set <ticket-id> title --value "..."
panels ticket set <ticket-id> priority --value P1
panels ticket set <ticket-id> deadline --value 2026-07-10
panels ticket set <ticket-id> deadline --clear
panels ticket set <ticket-id> project --value Vylo

panels ticket approve <ticket-id> [--ceiling <state|none>] [--at-cap stop|propose] \
  [--edit-file edited-proposal.md]

panels ticket block <ticket-id> --by <blocking-ticket-id>
panels ticket unblock <ticket-id> --by <blocking-ticket-id>
panels ticket copy <ticket-id>
panels ticket events <ticket-id>
```

`ticket approve` is the only human decision command in this wave. It should read the ticket and do
the right approval:

- If the ticket has a pending proposal on the current gating field, accept it. The command requires
  `--ceiling` and `--at-cap`, matching the existing approval/scope rule.
- If the ticket is `needs_review`, approve the review result and move to done. Scope flags are not
  accepted in that mode.
- Otherwise fail with a clear validation message.

The command may use the contract state machine (`GATING_FIELD`) rather than making the user name the
field. The user should approve "the ticket", not pick backend internals.

`ticket block <ticket-id> --by <blocking-ticket-id>` writes a `blocks` link from the blocking ticket
to the blocked ticket: `{from_id: blocking_ticket_id, to_id: ticket_id, kind: "blocks"}`. `ticket
unblock` removes that same directed link.

Post-create sprint placement is not a `ticket set` command. A ticket can carry `--sprint` or
`--sprint-item` at creation time, but assigning an existing ticket to a sprint or item is a sprint
operation.

### Sprint

Sprint commands own the sprint and sprint-item surfaces. Ticket creation remains under `ticket`.
Sprint commands may place existing tickets into the sprint or a sprint item.

```
panels sprint show [current|<sprint-id>]
panels sprint list
panels sprint create --name "..." --start 2026-07-01 --end 2026-07-14

panels sprint set <sprint-id|current> name --value "..."
panels sprint set <sprint-id|current> date-start --value 2026-07-01
panels sprint set <sprint-id|current> date-end --value 2026-07-14
panels sprint set <sprint-id|current> limiting-factor --body-file factor.md
panels sprint set <sprint-id|current> primary-bet --body-file bet.md
panels sprint set <sprint-id|current> supports --body-file supports.md
panels sprint set <sprint-id|current> premortem --body-file premortem.md
panels sprint set <sprint-id|current> mid-where-we-stand --body-file stand.md
panels sprint set <sprint-id|current> mid-whats-changed --body-file changed.md
panels sprint set <sprint-id|current> mid-what-to-adjust --body-file adjust.md
panels sprint set <sprint-id|current> outcomes --body-file outcomes.md
panels sprint set <sprint-id|current> solo-reflection --body-file solo.md
panels sprint set <sprint-id|current> joint-discussion --body-file joint.md
panels sprint set <sprint-id|current> updates-to-thinking --body-file updates.md
panels sprint set <sprint-id|current> carry-forward --body-file carry.md

panels sprint add-ticket <ticket-id> [--sprint current|<sprint-id>]
panels sprint remove-ticket <ticket-id> [--sprint current|<sprint-id>]
```

`sprint add-ticket` sets a standalone ticket's `sprint_id`. It must reject tickets parented under a
sprint item, because their sprint is derived from the item.

### Sprint items

Keep sprint items under `sprint item`, because items are part of sprint planning/tracking.

```
panels sprint item create --title "..." --project Vylo \
  [--sprint current|<sprint-id>] [--priority P1] [--deadline 2026-07-10] \
  [--body-file body.md]

panels sprint item show <item-id>
panels sprint item list [--sprint current|none|<sprint-id>] [--status todo] [--project Vylo]

panels sprint item set <item-id> title --value "..."
panels sprint item set <item-id> body --body-file body.md
panels sprint item set <item-id> priority --value P1
panels sprint item set <item-id> deadline --value 2026-07-10
panels sprint item set <item-id> deadline --clear
panels sprint item set <item-id> project --value Vylo
panels sprint item set <item-id> sprint --value current|<sprint-id>
panels sprint item set <item-id> sprint --clear

panels sprint item add-ticket <item-id> <ticket-id>
panels sprint item remove-ticket <item-id> <ticket-id>
panels sprint item approve <item-id>
```

`sprint item approve` accepts a pending done/deferred status proposal. There is no separate
`review` namespace in this wave.

### Worker

Worker commands send the agent actor header. They do not perform human approvals.

```
panels worker my-ticket
panels worker propose [ticket-id] --body-file proposal.md --recap-file recap.md
panels worker recap [ticket-id] --body-file recap.md
panels worker note [ticket-id] <field> --body-file note.md
panels worker propose-item-status <item-id> --to done|deferred_next_sprint [--body-file note.md]
```

`worker propose` infers the proposal field from the ticket's current gating state:

- `needs_success` -> `success`
- `needs_approach` -> `approach`
- `needs_plan` -> `plan`
- `in_progress` -> `result`

It fails in `needs_review`, `done`, or `dropped`. It must write the proposal and the recap in one
server operation.

`worker propose-item-status` is the worker-side proposal for final sprint-item status. Direct
manual sprint-item status setting is not part of this product CLI wave.

## Backend/API changes

### B1 — command actor ownership

The user-facing CLI should not expose a global "human mode". Internally, command groups choose the
right request class:

- `worker ...` sends `X-Plan-Actor` as today.
- `day`, `ticket`, and `sprint` product commands omit `X-Plan-Actor` unless a specific command is
  intentionally a worker/operator action.

This is needed because existing day/sprint text edits, ticket title/project edits, ticket approval,
and item approval are human-classified server routes. It stays internal to the command, not a new
user-facing mode.

Implementation shape: extend `planner.cli.http.send` to accept an internal actor class such as
`request_actor="agent" | "human"`, or add explicit `send_agent` / `send_human` wrappers. This is not
a user-facing mode. Old visible command homes are removed in the same integration that migrates tests
and skills; no hidden compatibility aliases.

### B2 — proposal plus recap in one write

Add a backend operation for the new worker proposal shape:

```
POST /api/tickets/{ticket_id}/propose
{ "body": "...", "recap": "..." }
```

The route:

- parses non-empty `body` and non-empty `recap`;
- resolves the current gating field from ticket state;
- rejects states with no proposal field;
- in one transaction, files the proposal and overwrites recap;
- preserves existing proposal auto-accept/park behavior;
- emits both the proposal events and `recap_updated`;
- returns normal ticket JSON.

This combined writer intentionally permits a recap update on the first `needs_success` proposal.
Standalone recap writes still keep the existing admission rule, but a proposal-with-recap is the
worker's first chance to summarize the ticket. Do not implement this by calling the standalone recap
writer after the proposal writer, because that would reject parked `needs_success` proposals and
would not be atomic.

The existing field-specific route may remain as an API compatibility route for non-CLI callers, but
the worker CLI and role skill should move to the new route. Do not make `recap` a fifth proposal
field.

Suggested data-layer shape: add `tickets_data.file_next_proposal_with_recap(...)` rather than
teaching the API to call two independent writers. This keeps the "one public writer = one
transaction" rule and avoids partial success.

### B3 — current sprint resolution for CLI inputs

Support `current` in CLI flags for sprint selectors by resolving through `GET /api/sprint/current`
inside the CLI. Do not add `current` as a database id. Commands that accept `none` for sprint
filters or clears map it deliberately:

- list backlog items: `--sprint none` -> query `sprint_id=null` (the current API's sentinel);
- clear item sprint: `sprint item set <id> sprint --clear` -> JSON `{"sprint_id": null}`;
- clear standalone ticket sprint is not exposed under `ticket set`; use `sprint remove-ticket`.

Add CLI tests for `current` success, no-current failure, and `none`/`null` mapping.

### B4 — existing ticket placement under sprint items

`sprint item add-ticket <item-id> <ticket-id>` needs a real backend writer/API route. Existing
`PATCH /api/tickets/{id}` cannot set `sprint_item_id`.

Add a canonical ticket-data writer for parent changes:

- add to item: read the item, set `tickets.sprint_item_id = item_id`, clear standalone
  `tickets.sprint_id`, clear `tickets.project` because project is derived while parented, and append
  a `ticket_updated` or specific placement event;
- remove from item: clear `sprint_item_id`, set standalone `sprint_id` to the parent item's
  `sprint_id` if present, leave `project` null unless a future product decision chooses a default,
  and append an event;
- reject impossible moves with structured errors: missing item, missing ticket, already parented to
  another item if the command is not explicitly a move, or derived sprint/project conflicts.

Expose routes for the CLI rather than overloading generic ticket PATCH:

```
POST /api/items/{item_id}/tickets
{ "ticket_id": "t_..." }

DELETE /api/items/{item_id}/tickets/{ticket_id}
```

## Implementation tickets

### CLI1 — backend proposal-with-recap contract

Owned files:

- `src/planner/tickets/contracts.py`
- `src/planner/tickets/data.py`
- `src/planner/tickets/api.py`
- `tests/unit/test_tickets_engine.py` or a focused new unit/API test

Work:

- Add request body shape for next proposal with recap.
- Add route `POST /api/tickets/{ticket_id}/propose`.
- Add data-layer writer that files the current gating proposal and writes recap in one transaction.
- Reuse the existing resolution decision for proposal semantics.
- Add tests for parked proposal + recap event, auto-accepted proposal + recap event, invalid proposal
  states, and a default `needs_success` parked proposal with recap.

Acceptance:

- Proposal write and recap write are atomic.
- Recap is required for the new route.
- Recap can be written by the combined route on a `needs_success` ticket even though standalone recap
  remains too early there.
- Existing field-specific proposal route keeps current behavior for non-CLI compatibility.

### CLI2 — backend sprint-item ticket placement

Owned files:

- `src/planner/sprints/contracts.py`
- `src/planner/tickets/data.py`
- `src/planner/sprints/api.py`
- read-view tests if placement affects sprint/item detail
- focused API/unit tests

Work:

- Add routes for adding/removing an existing ticket under a sprint item.
- Add canonical ticket writer(s) for parent assignment and removal.
- Preserve derived-field rules: parented tickets have derived sprint/project, standalone tickets own
  `sprint_id` and nullable `project`.

Acceptance:

- Adding an existing standalone ticket to an item clears standalone `sprint_id` and project.
- Removing a ticket from an item leaves it as a loose ticket in the item's sprint when the item has a
  sprint.
- Missing/invalid parent relationships return structured errors, not raw SQLite failures.

### CLI3 — CLI actor seam, selectors, and command surface migration

Owned files:

- `src/planner/cli/http.py`
- `src/planner/cli/main.py`
- `tests/e2e/test_cli_verbs.py`
- all e2e tests that call old CLI homes
- `skills/panels/SKILL.md`
- `skills/panels-worker/SKILL.md`
- `docs/cli.md`

Work:

- Add internal support for agent-classified and human-classified CLI requests.
- Add selector helpers for `current` and `none` sprint inputs.
- Restructure command groups to `day`, `ticket`, `sprint`, `sprint item`, and `worker`.
- Remove visible top-level `propose`, `recap`, `note`, `item`, and `idea` command homes in the same
  integration that migrates tests, docs, and skills. Do not leave hidden compatibility aliases.
- Keep `serve` top-level.
- Update worker role instructions to use `panels worker propose ... --recap-file ...`.
- Update e2e flow calls away from old top-level `propose`, `recap`, `note`, and `item`.

Acceptance:

- `panels --help` shows only the new product groups plus `worker` and `serve`.
- `panels worker --help` owns proposal/recap/note/status-proposal worker commands.
- `panels sprint item --help` owns sprint-item product commands.
- Commands that hit human-only routes succeed because they omit the agent header.
- Worker commands still send the agent header.
- No e2e test still depends on the removed command homes.

### CLI4 — day commands

Owned files:

- `src/planner/cli/main.py`
- CLI e2e tests
- docs

Work:

- Implement `day show`, `day list-tickets`, `day set`, `day add-ticket`, `day remove-ticket`.
- `day set` uses `panels day set <field> --date today ...`.
- `day show` human output must say it includes overview plus ticket count/list.
- `day list-tickets` emits only ticket rows in human mode and `{"tickets": [...]}` under `--json`.

Acceptance:

- Day overview fields can be edited from CLI.
- Day ticket list is available without parsing full day output.
- Existing add/remove behavior still wakes System A through the server route.

### CLI5 — ticket commands

Owned files:

- `src/planner/cli/main.py`
- tests
- docs

Work:

- Implement field-based `ticket set`.
- Implement `ticket approve` as the smart current approval gate.
- Implement `ticket block` / `ticket unblock` as wrappers around `links.kind=blocks`.
- Implement `ticket copy` and `ticket events`.
- Preserve `ticket create --sprint/--sprint-item` placement.
- Do not implement post-create `ticket set sprint`.

Out of scope:

- `ticket state`
- `ticket status`
- runtime force/retry
- takeover/release, unless a separate owner decision says they are product commands

Acceptance:

- Gating proposal approval requires and applies the scope pair.
- `needs_review` approval works without scope.
- `ticket block <target> --by <blocker>` creates a directed `blocker -> target` blocks link and the
  target reads as blocked.
- State/status cannot be manually forced through the CLI.

### CLI6 — sprint and sprint-item commands

Owned files:

- `src/planner/cli/main.py`
- tests
- docs

Work:

- Implement `sprint list`, `sprint create`, `sprint set`, `sprint add-ticket`, `sprint remove-ticket`.
- Implement `sprint item create/show/list/set/add-ticket/remove-ticket/approve`.
- Add body support to `sprint item create`.
- Do not implement broad manual `sprint item set status` or `blocked-by` in this wave.

Acceptance:

- A sprint can be planned from CLI: create sprint, set kickoff fields, create items, place tickets.
- Creating a ticket still happens only through `ticket create`.
- Existing status proposal/accept semantics stay intact through `worker propose-item-status` and
  `sprint item approve`.
- `current` and `none` sprint selectors have focused CLI tests.

### CLI7 — docs and final dogfood

Owned files:

- `docs/cli.md`
- `docs/README.md` if needed
- `skills/panels/SKILL.md`
- `skills/panels-worker/SKILL.md`
- a focused dogfood/e2e script or test

Work:

- Ensure docs and skills match the final surface after CLI3-CLI6.
- Remove stale language saying the CLI is only workers/debugging; it now covers day/ticket/sprint
  planning while worker commands remain proposal-only.
- Run a scripted CLI dogfood against a test server: create a sprint, set sprint fields, create an
  item, create a ticket, place it under the item, add it to today, list day tickets, worker-propose
  with recap, approve the ticket, and inspect events.

Acceptance:

- Worker skills teach the new proposal-with-recap rule.
- Docs explain that state/status are code-owned and not CLI knobs.
- Full `./verify` passes.

## Review plan

Built-in subagents reviewed the draft plan before implementation:

1. Product-shape review: found stale compatibility aliases, `idea` as an unapproved fourth surface,
   duplicate sprint assignment under `ticket set`, ambiguous `day set`, and broad item status knobs.
   The plan now removes aliases/idea, removes post-create `ticket set sprint`, uses `day set <field>
   --date`, and excludes broad sprint-item status setting from this wave.
2. Backend/test review: found missing API work for item ticket placement, the recap admission conflict
   on first proposals, a sequencing risk from removing old commands before e2e migration, missing
   blocker-link direction, and underspecified `current`/`none` selector tests. The plan now adds B4,
   explicitly permits proposal-with-recap on `needs_success`, migrates old commands atomically with
   tests/skills, spells out block direction, and requires selector tests.

After implementation, run a second review round focused on the diff before `./verify`.

## Risks and open calls

- Command removal is intentionally sharp: top-level `propose`, `recap`, `note`, `item`, and `idea`
  disappear from the visible CLI. Tests, docs, and skills must migrate in the same integration.
- `ticket approve` has two backend meanings: accept a pending proposal or approve final review. The
  plan deliberately makes it one smart command because the user is approving the ticket's current
  ask, not selecting an internal route.
- Ideas are not placed in the new top-level shape. The visible `idea` CLI is removed in this wave;
  where idea capture belongs is a later product decision.
- `sprint item add-ticket/remove-ticket` is real write-model work, not just CLI plumbing. It needs
  direct writer review because it changes derived sprint/project ownership on tickets.
- `worker propose` changes worker habits and skills. The role skill update must land with the route
  and CLI command, or live workers will keep using the old field-specific proposal command without
  recap.
