# The command-line tool

`panels` speaks to the Panels server over HTTP. Add `--json` to receive the existing
machine-readable response from each route.

Ordinary help exposes these roots:

- `send-message` sends text to an existing Panels conversation.
- `project` reads and changes Projects.
- `day` reads historical Days.
- `ticket` reads and changes Tickets.
- `orchestrator` reads and changes ordinary orchestrators.
- `sprint` retains historical reads and the stable `sprint item` alias.

The command name does not grant authority. Each request carries the principal from the
runtime context. The server admits or refuses that principal through the existing
authority service.

Host, deployment, schedules, feedback, Worker types, skills, launch configuration, and
conversation recovery remain callable administrative paths. They do not appear in
ordinary root help. A specialist skill names one when approved work needs it.

## Record reads

`project show`, `day show`, `ticket show`, `sprint show`, and `sprint item show` return a
header and a part manifest. Add one comma-separated part list to expand selected values.

```sh
panels ticket show t_example success_condition,what_changes --json
panels ticket show brief,guidance --json
panels sprint show current primary_bet,kickoff --json
panels sprint item show si_example body --json
panels day show 2026-08-10 focus,watchout --json
```

`ticket show` with no Ticket ID reads the authenticated Worker's Ticket through the
worker-self route. It includes the Worker type and specialist identity. A named read uses
the ordinary Ticket record route. Neither form reads conversation history. Use `ticket
history` when that record is necessary.

Select `memory_notice_inspection` to inspect automatic memory notice reservations,
cutover holds, and available evidence references, including former conversations:

```sh
panels ticket show t_example memory_notice_inspection --json
panels worker my-ticket memory_notice_inspection --json
```

