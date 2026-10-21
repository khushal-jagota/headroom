# The command-line tool

`panels` speaks to the Panels server over HTTP. Add `--json` to receive the existing
machine-readable response from each route.

Ordinary help exposes these roots:

- `send-message` sends text to an existing Panels conversation.
- `project` reads and changes Projects.
- `ticket` reads and changes Tickets.
- `manager` reads and changes ordinary Managers.

The command name does not grant authority. Each request carries the principal from the
runtime context. The server admits or refuses that principal through the existing
authority service.

Host, deployment, schedules, feedback, Worker types, skills, launch configuration, and
conversation recovery remain callable administrative paths. They do not appear in
ordinary root help. A specialist skill names one when approved work needs it.

## Skill edits

`panels skill list` lists every managed skill. `panels skill show NAME` prints its
Markdown body. Add `--json` to read the description and name as well.

```sh
panels skill edit panels-worker-coding < BODY_FILE
panels skill edit panels-worker-coding --description "Coding guidance"
panels skill edit panels-worker-coding --description "Coding guidance" < BODY_FILE
```

A body edit preserves the description. A description edit preserves the body.
Empty redirected input with a description means description-only. Blank body text fails.
The skill name cannot change. The old Worker-type skill command remains an adapter,
with an optional description. Both commands use the same server writer and authority.

