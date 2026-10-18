---
name: panels-ticket-creation
description: The shared Panels model for creating a coherent Ticket.
---

# Creating a Ticket

Use a Ticket for a concrete unit of work that one Worker can carry through its
lifecycle. An orchestrator holds the shared responsibility for its Workers. An idea is a loose thought that is not committed work. Choose the Worker type whose lifecycle
fits the job, use its registered specialist guidance, and let that type supply its
normal employee runtime defaults.

Give the Ticket a clear outcome-oriented title and a light, faithful Brief. Intake is
the user's request plus only the factual context the Worker needs to understand it.
Ground references to existing work, preserve agreed boundaries and dependencies, and
do not turn creation into premature planning.

## Explicit orchestrator assignment

A Worker names Chief or one ordinary orchestrator. Inspect existing orchestrators and
choose the one that describes responsibility for the job. Use Chief when no ordinary
orchestrator applies. Do not create a thin fallback Item for Chief.

The canonical field is `orchestrator`, with `kind` and `id`. Chief is
`{"kind":"chief","id":"chief"}`. An ordinary orchestrator is
`{"kind":"sprint_item","id":"<existing-item-id>"}`. Former Sprint Item IDs and
supervisor conversations remain stable. Assignment is independent of the ceiling holder
and Chief's global authority. Existing Project validation still applies.

Today and live Sprint placement are retired. Creation does not create a Day or Day
membership. Workers continue through normal rules without Today. Do not use settlement
as a replacement activation gate. Do not create, retype, or schedule `personal`,
`planning-day`, `planning-midday-check`, or `planning-sprint` work. Do not create
replacement recurring reviews. Existing personal Tickets keep their ordinary Stage gates.

## Timing and dependencies

A real calendar constraint belongs in the deadline. A genuine prerequisite belongs in
the Ticket's blockers so Panels can hold the dependent work until the prerequisite is
resolved.

## The ceiling stated at creation

By default a new Ticket parks its Brief for its creator's approval. That default is right
for ordinary intake: somebody wants to sense-check what work exists before it starts.

State the ceiling instead when the user gave you the ceiling to grant. The `ceiling` key says the
last thing the new Worker may do: it does that thing, proposes it, and waits for the
user. Stating a ceiling past the Brief settles the
Brief and starts the Ticket at its next Stage, so work the user has already authorized
begins instead of waiting for a second approval.

State only the ceiling you were actually given. Widening a Ticket beyond what the user
authorized is not a creation detail.

After creation, read the Ticket back as a whole. Its title, Worker type, Brief,
Project, explicit orchestrator assignment, deadline, and blockers must tell
one coherent story. Correct a mismatch through the
ordinary owning surface rather than compensating for it in prose.

Create the Ticket through one structured body:

```sh
panels ticket create --input-json - <<'JSON'
{
  "title": "Clear outcome title",
  "worker_type": "coding",
  "project_id": "project_example",
  "orchestrator": {"kind": "sprint_item", "id": "si_example"},
  "kickoff_note": "Faithful intake context",
  "ceiling": "needs_success_condition",
  "ceiling_holder": {"kind": "sprint_item", "id": "si_example"}
}
JSON
```

Only `title` and `worker_type` are always required. Add optional keys only when the
creation decision supplies them. Use canonical IDs and an explicit orchestrator reference. Read the created Ticket with `panels ticket show <id>`.