This read uses ordinary Ticket read authority. It includes no prompt text and changes
no record. A reservation records consumed permission, not an attempt or delivery. See
[Worker memory notices](worker-orchestration.md#telling-a-worker-it-lost-its-memory)
for the omission tradeoff and recovery limits.

`ticket show` and `worker my-ticket` include `ceiling_holder` beside `stage` and
`ceiling` in the header. Text and JSON output preserve the holder's principal kind
and identity, with or without selected parts. The holder names the review recipient.
It does not grant approval permission or identify assignment or Stage ownership.

List commands return bounded summaries. `ticket list`, `sprint list`, `sprint item list`,
and `project list` use pages of 30 by default. Every result reports omissions and the
next offset. Ticket list no longer filters live work by Day or Sprint.

Ticket lists accept `--backlogged`, `--not-backlogged`, and repeated `--worker-type`
filters. Historical cancelled records require `--include-terminal`. Add `--stage cancelled`
to select them. Direct record reads remain available regardless of those list filters.

```sh
panels ticket backlog t_example
panels ticket resume t_example
panels ticket list --backlogged --worker-type coding --limit 30
```

Backlog and Resume accept `--expected-record-sha256` from the full Ticket read. A stale
hash refuses the action without a write. Resume does not grant an approval or clear a
blocker. These commands use existing caller authority.

Retire is no longer a Ticket action. Historical cancelled records keep their original
retirement provenance. Read it with `panels ticket show t_example retirement --json`.
The historical record does not admit a new cancellation or restore an actionable approval.

## Structured Ticket writes

`ticket create` reads one JSON object and sends it unchanged to the existing Ticket
create route. The server applies existing defaults and validates the complete Ticket
before one atomic write.

```sh
panels ticket create --input-json - <<'JSON'
{
  "title": "Clear outcome title",
  "worker_type": "coding",
  "project_id": "project_panels",
  "orchestrator": {"kind": "chief", "id": "chief"},
  "brief": "Faithful intake context"
}
JSON
```

`title` and `worker_type` are required. Existing optional keys cover launch overrides,
priority, deadline, canonical placement IDs, blockers, Brief, ceiling, and ceiling
holder. Hidden legacy flags remain callable during migration. A call cannot mix those
flags with `--input-json`.

Create rejects unknown JSON keys before it creates anything. The validation error
identifies the offending key. Use `brief` for the Brief text. The old `kickoff_note`
JSON key and unsupported launch options such as `employee_launch_reasoning_effort`
produce an error. Hidden legacy `--kickoff-note` flags send the text as `brief`.

`ticket edit` sends one JSON object to the existing Ticket PATCH route. One request can
change one or more fields that the route already accepts.

```sh
panels ticket edit t_example --input-json - <<'JSON'
{
  "priority": "P1",
  "recap": "Ready for review.",
  "guidance_append": "Keep the public surface small."
}
JSON
```

The server validates the intended final state and writes it in one transaction. Proposal
transitions, blockers, Worker restart, deletion, help, and messages keep dedicated
commands because they have effects beyond a Ticket field edit.

## Ticket proposals

One command owns the proposal lifecycle:

```sh
panels ticket proposal t_example submit < proposal.md
panels ticket proposal t_example accept --ceiling needs_plan
panels ticket proposal t_example revise < guidance.md
```

Submit remains restricted to the authenticated Worker for its own current Stage. Accept
and revise retain the existing proposal resolver and authority checks. `ticket complete`
remains separate because it completes a user-owned Stage directly.

Revise stores the attributed feedback, removes the proposal, and returns the Stage to
rest in one commit. Normal readiness
then reuses the existing conversation for one revision prompt.

`ticket request-help` reads its message from standard input. It records the Ticket
attention fact and sends the message through the existing help route. `send-message`
only sends conversation text and does not change that attention fact.

## Worker restart

`ticket restart-worker` resets the dead conversation and makes one explicit start
attempt. Its JSON response includes `started` and `delivery_fate`. Started, queued, and
injected fates set `started` to true. Refused and uncertain fates set it to false. A
failure before delivery sets `delivery_fate` to null and reports `not_started_because`.

An uncertain delivery keeps the Worker claim and does not trigger an automatic retry.
The human output reports the uncertainty without a restart success claim. Panels does
not enforce a delay. Use one explicit `ticket restart-worker` call when it is safe to try
again.

## Orchestrator assignment and settlement

`orchestrator create/list/show/set/workspace` keeps the existing Item identity and
conversation. `sprint item` remains an alias. Artifact operations keep their existing
managed roots. `send-message --sprint-item` keeps its stable recipient address.
Existing ordinary orchestrator supervisors can use it to coordinate with one another.
The permission sends a message; approvals, ceilings, assignments, and other writes
keep their existing authority requirements. Workers cannot use it to address an
unrelated supervisor.

`ticket assign <ticket> --chief` assigns to Chief. `--orchestrator <item>` assigns to
an ordinary orchestrator. Structured create and edit use the same explicit `orchestrator`
reference. Assignment and the ceiling holder are independent.

`orchestrator settle <item>` is owner-only. Add `--active` to return it to active.
Chief cannot settle. Day edits, Sprint edits, commitments, and carry-forward are retired.
Historical `day show` and `sprint list/show/list-items` remain readable.

## Compatibility and errors

Hidden aliases retain Worker, Ticket edit, proposal, historical Day list, and Item
conversation paths. No alias restores retired calendar writes.

`ticket restart-worker` also recovers an older rejected Ticket only when it is
Worker-owned, Empty, claim-free, attached to a conversation, and holds matching revision
feedback. Recovery keeps that conversation and refuses launch overrides. Historical Day
membership does not affect ordinary continuation.

Human errors use one cause line and, when a valid recovery exists, one exact command
shape. JSON errors keep the existing envelope and exit code. Authority refusals do not
invent another route.

On the production host, `~/.local/bin/panels` follows the current deployed application.
The launcher preserves the runtime identity and uses the interpreter from that exact
deployment.

Ticket creation accepts `locale` in its JSON object or `--locale vps|macos` beside
`--input-json`. Use one input source for the locale. An idle Ticket moves with
`panels ticket move <ticket> --locale vps|macos`.

Projects accept `--requires-macos` at creation and
`panels project set <project> requires-macos --value true|false` for later edits.
These requirements affect future locale resolution. See
[Ticket locale rules](tickets-and-gates.md#execution-locale).
