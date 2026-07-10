# Contract — Ticket implementation and closeout lifecycle

Ticket: `t_p6de8rje`

## Accepted outcome

Panels uses the visible sequence Success → Approach → Plan → Implementation → Closeout → Done. Implementation produces the reviewable work package. After it is approved, Closeout performs only the applicable merge, deploy, follow-up, and bookkeeping and proposes a final verified report. Done is terminal and has no field.

## Canonical model

- States: `needs_success`, `needs_approach`, `needs_plan`, `needs_implementation`, `needs_closeout`, `done`; `dropped` remains terminal outside the linear order.
- Fields: `success`, `approach`, `plan`, `implementation`, `closeout`.
- Gate mapping: each non-terminal linear state gates its same-named field.
- Advance mapping: Success → Approach → Plan → Implementation → Closeout → Done.
- `ticket_status`, ceiling, `at_cap`, proposal approval, return-for-revision, and employee execution retain their existing meanings. There is no ready/review lifecycle state.
- Implementer assignment and artifact-planning behavior are out of scope.

## Migration contract

- `in_progress` state/ceiling → `needs_implementation`.
- `needs_review` state/ceiling → `needs_closeout` for settled idle work.
- Legacy `result` slot → `implementation`; initialize `closeout` empty without losing value, proposal, or user note.
- Preserve terminal state, scope, status, recap, user note, sessions, placement, and events.
- A legacy `needs_review` row with non-empty runtime control represents an active/rejected implementation revision and must not be silently approved into Closeout. Keep it at `needs_implementation`. Reconstruct `awaiting_approval` as a pending Implementation proposal and cap it at Implementation so it still requires human approval.
- Cover both current-schema upgrades and the old project-column table-rebuild path.

## Surfaces

Update contracts, schema/migration, pure state logic, data/actions/API, runtime/readiness/employee prompts, queues/views, CLI and strict Chief external-work prefixes, frontend stage/approval rendering, source role skills, live docs, fixtures, unit tests, and browser tests.

## Verification

Use vertical test-first slices. Focused tests must prove transitions, ceilings, `stop`/`propose`, pending approval, return-for-revision, external-work exact prefixes, migration variants, labels, queues, and loaded skill text. Independent Codex implementation review precedes one final `./verify`.
