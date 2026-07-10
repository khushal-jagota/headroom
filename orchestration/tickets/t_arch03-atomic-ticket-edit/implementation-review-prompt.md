# Item 3 implementation review prompt

Review the complete item 3 diff against baseline commit `3fa3a14` and every
artifact in this directory. This is a read-only implementation review. Ignore
unrelated unstaged `PROGRESS.md`, `decisions.md`, and `.claude/` changes.

Check, with concrete file and line evidence:

1. `TicketEdit` is a `TypedDict(total=False)` with exactly title, user note,
   priority, deadline, project ID, and sprint ID. It contains no wire-level
   `project` alias or unrelated fields.
2. Ordinary PATCH preserves the exact ordering of unknown-key, empty-body,
   field-authority, type/enum/null parsing, and project selector resolution,
   then calls `tickets_data.edit_ticket` exactly once. No durable effect occurs
   while parsing or resolving aliases.
3. `edit_ticket` opens one `BEGIN IMMEDIATE`, loads once, overlays the intended
   final Ticket, and validates before its first write in deterministic title ->
   deadline -> project -> sprint order. Project/sprint references resolved by
   the route are revalidated under the lock. Parent-derived restrictions depend
   on key presence, including equal or null values.
4. A real compound edit issues one Ticket UPDATE, reloads before commit, emits
   only actual-change `ticket_updated` events in stable title, user note,
   priority, deadline, project ID, sprint ID order with the exact existing
   payloads, and advances `ticket_changed` context exactly once.
5. Equal stored values, including explicit null-to-null clears, are true no-ops:
   no UPDATE, timestamp change, event, context, or readiness ring. A post-UPDATE
   event/context failure demonstrably rolls back the Ticket row, events, and
   context in the same transaction.
6. All project name/ID matching, mismatch, unknown project/sprint, invalid title
   and deadline, and parent-derived error codes/messages/details are unchanged.
   Nullable and omitted semantics remain distinct.
7. Worker authority is checked for the complete key set before any value parse;
   a mixed permitted/forbidden request is wholly rejected. Permitted worker
   priority/deadline/sprint edits still succeed without direct-user context.
   Unattributed and explicit-Chief ordinary PATCH retain actor-neutral ordinary
   event/context semantics.
8. Ordinary edits remain allowed during an active worker status and running
   Ticket chat turn. No claim/chat/status control guard was added.
9. The six shallow ordinary field writers and all callers are removed, while
   `set_field_user_note` and every other semantic writer remain. There is no
   generic patch engine or sharing with Chief external-work writers.
10. Ordinary PATCH depends on no doorbell/action wrapper and still rings zero.
    Item 2 route/action and item 1 runtime contracts remain intact.
11. Chief create/reconcile production code and behavior are unchanged; explicit
    Chief use of ordinary PATCH does not redirect into reconciliation.
12. HTTP request/response shape and frontend behavior are unchanged. There are
    no frontend, CLI, schema, migration, event-kind, candidate 4/6, or gateway-
    correlation changes. Production/docs/tests remain within declared scope.

Report only actionable violations introduced or left unresolved by the diff.
For each, give severity, file/line evidence, violated contract, and a concise
fix. If none exist, end with exactly `NO VIOLATIONS`.
