# Implementation dispatch

Implement the accepted plan in the current clean shared worktree. Do not commit and do not run the final `./verify`.

## Expected ownership

- `web/src/App.svelte`
- `web/src/routes/BoardRoute.svelte`
- `tests/e2e/test_chief_of_staff.py`
- `docs/frontend.md` if the live Workspace description changes

## Required behavior

- `#/workspace/<ticket-id>` is the source of truth for the Workspace inspector.
- Card clicks update the route; Chief of Staff and embedded deletion return to `#/workspace`.
- Direct load, refresh, switching, back/forward, plain Workspace, and unknown ticket IDs behave as accepted.
- `#/board`, the shared embedded `TicketRoute`, and the shell-owned Hide done choice remain intact.

## Required proof

Run the Svelte check, production build, and focused Workspace/Chief of Staff Playwright coverage. Report exact commands and results; the integrator owns Codex diff review and the one canonical `./verify`.
