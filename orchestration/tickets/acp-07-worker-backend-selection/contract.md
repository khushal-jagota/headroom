# ACP-07 generic Ticket employee-backend selection

## Why this ticket exists

Codex and Claude Code are functional Panels worker backends, not test-only ACP adapters. A Ticket must
therefore name which registered ACP backend owns its one employee conversation before that employee
starts. The owner selected one restrained product model: each Worker type supplies the normal default,
and Kickoff shows that value already selected so a user may override it for this Ticket before work.

Claude Code and Codex are backend programs, not model IDs. This ticket must not overload the existing
optional `WorkerProfile.model`, infer a backend from model text, or add backend-specific branches to the
conversation runtime or UI.

## Frozen domain shape

1. `WorkerProfile` gains required non-empty `default_employee_backend: str`. Every production and
   test Worker-type definition declares it explicitly. The Worker-type manifest exposes the same key
   as `default_employee_backend`.
2. Every Ticket stores required non-empty `employee_backend: str`. Fresh creation copies the selected
   Worker type's default unless the caller supplies an explicit registered override. Existing rows
   migrate once to `hermes`; there is no live fallback for a missing value.
3. The backend key is independent from Worker type, specialist skill, model, reasoning effort, and
   toolset. A Coding Ticket may use Hermes, Codex, or Claude Code while retaining the same Coding
   workflow and specialist skill.
4. The single available-backend catalog is the exact set of definitions registered in the production
   ACP composition. It exposes stable keys in registration order. Worker-type defaults, creation
   overrides, Ticket edits, conversation employee resolution, and binding CAS all validate against
   that same catalog; no duplicated frontend/config allowlist exists.
5. The generic public catalog is returned with the existing Worker-type manifest resource as
   `employee_backends: string[]`. The frontend derives restrained labels from those stable keys. Adding
   a conformant definition at the one composition registration point makes it selectable without a
   backend-name conditional in Ticket UI.

## Kickoff selection and freeze

6. A new Ticket visibly shows its stored backend as the already-selected **worker** choice in the
   existing Kickoff experience. The control uses existing compact selection styling; it does not add a
   card, settings screen, ACP jargon, backend-specific explanation, or page redesign.
7. The canonical backend writer runs in one immediate transaction and permits a change only while all
   of these are true: the Ticket is still at its unaccepted `needs_kickoff` stage,
   `ticket_status` is either the ordinary fresh-Ticket `awaiting_approval` state or `empty`,
   `employee_session_id is None`, and no durable `conversation_session_bindings` row exists. Fresh
   Ticket creation deliberately enters `awaiting_approval` with its Kickoff proposal, so requiring
   `empty` alone would make the promised Kickoff selector unusable. The writer validates the requested
   key against the injected registered catalog, writes the Ticket once, and appends one exact Ticket
   event. Same-value input is an idempotent no-op.
8. As soon as the first human or automatic employee demand claims/creates a durable session—or Kickoff
   otherwise advances—the backend is frozen. Changing backend does not silently delete a session,
   remint a conversation, migrate history, cancel a worker, or rewrite an existing binding. A future
   explicit backend-migration product action would be a separate decision.
   Merely opening or observing a pristine Kickoff Ticket is not employee demand and must not create a
   session or binding. The existing conversation rail stays in place; its first explicit prompt may
   attach and freeze the stored selection, while Kickoff advance enables the ordinary eager attach.
9. Every live Ticket creation boundary may accept an explicit backend override and otherwise copies the
   Worker-type default. The stored value is always explicit in API/read models/copy fixtures; no route,
   CLI, importer, test fixture, or data writer privately defaults to `hermes`.
10. Chief of Staff remains on its production-composed default backend in this ticket. The owner chose
    Ticket/Worker-type selection; no Chief selector is invented.

## Runtime ownership

