# t_mkkvq9qz — frontend, skill, and docs dispatch

This slice starts only after the backend wire contract is green.

## Frozen backend contract

`GET/PATCH /api/tickets/{id}` carries nullable `implementer` with values:

- `khushal` → `Khushal`
- `panels_worker` → `Panels worker`
- `hermes_codex` → `Hermes with Codex`
- `hermes_claude` → `Hermes with Claude`
- `null` → `(unassigned)`

PATCH is direct-only, atomic ordinary Ticket metadata. Editing does not itself change state or
`ticket_status`.

## Owned files

- `web/src/lib/types.ts`
- `web/src/routes/TicketRoute.svelte`
- `tests/e2e/test_flows_a.py`
- `skills/panels-worker/SKILL.md`
- `docs/tickets-and-gates.md`
- `orchestration/tickets/t_mkkvq9qz-implementer-assignment/frontend-report.md`

Do not edit backend files or unrelated dirty files.

## Strict vertical TDD

1. Add one focused Playwright test first. It must fail because the existing Ticket facts row has no
   implementer selector. Prove the selector is in the existing facts row, starts unassigned, sets
   Khushal, changes to an agent route, clears, and restores the cleared value after reload. At each
   edit, prove Ticket state and `ticket_status` do not change and no new edit/save/cancel interaction
   appears.
2. Add the smallest `TicketDetail` union/nullable field and one existing `EnumPill` in
   `TicketRoute.svelte` with `keyLabel="implementer"`, a stable `data-implementer` test hook, and the
   frozen labels. Reuse the existing `patch` and invalidation path; add no component, modal, tooltip,
   or new CSS unless the established row proves it necessary.
3. Add a concise `Implementer assignment` section to the worker skill:
   - It is a human-overridable execution route, not an account/capability system or automatic router.
   - Khushal: human judgment/access/external action/manual ownership; prepare a clear handoff.
   - Panels worker: bounded work completed directly with normal tools.
   - Hermes with Codex: repository implementation with explicit tests and review.
   - Hermes with Claude: broader/exploratory multi-file work needing sustained codebase reasoning.
   - Never silently substitute; recommend a human override when unsuitable.
   - For delegated routes the Panels worker retains the brief, integration, review, verification,
     and result accountability.
4. Update `docs/tickets-and-gates.md` only with mechanics: nullable fixed assignment, ordinary edit,
   actual worker prompt visibility, and the Khushal handoff when Plan is accepted. Do not duplicate
   suitability prose there.

## Proof

Capture exact RED and GREEN commands in `frontend-report.md`. Run the focused Playwright test,
frontend build/Svelte check used by the repository, any touched static checks, and
`git diff --check`. Do not commit or run `./verify`.