If a save fails after the row commits, the error states whether the file also changed.
Read the current skill before retry. An incomplete save does not undo the committed row.
See [Worker types](worker-types.md#skills-and-the-config-page) for publication behavior.

`panels skill apply --input-json MANIFEST.json` applies an owner-reviewed batch.
Each entry names `name`, full `source_text`, and `expected_sha256`.
Use an explicit null hash only when the row must be absent.
The server validates every entry before one atomic row update.
Only the owner can call this operation. The Chief cannot bypass that restriction.
If publication fails, retry the same complete manifest to publish its exact saved rows.
A changed source refuses its stale hash. Immutable versions and Worker bindings remain intact.


## Record reads

`project show`, `ticket show`, and `manager show` return a
header and a part manifest. Add one comma-separated part list to expand selected values.

```sh
panels ticket show t_example success_condition,what_changes --json
panels ticket show brief --json
panels manager show si_example goal,recap --json
```

`ticket show` with no Ticket ID reads the authenticated Worker's Ticket through the
worker-self route. It includes the Worker type and specialist identity. A named read uses
the ordinary Ticket record route. Neither form reads conversation history. Use `ticket
history` when that record is necessary.

Settled Ticket fields carry an inline approval label in text output, both in the manifest
and beside an expanded value. Explicit approval names Khushal, Chief, or the approving
manager's title. `Auto-accepted` and `Approval unknown` remain distinct. Unsettled
fields carry no approval label. JSON includes an `approval` object on each settled field
with `method`, `principal` (kind and ID), and `display_label`. Principal IDs appear in
JSON only. These reads do not add approval information to `ticket history`.

List commands return bounded summaries. `ticket list`, `manager list`,
and `project list` use pages of 30 by default. Every result reports omissions and the
next offset. Ticket list no longer filters live work by Day or Sprint.

Ticket lists accept `--backlogged`, `--not-backlogged`, and repeated `--worker-type`
filters. Completed records require `--include-terminal`; add `--stage done` to select
them. Direct record reads remain available regardless of those list filters.

```sh
panels ticket backlog t_example
panels ticket resume t_example
panels ticket list --backlogged --worker-type coding --limit 30
```

Backlog and Resume accept `--expected-record-sha256` from the full Ticket read. A stale
hash refuses the action without a write. Resume does not grant an approval or clear a
blocker. These commands use existing caller authority.

`panels ticket delete t_example` removes the Ticket record. Its artifact files survive.
Backlog preserves a Ticket for later work; Delete removes it. See `deployment.md` for
owner-run recovery when an older database contains cancelled Tickets.

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
  "manager": {"kind": "chief", "id": "chief"},
  "brief": "Faithful intake context"
}
JSON
```

`title` and `worker_type` are required. Existing optional keys cover launch overrides,
deadline, canonical placement IDs, blockers, Brief, ceiling, and ceiling
holder. Hidden legacy flags remain callable during migration. A call cannot mix those
flags with `--input-json`.

Create rejects unknown JSON keys before it creates anything. The validation error
identifies the offending key. Use `brief` for the Brief text. The old `kickoff_note`
JSON key produces an error. Hidden legacy `--kickoff-note` flags send the text as
`brief`.

Create accepts `employee_launch_reasoning_effort` with the selected backend and model:

```json
{
  "title": "Review the import path",
  "worker_type": "coding",
  "employee_backend": "claude",
  "employee_launch_model": "opus",
  "employee_launch_reasoning_effort": "high",
  "ceiling": "needs_implementation",
  "ceiling_holder": {"kind": "owner", "id": "owner"}
}
```

The model must be enabled and offer the requested effort. A blank, non-string, or
unsupported effort fails before Ticket creation. If the catalogue cannot validate an
explicit effort, creation fails with a clear error. No effort is silently replaced.

An omitted or null effort keeps the existing creation defaults. The Worker type's
effort applies only to its own backend and model. Other selections store null.
Null means no effort override: Claude receives no effort option, Codex receives null,
and Hermes receives no effort configuration. The backend controls the effort in that
case. The existing catalogue-unavailable fallback remains for omitted effort.

The creation response adds `supervision_receipt`, a plain-language explanation of the
effective grant. Normal CLI output prints it, and `--json` retains it beside the Ticket
fields. It lists only the Stages that remain unsupervised and the first review gate with
its recipient. For example:

> Runs unsupervised: Success Condition, What Changes, Plan, Implementation. First parks for review at Consequences, with Chief.

The default receipt reads:

> Nothing runs unsupervised. First parks for review at Brief, with the owner.

Stages that require user participation still require it. A grant with no unsupervised
Stage states that explicitly. If no review gate remains, the first sentence states:
"Nothing in this grant ever parks for review." Ordinary Ticket reads keep their existing shape.

Earlier model-selection work required a model for a different backend. A later correction
used each model's own effort options for validation. Neither change accepted an explicit
effort at creation. The create input now provides that choice without a Worker restart.

`ticket edit` sends one JSON object to the existing Ticket PATCH route. One request can
change one or more fields that the route already accepts.

```sh
panels ticket edit t_example --input-json - <<'JSON'
{
  "recap": "Ready for review."
}
JSON
```

The server validates the intended final state and writes it in one transaction. Proposal
transitions, blockers, Worker restart, deletion, help, and messages keep dedicated
commands because they have effects beyond a Ticket field edit.

## Correct saved models

The administrative `model-corrections` commands repair saved models within their selected
variant. Preview accepts explicit targets. Repeat target flags to inspect several records.

```sh
panels model-corrections preview --ticket t_example --manager si_example --chief --worker-default coding > corrections.json
panels model-corrections apply < corrections.json
```

Preview prints JSON without changes to saved model choices. Review the proposed models before apply.
Apply reads the complete preview from stdin. It checks the original launch choices and
the proposed model again, so a stale preview cannot overwrite a later choice.
Each result reports its own success or refusal. Both commands accept `--json`.

Corrections preserve backend and reasoning effort. They work for idle Workers and
user-owned Stages. Active turns finish on their current model. Apply does not start a turn,
restart a Worker, approve work, or change its Stage. The server checks authority for
each target again at apply.

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

## Manager assignment and settlement

`manager create/list/show/set/workspace` keeps the existing Item identity and
conversation. Legacy `orchestrator` and `sprint item` commands remain hidden aliases. `set` takes `goal`, `recap`, `title`,
`project` and `project-id`. There is no `body` field: a stale `set <item> body` fails
rather than writing the wrong one. Artifact operations keep their existing
managed roots. `send-message --manager` emits the canonical recipient address. The exact issued `--sprint-item` reply target remains executable.
`manager create --goal-file <goal-file>` starts the Manager with the exact goal
as a queued message from Panels. It requires no explicit reply to the creator and creates
no Focus-visible `explicit_reply_missing` signal if the Manager sends no reply.
No replacement signal detects a clean delivery followed by silent inaction.
Full shows the goal under Panels. The CLI exposes no system attribution override.
A blank or absent goal leaves it idle. The result reports the saved manager and
startup outcome. Refused, failed, or uncertain startup still leaves the manager saved.
Inspect it and recover on that same manager instead of creating another one.
Uncertain startup gets no automatic replay. For failed or uncertain startup, check the
conversation before any resend.
`--json` preserves the complete creation response, including `startup`. As with message
sends, a successful HTTP response exits zero. Check the startup fate for delivery success.

Existing ordinary Managers can use it to coordinate with one another.
The permission sends a message; approvals, ceilings, assignments, and other writes
keep their existing authority requirements. Workers cannot use it to address an
unrelated Manager.

`ticket assign <ticket> --chief` assigns to Chief. `--manager <item>` assigns to
an ordinary Manager. Structured create and edit use the same explicit `manager`
reference. Assignment and the ceiling holder are independent.

`manager list` labels each row active or settled. With no state flag, it includes both.
Use `--active` or `--settled` to filter before pagination. The flags are mutually exclusive
and combine with search and Project filters. JSON summaries and `show` headers carry
the boolean `settled`, including selected-part reads. An absent field does not establish
that a Manager is active. Legacy aliases use the same behavior.

Settled Managers are finished destinations. Choose an active Manager for new
work before Ticket creation or assignment. This guidance does not change permissions
or prevent an existing Worker from continuing.

`manager settle <item>` is owner-only. Add `--active` to return it to active.
Chief cannot settle. Day and date-range Sprint commands are removed.
Legacy command aliases continue to address the same Managers.

## Compatibility and errors

Hidden aliases retain Worker, Ticket edit, proposal, and Item
conversation paths. No alias restores retired calendar writes.

`ticket restart-worker` also recovers an older rejected Ticket only when it is
Worker-owned, Empty, claim-free, attached to a conversation, and holds matching revision
feedback. Recovery keeps that conversation and refuses launch overrides.

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