11. When a Ticket has no binding, `SqliteConversationBindingRepository.resolve_employee` reads the
    Ticket's stored `employee_backend`; it does not use a repository-wide Ticket default. First binding
    creation must use that exact key. When a binding exists, its backend must equal the stored Ticket
    backend or resolution fails closed.
12. Binding CAS accepts exactly the registered backend catalog and, for a Ticket, requires the candidate
    backend to equal its stored selection. Binding replacement/new conversation preserves that backend.
    `is_backend_available` checks registration, not equality with Hermes.
13. Ticket chat and `AcpStepGateway`/`EmployeeStepRunner` already resolve the same
    `ConversationEmployee`; they must therefore use the same selected backend, child, durable ACP
    session, typed transcript, and settlement path. No parallel “automatic-work backend” field exists.
14. The discovery loop, worker prompt, proposal resolution, Ticket status, and eligibility model do not
    change. Only the ACP child definition selected for that employee changes.

## Migration and cutover

15. The schema migration adds `tickets.employee_backend TEXT NOT NULL`. It assigns `hermes` to every
    existing Ticket because all pre-feature bindings/work used Hermes. Existing
    `conversation_session_bindings.backend_key` must already be `hermes`; any contradictory row aborts
    migration rather than being rewritten.
16. The one-time ACP legacy-session cutover may clear old session/binding rows as separately approved,
    but it does not alter a Ticket's stored backend selection. Disposable Codex and Claude dogfood
    Tickets are created fresh with their explicit selections.

## Required proof

- Fresh-schema and upgrade tests prove the non-null column, exact Hermes migration, contradictory
  binding failure, and no live missing-value fallback.
- Worker registry/manifest tests prove every definition's default is non-empty, registered, and served
  exactly; the test-only probe uses a non-first backend to catch order/default inference.
- Creation tests cover every HTTP/CLI/import/data boundary: absent override copies the Worker-type
  default; a registered override persists; unknown keys reject before mutation; read models and events
  carry the exact value.
- Canonical writer tests prove allowed pristine-Kickoff edits in both the ordinary fresh-Ticket
  `awaiting_approval` state and `empty`, same-value idempotence, and rejection after any other
  status/session/binding/stage change, including a transaction race with first binding creation.
- Binding repository and production-composition tests prove first demand selects the Ticket backend,
  existing binding mismatch fails closed, replacement preserves it, and backend availability comes from
  the registered definitions.
- Browser runtime tests mount the real fresh `awaiting_approval` Ticket route, prove observation creates
  no binding/session mirror, and prove the compact control is preselected, changes/persists while
  allowed, uses only served catalog options, and becomes non-editable after the backend freezes without
  changing the existing Panels layout. First prompt and Kickoff-advance paths both attach exactly once.
- A fake non-Hermes production-composition e2e runs one human Ticket prompt and one automatic Employee
  step on the same selected backend/session. ACP-07 Codex and ACP-08 Claude then repeat that proof with
  their real definitions through Computer Use.
- Focused Ruff, strict Mypy, affected unit/e2e/frontend suites pass. Do not run canonical `./verify`;
  ACP-10 owns the single final gate.

## Initial allowed-file families

The implementation plan must replace this family list with exact files before source work:

- Worker-type contracts, registry, production/test definitions, manifest tests;
- Ticket contracts, schema/migration, canonical data writer, API/CLI/import ingress, views/events and
  their focused tests;
- ACP production composition/registry catalog and SQLite binding repository plus focused tests;
- existing TypeScript Ticket/Worker-manifest types, resource invalidation coverage, the Ticket Kickoff
  route/control, the smallest deferred first-attach controller/component boundary, and focused browser
  tests;
- this ticket's plan/review/report/evidence files, `PROGRESS.md`, and `decisions.md`.

No Codex/Claude definition implementation, conversation wire type, ACP transcript component, turn
broker behavior, legacy deletion, Chief selector, Worker workflow/stage logic, model selector, shared
visual token, or unrelated page is in scope.
