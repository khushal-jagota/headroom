# The command-line tool

`panels` speaks to the Panels server over HTTP. Add `--json` to receive the existing
machine-readable response from each route.

Ordinary help exposes five roots:

- `send-message` sends text to an existing Panels conversation.
- `project` reads and changes Projects.
- `day` reads and changes Days and their Ticket membership.
- `ticket` reads and changes Tickets.
- `sprint` reads and changes Sprints and Sprint Items.

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

List commands return bounded summaries. `ticket list`, `sprint list`, `sprint item list`,
and `project list` use pages of 30 by default. Use `ticket list --day today` for Day
membership. Every result reports omissions and the next offset.

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
  "sprint_id": null,
  "kickoff_note": "Faithful intake context"
}
JSON
```

`title` and `worker_type` are required. Existing optional keys cover launch overrides,
priority, deadline, canonical placement IDs, blockers, kickoff, ceiling, and ceiling
holder. Hidden legacy flags remain callable during migration. A call cannot mix those
flags with `--input-json`.

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
rest in one commit. A Worker-owned Stage also returns to the current Day. Normal readiness
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

## Sprint Item membership

The public commands use the user-facing term Sprint Item:

- `sprint add-item`
- `sprint remove-item`
- `sprint list-items`
- `sprint carry-item`

They call the existing Sprint membership and carry routes. `sprint item` owns Sprint Item
records, Ticket classification, workspace reads, and artifacts. `send-message
--sprint-item` owns supervisor messages. Conversation reset remains an internal recovery
path.

## Compatibility and errors

Hidden aliases keep the old Worker, Ticket edit, proposal, Day list, Sprint outcome, and
Sprint Item conversation paths callable during skill migration. They call the same
handlers and routes as before. They do not appear in help.

`ticket restart-worker` also recovers an older rejected Ticket only when it is
Worker-owned, Empty, claim-free, attached to a conversation, and holds matching revision
feedback. Recovery keeps that conversation, returns the Ticket to the current Day, and
refuses launch overrides. Other prior-Day Tickets remain at rest.

Human errors use one cause line and, when a valid recovery exists, one exact command
shape. JSON errors keep the existing envelope and exit code. Authority refusals do not
invent another route.

On the production host, `~/.local/bin/panels` follows the current deployed application.
The launcher preserves the runtime identity and uses the interpreter from that exact
deployment.
