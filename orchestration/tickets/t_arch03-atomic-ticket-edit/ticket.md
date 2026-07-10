# t_arch03 — Make an ordinary Ticket edit one atomic operation

## Outcome

One `PATCH /api/tickets/{ticket_id}` request either applies all requested ordinary Ticket attributes and their existing events together, or applies none. A request that changes nothing is a true no-op.

This is the ordinary edit operation only. Chief external-work create/reconcile and all state, scope, proposal, approval, settled-field, recap, and lifecycle operations remain separate semantic operations.

## Public contracts

- Keep the existing HTTP PATCH shape, including `project`/`project_id` selector behavior and current authorization/error contracts.
- Add one typed `TicketEdit` contract containing only optional ordinary attributes: title, user note, priority, deadline, project ID, and sprint ID.
- Expose one public data writer, `edit_ticket`, for this operation. Remove or make private the six public one-field writers after callers migrate.
- The route fully parses enums/nulls/selectors, checks unknown keys and field-level authority, and resolves project/sprint references before calling the writer exactly once.
- The writer opens one `BEGIN IMMEDIATE`, loads the current Ticket once, overlays and validates the intended final Ticket as a whole, computes actual changes, and writes all changed columns together.
- Validation includes title, deadline, referenced project/sprint existence, and parent-derived project/sprint restrictions before any write.
- Preserve existing per-field `ticket_updated` event kinds and payloads. Emit them only for actual changes, in stable order: title, user note, priority, deadline, project ID, sprint ID.
- One successful compound direct edit records worker `ticket_changed` context once, not once per field.
- A true no-op changes neither `updated_at`, event history, nor worker context.
- Ordinary edits remain actor-neutral and allowed during active worker work. Existing worker field permissions remain field-level. Chief may use the ordinary route without changing its meaning.
- Ordinary PATCH does not ring readiness.

## Red-first acceptance tests

1. A compound PATCH with an early valid value and a later invalid/forbidden value returns the existing error and leaves Ticket values, `updated_at`, events, and worker context unchanged.
2. One PATCH changing all six attributes persists all six values, emits the exact existing per-field payloads in stable order, and advances `ticket_changed` once.
3. A PATCH containing only values already stored returns success and changes no timestamp, event, or worker context.
4. A worker PATCH mixing one permitted field with one forbidden field rejects the entire request; existing worker priority/deadline permissions still work.
5. Unattributed and explicit-Chief ordinary PATCH calls remain permitted under the existing actor-neutral contract.
6. Preserve project name/ID matching, mismatch, unknown-project, unknown-sprint, parent-project, and parent-sprint error codes/messages.
7. Preserve Chief external-work create/reconcile and current frontend direct-edit behavior without frontend changes.

## Contract and implementation scope

- `src/planner/tickets/contracts.py`
- `src/planner/tickets/data.py`
- `src/planner/tickets/api.py`
- Focused Ticket edit, authority, worker-context, Ticket engine, and Chief external-work regressions.
- Update `docs/tickets-and-gates.md` only where the ordinary edit transaction is explained.

## Explicit exclusions

- No frontend, schema, migration, CLI, new event kind, generic patch engine, readiness behavior, Chief-route, or external-work writer change.
- Do not share implementation with Chief reconciliation merely because both write several columns; their authority, active-work rules, state meaning, and events are different.
- No candidate 4 or candidate 6 work.
