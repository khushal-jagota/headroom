# t_mkkvq9qz — implementation dispatch

## Accepted contract

Add the smallest typed Ticket assignment:

- `implementer` is nullable and initially accepts exactly `khushal`, `panels_worker`,
  `hermes_codex`, or `hermes_claude`.
- It is ordinary Ticket metadata. Setting, changing, or clearing it is atomic and does not by
  itself change Ticket state, `ticket_status`, or readiness.
- It is shown and edited through the existing Ticket metadata-row interaction.
- The current value reaches the actual Hermes worker prompt. Selection/execution guidance lives in
  the `panels-worker` skill and plain-language docs, not a domain registry.
- When an accepted Plan advances `needs_plan -> needs_implementation`, `khushal` selects
  `user_takeover`. Agent assignments and `NULL` preserve the existing worker path.
- The assignment is human-overridable. A worker does not silently substitute another route.

## Explicit non-goals

No implementer table, catalog, account/capability system, automatic model router, new Ticket status,
new orchestration subsystem, board redesign, or new metadata interaction.

## Frozen wire values and labels

| Wire value | Ticket label | Skill meaning |
| --- | --- | --- |
| `khushal` | Khushal | Human judgment/access/external action/manual ownership; prepare a clear handoff. |
| `panels_worker` | Panels worker | Bounded work the current worker can complete directly. |
| `hermes_codex` | Hermes with Codex | Repository implementation with explicit test/review loop. |
| `hermes_claude` | Hermes with Claude | Broader/exploratory multi-file work needing sustained codebase reasoning. |

For Codex/Claude routes the Panels worker still owns the brief, integration, review, verification,
and result. If the route is unsuitable, it recommends a human override rather than changing it.

## Owned files

Production:

- `src/planner/core/db.py`
- `src/planner/tickets/contracts.py`
- `src/planner/tickets/data.py`
- `src/planner/tickets/api.py`
- `src/planner/tickets/views.py`
- `src/planner/runtime/employee_step_runner.py`
- `web/src/lib/types.ts`
- `web/src/routes/TicketRoute.svelte`
- `skills/panels-worker/SKILL.md`
- `docs/tickets-and-gates.md`

Tests:

- `tests/unit/test_db.py`
- `tests/unit/test_ticket_edit_api.py`
- `tests/unit/test_tickets_engine.py` and/or `tests/unit/test_ticket_lifecycle.py`
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_authctx_routes.py` only if needed to pin direct-only edit authority
- `tests/e2e/test_flows_a.py`

Ticket evidence may be written under this directory. Do not edit the unrelated current changes in
`src/planner/minds/shared_gateway.py`, `tests/unit/test_minds.py`, `docs/chat.md`, the frontend
worktree pointer, or their existing `PROGRESS.md` content.

## Vertical RED → GREEN slices

1. **Typed storage and migration.** First add failing fresh-schema and existing-schema migration
   assertions for the nullable checked column and all four values. Then add the enum/contract field,
   DDL column, idempotent migration, row mapping, and nullable creation storage. Prefer a simple
   post-lifecycle `ALTER TABLE ... ADD COLUMN` migration; do not extend the lifecycle rebuild unless
   current code proves it necessary.
2. **Atomic edit/read contract.** First add HTTP tests for valid set/change/clear, invalid value
   rollback, no-op behavior, event/context shape, and no state/status mutation. Then extend PATCH
   marshalling, direct-only authority, `TicketEdit`, the one ordinary edit transaction, and
   serialization. Do not ring readiness.
3. **Plan handoff.** First add direct-accept and auto-accepted Plan tests proving only
   `khushal` enters `user_takeover`, while each agent value and `NULL` keep the current path. Then
   put the decision in the canonical resolution-write boundary so the employee runner cannot clear
   takeover at turn settlement.
4. **Actual worker delivery.** First pin the exact submitted Hermes prompt for assigned and
   unassigned Tickets. Then include the current wire value in `_next_step_prompt`; do not put
   suitability prose in the backend.
5. **Existing metadata UI.** First add a Playwright flow that sees the at-rest value, sets, changes,
   clears, reloads, and observes no extra controls or status/state change. Then add one `EnumPill` in
   the existing facts row plus the `TicketDetail` type.
6. **Guidance/docs.** Add the agreed route-selection and accountability guidance to
   `skills/panels-worker/SKILL.md`. Keep `docs/tickets-and-gates.md` to the field, edit, prompt, and
   transition mechanics.

## Required proof

Focused checks must include the changed unit modules, the focused Playwright test, frontend build or
Svelte check used by the repository, Ruff/Mypy for touched Python, and `git diff --check`. After an
independent diff review is clean or fully dispositioned, run exactly one fresh `./verify` and preserve
its complete output. No commit: Closeout remains a separate Ticket stage.
